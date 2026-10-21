import socket
from pathlib import Path

from ethtester import watchdog


def test_notify_without_socket_does_nothing(monkeypatch):
    monkeypatch.delenv("NOTIFY_SOCKET", raising=False)
    watchdog.notify("WATCHDOG=1")


def test_notify_sends_datagram(monkeypatch, tmp_path: Path):
    # Bind relative to tmp_path: macOS limits AF_UNIX paths to 104 bytes.
    monkeypatch.chdir(tmp_path)
    path = "notify.sock"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    server.bind(path)
    server.settimeout(1)
    try:
        monkeypatch.setenv("NOTIFY_SOCKET", path)
        watchdog.notify("WATCHDOG=1")
        assert server.recv(64) == b"WATCHDOG=1"
    finally:
        server.close()


def test_notify_never_raises(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("NOTIFY_SOCKET", str(tmp_path / "missing.sock"))
    watchdog.notify("WATCHDOG=1")


def test_abstract_address_translated():
    assert watchdog._address("@ethtester") == "\0ethtester"
    assert watchdog._address("/run/systemd/notify") == "/run/systemd/notify"
