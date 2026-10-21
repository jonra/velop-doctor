"""HTTPS request through the tested interface; also reports the public IP."""

from ..results import InternetResult
from .command import CheckError, run_command


def parse_trace(raw: str) -> str | None:
    for line in raw.splitlines():
        if line.startswith("ip="):
            return line[3:].strip()
    return None


def check_internet(interface: str, url: str, run=run_command) -> InternetResult:
    try:
        out = run(["curl", "--interface", interface, "-s", "-f", "-m", "5", url], timeout=10)
    except CheckError:
        return InternetResult(ok=False, public_ip=None)
    return InternetResult(ok=True, public_ip=parse_trace(out))
