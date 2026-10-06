"""Scenario tests for the spa controller, without hardware.

Run from the repo root:  python tests/test_spa.py
See tests/README.md for what is covered and how the stubs work.
"""

import json
import logging
import os
import sys
import tempfile
import types
from datetime import datetime

TESTS = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(TESTS)
sys.path.insert(0, os.path.join(TESTS, "stubs"))  # fake RPi.GPIO
sys.path.insert(0, REPO)
logging.basicConfig(level=logging.INFO, format="  log: %(message)s")

import RPi.GPIO as GPIO  # noqa: E402  (the stub)

# paho and smbus are only needed to import the modules; replace them by empty modules
paho = types.ModuleType("paho")
mqtt = types.ModuleType("paho.mqtt")
for name in ("client", "packettypes", "reasoncodes"):
    module = types.ModuleType("paho.mqtt." + name)
    setattr(mqtt, name, module)
    sys.modules["paho.mqtt." + name] = module
sys.modules["paho"] = paho
sys.modules["paho.mqtt"] = mqtt
sys.modules["smbus"] = types.ModuleType("smbus")

import managespacontroller as mc  # noqa: E402
from display import Display  # noqa: E402
from entities import OperationSensor, SessionSwitch  # noqa: E402
from spa import Spa  # noqa: E402


class FakePublisher:
    def __init__(self):
        self.msgs = []
        self.timestamp = None

    def state(self, e):
        if e.state_topic and e.state is not None:
            self.msgs.append((e.unique_id, e.state))


config = json.load(open(os.path.join(REPO, "managespacontroller.py.json"), encoding="utf-8"))
entities, ts = mc.build_entities(config)
for e in entities.values():
    if isinstance(e, (mc.Output, mc.Input)):
        e.setup()
pub = FakePublisher()
sc = config["spa"]
spa = Spa(sc, entities, SessionSwitch("spa_session", sc["session_switch"]),
          OperationSensor("spa_operation", sc["operation_sensor"]), pub)
O = spa.outputs
failed = False


def on():
    return sorted(u for u, o in O.items() if o.is_on)


def check(name, cond):
    global failed
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        failed = True
        print("     outputs on:", on(), "state:", spa.state, "fault:", spa.fault)


W = datetime(2026, 10, 7, 12, 0)

for uid in ("spa_temp_1", "spa_temp_2", "spa_temp_3"):
    entities[uid].apply(36.0)
entities["spa_water_level"].apply(False)
spa.start()
check("start: only heat pump on, Standby", on() == ["spa_heatpump"] and spa.state == "Standby" and spa.operation_sensor.value == "Standby")
check("start: heat pump pin high (gpio_on=1)", GPIO.pins[26] == 1)
check("start: pump1 pin at off-level (active low = 1)", GPIO.pins[21] == 1)
check("start: buzzer and level power are not spa outputs", "spa_buzzer" not in O and "spa_water_level_power" not in O)
check("disabled pump 4: not an output, skipped in both lists", "spa_pump4" not in entities and all("spa_pump4" not in step for step in spa.session_list + spa.maintenance_list))

t = 1000.0
spa.handle_command("spa_session", "on", t)
check("session: lights + circulation at once", on() == ["spa_circulation", "spa_heatpump", "spa_lights"])
spa.tick(t + 0.5, W)
check("session: no pump before stagger delay", "spa_pump1" not in on())
spa.tick(t + 1.0, W)
check("session: pump1 after 1 s, pump2 not yet", "spa_pump1" in on() and "spa_pump2" not in on())
for i in range(2, 5):
    spa.tick(t + i, W)
check("session: pumps 1-3 on after 3 s, no blower", all(f"spa_pump{i}" in on() for i in range(1, 4)) and "spa_blower" not in on())
check("session: switch on, operation Session", spa.session_switch.is_on and spa.operation_sensor.value == "Session")

spa.tick(t + 10, datetime(2026, 10, 7, 14, 0))
check("maintenance time during session: nothing", spa.state == "Session")

spa.handle_command("spa_blower", "on", t + 11)
check("manual blower on during session", "spa_blower" in on())

spa.handle_command("spa_session", "off", t + 20)
check("session off: only heat pump, Standby, switch off", on() == ["spa_heatpump"] and spa.state == "Standby" and not spa.session_switch.is_on)

