"""Run the dashboard in the background at login (launchd on macOS, systemd --user on Linux)."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

LABEL = "io.github.velop-doctor"


def _plist_path():
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def _unit_path():
    return Path.home() / ".config" / "systemd" / "user" / "velop-doctor.service"


def _plist(port):
    log = Path.home() / "Library" / "Logs" / "velop-doctor.log"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{LABEL}</string>
  <key>ProgramArguments</key><array>
    <string>{sys.executable}</string><string>-m</string><string>velop_doctor</string>
    <string>serve</string><string>--port</string><string>{port}</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>{log}</string>
  <key>StandardErrorPath</key><string>{log}</string>
</dict></plist>
"""


def _unit(port):
    return f"""[Unit]
Description=velop-doctor dashboard

[Service]
ExecStart={sys.executable} -m velop_doctor serve --port {port}
Restart=on-failure

[Install]
WantedBy=default.target
"""


def install(port):
    if sys.platform == "darwin":
        path = _plist_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_plist(port))
        domain = f"gui/{os.getuid()}"
        subprocess.run(["launchctl", "bootout", domain, str(path)], capture_output=True)
        r = subprocess.run(["launchctl", "bootstrap", domain, str(path)], capture_output=True, text=True)
        if r.returncode:
            print(f"launchctl failed: {r.stderr.strip()}")
            return 1
        print(f"Installed. Dashboard: http://127.0.0.1:{port}\nLogs: ~/Library/Logs/velop-doctor.log")
        return 0
    path = _unit_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_unit(port))
    subprocess.run(["systemctl", "--user", "daemon-reload"])
    r = subprocess.run(["systemctl", "--user", "enable", "--now", "velop-doctor.service"])
    return r.returncode


def uninstall():
    if sys.platform == "darwin":
        path = _plist_path()
        subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", str(path)], capture_output=True)
        path.unlink(missing_ok=True)
        print("Removed.")
        return 0
    subprocess.run(["systemctl", "--user", "disable", "--now", "velop-doctor.service"])
    _unit_path().unlink(missing_ok=True)
    return 0
