# managespacontroller
My Raspberry Pi 4B base spa controller

## Connect
```bash
ssh SjeizAdmin@spa-controller.iot.cheizoo.lan
```
The repo lives in `/home/SjeizAdmin/python/managespacontroller/managespacontroller`.

## Logs
The script runs as the systemd service `managespacontroller` and logs to the journal.
Detailed output (`Message received`, `Gpio[...] -->`, schedules) only appears when `"debug": "true"` is set in `managespacontroller.py.json`.

```bash
# Follow live
journalctl -u managespacontroller.service -f

# A specific time window
journalctl -u managespacontroller.service --since "2026-10-04 23:25" --until "2026-10-04 23:35" --no-pager

# Who switched what: MQTT commands from HA (actor "user") and schedule actions
journalctl -u managespacontroller.service --since "24 hours ago" --no-pager | grep -E "Message received|Starting (ON|OFF) schedule"

# Service starts/stops and crashes
journalctl -u managespacontroller.service --since "14 days ago" --no-pager | grep -E "Started|Stopped|exited|Traceback|Error"

# Service status
systemctl status managespacontroller
```

## Deploy
The Pi runs a git clone of this repo; deploy by pushing to GitHub and pulling on the Pi.

1. Commit and push to `origin/main`.
2. On the Pi (see [Connect](#connect)):
   ```bash
   cd /home/SjeizAdmin/python/managespacontroller/managespacontroller && git pull
   ```
3. Activate the new code (this switches outputs, so pick the moment):
   ```bash
   sudo systemctl restart managespacontroller
   ```
4. Follow the log (see [Logs](#logs)): `journalctl -u managespacontroller.service -f`

## Design: state machine
Agreed design, not yet implemented.

### States
| State | Meaning |
|---|---|
| **Standby** | Nobody active. Nothing from the lists is running. |
| **Session** | Started by the user with the Session switch. |
| **Maintenance** | Started by the clock. Behaves like a session, with timers that end it. |

### Transitions
| From | To | When | Action |
|---|---|---|---|
| (start) | Standby | Controller starts | Every output to its `initial_state` |
| Standby | Session | Session switch on | Session list |
| Standby | Maintenance | Maintenance time reached | Maintenance list, timers start |
| Maintenance | Session | Session switch on | Session list, timers cancelled |
| Session or Maintenance | Standby | Session switch off | All list outputs off |
| Maintenance | Standby | Circulation timer expired | All list outputs off |

A maintenance time during a session or a running maintenance does nothing.

### Lists
| List | Contents, in order |
|---|---|
| **Session** | Lights and circulation, then pump 1, 2, 3, 4 |
| **Maintenance** | Circulation, then pump 1, 2, 3, 4, blower |

On entering Session or Maintenance:
- outputs in the list that are off are switched on; motors are staggered by the configured delay (16 A breaker inrush);
- outputs in the other list but not in this one are switched off;
- outputs in neither list are not touched.

### Maintenance timers
- Flush time expired: pumps and blower off.
- Circulation duration expired: back to Standby.

### Rules
| Rule | Config | Behaviour |
|---|---|---|
| **Manual control** | - | A command from HA switches only that output |
| **Mutual exclusion** | `conflict` | Switching one on switches the other off; switching one off returns the other to its `initial_state` |
| **Requirement** | `requires` | An output may only be on while the required output is on; when that goes off, it goes off too |

Applied:
- heat pump: `initial_state: on` (frost protection), `conflict` with the heater;
- heater: `requires: spa_circulation`.

### Fault interlock
A blocking layer above the state machine; the state machine itself has no fault handling.
1. **A problem monitor becomes active:** the state machine goes to Standby (the same transition as Session switch off) and every output goes off, including the heat pump (it drives the circulation pump, which would run dry without water). The buzzer pulses 1 s on, 1 s off (never continuously on).
2. **While active:** every switch-on is refused, whether from the Session switch, a maintenance time or manual control. Exempt: the buzzer and the water level sensor's power output (otherwise the water level could never be measured again and the fault would never clear).
3. **Cleared:** every output to its `initial_state`, as at controller start.

Monitors:
- water level: problem when the water is too low;
- water temperature: problem above 40 °C, cleared below 39.5 °C.

Each monitor that compares a value has its own hysteresis, so sensor jitter around the limit does not toggle the fault.

### Reporting to HA
- **Session switch:** on during Session and Maintenance.
- **`spa_operation`:** sensor with the values `Standby`, `Session`, `Maintenance`, `Error`. It replaces the current binary sensor: after deploying, remove the orphaned `binary_sensor.spa_controller_spa_operation` and point the card in the Jacuzzi dashboard to `sensor.spa_controller_spa_operation`.
- Outputs, sensors, water level and monitors: as now.

### Water level sensor
The sensor only gets power while measuring, to limit electrolysis on the electrodes:
1. power output on;
2. wait the settle time;
3. read the level input;
4. power output off.

Between measurements the last reading is kept. The first measurement runs at startup. The power output is a normal output with its own on/off level, so it works directly on 3V3 or through a relay. It is not exposed to HA.

### Threads
- Each sensor runs in its own thread and measures once per measuring interval (the DS18B20 temperature sensors take up to 0.75 s per reading; the water level sensor does its power/settle/read cycle).
- MQTT receives commands in its own thread (paho).
- All of them put their results in one queue. Only the main loop reads the queue, touches the Spa and switches outputs; the one exception is the water level sensor, which switches its own power output from its thread (no other code uses that pin).

### Config
- Per output: pin, on/off level, `initial_state`, optional `conflict` and `requires`, HA fields (`unique_id`, topics).
- Per sensor (temperature and water level): measuring interval (start: 10 s).
- Water level sensor: power output (GPIO 10), settle time (start: 0.1 s).
- Per monitor: limit and, for value checks, hysteresis (start: 0.5 °C).
- Section `spa`: session list, maintenance list, stagger delay, maintenance times (list of clock times, e.g. `["06:00", "18:00"]`), flush time, circulation duration.
- Removed: `actions_on`, `actions_off`, `schedule_on_secs`, `schedule_off_secs`.

### Code structure
| Module | Contents |
|---|---|
| `managespacontroller.py` | `main()`: read config, build objects, main loop (service entry point, unchanged) |
| `entities.py` | Base class (name, `unique_id`, topics, publish, discovery), Output, Input, WaterLevelSensor (Input with power output), TemperatureSensor, TimestampSensor, Monitor, SessionSwitch |
| `spa.py` | The state machine (state, transitions, lists, timers, rules) and the fault interlock; owns all entities; all commands go through it |
| `display.py` | LCD and buzzer; gets its information from the Spa |

- An Output switches the pin and remembers its state in one place; outputs are never read back. Only inputs and sensors are read.
- The fault interlock is checked at that single place where outputs are switched on.
- Each sensor class runs its own measuring thread and posts results to the queue.
- Every class declares its attributes explicitly with defaults; config values are mapped onto them (no `setattr` from config, no `hasattr` checks).
- No global variables.

## Open issues
1. **Water level sensor override (temporary).** The sensor contacts are oxidized and report a false low-water problem, so `spa_water_level` in `managespacontroller.py.json` is inverted (`gpio_on: 0`, `gpio_off: 1`) and named `Spa Water Level (OVERRIDE)`. Low-water protection is effectively disabled. After repairing the sensor (replace bolts with A4/316 stainless, all same metal), swap `gpio_on`/`gpio_off` back and remove `(OVERRIDE)` from the name. Once repaired, the inverted config trips a water problem, so it can't go unnoticed.
2. **Electrolysis on the water level electrodes.** The sensor runs on DC, which corrodes the anode. Solved in the design by powering it only while measuring (see [Water level sensor](#water-level-sensor)). Still open: whether the module works on 3V3 and within the GPIO current limit; otherwise power it through a relay.
3. **Pumps switch on when HA reboots while the heat pump is on.** Not visible in HA history. Unverified hypothesis: the MQTT broker (on the HA host) is down, `client.connect()` fails outside the `try` block, and systemd restarts the script every 5 s; before commit `e48c2f1` each start drove the active-low relays on. Start by checking the journal around an HA reboot:
   ```bash
   journalctl -u managespacontroller.service --since "14 days ago" --no-pager | grep -E "Started|Stopped|exited|Traceback|Error|refused|failed" | tail -60
   ```