r = spa.switch("spa_heater", True)
check("heater refused without circulation", not r and "spa_heater" not in on())
spa.handle_command("spa_circulation", "on", t + 30)
spa.handle_command("spa_heater", "on", t + 31)
check("heater on -> heat pump off", "spa_heater" in on() and "spa_heatpump" not in on())
spa.handle_command("spa_circulation", "off", t + 32)
check("circulation off -> heater off -> heat pump on", on() == ["spa_heatpump"])
spa.handle_command("spa_circulation", "on", t + 33)
spa.handle_command("spa_heater", "on", t + 34)
spa.handle_command("spa_heater", "off", t + 35)
check("heater off -> heat pump back on", "spa_heatpump" in on() and "spa_heater" not in on())
spa.handle_command("spa_circulation", "off", t + 36)

check("all outputs at initial state: reported Standby", spa.operation_sensor.value == "Standby")
spa.handle_command("spa_circulation", "on", t + 37)
check("manual circulation on: reported Manual, state Standby", spa.operation_sensor.value == "Manual" and spa.state == "Standby")
spa.handle_command("spa_circulation", "off", t + 38)
check("manual circulation off: reported Standby", spa.operation_sensor.value == "Standby")
spa.handle_command("spa_heatpump", "off", t + 38.5)
check("heat pump off manually: reported Standby (saves power, not Manual)", spa.operation_sensor.value == "Standby")
spa.handle_command("spa_heatpump", "on", t + 39)
check("heat pump back on: reported Standby", spa.operation_sensor.value == "Standby")
spa.handle_command("spa_pump2", "on", t + 40)
check("manual pump in Standby: only that pump", on() == ["spa_heatpump", "spa_pump2"] and spa.state == "Standby")
check("manual pump in Standby: reported Manual", spa.operation_sensor.value == "Manual" and "Status: Manual" in spa.status_line())

t = 5000.0
spa.tick(t, datetime(2026, 10, 7, 6, 0, 5))
check("maintenance at 06:00: circulation, no lights", spa.state == "Maintenance" and "spa_circulation" in on() and "spa_lights" not in on())
check("maintenance: switch on, operation Maintenance", spa.session_switch.is_on and spa.operation_sensor.value == "Maintenance")
for i in range(1, 7):
    spa.tick(t + i, datetime(2026, 10, 7, 6, 0, 10))
check("maintenance: pumps + blower on", all(f"spa_pump{i}" in on() for i in range(1, 4)) and "spa_blower" in on())
spa.tick(t + 60, datetime(2026, 10, 7, 6, 1))
check("maintenance: after flush only circulation + heat pump", on() == ["spa_circulation", "spa_heatpump"])
spa.tick(t + 120, datetime(2026, 10, 7, 6, 0, 50))
check("maintenance: same minute does not retrigger", spa.state == "Maintenance")
spa.tick(t + 3601, datetime(2026, 10, 7, 7, 0))
check("maintenance: circulation duration -> Standby", spa.state == "Standby" and on() == ["spa_heatpump"])

t = 9000.0
spa.tick(t, datetime(2026, 10, 7, 14, 0))
for i in range(1, 7):
    spa.tick(t + i, datetime(2026, 10, 7, 14, 0, 10))
check("maintenance 2: blower on", "spa_blower" in on())
spa.handle_command("spa_session", "on", t + 7)
check("maintenance -> session: blower off, lights on", "spa_blower" not in on() and "spa_lights" in on() and spa.state == "Session")
spa.tick(t + 4000, datetime(2026, 10, 7, 15, 0))
check("maintenance -> session: timers cancelled", spa.state == "Session" and "spa_pump1" in on())

