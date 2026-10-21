"""IPv4 lease details from NetworkManager."""

from ..results import DhcpResult
from .command import run_command


def parse_nmcli(raw: str) -> DhcpResult:
    ip = prefix = gateway = server = None
    dns: list[str] = []
    for line in raw.splitlines():
        key, _, value = line.partition(":")
        value = value.strip()
        if key.startswith("IP4.ADDRESS") and ip is None and "/" in value:
            address, _, bits = value.partition("/")
            if not address.startswith("169.254."):
                ip, prefix = address, int(bits)
        elif key == "IP4.GATEWAY" and value and value != "--":
            gateway = value
        elif key.startswith("IP4.DNS") and value:
            dns.append(value)
        elif key.startswith("DHCP4.OPTION"):
            name, _, option = value.partition(" = ")
            if name.strip() == "dhcp_server_identifier":
                server = option.strip()
    return DhcpResult(ip=ip, prefix=prefix, gateway=gateway, dhcp_server=server,
                      dns_servers=tuple(dns))


def check_dhcp(interface: str, run=run_command) -> DhcpResult:
    return parse_nmcli(run(["nmcli", "-t", "-f", "IP4,DHCP4", "device", "show", interface]))


def reactivate(interface: str, run=run_command) -> None:
    """Ask NetworkManager to reconnect, so a new plug gets a fresh DHCP lease."""
    run(["nmcli", "--wait", "0", "device", "connect", interface], check=False)
