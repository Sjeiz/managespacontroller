"""Spa controller: builds the objects from the config and runs the main loop.

Run as systemd service (python -u). Follow the log: journalctl -u managespacontroller.service -f
Requires: sudo apt install python3-paho-mqtt; 1-wire enabled via raspi-config.
"""

import json
import logging
import os
import queue
import sys
import time
from datetime import datetime

import paho.mqtt.client as MQTT
import paho.mqtt.packettypes as packettypes
import paho.mqtt.reasoncodes as reasoncodes
import RPi.GPIO as GPIO

import liquidcrystal_i2c  # https://github.com/pl31/python-liquidcrystal_i2c/tree/master
from display import Display
from entities import (
    Input,
    MeasuringEntity,
    Monitor,
    OperationSensor,
    Output,
    Publisher,
    SessionSwitch,
    TemperatureSensor,
    TimestampSensor,
    WaterLevelSensor,
)
from spa import Spa

log = logging.getLogger("managespacontroller")

FIRST_READINGS_TIMEOUT_SECS = 15


def str2bool(value):
    return str(value).lower() in ("yes", "true", "t", "1")


def load_config():
    with open(__file__ + ".json", "r", encoding="utf-8") as file:
        return json.load(file)


def build_entities(config):
    entities = {}
    for uid, cfg in config["gpios"].items():
        if cfg.get("direction") == "output":
            entities[uid] = Output(uid, cfg)
        elif "power" in cfg:
            entities[uid] = WaterLevelSensor(uid, cfg)
        else:
            entities[uid] = Input(uid, cfg)
    timestamp = None
    for uid, cfg in config["sensors"].items():
        if cfg.get("sensor_type") == "w1sensor":
            entities[uid] = TemperatureSensor(uid, cfg)
        elif cfg.get("sensor_type") == "timestamp":
            timestamp = TimestampSensor(uid, cfg)
            entities[uid] = timestamp
        else:
            log.error("Sensor %s has unknown sensor_type, ignored", uid)
    for uid, cfg in config["monitors"].items():
        entities[uid] = Monitor(uid, cfg)
    for entity in entities.values():
        if isinstance(entity, WaterLevelSensor) and entity.power_id:
            entity.power = entities[entity.power_id]
    return entities, timestamp


def create_mqtt_client(mqtt_config, events):
    client = MQTT.Client(protocol=MQTT.MQTTv5)
    client.username_pw_set(username=mqtt_config["user"], password=mqtt_config["password"])
    client.will_set(mqtt_config["statustopic"], mqtt_config["statusoffline"], qos=mqtt_config["qos"])

    # Callbacks run in the paho thread: they only put events in the queue
    def on_connect(client, userdata, flags, rc, properties=None):
        if rc == 0:
            client.subscribe(mqtt_config["subscribe_topic"])
            events.put(("connected",))
        else:
            log.warning("MQTT connection failed: %s", rc)

    def on_disconnect(client, userdata, *args):
        log.warning("MQTT disconnected")

    def on_message(client, userdata, msg):
        parts = msg.topic.split("/")
        if len(parts) >= 2:
            events.put(("command", parts[1], msg.payload.decode("UTF-8")))

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message
    return client


def publish_all(publisher, entities, extra, mqtt_config):
    publisher.status(mqtt_config["statustopic"], mqtt_config["statusonline"])
    for entity in list(entities.values()) + extra:
        publisher.discovery(entity)
        publisher.state(entity)


def wait_for_first_readings(events, entities, pending):
    """Collect events until every measuring entity has reported once (or timeout)."""
    measuring = [e for e in entities.values() if isinstance(e, MeasuringEntity)]
    deadline = time.monotonic() + FIRST_READINGS_TIMEOUT_SECS
    while time.monotonic() < deadline and not all(e.measured for e in measuring):
        try:
            event = events.get(timeout=0.5)
        except queue.Empty:
            continue
        if event[0] == "reading" and event[1] in entities:
            entities[event[1]].apply(event[2])
        else:
            pending.append(event)
    missing = [e.unique_id for e in measuring if not e.measured]
    if missing:
        log.warning("No first reading from %s", missing)


