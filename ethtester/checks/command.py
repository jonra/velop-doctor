"""Runs external tools and turns their failures into CheckError."""

import subprocess


class CheckError(Exception):
    pass


def run_command(cmd: list[str], timeout: float = 10.0, check: bool = True) -> str:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CheckError(f"{cmd[0]}: {exc}") from exc
    if check and proc.returncode != 0:
        raise CheckError(f"{cmd[0]} exited {proc.returncode}: {proc.stderr.strip()}")
    return proc.stdout