spa.handle_reading("spa_temp_1", 40.1)
spa.tick(t + 4001, datetime(2026, 10, 7, 15, 1))
check("fault 40.1: all off incl heat pump, Error, Standby", on() == [] and spa.fault and spa.operation_sensor.value == "Error" and spa.state == "Standby")
spa.handle_command("spa_session", "on", t + 4002)
check("fault: session refused", spa.state == "Standby" and on() == [] and not spa.session_switch.is_on)
spa.handle_command("spa_pump1", "on", t + 4003)
check("fault: manual pump refused", on() == [])
spa.handle_reading("spa_temp_1", 39.8)
spa.tick(t + 4004, datetime(2026, 10, 7, 15, 2))
check("fault: 39.8 still active (hysteresis)", spa.fault)
spa.tick(t + 4006, datetime(2026, 10, 7, 22, 0))
check("fault: maintenance time does nothing", spa.state == "Standby" and on() == [])
spa.handle_reading("spa_temp_1", 39.4)
spa.tick(t + 4007, datetime(2026, 10, 7, 22, 1))
check("cleared at 39.4: initial state, Standby", not spa.fault and on() == ["spa_heatpump"] and spa.operation_sensor.value == "Standby")
spa.handle_reading("spa_temp_1", 40.0)
spa.tick(t + 4008, datetime(2026, 10, 7, 22, 2))
check("40.0 does not trip", not spa.fault)

GPIO.inputs[12] = 0  # gpio_on = 0 -> problem
v = entities["spa_water_level"].measure()
check("level measure: reads problem, power off afterwards", v is True and GPIO.pins[22] == 0)
spa.handle_reading("spa_water_level", v)
spa.tick(t + 4010, datetime(2026, 10, 7, 22, 3))
check("water fault active with warning WaterLow", spa.fault and spa.warnings() == ["WaterLow"])


class FakeLCD:
    def __init__(s):
        s.lines = {}

    def printline(s, n, v):
        s.lines[n] = v

    def backlight(s): pass
    def display(s): pass
    def noDisplay(s): pass
    def noBacklight(s): pass


lcd = FakeLCD()
temps = [entities[u] for u in ("spa_temp_1", "spa_temp_2", "spa_temp_3")]
d = Display(lambda: lcd, entities["spa_buzzer"], temps)
d.update(spa, 100.2)
b1 = entities["spa_buzzer"].is_on
d.update(spa, 101.2)
b2 = entities["spa_buzzer"].is_on
check("buzzer pulses 1 s on / 1 s off", b1 is True and b2 is False)
check("LCD line 0 shows warning, 20 chars", lcd.lines[0].startswith("WARNING: WaterLow") and len(lcd.lines[0]) == 20)
check("LCD line 1 water temp", lcd.lines[1].startswith("Water Temp:") and lcd.lines[1].endswith("40.0"))
GPIO.inputs[12] = 1
spa.handle_reading("spa_water_level", entities["spa_water_level"].measure())
spa.tick(t + 4020, datetime(2026, 10, 7, 22, 4))
d.update(spa, 102.0)
check("water ok: fault cleared, buzzer off, heat pump on", not spa.fault and not entities["spa_buzzer"].is_on and on() == ["spa_heatpump"])

# ---- frost protection ----
t = 20000.0
W2 = datetime(2026, 10, 7, 23, 30)
spa.handle_command("spa_heatpump", "off", t)
check("no frost: heat pump can be switched off, reported Standby", "spa_heatpump" not in on() and spa.operation_sensor.value == "Standby")
spa.handle_reading("spa_temp_3", 3.9)
spa.tick(t + 1, W2)
check("frost 3.9: monitor on, heat pump forced on", entities["spa_status_frost"].is_on and "spa_heatpump" in on())
check("frost: reported Frost", spa.operation_sensor.value == "Frost")
spa.handle_command("spa_heatpump", "off", t + 2)
check("frost: heat pump off refused", "spa_heatpump" in on())
spa.handle_command("spa_circulation", "on", t + 3)
spa.handle_command("spa_heater", "on", t + 4)
check("frost: heater refused (would switch heat pump off)", "spa_heater" not in on() and "spa_heatpump" in on())
spa.handle_command("spa_circulation", "off", t + 5)
spa.handle_command("spa_session", "on", t + 6)
check("frost + session: reported Session", spa.operation_sensor.value == "Session")
spa.handle_command("spa_session", "off", t + 7)
check("session off during frost: reported Frost, heat pump on", spa.operation_sensor.value == "Frost" and on() == ["spa_heatpump"])
GPIO.inputs[12] = 0
spa.handle_reading("spa_water_level", entities["spa_water_level"].measure())
spa.tick(t + 8, W2)
check("frost + fault: fault wins, everything off, Error", on() == [] and spa.operation_sensor.value == "Error")
GPIO.inputs[12] = 1
spa.handle_reading("spa_water_level", entities["spa_water_level"].measure())
spa.tick(t + 9, W2)
check("fault cleared during frost: heat pump on, Frost", on() == ["spa_heatpump"] and spa.operation_sensor.value == "Frost")
spa.handle_reading("spa_temp_3", None)
spa.tick(t + 10, W2)
check("air temp unknown: frost active (unknown_active)", entities["spa_status_frost"].is_on)
spa.handle_reading("spa_temp_3", 10.0)
spa.tick(t + 11, W2)
check("air 10: frost off, reported Standby", not entities["spa_status_frost"].is_on and spa.operation_sensor.value == "Standby")
spa.handle_command("spa_heatpump", "off", t + 12)
check("after frost: heat pump can be switched off again", "spa_heatpump" not in on())
spa.handle_command("spa_heatpump", "on", t + 13)
check("other monitors: unknown water temp gives no fault", (spa.handle_reading("spa_temp_1", None), spa.tick(t + 14, W2), not spa.fault)[2])

