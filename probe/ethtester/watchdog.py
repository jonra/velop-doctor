"""systemd sd_notify over $NOTIFY_SOCKET, using only the standard library."""

import logging
import os
import socket

log = logging.getLogger(__name__)


def _address(value: str) -> str:
    return "\0" + value[1:] if value.startswith("@") else value


def notify(message: str) -> None:
    value = os.environ.get("NOTIFY_SOCKET")
    if not value:
        return
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.connect(_address(value))
            sock.sendall(message.encode())
    except Exception:
        log.warning("sd_notify %r failed", message, exc_info=True)
