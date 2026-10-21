import pytest

from ethtester.busywait import wait_until


class FakeTime:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def test_returns_once_predicate_is_true():
    t = FakeTime()
    answers = iter([False, False, True])
    wait_until(lambda: next(answers), 10, sleep=t.sleep, clock=t.clock)
    assert len(t.sleeps) == 2


def test_returns_immediately_without_sleeping():
    t = FakeTime()
    wait_until(lambda: True, 10, sleep=t.sleep, clock=t.clock)
    assert t.sleeps == []


def test_raises_after_deadline():
    t = FakeTime()
    with pytest.raises(TimeoutError, match="e-paper busy timeout"):
        wait_until(lambda: False, 10, sleep=t.sleep, clock=t.clock,
                   message="e-paper busy timeout")
    assert 10 <= t.now < 10.1