# ---- display failures ----
import display as display_module


def no_lcd():
    raise OSError(121, "Remote I/O error")


class BreakingLCD(FakeLCD):
    def __init__(s):
        super().__init__()
        s.broken = False

    def printline(s, n, v):
        if s.broken:
            raise OSError(121, "Remote I/O error")
        super().printline(n, v)


spa.handle_reading("spa_temp_1", 38.0)
spa.tick(t + 20, W2)
try:
    dd = Display(no_lcd, entities["spa_buzzer"], temps)
    dd.message(["Program started!"])
    ok = True
except Exception as e:
    ok = False
check("no display at startup: no exception", ok)
spa.handle_command("spa_session", "on", t + 21)
try:
    dd.update(spa, 300.0)
    ok = True
except Exception:
    ok = False
check("no display during session: update does not raise", ok)
# Buzzer still works without a display
GPIO.inputs[12] = 0
spa.handle_reading("spa_water_level", entities["spa_water_level"].measure())
spa.tick(t + 22, W2)
dd.update(spa, 400.0)
check("no display: buzzer still pulses on fault", entities["spa_buzzer"].is_on)
GPIO.inputs[12] = 1
spa.handle_reading("spa_water_level", entities["spa_water_level"].measure())
spa.tick(t + 23, W2)
dd.update(spa, 401.0)
check("no display: buzzer off after fault cleared", not entities["spa_buzzer"].is_on)

blcd = BreakingLCD()
dd._lcd_factory = lambda: blcd
dd._reconnect_at = 0.0  # force an immediate reconnect attempt
spa.handle_command("spa_session", "on", t + 24)
dd.update(spa, 500.0)
check("display reconnects when it becomes available", dd._lcd is blcd and blcd.lines.get(1, "").startswith("Water Temp:"))
blcd.broken = True
dd.update(spa, 501.0)
try:
    dd.update(spa, 502.0)
    dd.message(["Program stopped!"])
    ok = True
except Exception:
    ok = False
check("display lost during operation: no exception, marked unavailable", ok and dd._lcd is None)
spa.handle_command("spa_session", "off", t + 25)

# ---- secrets file ----
real_secrets_file = mc.SECRETS_FILE
with tempfile.TemporaryDirectory() as tmp:
    mc.SECRETS_FILE = os.path.join(tmp, "managespacontroller.secrets.json")
    try:
        mc.load_config()
        ok = False
    except SystemExit as error:
        ok = "Secrets file missing" in str(error)
    check("secrets file missing: controller stops with a clear message", ok)
    with open(mc.SECRETS_FILE, "w", encoding="utf-8") as file:
        file.write('{"mqtt": {"user": "u", "password": "p"}}')
    loaded = mc.load_config()
    check("secrets file present: user and password merged into mqtt config",
          loaded["mqtt"]["user"] == "u" and loaded["mqtt"]["password"] == "p" and "server" in loaded["mqtt"])
mc.SECRETS_FILE = real_secrets_file

print("RESULT:", "FAILED" if failed else "ALL PASSED")
sys.exit(1 if failed else 0)

