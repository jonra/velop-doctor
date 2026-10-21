from pathlib import Path

import pytest

from ethtester.checks.command import CheckError
from ethtester.checks.link import adapter_max_speed, check_link, parse_ethtool_max_speed, parse_link
from ethtester.results import LinkResult

GIGABIT_DONGLE = """Settings for eth0:
	Supported ports: [ TP MII ]
	Supported link modes:   10baseT/Half 10baseT/Full
	                        100baseT/Half 100baseT/Full
	                        1000baseT/Full
	Supported pause frame use: No
	Supports auto-negotiation: Yes
	Advertised link modes:  10baseT/Half 10baseT/Full
	                        100baseT/Half 100baseT/Full
	                        1000baseT/Full
	Speed: 1000Mb/s
"""

FAST_ETHERNET_DONGLE = """Settings for eth0:
	Supported ports: [ TP MII ]
	Supported link modes:   10baseT/Half 10baseT/Full
	                        100baseT/Half 100baseT/Full
	Supported pause frame use: No
	Advertised link modes:  10baseT/Half 10baseT/Full
	                        100baseT/Half 100baseT/Full
	Speed: 100Mb/s
"""

# A gigabit adapter whose ADVERTISED modes stop at 100: it can do more, so the cable is suspect.
GIGABIT_ADVERTISING_100 = """Settings for eth0:
	Supported link modes:   10baseT/Half 10baseT/Full
	                        100baseT/Half 100baseT/Full
	                        1000baseT/Full
	Supported pause frame use: No
	Advertised link modes:  10baseT/Half 10baseT/Full
	                        100baseT/Half 100baseT/Full
	Link partner advertised link modes:  10baseT/Half 10baseT/Full
	                                     100baseT/Half 100baseT/Full
	Speed: 100Mb/s
"""

MULTIGIG_DONGLE = """Settings for eth0:
	Supported link modes:   10baseT/Half 10baseT/Full 100baseT/Half 100baseT/Full
	                        1000baseT/Full 2500baseT/Full
	Supported pause frame use: Symmetric
"""


def stub(output=None, error=None):
    calls = []

    def run(cmd, timeout=10.0, check=True):
        calls.append(cmd)
        if error:
            raise error
        return output
    run.calls = calls
    return run


def fake_sysfs(root: Path, **files: str) -> None:
    base = root / "eth0"
    base.mkdir(parents=True)
    for name, value in files.items():
        (base / name).write_text(value + "\n")


def test_link_up_gigabit(tmp_path: Path):
    fake_sysfs(tmp_path, carrier="1", speed="1000", duplex="full", carrier_changes="4")
    assert check_link("eth0", tmp_path, run=stub(GIGABIT_DONGLE)) == LinkResult(True, 1000, "full", 4, 1000)


def test_link_down(tmp_path: Path):
    fake_sysfs(tmp_path, carrier="0", speed="-1", duplex="unknown", carrier_changes="5")
    run = stub(GIGABIT_DONGLE)
    assert check_link("eth0", tmp_path, run=run) == LinkResult(False, None, None, 5)
    assert run.calls == [], "no point asking the adapter anything while the link is down"


def test_dongle_missing(tmp_path: Path):
    assert check_link("eth0", tmp_path, run=stub(GIGABIT_DONGLE)) == LinkResult(False)


def test_unknown_speed():
    assert parse_link("1", "-1", "full", "2") == LinkResult(True, None, "full", 2)


def test_unreadable_carrier():
    assert parse_link(None, None, None, None) == LinkResult(False)


@pytest.mark.parametrize("output, expected", [
    (GIGABIT_DONGLE, 1000),
    (FAST_ETHERNET_DONGLE, 100),
    (GIGABIT_ADVERTISING_100, 1000),  # supported, not advertised or partner modes
    (MULTIGIG_DONGLE, 2500),
    ("Supported link modes:   Not reported\n", None),
    ("Settings for eth0:\n\tSpeed: 100Mb/s\n", None),
    ("", None),
])
def test_parse_ethtool_max_speed(output, expected):
    assert parse_ethtool_max_speed(output) == expected


def test_check_link_records_the_adapter_ceiling(tmp_path: Path):
    fake_sysfs(tmp_path, carrier="1", speed="100", duplex="full", carrier_changes="2")
    link = check_link("eth0", tmp_path, run=stub(FAST_ETHERNET_DONGLE))
    assert (link.speed_mbps, link.max_speed_mbps) == (100, 100)


def test_check_link_asks_ethtool_about_the_right_interface(tmp_path: Path):
    fake_sysfs(tmp_path, carrier="1", speed="1000", duplex="full", carrier_changes="2")
    run = stub(GIGABIT_DONGLE)
    check_link("eth0", tmp_path, run=run)
    assert run.calls == [["ethtool", "eth0"]]


def test_missing_ethtool_leaves_the_ceiling_unknown(tmp_path: Path):
    fake_sysfs(tmp_path, carrier="1", speed="100", duplex="full", carrier_changes="2")
    link = check_link("eth0", tmp_path, run=stub(error=CheckError("ethtool: not found")))
    assert link == LinkResult(True, 100, "full", 2, None)
    assert adapter_max_speed("eth0", stub(error=CheckError("boom"))) is None
