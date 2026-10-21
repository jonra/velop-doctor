import json

import pytest

from ethtester.checks.command import CheckError, run_command
from ethtester.checks.lldp import check_lldp, parse_lldp
from ethtester.results import LldpResult

NAMED = json.dumps({"lldp": {"interface": {"eth0": {
    "via": "LLDP", "rid": "1", "age": "0 day, 00:01:23",
    "chassis": {"SW-Office": {"id": {"type": "mac", "value": "aa:bb:cc:dd:ee:ff"},
                              "descr": "Managed switch"}},
    "port": {"id": {"type": "ifname", "value": "gi0/14"}, "descr": "Port 14"},
}}}})

UNNAMED_LIST = json.dumps({"lldp": {"interface": [{"eth0": {
    "via": "LLDP", "age": "1 day, 02:00:05",
    "chassis": {"id": {"type": "mac", "value": "aa:bb:cc:dd:ee:ff"}},
    "port": {"id": {"type": "mac", "value": "11:22:33:44:55:66"}, "descr": "Port 7"},
}}]}})


def test_named_switch():
    assert parse_lldp(NAMED, "eth0") == LldpResult("SW-Office", "gi0/14", 83)


def test_unnamed_switch_in_list_form():
    assert parse_lldp(UNNAMED_LIST, "eth0") == LldpResult("aa:bb:cc:dd:ee:ff", "Port 7", 93605)


def test_no_neighbour():
    assert parse_lldp('{"lldp": {}}', "eth0") is None
    assert parse_lldp(NAMED, "eth1") is None


def test_check_lldp_uses_runner():
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return NAMED

    assert check_lldp("eth0", run=fake_run).port == "gi0/14"
    assert calls == [["lldpcli", "-f", "json", "show", "neighbors", "ports", "eth0"]]


def test_run_command_errors():
    assert run_command(["echo", "hi"]) == "hi\n"
    with pytest.raises(CheckError):
        run_command(["false"])
    assert run_command(["false"], check=False) == ""
    with pytest.raises(CheckError):
        run_command(["definitely-not-a-command-xyz"])
