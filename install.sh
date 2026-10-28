#!/usr/bin/env bash
# Set up a fresh Raspberry Pi OS for the spa controller. Safe to run again on a
# machine that is already set up: every step checks before it changes anything.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_TXT=/boot/firmware/config.txt
UNIT=/lib/systemd/system/managespacontroller.service
JOURNALD_DROPIN=/etc/systemd/journald.conf.d/00-size.conf
TMP_UNIT=/etc/systemd/system/tmp.mount
TMP_SOURCE=/usr/share/systemd/tmp.mount
SECRETS="$REPO/managespacontroller.secrets.json"

step() { printf '\n== %s\n' "$1"; }

[[ ${EUID} -eq 0 ]] || { echo "Run this with sudo." >&2; exit 1; }
[[ -f ${CONFIG_TXT} ]] || { echo "No ${CONFIG_TXT}; this is not Raspberry Pi OS Bookworm or later." >&2; exit 1; }

step "1-Wire and I2C"
# Appended to the end, which is inside the [all] section of a stock config.txt
for line in "dtparam=i2c_arm=on" "dtoverlay=w1-gpio"; do
    if grep -qxF "${line}" "${CONFIG_TXT}"; then
        echo "already set: ${line}"
    else
        printf '%s\n' "${line}" >> "${CONFIG_TXT}"
        echo "added: ${line}"
    fi
done

step "Packages"
# RPi.GPIO, paho.mqtt and smbus; everything else the controller imports is standard library
apt-get update -qq
apt-get install -y python3-rpi.gpio python3-paho-mqtt python3-smbus

step "Service"
# The unit in the repo carries a path; install it pointing at this clone
sed "s#^ExecStart=.*#ExecStart=/usr/bin/python -u ${REPO}/managespacontroller.py#" \
    "${REPO}/managespacontroller.service" > "${UNIT}"
systemctl daemon-reload
systemctl enable managespacontroller
echo "installed, pointing at ${REPO}/managespacontroller.py"

step "Journal and /tmp in RAM"
# Keeps the card out of normal operation; both are lost on a reboot, which is the point
mkdir -p "$(dirname "${JOURNALD_DROPIN}")"
printf '[Journal]\nStorage=volatile\nRuntimeMaxUse=64M\n' > "${JOURNALD_DROPIN}"
rm -rf /var/log/journal
if [[ -f ${TMP_SOURCE} ]]; then
    cp "${TMP_SOURCE}" "${TMP_UNIT}"
    systemctl daemon-reload
    systemctl enable tmp.mount
    echo "journal volatile, /tmp on tmpfs"
else
    echo "no ${TMP_SOURCE}; /tmp stays on the card" >&2
fi

step "Secrets"
if [[ -f ${SECRETS} ]]; then
    echo "present"
else
    cat <<EOF
Missing: ${SECRETS}
Create it with the MQTT user and password, then chmod 600:
  {"mqtt": {"user": "<user>", "password": "<password>"}}
EOF
fi

step "Reboot to pick up 1-Wire, I2C and the RAM mounts: sudo reboot"
