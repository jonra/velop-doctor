#!/usr/bin/env bash
# Installs ethtester on Raspberry Pi OS Lite (Bookworm or Trixie). Usage: sudo deploy/install.sh [interface]
# The interface defaults to the one in /boot/firmware/ethtester.toml, then eth0.
set -euo pipefail

CONFIG=/boot/firmware/ethtester.toml
IFACE="${1:-}"
if [ -z "$IFACE" ] && [ -f "$CONFIG" ]; then
  IFACE="$(sed -n 's/^interface *= *"\(.*\)".*/\1/p' "$CONFIG")"
  IFACE="${IFACE%%$'\n'*}"
fi
IFACE="${IFACE:-eth0}"
SRC="$(cd "$(dirname "$0")/.." && pwd)"

if [ "$(id -u)" -ne 0 ]; then
  echo "Run with sudo." >&2
  exit 1
fi

apt-get update
apt-get install -y python3-pil python3-spidev python3-gpiozero python3-lgpio lldpd curl fonts-dejavu-core

raspi-config nonint do_spi 0
systemctl enable --now lldpd

if ! nmcli -t -f NAME connection show | grep -x ethtester >/dev/null; then
  nmcli connection add type ethernet ifname "$IFACE" con-name ethtester \
    ipv4.method auto ipv4.dhcp-timeout 2147483647 ipv6.method ignore \
    connection.autoconnect-retries 0 connection.autoconnect-priority 10
fi

mkdir -p /opt/ethtester /var/log/ethtester
rm -rf /opt/ethtester/ethtester
cp -r "$SRC/ethtester" /opt/ethtester/
find /opt/ethtester -name __pycache__ -prune -exec rm -rf {} +

if [ ! -f "$CONFIG" ]; then
  cp "$SRC/deploy/ethtester.toml" "$CONFIG"
fi

cp "$SRC/deploy/ethtester.service" /etc/systemd/system/ethtester.service
systemctl daemon-reload
systemctl enable ethtester

echo "Installed. Check the display version in /boot/firmware/ethtester.toml, then reboot."
