"""Entities of the spa controller: everything that is a GPIO, sensor or HA entity."""

import json
import logging
import threading
import time
from datetime import datetime, timezone

import RPi.GPIO as GPIO

log = logging.getLogger(__name__)


class Publisher:
    """Publishes entity states and HA discovery messages over MQTT."""

    def __init__(self, client, device, qos):
        self._client = client
        self._device = device
        self._qos = qos
        self.timestamp = None  # TimestampSensor, published along with every state

    def state(self, entity):
        if entity.state_topic is None or entity.state is None:
            return
        self._client.publish(entity.state_topic, entity.state)
        attributes = entity.attributes()
        if attributes is not None:
            self._client.publish(entity.state_topic + "/attributes", json.dumps(attributes))
        if self.timestamp is not None:
            self._client.publish(self.timestamp.state_topic, self.timestamp.state)

    def discovery(self, entity):
        if entity.config_topic is None:
            return
        payload = {"device": self._device}
        payload.update(entity.discovery_payload())
        self._client.publish(
            entity.config_topic, json.dumps(payload), qos=self._qos, retain=True
        )

    def status(self, topic, payload):
        self._client.publish(topic, payload, qos=self._qos)


class Entity:
    """Common part of everything that is published to HA."""

    def __init__(self, unique_id, config):
        self.unique_id = unique_id
        self.name = config.get("name", unique_id)
        self.device_class = config.get("device_class")
        self.config_topic = config.get("config_topic")
        self.state_topic = config.get("state_topic")
        self.command_topic = config.get("command_topic")
        self.unit_of_measurement = config.get("unit_of_measurement")
        self.value_template = config.get("value_template")
        self.payload_on = config.get("payload_on", "on")
        self.payload_off = config.get("payload_off", "off")

    @property
    def state(self):
        """Payload to publish on the state topic, or None if unknown."""
        return None

    def attributes(self):
        """Extra attributes published as JSON on <state_topic>/attributes, or None."""
        return None

    def discovery_payload(self):
        payload = {
            "name": self.name,
            "unique_id": self.unique_id,
            "state_topic": self.state_topic,
        }
        optional = {
            "device_class": self.device_class,
            "command_topic": self.command_topic,
            "unit_of_measurement": self.unit_of_measurement,
            "value_template": self.value_template,
        }
        payload.update({key: value for key, value in optional.items() if value is not None})
        return payload


class BinaryEntity(Entity):
    """An entity with an on/off state."""

    def __init__(self, unique_id, config):
        super().__init__(unique_id, config)
        self.is_on = False

    @property
    def state(self):
        return self.payload_on if self.is_on else self.payload_off

    def discovery_payload(self):
        payload = super().discovery_payload()
        payload.update({"payload_on": self.payload_on, "payload_off": self.payload_off})
        return payload


class Output(BinaryEntity):
    """A relay output. Switches its pin and remembers the state; contains no rules."""

    def __init__(self, unique_id, config):
        super().__init__(unique_id, config)
        self.pin = int(config["pin"])
        self.gpio_on = int(config.get("gpio_on", 1))
        self.gpio_off = int(config.get("gpio_off", 0))
        self.initial_on = config.get("initial_state", "off") == "on"
        self.conflict = config.get("conflict")
        self.requires = config.get("requires")
        self.short_name = config.get("short_name")

    @property
    def controllable(self):
        """Outputs with a command topic are controlled by the Spa and HA."""
        return self.command_topic is not None

    def setup(self):
        # Drive the off-level before switching to output, so active-low relays don't pulse on at startup
        GPIO.setup(self.pin, GPIO.OUT, initial=self.gpio_off)
        self.is_on = False

    def write(self, on):
        GPIO.output(self.pin, self.gpio_on if on else self.gpio_off)
        self.is_on = on


