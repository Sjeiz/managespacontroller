# managespacontroller
My Raspberry Pi 4B base spa controller

## Connect
```bash
ssh SjeizAdmin@spa-controller.iot.cheizoo.lan
```
The repo lives in `/home/SjeizAdmin/python/managespacontroller/managespacontroller`.

## Logs
The script runs as the systemd service `managespacontroller` and logs to the journal.
Logged: commands from HA (`Message received`), state changes (`State:`), switched outputs (`Output`), refused switch-ons/offs (`Refused`), forced switch-ons by frost protection (`Force on`) and faults (`Fault:`). A status line (`Status:`) is logged at startup and every `status_log_secs` (default 300 s): state, outputs on, temperatures and inputs.

```bash
# Follow live
journalctl -u managespacontroller.service -f

# A specific time window
journalctl -u managespacontroller.service --since "2026-10-04 23:25" --until "2026-10-04 23:35" --no-pager

# Who switched what: commands from HA, state changes, refusals and faults
journalctl -u managespacontroller.service --since "24 hours ago" --no-pager | grep -E "Message received|State:|Refused|Fault:"

# Service starts/stops and crashes
journalctl -u managespacontroller.service --since "14 days ago" --no-pager | grep -E "Started|Stopped|exited|Traceback|Error"

# Service status
systemctl status managespacontroller
```

## Secrets
The MQTT user and password are not in git. They live on the Pi in `managespacontroller.secrets.json`, next to the script (listed in `.gitignore`). Without it the controller stops with an error. Create it once:

```bash
cd /home/SjeizAdmin/python/managespacontroller/managespacontroller
cat > managespacontroller.secrets.json <<'EOF'
{"mqtt": {"user": "<mqtt user>", "password": "<mqtt password>"}}
EOF
chmod 600 managespacontroller.secrets.json
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

## State machine

### States
| State | Meaning |
|---|---|
| **Standby** | No session or maintenance active. Outputs can be on through manual control. |
| **Session** | Started by the user with the Session switch. |
| **Maintenance** | Started by the clock. Behaves like a session, with timers that end it. |

### Transitions
| From | To | When | Action |
|---|---|---|---|
| (start) | Standby | Controller starts, after the first sensor readings (max 15 s) | Every output to its `initial_state`; if a problem monitor is active, the fault interlock applies instead |
| Standby | Session | Session switch on | Session list on; outputs outside the list are left as they are |
| Standby | Maintenance | Maintenance time reached | Maintenance list on, timers start; outputs outside the list are left as they are |
| Maintenance | Session | Session switch on | Maintenance-only outputs off, session list on, timers cancelled |
| Session or Maintenance | Standby | Session switch off | Every output to its `initial_state` |
| Maintenance | Standby | Circulation timer expired | Every output to its `initial_state` |

Standby is what the config says: leaving a list puts every output back to its `initial_state`, so an output that no list owns keeps running and one that is on by default comes back on. Starting a list only switches that list on, so an output switched on by hand stays on.

A maintenance time during a session or a running maintenance does nothing. When the controller stops, every spa output goes to its `initial_state`.

The pins are claimed before the first readings: every output is set up at the level of its own `initial_state`, so nothing changes level during those first seconds.

### Lists
The config defines a session list and a maintenance list (`spa.session_list`, `spa.maintenance_list`): ordered steps, where the outputs in one step switch on together.

On entering Session or Maintenance:
- outputs in the list that are off are switched on; motors are staggered by the configured delay (16 A breaker inrush);
- outputs in the other list but not in this one are switched off;
- outputs in neither list are not touched.

### Maintenance timers
- Flush time expired (counted from when the maintenance list is fully on): every output of the maintenance list except the circulation output goes off.
- Circulation duration expired: back to Standby.

### Rules
| Rule | Config | Behaviour |
|---|---|---|
| **Manual control** | - | A command from HA switches only that output |

### Fault interlock
A blocking layer above the state machine; the state machine itself has no fault handling.
1. **A problem monitor becomes active:** the state machine goes to Standby (the same transition as Session switch off) and every spa output goes off, also outputs whose `initial_state` is on (the heat pump drives the circulation pump, which would run dry without water). The buzzer pulses 1 s on, 1 s off (never continuously on).
2. **While active:** every switch-on is refused, whether from the Session switch, a maintenance time or manual control. Exempt: the buzzer and the water level sensor's power output (otherwise the water level could never be measured again and the fault would never clear).
3. **Cleared:** every output to its `initial_state`, as at controller start.

Monitors are defined in the config (`monitors`). A monitor with `device_class: problem` triggers the fault interlock; a monitor with `force_on` is a [frost protection](#frost-protection)-type override.

**The order of the blocks under `monitors` in the config is the priority of Spa Status: the first active monitor wins.** To change the priority, move the blocks. A monitor may also check monitors defined above it.

Each monitor that compares a value has its own hysteresis, so sensor jitter around the limit does not toggle the fault.

### Frost protection
A monitor with `force_on` keeps that output on while it is active; switching it off is refused. With `unknown_active` the monitor also counts as active when its value is unknown (sensor failure). The fault interlock takes precedence.
While a `force_on` monitor is active the LCD stays readable in Standby and shows its `warning` in capitals, but without backlight: the condition can last for weeks.

### Reporting to HA
- **Session switch:** on during Session and Maintenance.
- **`spa_operation`:** sensor showing the highest applicable value, in this priority:

  | Value | When |
  |---|---|
  | `Error` | Fault interlock active |
  | `Active` | State Session (started with the Session switch) |
  | `Maintenance` | State Maintenance (clock-started run) |
  | `Frost` | A `force_on` monitor is active |
  | `Manual` | Standby with an output on whose `initial_state` is off |
  | `Standby` | None of the above |

  `Active`, `Maintenance` and `Standby` follow the state machine; `Error`, `Frost` and `Manual` are reporting only.
- **Spa Status:** sensor with the `warning` of the first active monitor in config order, or `Normal`. The attribute `active` lists all active warnings. The monitors themselves are not separate entities.
- **Spa Response:** text sensor with the response to the last command from HA or the web page, e.g. `<target> on: OK` or `<target> on: refused (<reason>)`. The time is an attribute (`time`), so a repeated identical response still counts as an update; on a dashboard use `last-updated` for "xx ago".
- Outputs, sensors and the water level input: each its own entity.

### Web page
Control without Home Assistant: open `http://spa-controller.iot.cheizoo.lan` (port from `web.port`) in a browser on the local network. No password.
- Shows the same as the HA dashboard: Spa Operation, Spa Status, temperatures, water level, and a switch for the Session switch and every spa output. The layout stacks on a phone.
- Refreshes every 2 s. A command goes into the same queue as MQTT commands, so all rules apply; a refused command shows its reason (e.g. `Refused: fault active`) and the switch falls back.
- Works when HA or the MQTT broker is down. If the port cannot be opened, the controller logs it and keeps running without the page.
- Without a `web` section in the config the page is off.

