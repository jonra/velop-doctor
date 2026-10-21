"""Polls a condition with a deadline, so a stuck pin can't hang the service."""

import time


def wait_until(predicate, timeout: float, sleep=time.sleep, clock=time.monotonic,
               interval: float = 0.01, message: str = "timed out") -> None:
    deadline = clock() + timeout
    while not predicate():
        if clock() >= deadline:
            raise TimeoutError(message)
        sleep(interval)
