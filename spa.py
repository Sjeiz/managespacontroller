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
ACTIVE = "Active"  # reported for the Session state


class Spa:
    def __init__(self, config, entities, session_switch, operation_sensor, status_sensor, response_sensor, publisher):
        """entities: dict unique_id -> entity (outputs, inputs, sensors, monitors)."""
        self.entities = entities
        self.session_switch = session_switch
        self.operation_sensor = operation_sensor
        self.status_sensor = status_sensor
        self.response_sensor = response_sensor
        self._publisher = publisher

        # Only outputs with a command topic are spa outputs; internal outputs (buzzer,
        # sensor power) are managed by their owners and never touched here.
        self.outputs = {
            uid: entity
            for uid, entity in entities.items()
            if isinstance(entity, Output) and entity.controllable
        }
        self.monitors = [entity for entity in entities.values() if isinstance(entity, Monitor)]

        # Each output says which runs start it, so the state machine holds no lists.
        # The order is that of the outputs in the config; only the staggering matters.
        self.session_outputs = [uid for uid, output in self.outputs.items() if output.session]
        self.maintenance_outputs = [
            uid for uid, output in self.outputs.items() if output.maintenance_secs is not None
        ]
        self.stagger_secs = float(config.get("stagger_secs", 1))
        self.maintenance_times = list(config.get("maintenance_times", []))
        self._validate()

        self.state = STANDBY
        self.fault = False
        self._starting = []  # outputs still to be switched on, one per stagger delay
        self._next_start_at = 0.0
        self._until = {}  # uid -> monotonic deadline, while a maintenance run drives it
        self._last_maintenance_slot = None
        self._command_target = None  # target of the command being handled, for refusal reporting
        self._refusal = None

    def _validate(self):
        names = {monitor.force_on for monitor in self.monitors if monitor.force_on}
        unknown = sorted(name for name in names if name not in self.outputs)
        if unknown:
            raise ValueError(f"Spa config refers to unknown outputs: {unknown}")

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
        """Handle a command from HA or the web page. Returns the refusal reason, or None."""
        log.info("Message received: target=%s, value=%s", target, payload)
        self._command_target = target
        self._refusal = None
        if target == self.session_switch.unique_id:
            if payload == self.session_switch.payload_on:
                self._enter_session(now)
            elif self.state == SESSION:
                self._end_session()
            self._publish_state()
        elif target in self.outputs:
            self.switch(target, payload == self.outputs[target].payload_on)
        else:
            log.warning("Command for unknown target %s ignored", target)
            self._refusal = f"unknown target {target}"
        self._command_target = None
        result = "OK" if self._refusal is None else f"refused ({self._refusal})"
        self.response_sensor.set(f"{target} {payload}: {result}")
        self._publisher.state(self.response_sensor)
        return self._refusal

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
        self._advance_start(now)
        self._check_deadlines(now)

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
        self._abandon_run()
        self._set_state(STANDBY)
        for uid in self.outputs:
            self.switch(uid, False)

    def warnings(self):
        return [m.warning for m in self._problem_monitors() if m.is_on and m.warning]

    def forced_warnings(self):
        """Warnings of active force_on monitors (frost protection), for the display."""
        return [m.warning for m in self.monitors if m.is_on and m.force_on and m.warning]

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

    def switch(self, uid, on):
        """Switch a spa output, applying the fault interlock.
        Returns True if the output ends up in the requested state."""
        output = self.outputs[uid]
        if on:
            if output.is_on:
                return True
            if self.fault:
                return self._refuse(output, "fault active")
            self._write(output, True)
            return True

        if not output.is_on:
            return True
        if not self.fault and self._forced_on(uid):
            return self._refuse(output, "forced on", requested=False)
        self._write(output, False)
        return True

    def _write(self, output, on):
        output.write(on)
        log.info("Output %s -> %s", output.unique_id, output.state)
        self._publisher.state(output)
        self._publish_operation()

    def _refuse(self, output, reason, requested=True):
        log.info("Refused: %s %s (%s)", output.unique_id, "on" if requested else "off", reason)
        if output.unique_id == self._command_target and self._refusal is None:
            self._refusal = reason
        # Republish the actual state so the HA switch falls back
        self._publisher.state(output)
        return False

    def _apply_initial_states(self):
        """The stand without a run: controller start, a cleared fault. An output already
        at its initial_state is not written, so no relay changes level."""
        for output in self.outputs.values():
            self.switch(output.unique_id, output.initial_on)

    # ---- state machine -------------------------------------------------------

    def _set_state(self, state):
        if state != self.state:
            log.info("State: %s -> %s", self.state, state)
            self.state = state

    def _enter_session(self, now):
        if self.fault:
            log.info("Refused: session (fault active)")
            self._refusal = "fault active"
            return
        if self.state == SESSION:
            return
        if self.state == MAINTENANCE:
            self._stop_run()  # the maintenance run ends here; what it drove goes off
        self._set_state(SESSION)
        self._start(self.session_outputs, now)

    def _enter_maintenance(self, now):
        self._set_state(MAINTENANCE)
        self._start(self.maintenance_outputs, now)
        self._publish_state()

    def _end_session(self):
        """The user ended the session, so the spa goes quiet: every output off."""
        self._abandon_run()
        for uid in self.outputs:
            self.switch(uid, False)
        self._set_state(STANDBY)

    def _stop_run(self):
        """Stop what the running run drives and forget its schedule."""
        self._starting = []
        for uid in list(self._until):
            del self._until[uid]
            self.switch(uid, False)

    def _abandon_run(self):
        """Forget the schedule without switching; the caller switches the outputs itself."""
        self._starting = []
        self._until = {}

    def _start(self, uids, now):
        self._starting = list(uids)
        self._next_start_at = now
        self._advance_start(now)

    def _advance_start(self, now):
        """Switch on the outputs of the run that is starting, one per stagger delay."""
        while self._starting and now >= self._next_start_at:
            uid = self._starting.pop(0)
            output = self.outputs[uid]
            if self.state == MAINTENANCE and output.maintenance_secs is not None:
                self._until[uid] = now + output.maintenance_secs
            if not output.is_on and self.switch(uid, True):
                # Stagger motor starts to stay under the breaker's inrush limit
                self._next_start_at = now + self.stagger_secs

    def _check_deadlines(self, now):
        """Every participant of a maintenance run goes off when its own time is up. Once
        they all have, the run is over and there is nothing left to switch off."""
        if self.state != MAINTENANCE:
            return
        for uid in [uid for uid, until in self._until.items() if now >= until]:
            del self._until[uid]
            self.switch(uid, False)
        if not self._starting and not self._until:
            self._set_state(STANDBY)
            self._publish_state()

    def _check_maintenance_time(self, now, wallclock):
        slot = wallclock.strftime("%Y-%m-%d %H:%M")
        if wallclock.strftime("%H:%M") not in self.maintenance_times or slot == self._last_maintenance_slot:
            return
        self._last_maintenance_slot = slot
        if self.state == STANDBY and not self.fault:
            self._enter_maintenance(now)

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

    def snapshot(self):
        """Current state for the web page (built in the main loop, read by the web thread)."""
        return {
            "operation": self.operation(),
            "status": self.status_sensor.state,
            "active": self.status_sensor.active(),
            "controls": [{"id": self.session_switch.unique_id, "name": self.session_switch.name, "on": self.session_switch.is_on}]
            + [{"id": o.unique_id, "name": o.name, "on": o.is_on} for o in self.outputs.values()],
            "values": [
                {"name": e.name, "value": e.value, "unit": e.unit_of_measurement or ""}
                for e in self.entities.values()
                if isinstance(e, TemperatureSensor)
            ]
            + [{"name": e.name, "value": e.state, "unit": ""} for e in self.entities.values() if isinstance(e, Input)],
        }

    def operation(self):
        """Value reported as spa_operation."""
        # Display priority: Error, Active (Session), Maintenance, Frost, Manual, Standby
        if self.fault:
            return ERROR
        if self.state == SESSION:
            return ACTIVE
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
        # The switch is the user's session, not a display of the machine: a maintenance
        # run leaves it off, so switching it on hands that run over to a session.
        self.session_switch.is_on = self.state == SESSION
        self._publisher.state(self.session_switch)
        self.operation_sensor.value = self.operation()
        self._publisher.state(self.operation_sensor)
