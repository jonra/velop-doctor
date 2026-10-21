import json
from dataclasses import replace
from pathlib import Path

from ethtester.results import GatewayResult, LinkResult
from ethtester.statelog import StateLogger
from ethtester.verdict import evaluate
from tests.factories import ok_snapshot


def test_logs_only_on_meaningful_change(tmp_path: Path):
    path = tmp_path / "logs" / "ethtester.log"
    logger = StateLogger(path)
    s = ok_snapshot()
    assert logger.record(s, evaluate(s, 1000, 20), 1_700_000_000)
    jittery = replace(s, gateway=GatewayResult(True, 9.9, 0.0))
    assert not logger.record(jittery, evaluate(jittery, 1000, 20), 1_700_000_005)
    slow = replace(s, link=LinkResult(up=True, speed_mbps=100, duplex="full"))
    assert logger.record(slow, evaluate(slow, 1000, 20), 1_700_000_010)

    lines = [json.loads(line) for line in path.read_text().splitlines()]
    assert [line["verdict"] for line in lines] == ["OK", "SLOW_LINK"]
    assert lines[1]["text"] == "CABLE? (100M)"
    assert lines[1]["snapshot"]["link"]["speed_mbps"] == 100
    assert "ts" in lines[0]


def test_swapping_the_dongle_is_logged(tmp_path: Path):
    logger = StateLogger(tmp_path / "ethtester.log")
    fast_ethernet = replace(ok_snapshot(), link=LinkResult(up=True, speed_mbps=100, duplex="full", max_speed_mbps=100))
    gigabit_stuck = replace(ok_snapshot(), link=LinkResult(up=True, speed_mbps=100, duplex="full", max_speed_mbps=1000))
    assert logger.record(fast_ethernet, evaluate(fast_ethernet, 1000, 20), 1_700_000_000)
    assert logger.record(gigabit_stuck, evaluate(gigabit_stuck, 1000, 20), 1_700_000_010)
