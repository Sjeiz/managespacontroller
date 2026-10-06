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
Agreed design, not yet implemented. Replaces the per-output `actor`, `actions_on`/`actions_off` and per-output schedules, whose interplay caused a deadlock: a scheduled pump 1 run switched on the lights, which still carried `actor = "user"`, so `spa_operation` went active, schedules froze, and everything kept running all night (2026-10-05).

### States
| State | Meaning |
|---|---|
| **Standby** | Nobody active. Nothing from the lists is running. |
| **Session** | Spa active, started by the user or by the clock (maintenance). |
| **Fault** | A problem monitor is active. Everything is off. |

A clock-started session is an ordinary session with timers that end it.

### Lists (config)
| List | Contents, in order |
|---|---|
| **Session** | Lights and circulation, then pump 1, 2, 3, 4 |
| **Maintenance** | Circulation, then pump 1, 2, 3, 4, blower |

On every session start:
- outputs in the list that are off are switched on; motors are staggered by the configured delay (to stay under the 16 A breaker's inrush limit);
- outputs in the *other* list but not in this one are switched off.

Heat pump, heater and ozonator are in neither list. The user controls them; the state machine leaves them alone except for Fault and the rules below.

### Transitions
| From | To | When | Action |
|---|---|---|---|
| Standby | Session | Session switch on | Session list |
| Standby | Session (clock) | Configured time reached | Maintenance list, timers start |
| Session (clock) | Session | Session switch on | Session list, timers cancelled |
| Session | Standby | Session switch off | All list outputs off |
| Session (clock) | Standby | Circulation timer expired | All list outputs off |
| Any | Fault | Problem monitor active | Everything off |
| Fault | Standby | Problem cleared | Nothing restarts automatically |

Clock-session timers: pumps and blower go off after the flush time; after the circulation duration the spa returns to Standby.
A configured maintenance time during a session does nothing.

### Rules
- **Manual control:** every output can be switched individually from HA and does only that, except for the two rules below.
- **Heat pump and heater are mutually exclusive:** switching one on switches the other off. No automatic switch-back.
- **Heater requires circulation:** the heater may only be on while circulation runs; when circulation stops, the heater goes off. The heater does not start circulation.
- **Session switch in HA:** a new MQTT discovery switch; on during every session, including clock-started ones.

### Config
- Session list, maintenance list, stagger delay.
- Maintenance: start times, flush time, circulation duration.
- Mutually exclusive outputs (existing `conflict`).

## Open issues
1. **Water level sensor override (temporary).** The sensor contacts are oxidized and report a false low-water problem, so `spa_water_level` in `managespacontroller.py.json` is inverted (`gpio_on: 0`, `gpio_off: 1`) and named `Spa Water Level (OVERRIDE)`. Low-water protection is effectively disabled. After repairing the sensor (replace bolts with A4/316 stainless, all same metal), swap `gpio_on`/`gpio_off` back and remove `(OVERRIDE)` from the name. Once repaired, the inverted config trips a water problem, so it can't go unnoticed.
2. **Electrolysis on the water level electrodes.** The sensor most likely runs on DC, which corrodes the anode. Idea: power the sensor from a GPIO only while sampling (e.g. briefly every 10 s). Needs the module's make/type to check its current draw against the GPIO limit.
3. **Pumps switch on when HA reboots while the heat pump is on.** Not visible in HA history. Unverified hypothesis: the MQTT broker (on the HA host) is down, `client.connect()` fails outside the `try` block, and systemd restarts the script every 5 s; before commit `e48c2f1` each start drove the active-low relays on. Start by checking the journal around an HA reboot:
   ```bash
   journalctl -u managespacontroller.service --since "14 days ago" --no-pager | grep -E "Started|Stopped|exited|Traceback|Error|refused|failed" | tail -60
   ```
