"""LAN devices this computer has recently talked to, from the kernel neighbour cache.

PASSIVE: this only reads what the kernel already holds. It sends no ARP requests,
no pings, no scans, and does no reverse-DNS or MAC-vendor lookups.
The table is NOT a full inventory: it lists only hosts this machine already
exchanged packets with, and entries age out after a few minutes.
"""
from __future__ import annotations

import ipaddress
import json
import os
import subprocess
from subprocess import DEVNULL
from typing import Callable, Dict, List, Optional, Tuple, Union

from ctop.collectors.base import Collector
from ctop.models import Device

# raw row used between parsers and read_devices: (ip, iface, mac_lowercase, state_uppercase)
Row = Tuple[str, str, str, str]
Addr = Union[ipaddress.IPv4Address, ipaddress.IPv6Address]

IP_JSON = ["ip", "-j", "neigh", "show"]
IP_TEXT = ["ip", "neigh", "show"]
CMD_TIMEOUT = 2.0
MAX_ROWS = 256
DROP_STATES = ("FAILED", "INCOMPLETE")          # no reply, so the MAC is not trustworthy
NULL_MACS = ("00:00:00:00:00:00", "ff:ff:ff:ff:ff:ff")


def run_cmd(argv: List[str], timeout: float) -> Optional[str]:
    """Run a read-only command; its stdout, or None if it is missing or hangs."""
    # LC_ALL=C keeps the text parser's words in English whatever the locale.
    env = dict(os.environ, LC_ALL="C")
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                              stdin=DEVNULL, env=env).stdout
    except (OSError, subprocess.SubprocessError):
        return None


def _after(tokens: List[str], key: str) -> Optional[str]:
    """The token that follows `key`, or None when `key` is absent or last."""
    if key not in tokens[:-1]:
        return None
    return tokens[tokens.index(key) + 1]


def parse_neigh_json(text: str) -> List[Row]:
    """Rows from `ip -j neigh show`. Entries missing dst/dev/lladdr are dropped; [] on bad JSON."""
    try:
        entries = json.loads(text)
    except ValueError:
        return []
    if not isinstance(entries, list):
        return []
    rows: List[Row] = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        dst, dev, mac = e.get("dst"), e.get("dev"), e.get("lladdr")
        if not all(isinstance(x, str) and x for x in (dst, dev, mac)):
            continue
        # iproute2 has used both a list and a plain string for "state" over time
        state = e.get("state")
        if isinstance(state, list):
            state = state[0] if state else None
        rows.append((dst, dev, mac.lower(), str(state).upper() if state else "UNKNOWN"))
    return rows


def parse_neigh_text(text: str) -> List[Row]:
    """Rows from plain `ip neigh show`, for iproute2 builds without `-j`."""
    rows: List[Row] = []
    for line in text.splitlines():
        t = line.split()
        if not t:
            continue
        dev = _after(t, "dev")
        mac = _after(t, "lladdr")
        if dev is None or mac is None:
            continue
        # the state is the last word; "router" and the MAC never end a line
        last = t[-1]
        state = last.upper() if last.isalpha() else "UNKNOWN"
        rows.append((t[0], dev, mac.lower(), state))
    return rows


def parse_proc_arp(text: str) -> List[Row]:
    """Rows from /proc/net/arp (IPv4 only). The header line fails the hex parse and is skipped."""
    rows: List[Row] = []
    for line in text.splitlines()[1:]:           # first line is the column header
        p = line.split()
        if len(p) < 6:
            continue
        try:
            flags = int(p[2], 16)
        except ValueError:
            continue
        if flags & 0x4:
            state = "PERMANENT"
        elif flags & 0x2:
            # the file does not say more; "known, not freshly verified"
            state = "STALE"
        else:
            continue                             # 0x0: ARP request sent, no MAC yet
        rows.append((p[0], p[5], p[3].lower(), state))
    return rows


def is_random_mac(mac: str) -> bool:
    """True when the locally-administered bit is set (a phone or laptop private Wi-Fi address)."""
    try:
        return (int(mac.split(":")[0], 16) & 0x02) != 0
    except ValueError:
        return False


def _addr_if_usable(row: Row) -> Optional[Addr]:
    """The row's IP as an address, or None when it should not be listed."""
    ip, iface, mac, state = row
    if iface == "lo" or state in DROP_STATES or mac in NULL_MACS:
        return None
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return None
    if addr.is_multicast or addr.is_loopback:
        return None
    return addr


def _read_file(path: str) -> str:
    try:
        with open(path) as f:
            return f.read()
    except (OSError, ValueError):
        return ""


def read_devices(neigh_json: Optional[str] = None, gateway_ip: Optional[str] = None,
                 runner: Optional[Callable[[List[str], float], Optional[str]]] = None,
                 arp_path: str = "/proc/net/arp") -> Tuple[Device, ...]:
    run = runner or run_cmd
    # the first source that yields a row wins; `ip -j` is the richest one
    if neigh_json is not None:
        rows = parse_neigh_json(neigh_json)
    else:
        rows = parse_neigh_json(run(IP_JSON, CMD_TIMEOUT) or "")
    if not rows:
        rows = parse_neigh_text(run(IP_TEXT, CMD_TIMEOUT) or "")
    if not rows:
        rows = parse_proc_arp(_read_file(arp_path))

    # One device per (mac, iface): a phone seen on IPv4 and two IPv6 addresses is one device.
    # Its shown IP is the gateway IP if it is one, else the lowest IPv4, else the lowest IPv6.
    groups: Dict[Tuple[str, str], List[Tuple[Addr, Row]]] = {}
    for row in rows:
        addr = _addr_if_usable(row)
        if addr is not None:
            groups.setdefault((row[2], row[1]), []).append((addr, row))

    found = []
    for members in groups.values():
        addr, (ip, iface, mac, state) = min(
            members, key=lambda m: (m[1][0] != gateway_ip, m[0].version, int(m[0])))
        found.append((addr, Device(ip=ip, mac=mac, iface=iface, state=state,
                                   is_gateway=ip == gateway_ip,
                                   random_mac=is_random_mac(mac))))
    # gateway first, then numeric IP order so 192.0.2.9 sorts before 192.0.2.23
    found.sort(key=lambda d: (not d[1].is_gateway, d[0].version, int(d[0]), d[1].iface))
    return tuple(dev for _, dev in found[:MAX_ROWS])


class LanCollector(Collector):
    name = "lan"
    interval = 10.0

    def __init__(self, gateway_fn: Optional[Callable[[], Tuple[Optional[str], Optional[str]]]] = None,
                 runner: Optional[Callable[[List[str], float], Optional[str]]] = None) -> None:
        super().__init__()
        if gateway_fn is None:
            # imported here so this module loads without ctop.system until it is needed
            from ctop.system import default_route
            gateway_fn = default_route
        self._gateway_fn = gateway_fn
        self._runner = runner

    def refresh(self) -> None:
        self.data = read_devices(gateway_ip=self._gateway_fn()[1], runner=self._runner)
