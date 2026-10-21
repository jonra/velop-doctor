"""A single DNS A-record lookup sent only through the tested interface."""

import random
import socket
import struct

from ..results import DnsResult

SO_BINDTODEVICE = getattr(socket, "SO_BINDTODEVICE", 25)


def build_query(name: str, qid: int) -> bytes:
    header = struct.pack(">HHHHHH", qid, 0x0100, 1, 0, 0, 0)
    labels = name.strip(".").split(".")
    qname = b"".join(bytes([len(label)]) + label.encode("ascii") for label in labels) + b"\x00"
    return header + qname + struct.pack(">HH", 1, 1)


def parse_response(data: bytes, qid: int) -> bool:
    if len(data) < 12:
        return False
    rid, flags, _questions, answers = struct.unpack(">HHHH", data[:8])
    is_response = bool(flags & 0x8000)
    rcode = flags & 0x000F
    return rid == qid and is_response and rcode == 0 and answers > 0


def dns_query(server: str, name: str, interface: str, timeout: float = 2.0) -> bool:
    qid = random.randrange(65536)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, SO_BINDTODEVICE, interface.encode())
        sock.settimeout(timeout)
        sock.sendto(build_query(name, qid), (server, 53))
        data, _ = sock.recvfrom(4096)
    return parse_response(data, qid)


def check_dns(interface: str, servers: tuple[str, ...], name: str, query=dns_query) -> DnsResult:
    if not servers:
        return DnsResult(ok=False, server=None)
    server = servers[0]
    try:
        ok = query(server, name, interface)
    except OSError:
        ok = False
    return DnsResult(ok=ok, server=server)
