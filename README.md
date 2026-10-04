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
