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
The MQTT user and password are not in git. They live on the Pi in `managespacontroller.secrets.json`, next to the script (listed in `.gitignore`). Create it once:

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
| **Standby** | No run active. Outputs can be on through manual control. |
| **Session** | A run started by the user with the Session switch. |
| **Maintenance** | A run started by the clock, which ends itself. |

### Transitions
| From | To | When | Action |
|---|---|---|---|
| (start) | Standby | Controller starts, after the first sensor readings | Every output to its `initial_state`; if a problem monitor is active, the fault interlock applies instead |
| Standby | Session | Session switch on | The outputs that take part in a session switch on |
| Standby | Maintenance | Maintenance time reached | The outputs that have a run time switch on, each with its own time |
| Maintenance | Session | Session switch on | What the maintenance run drives goes off, then the session starts |
| Session | Standby | Session switch off | Every spa output off |
| Maintenance | Standby | Every participant has run its time | Nothing; they switched themselves off |

A run only starts and stops the outputs that take part in it, so an output that takes part in neither run is never touched by one. Ending a session is the user saying they are done, so it switches every spa output off, a hand-switched one included. A maintenance run ends by itself and leaves anything it did not drive as it is.

A maintenance time during a run does nothing, and the same time fires only once. When the controller stops, every spa output goes to its `initial_state`.

The pins are claimed before the first readings: every output is set up at the level of its own `initial_state`, so nothing changes level during those first seconds.

### Which outputs take part
Each output says so itself: `session` marks it as part of a session, `maintenance` gives it a run time in a maintenance run. Without either key the state machine never starts or stops it, which is why the heat pump is not touched by a run.

Outputs switch on one per `spa.stagger_secs` (16 A breaker inrush), in the order in which they appear in the config; that order has no other meaning, because only the spreading of the starts matters.

### Maintenance run times
Every participant goes off when its own run time is up, counted from the moment it switched on. The run is over once the last one has, and then there is nothing left to switch off. The circulation output has the longest time, so it is the one that ends the run.

### Manual control
A command from HA or the web page switches only that output. It does not start or end a run. An output switched on by hand keeps running until the end of a session, the fault interlock, or a command that switches it off.

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
- **Session switch:** on during a session. A maintenance run leaves it off, so switching it on hands that run over to a session.
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
The keys speak for themselves in `managespacontroller.py.json`. The one thing it does not show: an output with a `command_topic` is a spa output, one without (buzzer, water level power) is internal. Without a `display` section there is no LCD and no buzzer; the `display` section names the output that is the buzzer, like the water level sensor names its power output.

### Code structure
| Module | Contents |
|---|---|
| `managespacontroller.py` | `main()`: read config, build objects, main loop (service entry point). |
| `entities.py` | Publisher (MQTT states, attributes and discovery), base class Entity (name, `unique_id`, topics, discovery payload), Output, Input, WaterLevelSensor (Input with power output), TemperatureSensor, TimestampSensor, Monitor, SessionSwitch, OperationSensor, StatusSensor, ResponseSensor |
| `spa.py` | The state machine (states, transitions, which outputs a run starts, run times, rules), the fault interlock and frost protection; owns all entities; all commands go through it |
| `webserver.py`, `webserver.html` | Web page: HTTP server in its own thread (status snapshot from the main loop, commands via the queue) and the page itself |
| `display.py` | LCD and buzzer; gets its information from the Spa. The buzzer does not depend on the LCD. |

- An Output switches the pin and remembers its state in one place; outputs are never read back. Only inputs and sensors are read.
- The fault interlock and frost protection are checked at that single place where outputs are switched.
- Each sensor class runs its own measuring thread and posts results to the queue.
- Every class declares its attributes explicitly with defaults; config values are mapped onto them (no `setattr` from config, no `hasattr` checks).
- No global variables.

## Error handling
The controller keeps running wherever it can. It needs its config; everything else is optional.

- **Never fatal:** a broker that is down, missing or damaged credentials, no LCD, no buzzer, a web port already in use, a sensor that cannot be read. Each is logged once and retried, and the controller carries on without it. Without credentials there is no Home Assistant, but the web page still switches outputs.
- **An unexpected error in the main loop** is logged with a traceback and sounded as three beeps, after which the loop simply continues. An error that keeps coming back keeps beeping.
- **Fatal:** a missing or damaged `managespacontroller.py.json`, and a `power` or `force_on` that names an output the config does not define. Without a config there is nothing to control, and switching relays on a half-read one is worse than stopping.

## Home Assistant
- **Entities** come from MQTT discovery (device "Spa Controller"). Since HA 2026.06 a new entity gets the area as prefix (`garden_...`); rename it once in HA to `<domain>.spa_controller_<name>`. The name stays after restarts because the `unique_id` does not change.
- **Removing an entity** (after taking it out of the config): publish an empty retained message to its discovery topic, e.g. action `mqtt.publish` with `topic: homeassistant/<component>/spa-controller/<unique_id>/config`, `payload: ""`, `retain: true`. A new entity shows `unknown` for up to 60 s, until the next republish.
- **Dashboard** "Jacuzzi" (`dashboard-jacuzzi`) is managed as code in the separate repo `homeassistant-dashboards` with hadsync (`hadsync validate/push/pull`). The Spa Operation row uses `custom:template-entity-row` for the state-dependent icon color. Change the dashboard in that repo, not in the HA UI: the next push overwrites whatever the UI wrote.

## Tests
Scenario tests without hardware: `python tests/test_spa.py`. Run them before every commit. What they cover, how the stubs work and how to add a test: [tests/README.md](tests/README.md).

## Open issues
1. **Water level sensor bridged in hardware (temporary).** The electrodes are oxidized and have to be replaced by titanium bolts with a round head, both of the same metal. Until then the contact is bridged physically, so the input always reads "water present" and low-water protection is inactive. The config carries the real polarity, so removing the bridge restores the protection without a config change.
2. **The heat pump itself cannot be reached.** The controller only switches its mains power; whether it then heats is up to the heat pump's own controls, which are reachable only through the Poolex app. So the spa cannot be warmed up from away unless that app is at hand. To be worked out: a way to set the heat pump on, off and to a temperature without that app.