class MeasuringEntity(Entity):
    """An entity that measures in its own thread and posts results to the queue."""

    def __init__(self, unique_id, config):
        super().__init__(unique_id, config)
        self.interval_secs = float(config.get("interval_secs", 10))
        self.value = None
        self.measured = False  # True once the first result has been applied

    def measure(self):
        """Return a new value, or None if the measurement failed. Runs in the thread."""
        raise NotImplementedError

    def apply(self, value):
        """Apply a measurement from the queue (main loop). Returns True if the state changed."""
        old_state = self.state
        self.value = value
        self.measured = True
        return self.state != old_state

    def start(self, events):
        thread = threading.Thread(
            target=self._run, args=(events,), name=self.unique_id, daemon=True
        )
        thread.start()

    def _run(self, events):
        while True:
            try:
                value = self.measure()
            except Exception:
                log.exception("Measurement %s failed", self.unique_id)
                value = None
            events.put(("reading", self.unique_id, value))
            time.sleep(self.interval_secs)


class Input(MeasuringEntity):
    """A digital input, read once per measuring interval."""

    _PULL = {"up": GPIO.PUD_UP, "down": GPIO.PUD_DOWN, "off": GPIO.PUD_OFF}

    def __init__(self, unique_id, config):
        super().__init__(unique_id, config)
        self.pin = int(config["pin"])
        self.gpio_on = int(config.get("gpio_on", 1))
        self.pull = self._PULL.get(config.get("pull_up_down", "down"), GPIO.PUD_DOWN)

    @property
    def is_on(self):
        return self.value is True

    @property
    def state(self):
        if self.value is None:
            return None
        return self.payload_on if self.value else self.payload_off

    def setup(self):
        GPIO.setup(self.pin, GPIO.IN, pull_up_down=self.pull)

    def measure(self):
        return GPIO.input(self.pin) == self.gpio_on

    def discovery_payload(self):
        payload = super().discovery_payload()
        payload.update({"payload_on": self.payload_on, "payload_off": self.payload_off})
        return payload


class WaterLevelSensor(Input):
    """Water level input that is only powered while measuring, to limit electrolysis."""

    def __init__(self, unique_id, config):
        super().__init__(unique_id, config)
        self.power_id = config.get("power")
        self.settle_secs = float(config.get("settle_secs", 0.1))
        self.power = None  # Output, resolved after all outputs are built

    def measure(self):
        if self.power is None:
            return super().measure()
        # The power output is only used by this sensor, so switching it from this thread is safe
        self.power.write(True)
        try:
            time.sleep(self.settle_secs)
            return super().measure()
        finally:
            self.power.write(False)


class TemperatureSensor(MeasuringEntity):
    """A DS18B20 1-wire temperature sensor."""

    _READ_ATTEMPTS = 5

    def __init__(self, unique_id, config):
        super().__init__(unique_id, config)
        self.filename = config["filename"]
        self.scale = float(config.get("scale", 1))
        self.offset = float(config.get("offset", 0))
        self.round_digits = int(config.get("round_digits", 1))

    @property
    def state(self):
        return None if self.value is None else str(self.value)

    def measure(self):
        for _ in range(self._READ_ATTEMPTS):
            with open(self.filename, "r") as file:
                lines = file.readlines()
            if len(lines) >= 2 and lines[0].strip().endswith("YES"):
                position = lines[1].find("t=")
                if position == -1:
                    return None
                raw = lines[1][position + 2 :].strip()
                try:
                    value = float(raw) * self.scale + self.offset
                except ValueError:
                    return None
                return round(value, self.round_digits)
            time.sleep(0.2)
        return None


class TimestampSensor(Entity):
    """Time of the last update, published along with every state."""

    @property
    def state(self):
        # S = summer time, W = winter time; decoded by the value_template in HA
        suffix = "S" if time.localtime().tm_isdst > 0 else "W"
        return datetime.now().strftime("%y%m%d%H%M%S") + suffix


