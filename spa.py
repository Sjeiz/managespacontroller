"""The spa state machine and the fault interlock. Only the main loop calls into it."""

import logging

from entities import Input, Monitor, Output, TemperatureSensor

log = logging.getLogger(__name__)

STANDBY = "Standby"
SESSION = "Session"
MAINTENANCE = "Maintenance"
ERROR = "Error"
MANUAL = "Manual"  # reported only: Standby with an output on that is off by default
FROST = "Frost"  # reported only: a force_on monitor (frost protection) is active


class Spa:
    def __init__(self, config, entities, session_switch, operation_sensor, status_sensor, publisher):
        """entities: dict unique_id -> entity (outputs, inputs, sensors, monitors)."""
        self.entities = entities
        self.session_switch = session_switch
        self.operation_sensor = operation_sensor
        self.status_sensor = status_sensor
        self._publisher = publisher

        # Only outputs with a command topic are spa outputs; internal outputs (buzzer,
        # sensor power) are managed by their owners and never touched here.
        self.outputs = {
            uid: entity
            for uid, entity in entities.items()
            if isinstance(entity, Output) and entity.controllable
        }
        self.monitors = [entity for entity in entities.values() if isinstance(entity, Monitor)]

        self.session_list = [list(step) for step in config["session_list"]]
        self.maintenance_list = [list(step) for step in config["maintenance_list"]]
        self.stagger_secs = float(config.get("stagger_secs", 1))
        self.maintenance_times = list(config.get("maintenance_times", []))
        self.flush_secs = float(config.get("flush_secs", 30))
        self.circulation_secs = float(config.get("circulation_secs", 3600))
        self.circulation = config["circulation"]
        self._validate()

        self.state = STANDBY
        self.fault = False
        self._sequence = []
        self._next_step_at = 0.0
        self._flush_until = None
        self._circulation_until = None
        self._last_maintenance_slot = None

    def _validate(self):
        # Outputs moved to the config's "disabled" section are skipped in the lists
        listed = {uid for step in self.session_list + self.maintenance_list for uid in step}
        skipped = sorted(uid for uid in listed if uid not in self.outputs)
        if skipped:
            log.warning("Spa list skips unknown outputs: %s", skipped)
            self.session_list = self._known_steps(self.session_list)
            self.maintenance_list = self._known_steps(self.maintenance_list)
        names = {self.circulation}
        names.update(monitor.force_on for monitor in self.monitors if monitor.force_on)
        unknown = sorted(name for name in names if name not in self.outputs)
        if unknown:
            raise ValueError(f"Spa config refers to unknown outputs: {unknown}")

    def _known_steps(self, steps):
        known = [[uid for uid in step if uid in self.outputs] for step in steps]
        return [step for step in known if step]

    # ---- lists ---------------------------------------------------------------

    @staticmethod
    def _names(steps):
        return [uid for step in steps for uid in step]

    def _list_outputs(self):
        return set(self._names(self.session_list)) | set(self._names(self.maintenance_list))

    # ---- startup, commands, readings -----------------------------------------

    def start(self):
        """Initial state at controller start; call after the first sensor readings."""
        self._evaluate_monitors()
        if any(monitor.is_on for monitor in self._problem_monitors()):
            self._activate_fault()
        else:
            self._apply_initial_states()
        self._publish_state()
        self._publisher.state(self.status_sensor)

    def handle_command(self, target, payload, now):
        log.info("Message received: target=%s, value=%s", target, payload)
        if target == self.session_switch.unique_id:
            if payload == self.session_switch.payload_on:
                self._enter_session(now)
            elif self.state in (SESSION, MAINTENANCE):
                self._to_standby()
            self._publish_state()
        elif target in self.outputs:
            self.switch(target, payload == self.outputs[target].payload_on)
        else:
            log.warning("Command for unknown target %s ignored", target)

    def handle_reading(self, uid, value):
        entity = self.entities.get(uid)
        if entity is None:
            return
        if entity.apply(value):
            self._publisher.state(entity)

    # ---- main loop -----------------------------------------------------------

    def tick(self, now, wallclock):
        """now: monotonic seconds; wallclock: local datetime."""
        self._evaluate_monitors()
        self._update_fault()
        self._apply_force_on()
        self._check_maintenance_time(now, wallclock)
        self._advance_sequence(now)
        self._check_maintenance_timers(now)

    def _evaluate_monitors(self):
        # Config order matters: a monitor may check monitors defined before it,
        # and the first active monitor determines the Spa Status value
        changed = False
        for monitor in self.monitors:
            if monitor.evaluate(self.entities):
                self._publisher.state(monitor)
                changed = True
        if changed:
            self._publish_operation()
            self._publisher.state(self.status_sensor)

    def _problem_monitors(self):
        return [monitor for monitor in self.monitors if monitor.device_class == "problem"]

    # ---- fault interlock -----------------------------------------------------

    def _update_fault(self):
        problem = any(monitor.is_on for monitor in self._problem_monitors())
        if problem and not self.fault:
            self._activate_fault()
            self._publish_state()
        elif not problem and self.fault:
            self.fault = False
            log.info("Fault: cleared")
            self._apply_initial_states()
            self._publish_state()

    def _activate_fault(self):
        warnings = [m.warning for m in self._problem_monitors() if m.is_on and m.warning]
        log.warning("Fault: active (%s)", ", ".join(warnings) or "problem")
        self.fault = True
        self._to_standby()
        for uid in self.outputs:
            self.switch(uid, False, restore_conflict=False)

    def warnings(self):
        return [m.warning for m in self._problem_monitors() if m.is_on and m.warning]

    # ---- force_on (frost protection); the fault interlock takes precedence ---

    def _forced_on(self, uid):
        return any(m.is_on and m.force_on == uid for m in self.monitors)

    def _apply_force_on(self):
        if self.fault:
            return
        for monitor in self.monitors:
            if monitor.is_on and monitor.force_on and not self.outputs[monitor.force_on].is_on:
                log.info("Force on: %s (%s)", monitor.force_on, monitor.unique_id)
                self.switch(monitor.force_on, True)

    # ---- switching: the single place where spa outputs are switched ----------

    def switch(self, uid, on, restore_conflict=True):
        """Switch a spa output, applying the fault interlock, conflict and requires rules.
        Returns True if the output ends up in the requested state."""
        output = self.outputs[uid]
        if on:
            if output.is_on:
                return True
            if self.fault:
                return self._refuse(output, "fault active")
            if output.requires and not self.outputs[output.requires].is_on:
                return self._refuse(output, f"requires {output.requires}")
            if output.conflict and self._forced_on(output.conflict):
                return self._refuse(output, f"{output.conflict} is forced on")
            if output.conflict and self.outputs[output.conflict].is_on:
                self.switch(output.conflict, False, restore_conflict=False)
            self._write(output, True)
            return True

        if not output.is_on:
            return True
        if not self.fault and self._forced_on(uid):
            return self._refuse(output, "forced on", requested=False)
        self._write(output, False)
        for dependent in self.outputs.values():
            if dependent.requires == uid and dependent.is_on:
                self.switch(dependent.unique_id, False)
        if restore_conflict and output.conflict:
            partner = self.outputs[output.conflict]
            if partner.initial_on and not partner.is_on:
                self.switch(partner.unique_id, True)
        return True

    def _write(self, output, on):
        output.write(on)
        log.info("Output %s -> %s", output.unique_id, output.state)
        self._publisher.state(output)
        self._publish_operation()

    def _refuse(self, output, reason, requested=True):
        log.info("Refused: %s %s (%s)", output.unique_id, "on" if requested else "off", reason)
        # Republish the actual state so the HA switch falls back
        self._publisher.state(output)
        return False

    def _apply_initial_states(self):
        for output in self.outputs.values():
            self.switch(output.unique_id, output.initial_on, restore_conflict=False)

    # ---- state machine -------------------------------------------------------

    def _set_state(self, state):
        if state != self.state:
            log.info("State: %s -> %s", self.state, state)
            self.state = state

    def _enter_session(self, now):
        if self.fault:
            log.info("Refused: session (fault active)")
            return
        if self.state == SESSION:
            return
        self._cancel_timers()
        self._set_state(SESSION)
        self._start_list(self.session_list, self.maintenance_list, now)

    def _enter_maintenance(self, now):
        self._set_state(MAINTENANCE)
        self._flush_until = None  # starts when the list has been switched on
        self._circulation_until = now + self.circulation_secs
        self._start_list(self.maintenance_list, self.session_list, now)
        self._publish_state()

    def _to_standby(self):
        self._cancel_timers()
        self._sequence = []
        for uid in self._list_outputs():
            self.switch(uid, False)
        self._set_state(STANDBY)

    def _cancel_timers(self):
        self._flush_until = None
        self._circulation_until = None

    def _start_list(self, steps, other_steps, now):
        keep = set(self._names(steps))
        for uid in self._names(other_steps):
            if uid not in keep:
                self.switch(uid, False)
        self._sequence = [list(step) for step in steps]
        self._next_step_at = now
        self._advance_sequence(now)

    def _advance_sequence(self, now):
        while self._sequence and now >= self._next_step_at:
            step = self._sequence.pop(0)
            switched = False
            for uid in step:
                if not self.outputs[uid].is_on and self.switch(uid, True):
                    switched = True
            if switched:
                # Stagger motor starts to stay under the breaker's inrush limit
                self._next_step_at = now + self.stagger_secs
            if not self._sequence and self.state == MAINTENANCE and self._flush_until is None:
                self._flush_until = now + self.flush_secs

    def _check_maintenance_time(self, now, wallclock):
        slot = wallclock.strftime("%Y-%m-%d %H:%M")
        if wallclock.strftime("%H:%M") not in self.maintenance_times or slot == self._last_maintenance_slot:
            return
        self._last_maintenance_slot = slot
        if self.state == STANDBY and not self.fault:
            self._enter_maintenance(now)

    def _check_maintenance_timers(self, now):
        if self.state != MAINTENANCE:
            return
        if self._flush_until is not None and now >= self._flush_until:
            self._flush_until = float("inf")  # done; don't flush again in this run
            for uid in self._names(self.maintenance_list):
                if uid != self.circulation:
                    self.switch(uid, False)
        if self._circulation_until is not None and now >= self._circulation_until:
            self._to_standby()
            self._publish_state()

    # ---- reporting -----------------------------------------------------------

    def status_line(self):
        """One-line summary for the periodic status log."""
        status = self.operation()
        if self.fault:
            status += f" ({', '.join(self.warnings()) or 'problem'})"
        on = " ".join(o.short_name or o.unique_id for o in self.outputs.values() if o.is_on) or "-"
        temperatures = ", ".join(
            f"{e.name}: {'-' if e.value is None else e.value} °C"
            for e in self.entities.values()
            if isinstance(e, TemperatureSensor)
        )
        inputs = ", ".join(
            f"{e.name}: {e.state or '-'}" for e in self.entities.values() if isinstance(e, Input)
        )
        return f"Status: {status} | on: {on} | {temperatures} | {inputs}"

    def operation(self):
        """Value reported as spa_operation."""
        # Display priority: Error, Session, Maintenance, Frost, Manual, Standby
        if self.fault:
            return ERROR
        if self.state != STANDBY:
            return self.state
        if any(m.is_on and m.force_on for m in self.monitors):
            return FROST
        if any(o.is_on and not o.initial_on for o in self.outputs.values()):
            return MANUAL
        return STANDBY

    def _publish_operation(self):
        value = self.operation()
        if value != self.operation_sensor.value:
            self.operation_sensor.value = value
            self._publisher.state(self.operation_sensor)

    def _publish_state(self):
        self.session_switch.is_on = self.state in (SESSION, MAINTENANCE)
        self._publisher.state(self.session_switch)
        self.operation_sensor.value = self.operation()
        self._publisher.state(self.operation_sensor)
