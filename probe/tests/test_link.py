from pathlib import Path

from ethtester.checks.link import check_link, parse_link
from ethtester.results import LinkResult


def fake_sysfs(root: Path, **files: str) -> None:
    base = root / "eth0"
    base.mkdir(parents=True)
    for name, value in files.items():
        (base / name).write_text(value + "\n")


def test_link_up_gigabit(tmp_path: Path):
    fake_sysfs(tmp_path, carrier="1", speed="1000", duplex="full", carrier_changes="4")
    assert check_link("eth0", tmp_path) == LinkResult(True, 1000, "full", 4)


def test_link_down(tmp_path: Path):
    fake_sysfs(tmp_path, carrier="0", speed="-1", duplex="unknown", carrier_changes="5")
    assert check_link("eth0", tmp_path) == LinkResult(False, None, None, 5)


def test_dongle_missing(tmp_path: Path):
    assert check_link("eth0", tmp_path) == LinkResult(False)


def test_unknown_speed():
    assert parse_link("1", "-1", "full", "2") == LinkResult(True, None, "full", 2)


def test_unreadable_carrier():
    assert parse_link(None, None, None, None) == LinkResult(False)
