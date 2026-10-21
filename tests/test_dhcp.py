from ethtester.checks.dhcp import check_dhcp, parse_nmcli, reactivate
from ethtester.results import DhcpResult

LEASE = """IP4.ADDRESS[1]:192.168.1.57/24
IP4.GATEWAY:192.168.1.1
IP4.ROUTE[1]:dst = 192.168.1.0/24, nh = 0.0.0.0, mt = 100
IP4.DNS[1]:192.168.1.1
IP4.DNS[2]:1.1.1.1
IP4.DOMAIN[1]:lan
DHCP4.OPTION[1]:broadcast_address = 192.168.1.255
DHCP4.OPTION[2]:dhcp_server_identifier = 192.168.1.1
DHCP4.OPTION[3]:ip_address = 192.168.1.57
"""

NO_LEASE = """IP4.GATEWAY:--
"""


def test_lease():
    assert parse_nmcli(LEASE) == DhcpResult(
        ip="192.168.1.57", prefix=24, gateway="192.168.1.1",
        dhcp_server="192.168.1.1", dns_servers=("192.168.1.1", "1.1.1.1"),
    )


def test_no_lease():
    assert parse_nmcli(NO_LEASE) == DhcpResult()
    assert parse_nmcli("") == DhcpResult()


def test_link_local_is_not_a_lease():
    assert parse_nmcli("IP4.ADDRESS[1]:169.254.10.20/16\n").ip is None


def test_check_dhcp_command():
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return LEASE

    assert check_dhcp("eth0", run=fake_run).ip == "192.168.1.57"
    assert calls == [["nmcli", "-t", "-f", "IP4,DHCP4", "device", "show", "eth0"]]


def test_reactivate_reconnects_device_without_waiting():
    calls = []

    def run(cmd, check=True):
        calls.append((cmd, check))
        return ""

    reactivate("eth0", run=run)
    assert calls == [(["nmcli", "--wait", "0", "device", "connect", "eth0"], False)]