### Water level sensor
The sensor only gets power while measuring, to limit electrolysis on the electrodes:
1. power output on;
2. wait the settle time;
3. read the level input;
4. power output off.

Between measurements the last reading is kept. The first measurement runs at startup. The power output is a normal output with its own on/off level, so it works directly on 3V3 or through a relay. It is not exposed to HA.

### Threads
- Each sensor runs in its own thread and measures once per measuring interval (the DS18B20 temperature sensors take up to 0.75 s per reading; the water level sensor does its power/settle/read cycle).
- MQTT receives commands in its own thread (paho); the web server serves requests in its own thread and puts commands in the same queue, getting the refusal reason back.
- All of them put their results in one queue. Only the main loop reads the queue, touches the Spa and switches outputs; the one exception is the water level sensor, which switches its own power output from its thread (no other code uses that pin).

### Config
- Section `disabled`: entity blocks moved here are not read (not set up, not announced to HA); outputs still listed in the session/maintenance lists are skipped with a log warning. Move a block back to re-enable it.
- Per output: pin, on/off level, `initial_state`, HA fields (`unique_id`, topics). Outputs with a `command_topic` are spa outputs; outputs without one (buzzer, water level power) are internal.
- Per sensor (temperature and water level): measuring interval.
- Water level sensor: `power` (the output that powers it) and settle time.
- Per monitor (order = Spa Status priority): `warning` (Spa Status value), limit and, for value checks, hysteresis; optional `force_on` (output kept on while active) and `unknown_active` (an unknown value counts as true).
- Section `web`: port of the web page.
- Section `spa`: Session switch, `spa_operation`, Spa Status and Spa Response sensors (HA fields), circulation output, session list, maintenance list (lists of steps; outputs in one step switch on together), stagger delay, maintenance times (list of clock times, e.g. `["06:00", "18:00"]`), flush time, circulation duration, status log interval.

