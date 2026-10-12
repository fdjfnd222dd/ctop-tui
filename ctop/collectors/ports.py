"""Listening ports and active connections, read from the kernel socket table.

One psutil.net_connections() scan feeds both readers, so the Security page pays
for the /proc/*/fd walk once per refresh.
"""
from __future__ import annotations

import ipaddress
import socket
import time
from typing import Callable, Iterable, Optional, Tuple, Union

import psutil

from ctop.collectors.base import Collector
from ctop.models import Connection, Listener

MAX_CONNECTIONS = 200
MAX_LISTENERS = 300
LISTEN = "LISTEN"

_PRIVATE_NETS = tuple(ipaddress.ip_network(n) for n in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16",
    "127.0.0.0/8", "100.64.0.0/10",          # CGNAT, also used by Tailscale
    "::1/128", "fc00::/7", "fe80::/10"))

# Lower rank = more interesting. Busy TIME_WAIT churn goes last so the cap drops it first.
_STATUS_RANK = {"ESTABLISHED": 0, "SYN_SENT": 1, "SYN_RECV": 1, "NONE": 2, "TIME_WAIT": 9}


def _norm(text: str) -> Optional[Union[ipaddress.IPv4Address, ipaddress.IPv6Address]]:
    """Parse an address, dropping a %scope suffix and unwrapping ::ffff:a.b.c.d."""
    try:
        ip = ipaddress.ip_address(str(text).split("%", 1)[0])
    except ValueError:
        return None
    if ip.version == 6 and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    return ip


def is_private_ip(text: str) -> bool:
    ip = _norm(text)
    if ip is None:
        return False
    # Check family first: ipaddress does not compare v4 and v6 networks cleanly.
    return any(ip in net for net in _PRIVATE_NETS if net.version == ip.version)


def exposure_of(address: str) -> str:
    ip = _norm(address)
    if ip is None:
        return "specific"           # unparsable: not loopback, not wildcard
    if ip.is_loopback:
        return "local"
    if ip.is_unspecified:
        return "network"
    return "specific"


def _addr_key(address: str) -> str:
    """Group key: all wildcards share one row, all loopback shares one row."""
    ip = _norm(address)
    if ip is None:
        return address
    if ip.is_unspecified:
        return "*"
    if ip.is_loopback:
        return "lo"
    return str(ip)


def _pick_address(addresses: list) -> str:
    # Prefer the IPv4 text so the 0.0.0.0 + :: pair shows as one 0.0.0.0 row.
    for address in addresses:
        ip = _norm(address)
        if ip is not None and ip.version == 4:
            return address
    return addresses[0]


def _is_loopback(text: str) -> bool:
    ip = _norm(text)
    return ip is not None and ip.is_loopback


def _proto(conn) -> Optional[str]:
    if conn.type == socket.SOCK_STREAM:
        return "tcp"
    if conn.type == socket.SOCK_DGRAM:
        return "udp"
    return None


class ProcessNames:
    """pid -> process name, cached so each refresh makes few psutil lookups."""

    def __init__(self, ttl: float = 60.0, clock: Optional[Callable[[], float]] = None,
                 lookup: Optional[Callable[[int], str]] = None) -> None:
        self._ttl = ttl
        self._clock = clock or time.monotonic
        self._lookup = lookup or _psutil_name
        self._cache = {}  # pid -> (name, expires_at)

    def __call__(self, pid: Optional[int]) -> Optional[str]:
        if pid is None:
            return None
        now = self._clock()
        hit = self._cache.get(pid)
        if hit is not None and hit[1] > now:
            return hit[0]
        try:
            name = self._lookup(pid)
        except (psutil.Error, OSError):
            name = None             # process vanished or is not ours; remember that too
        # Bound memory on a long-running dashboard; a cleared cache just costs lookups.
        if len(self._cache) > 2000:
            self._cache.clear()
        self._cache[pid] = (name, now + self._ttl)
        return name


def _psutil_name(pid: int) -> str:
    return psutil.Process(pid).name()


def _scan() -> list:
    # Permission or race errors on the socket walk mean "no rows", not a crash.
    try:
        return list(psutil.net_connections(kind="inet"))
    except (psutil.Error, OSError):
        return []


_NAMES = ProcessNames()


def read_listeners(conns: Optional[Iterable] = None,
                   name_of: Optional[Callable[[Optional[int]], Optional[str]]] = None) -> Tuple[Listener, ...]:
    if conns is None:
        conns = _scan()
    if name_of is None:
        name_of = _NAMES

    groups = {}  # (proto, port, process, addr_key) -> [(address, pid), ...]
    for c in conns:
        proto = _proto(c)
        if proto is None:
            continue
        if proto == "tcp":
            if c.status != LISTEN:
                continue
        elif c.raddr or c.laddr.port == 0:
            # A UDP socket with a remote end is a conversation, not a service.
            continue
        process = name_of(c.pid)
        key = (proto, c.laddr.port, process, _addr_key(c.laddr.ip))
        groups.setdefault(key, []).append((c.laddr.ip, c.pid))

    listeners = []
    for (proto, port, process, _), members in groups.items():
        pids = [pid for _, pid in members if pid is not None]
        address = _pick_address([addr for addr, _ in members])
        listeners.append(Listener(
            proto=proto, address=address, port=port,
            pid=min(pids) if pids else None,
            process=process, exposure=exposure_of(address)))
    listeners.sort(key=lambda row: (row.port, row.proto, row.address))
    return tuple(listeners[:MAX_LISTENERS])


def read_connections(conns: Optional[Iterable] = None,
                     name_of: Optional[Callable[[Optional[int]], Optional[str]]] = None) -> Tuple[Connection, ...]:
    if conns is None:
        conns = _scan()
    if name_of is None:
        name_of = _NAMES

    rows = []
    for c in conns:
        proto = _proto(c)
        if proto is None or not c.raddr or c.status == LISTEN:
            continue
        # Loopback-to-loopback is local plumbing (IPC, dev servers), not network traffic.
        if _is_loopback(c.laddr.ip) and _is_loopback(c.raddr.ip):
            continue
        remote_ip = str(c.raddr.ip).split("%", 1)[0]
        rows.append(Connection(
            proto=proto, local_port=c.laddr.port, remote_ip=remote_ip,
            remote_port=c.raddr.port, status=c.status, pid=c.pid,
            process=name_of(c.pid), remote_is_private=is_private_ip(c.raddr.ip)))

    rows.sort(key=_connection_sort_key)
    # Cap after sorting so the cap drops the least interesting rows.
    return tuple(rows[:MAX_CONNECTIONS])


def _connection_sort_key(row: Connection):
    rank = _STATUS_RANK.get(row.status, 3)
    return (rank, row.process is None, row.process or "", row.remote_ip, row.remote_port)


class PortsCollector(Collector):
    name = "ports"
    interval = 5.0

    def __init__(self, conns_fn: Optional[Callable[[], Iterable]] = None) -> None:
        super().__init__()
        self._conns_fn = conns_fn if conns_fn is not None else (
            lambda: psutil.net_connections(kind="inet"))

    def refresh(self) -> None:
        # One scan, shared by both readers, so they always describe the same moment.
        conns = list(self._conns_fn())
        self.data = (read_listeners(conns), read_connections(conns))