def main():
    config = load_config()
    mqtt_config = config["mqtt"]
    logging.basicConfig(
        level=logging.DEBUG if str2bool(mqtt_config.get("debug", "false")) else logging.INFO,
        format="%(message)s",
        stream=sys.stdout,
    )
    log.info("Spa Controller: script started")

    os.system("modprobe w1-gpio")
    os.system("modprobe w1-therm")
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)

    entities, timestamp = build_entities(config)
    for entity in entities.values():
        if isinstance(entity, (Output, Input)):
            entity.setup()

    events = queue.Queue()
    client = create_mqtt_client(mqtt_config, events)
    publisher = Publisher(client, mqtt_config["device"], mqtt_config["qos"])
    publisher.timestamp = timestamp

    spa_config = config["spa"]
    session_switch = SessionSwitch("spa_session", spa_config["session_switch"])
    operation_sensor = OperationSensor("spa_operation", spa_config["operation_sensor"])
    spa = Spa(spa_config, entities, session_switch, operation_sensor, publisher)

    lcd = liquidcrystal_i2c.LiquidCrystal_I2C(0x27, 1, numlines=4)
    temperature_sensors = [e for e in entities.values() if isinstance(e, TemperatureSensor)]
    display = Display(lcd, entities["spa_buzzer"], temperature_sensors)
    display.message(["Program started!"])

    # Connect asynchronously: paho keeps retrying, so a broker that is down doesn't stop the spa
    client.connect_async(
        host=mqtt_config["server"], port=mqtt_config["port"], keepalive=mqtt_config["keepalive"]
    )
    client.loop_start()

    for entity in entities.values():
        if isinstance(entity, MeasuringEntity):
            entity.start(events)

    pending = []
    wait_for_first_readings(events, entities, pending)
    spa.start()
    for event in pending:
        events.put(event)

    extra = [session_switch, operation_sensor]
    republished_at = time.monotonic()
    status_log_secs = float(spa_config.get("status_log_secs", 300))
    log.info(spa.status_line())
    status_logged_at = time.monotonic()
    try:
        while True:
            while True:
                try:
                    event = events.get_nowait()
                except queue.Empty:
                    break
                if event[0] == "reading":
                    spa.handle_reading(event[1], event[2])
                elif event[0] == "command":
                    spa.handle_command(event[1], event[2], time.monotonic())
                elif event[0] == "connected":
                    log.info("MQTT connected")
                    publish_all(publisher, entities, extra, mqtt_config)

            spa.tick(time.monotonic(), datetime.now())
            display.update(spa, time.time())

            if time.monotonic() - status_logged_at >= status_log_secs:
                status_logged_at = time.monotonic()
                log.info(spa.status_line())

            if time.monotonic() - republished_at >= mqtt_config["republish_sec"]:
                republished_at = time.monotonic()
                publish_all(publisher, entities, extra, mqtt_config)

            time.sleep(mqtt_config["sleep"])

    except KeyboardInterrupt:
        log.info("Spa Controller: script halted")
        display.message(["Program stopped!", "CTRL-C pressed."])

    except Exception as error:
        log.exception("Spa Controller: unexpected error")
        line = error.__traceback__.tb_lineno if error.__traceback__ else "?"
        text = f"Line{line}:{type(error).__name__}({error})"
        display.message([text[i : i + 20] for i in range(0, 80, 20)])

    finally:
        # Leave the outputs in their initial state (heat pump on, rest off)
        for entity in entities.values():
            if isinstance(entity, Output):
                entity.write(entity.initial_on and entity.controllable)
        # Publish and disconnect while the network loop still runs, so the messages get out
        client.publish(mqtt_config["statustopic"], mqtt_config["statusoffline"], qos=mqtt_config["qos"])
        client.disconnect(reasoncodes.ReasonCodes(packettypes.PacketTypes.DISCONNECT, "Disconnect", 4))
        client.loop_stop()


if __name__ == "__main__":
    main()