### Code structure
| Module | Contents |
|---|---|
| `managespacontroller.py` | `main()`: read config, build objects, main loop (service entry point). MQTT connects asynchronously and keeps retrying, so a broker that is down does not stop the controller. |
| `entities.py` | Publisher (MQTT states, attributes and discovery), base class Entity (name, `unique_id`, topics, discovery payload), Output, Input, WaterLevelSensor (Input with power output), TemperatureSensor, TimestampSensor, Monitor, SessionSwitch, OperationSensor, StatusSensor, ResponseSensor |
| `spa.py` | The state machine (state, transitions, lists, timers, rules), the fault interlock and frost protection; owns all entities; all commands go through it |
| `webserver.py`, `webserver.html` | Web page: HTTP server in its own thread (status snapshot from the main loop, commands via the queue) and the page itself |
| `display.py` | LCD and buzzer; gets its information from the Spa. The LCD is optional: if it is missing at startup or fails later, the controller keeps running, logs it once and retries every 60 s. The buzzer does not depend on the LCD. |

- An Output switches the pin and remembers its state in one place; outputs are never read back. Only inputs and sensors are read.
- The fault interlock and frost protection are checked at that single place where outputs are switched.
- Each sensor class runs its own measuring thread and posts results to the queue.
- Every class declares its attributes explicitly with defaults; config values are mapped onto them (no `setattr` from config, no `hasattr` checks).
- No global variables.

## Home Assistant
- **Entities** come from MQTT discovery (device "Spa Controller"). Since HA 2026.06 a new entity gets the area as prefix (`garden_...`); rename it once in HA to `<domain>.spa_controller_<name>`. The name stays after restarts because the `unique_id` does not change.
- **Removing an entity** (after taking it out of the config): publish an empty retained message to its discovery topic, e.g. action `mqtt.publish` with `topic: homeassistant/<component>/spa-controller/<unique_id>/config`, `payload: ""`, `retain: true`. A new entity shows `unknown` for up to 60 s, until the next republish.
- **Dashboard** "Jacuzzi" (`dashboard-jacuzzi`) is managed as code in the separate repo `homeassistant-dashboards` with hadsync (`hadsync validate/push/pull`). The Spa Operation row uses `custom:template-entity-row` for the state-dependent icon color.

## Tests
Scenario tests without hardware: `python tests/test_spa.py`. Run them before every commit. What they cover, how the stubs work and how to add a test: [tests/README.md](tests/README.md).

## Open issues
1. **Water level sensor bridged in hardware (temporary).** The electrodes are oxidized and have to be replaced (bolts in A4/316 stainless, all the same metal). Until then the contact is bridged physically, so the input always reads "water present" and low-water protection is inactive. The config carries the real polarity again, so removing the bridge restores the protection without a config change. The bypass sits in the wiring, where it is visible, instead of in an inverted config.
2. **Frost protection reduced while the thermometers are disconnected (temporary).** `unknown_active` is off on the frost monitor, so an unknown outside temperature no longer forces the heat pump on. Switch it back on once the DS18B20 sensors are wired, otherwise a failing sensor no longer protects against frost.
3. **The relay channels are wired in reverse order and corrected in software.** The load meant for the first channel sits on the last one and so on, break contact included, so every output in the config carries the pin of its mirrored channel. Rewiring the board would let that mirroring go, but changes nothing functionally.
4. **The heat pump runs whenever the controller does not hold it off.** Its channel is wired through the break contact, so the heat pump is powered as soon as that channel is not driven. That is deliberate: a dead Pi in winter does not mean a frozen spa. The consequence runs the other way as well: if the Pi dies while the fault interlock is active (low water, or an empty spa), the heat pump starts anyway and drives the circulation pump dry. Software cannot cover this; it needs hardware (a flow switch in series) if it has to be covered at all.
5. **Pumps switched on when HA rebooted while the heat pump was on.** Not visible in HA history. Unverified hypothesis: the script crashed while the MQTT broker (on the HA host) was down and systemd restarted it every 5 s. The controller now keeps running without a broker; check at the next HA reboot that nothing switches:
   ```bash
   journalctl -u managespacontroller.service --since "14 days ago" --no-pager | grep -E "Started|Stopped|exited|Traceback|Error|refused|failed" | tail -60
   ```
6. **Rebuild and SD card backup.** Not decided. Idea: an install script in this repo (enable 1-wire and I2C, install packages, install and enable the service), so a rebuild is: flash Raspberry Pi OS, clone, run the script, create the secrets file. For an image backup: `image-backup` (RonR's image-utils) to a network share, or a one-off `dd` over SSH. Open question: is there a network share the Pi can write to?
