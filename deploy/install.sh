#!/usr/bin/env bash
# Installs ethtester on Raspberry Pi OS Lite (Bookworm). Usage: sudo deploy/install.sh [interface]
set -euo pipefail

IFACE="${1:-eth0}"
SRC="$(cd "$(dirname "$0")/.." && pwd)"

if [ "$(id -u)" -ne 0 ]; then
  echo "Run with sudo." >&2
  exit 1
fi

apt-get update
apt-get install -y python3-pil python3-spidev python3-gpiozero python3-lgpio lldpd curl fonts-dejavu-core

raspi-config nonint do_spi 0
systemctl enable --now lldpd

if ! nmcli -t -f NAME connection show | grep -qx ethtester; then
  nmcli connection add type ethernet ifname "$IFACE" con-name ethtester \
    ipv4.method auto ipv4.dhcp-timeout 2147483647 ipv6.method ignore \
    connection.autoconnect-retries 0 connection.autoconnect-priority 10
fi

mkdir -p /opt/ethtester /var/log/ethtester
rm -rf /opt/ethtester/ethtester
cp -r "$SRC/ethtester" /opt/ethtester/
find /opt/ethtester -name __pycache__ -prune -exec rm -rf {} +

if [ ! -f /boot/firmware/ethtester.toml ]; then
  cp "$SRC/deploy/ethtester.toml" /boot/firmware/ethtester.toml
fi

cp "$SRC/deploy/ethtester.service" /etc/systemd/system/ethtester.service
systemctl daemon-reload
systemctl enable ethtester

echo "Installed. Check the display version in /boot/firmware/ethtester.toml, then reboot."
