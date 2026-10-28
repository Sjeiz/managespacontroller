# managespacontroller
My Raspberry Pi 4B based spa controller

## Connect
```bash
ssh SjeizAdmin@spa-controller.iot.cheizoo.lan
```
The repo lives in `/home/SjeizAdmin/python/managespacontroller/managespacontroller`.

## Logs
The service logs to the journal: commands from HA (`Message received`), state changes (`State:`), switched outputs (`Output`), refusals (`Refused`), frost overrides (`Force on`), faults (`Fault:`), and a status line (`Status:`) at startup and every `status_log_secs`, or only at startup when that is 0. The journal lives in RAM, so it starts empty after every reboot.

```bash
journalctl -u managespacontroller.service -f          # follow
journalctl -u managespacontroller.service --no-pager  # all of it, pipe into grep
```

## Secrets
The MQTT user and password are not in git. They live on the Pi in `managespacontroller.secrets.json`, next to the script (listed in `.gitignore`). Without the file, or with a damaged one, the controller runs on and logs it: no HA, but the web page still works. Create it once:

```bash
cd /home/SjeizAdmin/python/managespacontroller/managespacontroller
cat > managespacontroller.secrets.json <<'EOF'
{"mqtt": {"user": "<mqtt user>", "password": "<mqtt password>"}}
EOF
chmod 600 managespacontroller.secrets.json
```

## Fresh install
Flash Raspberry Pi OS, clone this repo, run `sudo ./install.sh`, create the secrets file and reboot. The script enables 1-Wire and I2C, installs the packages and the service, and puts the journal and `/tmp` in RAM. Running it again changes nothing.

## Deploy
The Pi runs a clone of this repo. Push to `origin/main`, then on the Pi:

```bash
cd /home/SjeizAdmin/python/managespacontroller/managespacontroller && git pull && sync
sudo systemctl restart managespacontroller
```

The restart switches outputs, so pick the moment.

## SD card
The controller writes no files and the journal and `/tmp` are in RAM, so only housekeeping and `git pull` reach the card. Image it after `sudo fstrim -av`, which lets the free space compress away; `backup/` is in `.gitignore`.

```bash
ssh spa-controller.iot.cheizoo.lan "sudo dd if=/dev/mmcblk0 bs=4M status=none | gzip -1" \
  > backup/spa-controller-$(date +%Y-%m-%d_%H%M).img.gz
```

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
| (start) | Standby | Controller starts, after the first sensor readings | Every output to its `initial_state`; if a problem monitor is active, the fault interlock applies instead |
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

### Manual control
A command from HA or the web page switches only that output. It does not start or end a state, and an output switched on by hand is left alone until a list or the fault interlock touches it.

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
While a `force_on` monitor is active its `warning` is shown in capitals on the LCD, in front of the active outputs. The LCD itself follows the usual rule: off in Standby.

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
The keys speak for themselves in `managespacontroller.py.json`. Two things it does not show:
- Section `disabled`: entity blocks moved here are not read at all, and an output still named in a list is skipped with a log warning. Move a block back to re-enable it.
- An output with a `command_topic` is a spa output; one without (buzzer, water level power) is internal.

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
- **Dashboard** "Jacuzzi" (`dashboard-jacuzzi`) is managed as code in the separate repo `homeassistant-dashboards` with hadsync (`hadsync validate/push/pull`). The Spa Operation row uses `custom:template-entity-row` for the state-dependent icon color. Change the dashboard in that repo, not in the HA UI: the next push overwrites whatever the UI wrote.

## Tests
Scenario tests without hardware: `python tests/test_spa.py`. Run them before every commit. What they cover, how the stubs work and how to add a test: [tests/README.md](tests/README.md).

## Open issues
1. **Water level sensor bridged in hardware (temporary).** The electrodes are oxidized and have to be replaced by titanium bolts with a round head, both of the same metal. Until then the contact is bridged physically, so the input always reads "water present" and low-water protection is inactive. The config carries the real polarity, so removing the bridge restores the protection without a config change.
