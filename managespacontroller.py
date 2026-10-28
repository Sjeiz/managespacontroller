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
    ResponseSensor,
    SessionSwitch,
    StatusSensor,
    TemperatureSensor,
    TimestampSensor,
    WaterLevelSensor,
)
from spa import Spa
from webserver import WebServer

log = logging.getLogger("managespacontroller")

FIRST_READINGS_TIMEOUT_SECS = 15
# Holds the MQTT user and password; not in git (see README, Secrets)
SECRETS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "managespacontroller.secrets.json")


def str2bool(value):
    return str(value).lower() in ("yes", "true", "t", "1")


def load_config():
    with open(__file__ + ".json", "r", encoding="utf-8") as file:
        config = json.load(file)
    config["mqtt"].update(read_secrets())
    return config


def read_secrets():
    """The MQTT credentials. Missing or unreadable is not fatal: without them the
    controller runs on, reachable through the web page, and keeps retrying MQTT."""
    try:
        with open(SECRETS_FILE, "r", encoding="utf-8") as file:
            return json.load(file).get("mqtt", {})
    except FileNotFoundError:
        log.error("Secrets file missing: %s, running without MQTT (see README, Secrets)", SECRETS_FILE)
    except (ValueError, OSError) as error:
        log.error("Secrets file unusable: %s (%s), running without MQTT", SECRETS_FILE, error)
    return {}


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
    if mqtt_config.get("user"):
        client.username_pw_set(username=mqtt_config["user"], password=mqtt_config.get("password"))
    client.will_set(
        mqtt_config["statustopic"], mqtt_config["statusoffline"], qos=mqtt_config["qos"], retain=True
    )

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
    # Logging first, so anything the config itself complains about is logged like the rest
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
    log.info("Spa Controller: script started")
    config = load_config()
    mqtt_config = config["mqtt"]
    if str2bool(mqtt_config.get("debug", "false")):
        logging.getLogger().setLevel(logging.DEBUG)

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
    monitors = [e for e in entities.values() if isinstance(e, Monitor)]
    status_sensor = StatusSensor("spa_status", spa_config["status_sensor"], monitors)
    response_sensor = ResponseSensor("spa_response", spa_config["response_sensor"])
    spa = Spa(spa_config, entities, session_switch, operation_sensor, status_sensor, response_sensor, publisher)

    temperature_sensors = [e for e in entities.values() if isinstance(e, TemperatureSensor)]
    display = Display(
        lambda: liquidcrystal_i2c.LiquidCrystal_I2C(0x27, 1, numlines=4),
        entities.get("spa_buzzer"),  # optional: the display runs without it
        temperature_sensors,
    )
    display.message(["Program started!"])

    # Connect asynchronously: paho keeps retrying, so a broker that is down doesn't stop the spa
    client.connect_async(
        host=mqtt_config["server"], port=mqtt_config["port"], keepalive=mqtt_config["keepalive"]
    )
    client.loop_start()

    for entity in entities.values():
        if isinstance(entity, MeasuringEntity):
            entity.start(events)

    web = None
    if "web" in config:
        web = WebServer(int(config["web"].get("port", 80)), events)
        if not web.start():
            web = None

    pending = []
    wait_for_first_readings(events, entities, pending)
    spa.start()
    for event in pending:
        events.put(event)

    extra = [session_switch, operation_sensor, status_sensor, response_sensor]
    republished_at = time.monotonic()
    status_log_secs = float(spa_config.get("status_log_secs", 300))
    log.info(spa.status_line())
    status_logged_at = time.monotonic()
    try:
        while True:
            try:
                while True:
                    try:
                        event = events.get_nowait()
                    except queue.Empty:
                        break
                    if event[0] == "reading":
                        spa.handle_reading(event[1], event[2])
                    elif event[0] == "command":
                        spa.handle_command(event[1], event[2], time.monotonic())
                    elif event[0] == "web_command":
                        event[3].put(spa.handle_command(event[1], event[2], time.monotonic()))
                    elif event[0] == "connected":
                        log.info("MQTT connected")
                        publish_all(publisher, entities, extra, mqtt_config)

                spa.tick(time.monotonic(), datetime.now())
                display.update(spa, time.time())
                if web is not None:
                    web.update(spa.snapshot())

                # A zero interval switches the periodic status line off; the one at startup stays
                if status_log_secs > 0 and time.monotonic() - status_logged_at >= status_log_secs:
                    status_logged_at = time.monotonic()
                    log.info(spa.status_line())

                if time.monotonic() - republished_at >= mqtt_config["republish_sec"]:
                    republished_at = time.monotonic()
                    publish_all(publisher, entities, extra, mqtt_config)

                time.sleep(mqtt_config["sleep"])
            except Exception as error:
                # A spa is better off with a hiccup than with a controller that quits
                log.exception("Unexpected error, carrying on")
                line = error.__traceback__.tb_lineno if error.__traceback__ else "?"
                display.error(f"ERR {type(error).__name__} L{line}")
                display.beep()
                time.sleep(mqtt_config["sleep"])

    except KeyboardInterrupt:
        log.info("Spa Controller: script halted")
        display.message(["Program stopped!", "CTRL-C pressed."])

    finally:
        # Leave the outputs in their initial state (heat pump on, rest off)
        for entity in entities.values():
            if isinstance(entity, Output):
                entity.write(entity.initial_on and entity.controllable)
        # Publish and disconnect while the network loop still runs, so the messages get out
        client.publish(
            mqtt_config["statustopic"], mqtt_config["statusoffline"], qos=mqtt_config["qos"], retain=True
        )
        client.disconnect(reasoncodes.ReasonCodes(packettypes.PacketTypes.DISCONNECT, "Disconnect", 4))
        client.loop_stop()


if __name__ == "__main__":
    main()