class Monitor(BinaryEntity):
    """A check on other entities, with optional hysteresis for value checks.
    device_class "problem" makes it a fault; force_on keeps an output on while active."""

    def __init__(self, unique_id, config):
        super().__init__(unique_id, config)
        self.warning = config.get("warning")
        self.hysteresis = float(config.get("hysteresis", 0))
        self.force_on = config.get("force_on")
        # Value checks count as true when the value is unknown (fail-safe, e.g. frost)
        self.unknown_active = bool(config.get("unknown_active", False))
        self.checks = []
        for key in sorted(config.get("monitor", {})):
            parts = [part.strip() for part in config["monitor"][key].split(",")]
            kind, target = parts[0], parts[1]
            limit = float(parts[2]) if len(parts) > 2 else None
            self.checks.append((kind, target, limit))

    def evaluate(self, entities):
        """Re-evaluate against the current entity values. Returns True if the state changed."""
        active = any(self._check(kind, entities.get(target), limit) for kind, target, limit in self.checks)
        changed = active != self.is_on
        self.is_on = active
        return changed

    def _check(self, kind, entity, limit):
        if kind in ("state_on", "state_off"):
            if not isinstance(entity, (BinaryEntity, Input)):
                return False
            return entity.is_on if kind == "state_on" else not entity.is_on
        if not isinstance(entity, MeasuringEntity) or limit is None:
            return False
        value = entity.value
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return self.unknown_active
        # Hysteresis: once active, a value check only clears beyond limit -/+ hysteresis
        margin = self.hysteresis if self.is_on else 0
        if kind == "value_greater":
            return value > limit - margin
        if kind == "value_less":
            return value < limit + margin
        log.error("Unknown monitor check '%s' in %s", kind, self.unique_id)
        return False


class SessionSwitch(BinaryEntity):
    """The Session switch in HA. Commands go to the Spa; the state follows the Spa."""


class OperationSensor(Entity):
    """Reports the Spa state to HA as an enum sensor."""

    OPTIONS = ["Standby", "Manual", "Frost", "Active", "Maintenance", "Error"]

    def __init__(self, unique_id, config):
        super().__init__(unique_id, config)
        self.value = None

    @property
    def state(self):
        return self.value

    def discovery_payload(self):
        payload = super().discovery_payload()
        payload.update({"device_class": "enum", "options": self.OPTIONS})
        return payload


class ResponseSensor(Entity):
    """Reports the response to the last command (from HA or the web page).
    The time is an attribute, so a repeated identical response still updates HA."""

    def __init__(self, unique_id, config):
        super().__init__(unique_id, config)
        self.value = None
        self.time = None

    def set(self, value):
        self.value = value
        self.time = datetime.now(timezone.utc).isoformat(timespec="seconds")

    @property
    def state(self):
        return self.value

    def attributes(self):
        return None if self.time is None else {"time": self.time}

    def discovery_payload(self):
        payload = super().discovery_payload()
        payload["json_attributes_topic"] = self.state_topic + "/attributes"
        return payload


class StatusSensor(Entity):
    """Reports the active monitor warnings to HA as one enum sensor.
    The value is the warning of the first active monitor in config order; all
    active warnings are in the attribute 'active'."""

    NORMAL = "Normal"

    def __init__(self, unique_id, config, monitors):
        super().__init__(unique_id, config)
        self._monitors = [m for m in monitors if m.warning]
        self.options = [m.warning for m in self._monitors] + [self.NORMAL]

    def active(self):
        return [m.warning for m in self._monitors if m.is_on]

    @property
    def state(self):
        active = self.active()
        return active[0] if active else self.NORMAL

    def attributes(self):
        return {"active": self.active()}

    def discovery_payload(self):
        payload = super().discovery_payload()
        payload.update(
            {
                "device_class": "enum",
                "options": self.options,
                "json_attributes_topic": self.state_topic + "/attributes",
            }
        )
        return payload
