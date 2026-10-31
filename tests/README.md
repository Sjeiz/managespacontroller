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
- A run takes a few seconds. Most of that is the water level sensor: every measurement really waits the configured settle time.

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
| `RPi.GPIO` | `tests/stubs/RPi/GPIO.py` | Records outputs in `GPIO.pins` (pin → level) and every write in `GPIO.writes` as (pin, level), so a test can show that nothing changes level. Inputs are read from `GPIO.inputs` (pin → level, default `1`). A test sets e.g. `GPIO.inputs[23] = 1` to simulate low water. |
| `paho.mqtt` | Created in `test_spa.py` | Empty modules, only so the controller modules can be imported. |
| `smbus` | Created in `test_spa.py` | Empty module, only so `liquidcrystal_i2c` can be imported. |

## What is covered

| Area | Checks |
|---|---|
| **Startup** | Only the heat pump is on (`initial_state`); the pin levels right after `setup()` already match `initial_state`, and starting the controller writes to no pin at all, so no relay changes level; pin levels for the relay board and for the heat pump channel on its break contact; buzzer and water level power are internal outputs. |
| **Session** | A manually switched blower is left alone when the session starts; the participants switch on one per stagger delay, the first at once and the rest after their delay; Session switch and `spa_operation` follow the state; a maintenance time during a session does nothing; manual control during a session; Session switch off switches every spa output off, the hand-switched blower and the heat pump included. |
| **Manual reporting** | `Manual` only when an output that is off by default is on; switching the heat pump off stays `Standby`; manual control in Standby switches only that output. |
| **Maintenance** | Starts at a configured clock time with the circulation output and without lights; the Session switch stays off while it runs; pumps and blower flush and each goes off when its own run time is up, leaving the circulation output running; the run does not touch a heat pump the user switched off, which is still off when the run ends; the same minute does not retrigger; switching the Session switch on hands the run over to a session (blower off at once, session outputs on, run times gone). |
| **Fault interlock** | Water temperature above 40 °C: everything off including the heat pump, `Error`; Session switch and manual control refused; hysteresis (still active at 39.8 °C, cleared at 39.4 °C, 40.0 °C does not trip); a maintenance time during a fault does nothing; clearing restores `initial_state`. |
| **Water level sensor** | The power output is switched on only while measuring and is off afterwards; low water gives a fault with warning `WaterLow`. |
| **Buzzer and LCD** | A display without a buzzer does not raise, and `beep()` pulses and leaves it off; the buzzer pulses 1 s on / 1 s off during a fault and is off afterwards; LCD line 0 shows the warning, line 1 the water temperature. |
| **Frost protection** | Below 4 °C the heat pump is forced on and `Frost` is reported; switching it off and switching the heater on are refused; `Active` (Session) takes display priority over `Frost`; a fault takes precedence (everything off), and clearing it switches the heat pump back on; an unknown outside temperature counts as frost, so a failing sensor still protects; above 4 °C the heat pump can be switched off again; an unknown water temperature does not cause a fault. |
| **Spa Status** | `Normal` at startup with nothing active; options follow the config order of the monitors; monitors are not separate HA entities; `TempHigh`, `WaterLow` and `Frost` when active; with low water and frost together the first monitor in the config (`WaterLow`) wins and the attribute `active` lists both. |
| **Display failures** | No LCD at startup and during a session does not raise; the buzzer keeps working without an LCD; the LCD reconnects when it becomes available; losing the LCD during operation does not raise and marks it unavailable; without a `display` section there is no LCD factory and no buzzer, and messages, updates, beeps and errors all still run. |
| **Spa Response** | `OK` for a successful command; a refused command with its reason; an unknown target is reported; a repeated identical response gets a new `time` attribute. |
| **Web server** | A real server on a free port with a stand-in for the main loop: page and status served; controls start with the Session switch and contain no internal outputs; a command switches an output; a refused command returns its reason; the Session switch starts a session; invalid commands give 400, unknown paths 404. |
| **Secrets file** | A missing `managespacontroller.secrets.json`, and one with a syntax error, both leave the controller running without MQTT credentials; a present file is merged into the MQTT config. The test uses a temporary file and never touches a real secrets file. |

## Dependence on the config

The tests read `managespacontroller.py.json` and assume its current values. A change to one of these needs a matching change in the tests:

- which outputs take part in a session (circulation, pumps 1-3, lights) and in a maintenance run (circulation, pumps 1-3, blower);
- stagger delay 1 s, and run times of 30 s for pumps 1-3 and the blower against 3600 s for the circulation output, which therefore ends the run;
- maintenance times `04:00`, `15:00` and `23:00` (the fault check uses `23:00` to verify that a maintenance time during a fault does nothing);
- temperature monitor limit 40 °C with hysteresis 0.5 °C;
- frost monitor below 4 °C on `spa_temp_3` with `force_on: spa_heatpump` and `unknown_active` switched on;
- monitor order water, temperature, frost with warnings `WaterLow`, `TempHigh`, `Frost` (Spa Status priority);
- water level input on pin 23 with `gpio_on: 1`, power output on pin 26 (active low), settle time 0.5 s and a measuring interval of 60 s;
- heat pump on pin 17 (on = high, because its channel drives the power relay through its break contact), pump 1 on pin 22 (active low);
- a display of 20 columns by 4 lines with `spa_buzzer` as its buzzer (the LCD checks count the characters on a line).

## What is not covered

These parts need the real hardware or broker and are checked on the Pi after a deploy (see the main README, Deploy and Logs):

- MQTT: connecting, discovery messages, commands arriving from HA, reconnecting;
- the sensor threads and their timing, and reading the real DS18B20 files;
- real GPIO pins and relays;
- the real LCD over I2C;
- the JavaScript of the web page in a real browser (only the server side and the JSON are tested);
- `main()` itself (startup order, the main loop, shutdown).

## Adding a test

1. Find the place in the scenario where the required starting state exists, or add the check at the end.
2. Drive the controller with `spa.handle_command`, `spa.handle_reading`, `spa.tick` and, for sensors, `GPIO.inputs`.
3. Assert with `check("<what is expected>", <condition>)`. The helper `on()` returns the sorted list of spa outputs that are on.
4. Use a monotonic time (`t + ...`) later than the previous checks, so timers behave as expected.
