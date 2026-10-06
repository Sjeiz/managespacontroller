# Tests

Scenario tests for the spa controller. They run on any machine with Python, without a Raspberry Pi, relays, sensors, LCD or MQTT broker.

## Running

From the repository root:

```bash
python tests/test_spa.py
```

- Requires Python 3 (tested with 3.12). No packages need to be installed; `RPi.GPIO`, `paho-mqtt` and `smbus` are replaced by stubs (see [Stubs](#stubs)).
- Every check prints `PASS <name>` or `FAIL <name>`; a failure also prints the outputs that are on, the state and the fault flag.
- Log lines of the controller go to stderr, with the prefix `  log:`.
- The last line is `RESULT: ALL PASSED` or `RESULT: FAILED`. The exit code is `0` when all checks pass and `1` otherwise, so the script can be used in a script or CI step.

To see only the result and the failures:

```bash
python tests/test_spa.py 2>/dev/null | grep -E "^FAIL|RESULT"
```

Run the tests after every change to `spa.py`, `entities.py`, `display.py`, `managespacontroller.py` or `managespacontroller.py.json`, before committing.

## How the tests work

`test_spa.py` builds the real objects from the real config file (`managespacontroller.py.json`) with `build_entities()` and the `Spa` class, exactly as `main()` does. It then drives them directly, without threads or MQTT:

| Real controller | In the tests |
|---|---|
| A command from HA (MQTT) | `spa.handle_command(target, payload, now)` |
| A sensor reading from a sensor thread | `spa.handle_reading(uid, value)` |
| One main loop tick | `spa.tick(now, wallclock)` with a chosen monotonic time and clock time |
| Publishing to MQTT | `FakePublisher` records the published states |
| The LCD | `FakeLCD` records the printed lines; `BreakingLCD` raises an I/O error on demand |

Because time is passed in explicitly, timers (stagger delay, flush time, circulation duration) and clock-based maintenance times are tested without waiting.

The checks form **one continuous scenario**: each check starts from the state the previous checks left behind. The order matters; add new checks where the required starting state exists, or at the end.

## Stubs

| Stub | Location | Behaviour |
|---|---|---|
| `RPi.GPIO` | `tests/stubs/RPi/GPIO.py` | Records outputs in `GPIO.pins` (pin → level). Inputs are read from `GPIO.inputs` (pin → level, default `1`). A test sets e.g. `GPIO.inputs[12] = 0` to simulate low water. |
| `paho.mqtt` | Created in `test_spa.py` | Empty modules, only so the controller modules can be imported. |
| `smbus` | Created in `test_spa.py` | Empty module, only so `liquidcrystal_i2c` can be imported. |

## What is covered

| Area | Checks |
|---|---|
| **Startup** | Only the heat pump is on (`initial_state`); pin levels for active-high and active-low relays; buzzer and water level power are internal outputs; outputs in the `disabled` config section are skipped in the lists. |
| **Session** | Lights and circulation switch on together; pumps follow one by one after the stagger delay; Session switch and `spa_operation` follow the state; a maintenance time during a session does nothing; manual control during a session; Session switch off returns to Standby. |
| **Rules** | `requires` (heater refused without circulation); `conflict` (heater on switches the heat pump off, heater off switches it back on); circulation off switches the heater off and the heat pump back on. |
| **Manual reporting** | `Manual` only when an output that is off by default is on; switching the heat pump off stays `Standby`; manual control in Standby switches only that output. |
| **Maintenance** | Starts at a configured clock time with circulation and without lights; pumps and blower flush; after the flush time only circulation (and the heat pump) remain; the same minute does not retrigger; ends after the circulation duration; the Session switch during maintenance turns it into a session (blower off, lights on, timers cancelled). |
| **Fault interlock** | Water temperature above 40 °C: everything off including the heat pump, `Error`; Session switch and manual control refused; hysteresis (still active at 39.8 °C, cleared at 39.4 °C, 40.0 °C does not trip); a maintenance time during a fault does nothing; clearing restores `initial_state`. |
| **Water level sensor** | The power output is switched on only while measuring and is off afterwards; low water gives a fault with warning `WaterLow`. |
| **Buzzer and LCD** | The buzzer pulses 1 s on / 1 s off during a fault and is off afterwards; LCD line 0 shows the warning, line 1 the water temperature. |
| **Frost protection** | Below 4 °C the heat pump is forced on and `Frost` is reported; switching it off and switching the heater on are refused; `Session` takes display priority over `Frost`; a fault takes precedence (everything off), and clearing it switches the heat pump back on; an unknown outside temperature counts as frost (`unknown_active`); above 4 °C the heat pump can be switched off again; an unknown water temperature does not cause a fault. |
| **Spa Status** | `Normal` at startup with nothing active; options follow the config order of the monitors; monitors are not separate HA entities; `TempHigh`, `WaterLow` and `Frost` when active; with low water and frost together the first monitor in the config (`WaterLow`) wins and the attribute `active` lists both. |
| **Display failures** | No LCD at startup and during a session does not raise; the buzzer keeps working without an LCD; the LCD reconnects when it becomes available; losing the LCD during operation does not raise and marks it unavailable. |
| **Secrets file** | A missing `managespacontroller.secrets.json` stops the controller with a clear message; a present file is merged into the MQTT config. The test uses a temporary file and never touches a real secrets file. |

## Dependence on the config

The tests read `managespacontroller.py.json` and assume its current values. A change to one of these needs a matching change in the tests:

- the session list (lights and circulation, then pumps 1-3) and the maintenance list (circulation, pumps 1-3, blower);
- `spa_pump4` in the `disabled` section;
- stagger delay 1 s, flush time 30 s, circulation duration 3600 s;
- maintenance times `06:00`, `14:00` and `22:00` (the fault check uses `22:00` to verify that a maintenance time during a fault does nothing);
- temperature monitor limit 40 °C with hysteresis 0.5 °C;
- frost monitor below 4 °C on `spa_temp_3` with `force_on: spa_heatpump` and `unknown_active`;
- monitor order water, temperature, frost with warnings `WaterLow`, `TempHigh`, `Frost` (Spa Status priority);
- water level input on pin 12 with `gpio_on: 0` (the current override), power output on pin 22;
- heat pump on pin 26 (active high), pump 1 on pin 21 (active low).

## What is not covered

These parts need the real hardware or broker and are checked on the Pi after a deploy (see the main README, Deploy and Logs):

- MQTT: connecting, discovery messages, commands arriving from HA, reconnecting;
- the sensor threads and their timing, and reading the real DS18B20 files;
- real GPIO pins and relays;
- the real LCD over I2C;
- `main()` itself (startup order, the main loop, shutdown).

## Adding a test

1. Find the place in the scenario where the required starting state exists, or add the check at the end.
2. Drive the controller with `spa.handle_command`, `spa.handle_reading`, `spa.tick` and, for sensors, `GPIO.inputs`.
3. Assert with `check("<what is expected>", <condition>)`. The helper `on()` returns the sorted list of spa outputs that are on.
4. Use a monotonic time (`t + ...`) later than the previous checks, so timers behave as expected.
