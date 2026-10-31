# Extracting the heat pump's Tuya `local_key`

A runbook for recovering the Tuya `local_key` of the **Jacuzzi Warmtepomp**
(a Poolex pool heat pump) so it can be driven locally from Home Assistant
(`tuya_local`) or `tinytuya`, with no cloud in the loop.

This builds on the walkthrough and scripts in
[cerealkiller57540/tuya-localkey-frida](https://github.com/cerealkiller57540/tuya-localkey-frida).
Read that first for the *why*. This file records the *how* for **this** device,
and — more importantly — the points where reality deviated from that README, so
the trick can be repeated without re-discovering them.

> The `local_key` is a live secret and is **not** in this file. It lives in
> KeePass. It rotates whenever the device is re-paired or reset — if local
> control stops, the key changed and you dump it again.

---

## Target device (as of 2026-10-31)

| Field | Value | How obtained |
| --- | --- | --- |
| Name | `Jacuzzi Warmtepomp` | app / dump |
| `device_id` (`gwId`) | `bf6ffb89108eb13b54fn4l` | dump **and** LAN broadcast |
| `host` (LAN IP) | `192.168.12.48` | router / LAN; **not** the dump's `getIp` |
| `product_id` / `productKey` | `3tvkbyreb0mhnnlz` | dump **and** LAN broadcast |
| `protocol_version` | `3.3` | LAN broadcast |
| `local_key` | *in KeePass* | Frida dump only |
| tuya-local config | `poolex_qline_heatpump.yaml` → pick **"Pool heat pump"** | DPS signature |

The device is a **Q-Line**, not a Q-7 (despite being sold as a "Q7"). Its live
DPS signature — `{'1': False, '2': 'heating', '4': 15, '15': 0, '16': 23}` —
matches `poolex_qline_heatpump.yaml` (dps 2 is a *string* mode, dps 16 is the
water temperature, no dps 3/11/13), which is a different file from
`poolex_q7_heatpump.yaml`. In the tuya-local config flow the device type to
choose is therefore **"Pool heat pump"** (the Q-Line config), not the Q-7 entry.

The app that holds the key is the vendor app **Poolex** (`com.poolex.xxx`); its
splash activity is `com.smart.ThingSplashActivity`, i.e. it is a Tuya white-label
build. Frida reports its process as **`Poolex`**.

---

## What you need `local_key` for, and what you don't

- `device_id`, `product_id`, `protocol_version` and the LAN IP can **all** be
  read off the network without the app — the device broadcasts them (see
  [Getting device_id without the app](#appendix-device_id-without-the-app)).
- The `local_key` is the **only** value that is not on the wire. It exists in
  cleartext only inside the app's memory once logged in. That is the one thing
  the Frida route is for.

---

## Why an emulator (and not the phone)

Reading another app's memory needs **root**. The phone that runs the Poolex app
(an OPPO CPH2791) is **not rooted**, so Frida cannot attach to the app there. An
Android *emulator* with the `google_apis` image is `su`-able out of the box, so
frida-server can run as root — hence the whole emulator detour. (The
`google_apis_playstore` image blocks `su`; do not use it.)

The Poolex APK is **ARM-only** (`arm64_v8a`). The `x86_64` emulator image runs it
via the NDK translation layer (`libndk_translation.so`), which the `google_apis`
Android-14 image ships. Confirm with:
`adb shell getprop ro.product.cpu.abilist` → must list `arm64-v8a`.

---

## Host prerequisites (Windows)

- **Python 3** with `pip install "frida==16.*" tinytuya`.
  - Pin **frida 16.x**. Frida 17 dropped the bundled Java bridge and silently
    finds zero devices. Verify: `python -c "import frida; print(frida.__version__)"`
    must print `16.x`.
- **JDK 17** for the Android SDK tools — `winget install Microsoft.OpenJDK.17`.
  The cmdline-tools `sdkmanager` refuses JDK 11 ("Java version 17 or higher is
  required"). You do **not** need to change the system default JDK; set
  `JAVA_HOME` to the JDK-17 path for just the SDK commands.
- **Android SDK** in `C:\Android` (cmdline-tools, platform-tools, emulator) and
  the system image `system-images;android-34;google_apis;x86_64`. Accept the
  licenses (`sdkmanager --licenses`).
- **frida-server for Android**, the **16.x** build matching the host frida, arch
  `android-x86_64`, from Frida's GitHub releases (un-`.xz` it first).
- `adb` and `emulator` on PATH (or call by full path under `C:\Android`).

### Gotchas that cost time here

- **Emulator GPU:** the README's `-gpu swiftshader_indirect` **crashes** on this
  box — `opengl32sw` is missing, it falls back to system OpenGL and dies with
  `Failed to find ColorBuffer` / `exited with code 5`, right as the app renders.
  Launch with **`-gpu host`** instead. Add `-noaudio` to silence the harmless
  dsound buffer spam.
- **Two adb devices:** while the phone is plugged in for the APK pull, both the
  phone and the emulator are attached. Pin every adb/frida command to the
  emulator with `export ANDROID_SERIAL=emulator-5554`, or the script's bare
  `adb forward` fails and removes your forward.

---

## Step by step

### 1. Create and boot the emulator

```bash
# one-time: create the AVD (google_apis, NOT playstore)
avdmanager create avd -n tuya_avd -k "system-images;android-34;google_apis;x86_64" -d pixel_6

# boot it (host GPU, no audio); keep this running
emulator -avd tuya_avd -gpu host -cores 4 -memory 4096 -noaudio
```

Wait for boot, then confirm root:
`adb -e shell "su 0 id"` → must show `uid=0(root)`.

### 2. Start frida-server on the emulator

```bash
export ANDROID_SERIAL=emulator-5554
adb push frida-server /data/local/tmp/frida-server
adb shell "su 0 chmod 755 /data/local/tmp/frida-server"
adb shell "su 0 sh -c 'nohup /data/local/tmp/frida-server >/dev/null 2>&1 &'"
adb forward tcp:27042 tcp:27042
```

Sanity check from the host: `frida-ps -U` (or the Python one-liner below) should
list the emulator's processes. frida-server does **not** survive an emulator
reboot's filesystem but the binary does — just re-run the `nohup` line after a
reboot.

### 3. Get the Poolex APK onto the emulator

The app is pulled straight off the (Android) phone — the real, Google-signed
build, no mirror needed. USB-debugging must be enabled on the phone.

```bash
# find the installed paths (base + the arm64 split)
adb -d shell pm path com.poolex.xxx
# pull both to Windows paths (use Windows-style dest paths to avoid Git Bash
# path rewriting silently redirecting the pull)
adb -d pull "<.../base.apk>" base.apk
adb -d pull "<.../split_config.arm64_v8a.apk>" split_config.arm64_v8a.apk
# install both into the emulator
adb -e install-multiple base.apk split_config.arm64_v8a.apk
```

### 4. Log in (you, in the emulator window)

Launch the app, sign in with the Poolex account, and let the device list load.
The key is only in memory once the app has loaded the device. (If the install
is cloned from the phone's logged-in session, it may already be logged in.)

### 5. Dump the key — **use spawn, not attach**

This is the decisive deviation from the upstream README, which uses
`attach`. On the ARM-translated app, **`attach` fails**: the app process dies and
Frida reports `unable to write to process memory: No such process`. (The app also
tends to crash when you open the per-device panel under translation.)

`spawn` works. Start the app under Frida, resume it, then **poll** a re-scan RPC
until the `DeviceBean` instances are actually on the heap — the first scan right
after resume returns `devices: []` because the device list hasn't synced yet.

The scan hook (same getters as upstream `dump_localkey.py`, wrapped in an
`rpc.exports.scan` so it can be called repeatedly):

```js
rpc.exports = {
  scan: function () {
    return new Promise(function (resolve) {
      Java.perform(function () {
        var classes = [];
        Java.enumerateLoadedClasses({
          onMatch: function (name) { if (/DeviceBean$/.test(name)) classes.push(name); },
          onComplete: function () {}
        });
        var out = [];
        classes.forEach(function (name) {
          try { Java.choose(name, {
            onMatch: function (inst) {
              var row = { cls: name };
              ['getDevId','getName','getLocalKey','getProductId','getUuid','getMac','getIp']
                .forEach(function (m) { try { row[m] = '' + inst[m](); } catch (e) {} });
              out.push(row);
            },
            onComplete: function () {}
          }); } catch (e) {}
        });
        resolve({ classes: classes, devices: out });
      });
    });
  }
};
```

Driver (host, Python, frida 16):

```python
import frida, time, json
d = frida.get_device_manager().add_remote_device('127.0.0.1:27042')
pid = d.spawn(["com.poolex.xxx"])
session = d.attach(pid)
script = session.create_script(JS)   # JS = the hook above
script.load()
d.resume(pid)
for _ in range(40):                   # poll up to ~80s
    time.sleep(2)
    r = script.exports_sync.scan()
    if r.get("devices"):
        print(json.dumps(r, ensure_ascii=False, indent=2))
        break
```

Each row has `getDevId`, `getName`, `getLocalKey`, `getProductId`, `getIp`.
`getLocalKey` is the prize. Note `getIp` is usually the **public** IP, not the
device's LAN address — ignore it.

### 6. Verify on the LAN (no cloud)

`status()` is a direct TCP connection to port 6668, not a broadcast, so it works
even from a different subnet as long as the device is routable and 6668 is open.
Pass the key via an env var so it stays out of shell history and process lists.

```bash
export TUYA_KEY='<the local key>'
python -c "
import os, tinytuya
d = tinytuya.Device('bf6ffb89108eb13b54fn4l', '192.168.12.48', os.environ['TUYA_KEY'], version=3.3)
d.set_socketPersistent(False)
print(d.status())"
```

A `{'dps': {...}}` reply means the key and protocol are correct. `Error 905` is
usually a wrong key or wrong version (try 3.4 / 3.5); `Err 901` is a network
problem (wrong IP — don't use the dump's public `getIp` — or a VLAN your host
can't reach).

### 7. Add it to Home Assistant (`tuya_local`)

`tuya_local` is a HACS integration; it is **not** currently installed in this
HA instance — install it from HACS first. Then add the device **manually by IP**
(no cloud): `device_id`, `local_key`, `host` = `192.168.12.48`, version `3.3`.
In the device-type list pick **"Pool heat pump"** (the Q-Line config). With a
manual/IP add, `tuya_local` has no `product_id` to match on, so it matches purely
by the DPS signature — which is why the Q-Line config is what surfaces.

---

## Appendix: `device_id` without the app

The device broadcasts its `gwId`, `productKey` and protocol version in the clear
(encrypted with Tuya's fixed, public UDP key) on UDP 6666/6667/7000. This gets
everything **except** the `local_key`.

Broadcasts do **not** cross subnets. The host here is on `192.168.10.x` and the
heat pump on `192.168.12.x`, so `tinytuya scan` from the host sees nothing.
Listen from a host on the device's subnet instead — the spa-controller Pi
(`spa-controller.iot.cheizoo.lan`, `192.168.12.235`) is on it. Capture a packet
from `192.168.12.48` there and decrypt it with `tinytuya.unpack_message` +
`tinytuya.decrypt_udp` to read `gwId` / `productKey` / `version`.

---

## Known failure modes

- **`attach` kills the app** (`No such process`) — ARM translation and/or the
  app's `:monitor` anti-tamper process. Use `spawn` (step 5).
- **`devices: []` from the hook** — scanned before the device list loaded. Poll
  the re-scan RPC (step 5).
- **Emulator exits with code 5** shortly after boot — `swiftshader_indirect` /
  software-GL failure. Use `-gpu host` (step 1).
- **`frida.ProtocolError` / zero devices** — host frida is 17, not 16. Re-pin.
- **App data on disk doesn't contain the key** — the SDK encrypts its storage, so
  grepping `/data/data/com.poolex.xxx/` for the `device_id` finds nothing. The
  memory dump is the only route.
