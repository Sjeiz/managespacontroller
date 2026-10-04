# managespacontroller
My Raspberry Pi 4B base spa controller

## Deploy
The Pi runs a git clone of this repo; deploy by pushing to GitHub and pulling on the Pi.

1. Commit and push to `origin/main`.
2. On the Pi:
   ```bash
   ssh SjeizAdmin@spa-controller.iot.cheizoo.lan
   cd /home/SjeizAdmin/python/managespacontroller/managespacontroller && git pull
   ```
3. Activate the new code (this switches outputs, so pick the moment):
   ```bash
   sudo systemctl restart managespacontroller
   ```
4. Follow the log: `journalctl -u managespacontroller.service -f`

## Open issues
1. **Water level sensor override (temporary).** The sensor contacts are oxidized and report a false low-water problem, so `spa_water_level` in `managespacontroller.py.json` is inverted (`gpio_on: 0`, `gpio_off: 1`) and named `Spa Water Level (OVERRIDE)`. Low-water protection is effectively disabled. After repairing the sensor (replace bolts with A4/316 stainless, all same metal), swap `gpio_on`/`gpio_off` back and remove `(OVERRIDE)` from the name. Once repaired, the inverted config trips a water problem, so it can't go unnoticed.
2. **Electrolysis on the water level electrodes.** The sensor most likely runs on DC, which corrodes the anode. Idea: power the sensor from a GPIO only while sampling (e.g. briefly every 10 s). Needs the module's make/type to check its current draw against the GPIO limit.
3. **Pumps switch on when HA reboots while the heat pump is on.** Not visible in HA history. Unverified hypothesis: the MQTT broker (on the HA host) is down, `client.connect()` fails outside the `try` block, and systemd restarts the script every 5 s; before commit `e48c2f1` each start drove the active-low relays on. Start by checking the journal around an HA reboot:
   ```bash
   journalctl -u managespacontroller.service --since "14 days ago" --no-pager | grep -E "Started|Stopped|exited|Traceback|Error|refused|failed" | tail -60
   ```
