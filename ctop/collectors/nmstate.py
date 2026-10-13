"""NetworkManager state: radios, nearby Wi-Fi networks (NM's cached scan) and saved profiles.

Read-only. Never asks NetworkManager to scan: a scan wakes the radio and can take seconds,
so we only show what NM already has. Changing anything is ctop/tools' job, not this file's.
"""
from __future__ import annotations

import re
import time
from typing import Callable, Dict, FrozenSet, Mapping, NamedTuple, Optional, Tuple

from ctop.collectors.base import Collector
from ctop.collectors.wifi import channel_from_freq, run_cmd, split_nmcli_line
from ctop.models import NmState, SavedConnection, WifiNetwork

GENERAL = ["nmcli", "-t", "-f", "RUNNING,STATE,CONNECTIVITY,WIFI-HW,WIFI,WWAN", "general"]
DEVICES = ["nmcli", "-t", "-f", "DEVICE,TYPE,STATE", "device"]
# --rescan no: read the cached list only, a real scan is slow and wakes the radio.
WIFI_LIST = ["nmcli", "-t", "-f", "IN-USE,BSSID,SSID,CHAN,FREQ,RATE,SIGNAL,SECURITY",
             "device", "wifi", "list", "--rescan", "no"]
CONNS = ["nmcli", "-t", "-f", "NAME,UUID,TYPE,DEVICE,AUTOCONNECT,AUTOCONNECT-PRIORITY,ACTIVE,STATE,TIMESTAMP",
         "connection", "show"]
TIMEOUT = 3.0

NOT_FOUND = "NetworkManager not found - Wi-Fi controls need it (package: network-manager)"
NOT_RUNNING = "NetworkManager is not running"

MAX_NETWORKS = 60
MAX_SAVED = 40
MAX_SSID_LOOKUPS = 8          # each lookup is one nmcli call; spread the cost over refreshes

# uuid goes into argv, so only hex and dashes are allowed through.
_UUID_OK = re.compile(r"[0-9a-fA-F-]{8,64}")
_MAC_OK = re.compile(r"[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}")
_LEAD_INT = re.compile(r"-?\d+")
_ESCAPED = re.compile(r"\\(.)", re.DOTALL)

# Connection types a person would pick; bridges, tunnels etc. are plumbing and stay hidden.
_SKIP_TYPES = frozenset(("loopback", "bridge", "tun", "bond", "vlan", "dummy", "generic"))
_KINDS = {"802-11-wireless": "wifi", "802-3-ethernet": "ethernet", "vpn": "vpn", "wireguard": "wireguard"}


class _Ap(NamedTuple):
    """One access point row from the Wi-Fi list, before merging by SSID."""
    in_use: bool
    bssid: str
    ssid: str
    channel: Optional[int]
    freq: Optional[int]
    rate: Optional[int]
    signal: Optional[int]
    security: str


def band_of(freq_mhz: Optional[int]) -> str:
    if freq_mhz is None:
        return "?"
    if 2400 <= freq_mhz <= 2500:
        return "2.4 GHz"
    if 4900 <= freq_mhz <= 5925:
        return "5 GHz"
    if 5926 <= freq_mhz <= 7125:
        return "6 GHz"
    return "?"


def _tri(word: str) -> Optional[bool]:
    # nmcli says enabled/disabled, or "missing" when there is no such radio at all.
    if word == "enabled":
        return True
    if word == "disabled":
        return False
    return None


def _pad(fields, n):
    return list(fields) + [""] * (n - len(fields))


def _int_or_none(text: str) -> Optional[int]:
    try:
        return int(text.strip())
    except ValueError:
        return None


def _leading_int(text: str) -> Optional[int]:
    # "5680 MHz" -> 5680, "1170 Mbit/s" -> 1170
    m = _LEAD_INT.match(text.strip())
    return int(m.group(0)) if m else None


def parse_general(text: str) -> dict:
    line = next((ln for ln in text.splitlines() if ln.strip()), "")
    f = _pad(split_nmcli_line(line), 6)
    return {
        "running": f[0] == "running",
        "state": f[1],
        "connectivity": f[2],
        "wifi_hw": _tri(f[3]),
        "wifi": _tri(f[4]),
        "wwan": _tri(f[5]),
    }


def parse_devices(text: str) -> Optional[str]:
    for line in text.splitlines():
        f = _pad(split_nmcli_line(line), 3)
        # wifi-p2p is Wi-Fi Direct, not the radio you connect with.
        if f[1] == "wifi" and f[0]:
            return f[0]
    return None


def _security(raw: str) -> str:
    # nmcli prints "WPA1 WPA2"; WPA1 is shown as plain WPA. "--" and blanks mean none.
    parts = []
    for word in raw.split(" "):
        if word in ("", "--"):
            continue
        parts.append("WPA" if word == "WPA1" else word)
    return " ".join(parts)


def _parse_ap(line: str) -> Optional[_Ap]:
    f = _pad(split_nmcli_line(line), 8)
    if not _MAC_OK.fullmatch(f[1]):
        return None                       # garbage or a header-like line
    freq = _leading_int(f[4])
    chan = _int_or_none(f[3])
    return _Ap(
        in_use=f[0].strip() == "*",
        bssid=f[1].upper(),
        ssid=f[2],
        channel=chan if chan is not None else channel_from_freq(freq),
        freq=freq,
        rate=_leading_int(f[5]),
        signal=_int_or_none(f[6]),
        security=_security(f[7]),
    )


def _strength(ap: _Ap) -> int:
    return ap.signal if ap.signal is not None else -1


def parse_wifi_list(text: str, saved_ssids: FrozenSet[str] = frozenset()) -> Tuple[WifiNetwork, ...]:
    groups: Dict[str, list] = {}
    for line in text.splitlines():
        ap = _parse_ap(line)
        if ap is not None:
            groups.setdefault(ap.ssid, []).append(ap)   # "" (hidden) becomes one group too

    nets = []
    for ssid, rows in groups.items():
        in_use_rows = [ap for ap in rows if ap.in_use]
        # The row you are attached to describes you better than the strongest AP of the same name.
        pick = in_use_rows[0] if in_use_rows else max(rows, key=_strength)
        nets.append(WifiNetwork(
            ssid=ssid,
            bssid=pick.bssid,
            channel=pick.channel,
            freq_mhz=pick.freq,
            band=band_of(pick.freq),
            rate_mbps=pick.rate,
            signal_pct=pick.signal,
            security=pick.security,
            in_use=bool(in_use_rows),
            saved=bool(ssid) and ssid in saved_ssids,   # hidden ('') is never 'saved'
            aps=len(rows),
        ))
    # hidden networks last: a building's mesh can be dozens of nameless access points
    nets.sort(key=lambda n: (not n.in_use, n.ssid == "", n.signal_pct is None, -(n.signal_pct or 0), n.ssid))
    return tuple(nets[:MAX_NETWORKS])


def unescape_value(text: str) -> str:
    """Clean one value printed by `nmcli -g`: drop the newline, undo nmcli's backslash escapes."""
    return _ESCAPED.sub(r"\1", text.rstrip("\n"))


def ssid_argv(uuid: str) -> list:
    return ["nmcli", "-g", "802-11-wireless.ssid", "connection", "show", "uuid", uuid]


def parse_connections(text: str, ssids: Mapping[str, str] = None) -> Tuple[SavedConnection, ...]:
    ssids = ssids or {}
    out = []
    for line in text.splitlines():
        f = _pad(split_nmcli_line(line), 9)
        name, uuid, typ = f[0], f[1], f[2]
        if not uuid or typ in _SKIP_TYPES:
            continue
        device = f[3] if f[3] not in ("", "--") else None
        last = _int_or_none(f[8])
        kind = _KINDS.get(typ, "other")
        out.append(SavedConnection(
            name=name,
            uuid=uuid,
            kind=kind,
            device=device,
            active=f[6] == "yes",
            state=f[7] if f[7] not in ("", "--") else "",
            autoconnect=f[4] == "yes",
            priority=_int_or_none(f[5]) or 0,
            last_used=float(last) if last else None,      # 0 or bad = never used
            ssid=ssids.get(uuid) if kind == "wifi" else None,
        ))
    out.sort(key=lambda c: (not c.active, c.last_used is None, -(c.last_used or 0), c.name.lower()))
    return tuple(out[:MAX_SAVED])


class NmCollector(Collector):
    name = "nmstate"
    interval = 8.0

    def __init__(self, runner: Optional[Callable] = None, clock: Optional[Callable] = None):
        super().__init__()
        self._run = runner or run_cmd
        self._clock = clock or time.time
        # uuid -> SSID. A profile's SSID does not change, so each one is looked up once.
        self._ssid_cache: Dict[str, str] = {}

    def _lookup_missing_ssids(self, conns, names: Dict[str, str]) -> None:
        todo = [c.uuid for c in conns
                if c.kind == "wifi" and c.uuid not in self._ssid_cache and _UUID_OK.fullmatch(c.uuid)]
        for uuid in todo[:MAX_SSID_LOOKUPS]:
            out = self._run(ssid_argv(uuid), TIMEOUT)
            # A failed lookup is cached as the profile name so it is not retried every refresh.
            self._ssid_cache[uuid] = unescape_value(out) if out is not None else names.get(uuid, "")

    def refresh(self) -> None:
        now = self._clock()
        gen_text = self._run(GENERAL, TIMEOUT)
        if gen_text is None:
            self.data = NmState(available=False, reason=NOT_FOUND, scanned_at=now)
            return
        gen = parse_general(gen_text)
        if not gen["running"]:
            self.data = NmState(available=False, reason=NOT_RUNNING, scanned_at=now)
            return

        dev_text = self._run(DEVICES, TIMEOUT)
        wifi_dev = parse_devices(dev_text) if dev_text else None

        conns_text = self._run(CONNS, TIMEOUT) or ""
        conns = parse_connections(conns_text, self._ssid_cache)
        names = {c.uuid: c.name for c in conns}
        self._lookup_missing_ssids(conns, names)
        conns = parse_connections(conns_text, self._ssid_cache)     # second pass carries the new SSIDs
        saved_ssids = frozenset(c.ssid for c in conns if c.kind == "wifi" and c.ssid is not None)

        networks: Tuple[WifiNetwork, ...] = ()
        if wifi_dev and gen["wifi"] is True:
            wl_text = self._run(WIFI_LIST, TIMEOUT)
            if wl_text is not None:
                networks = parse_wifi_list(wl_text, saved_ssids)

        self.data = NmState(
            available=True,
            wifi_enabled=gen["wifi"],
            wifi_hw=gen["wifi_hw"],
            wwan_enabled=gen["wwan"],
            state=gen["state"],
            connectivity=gen["connectivity"],
            wifi_device=wifi_dev,
            networks=networks,
            saved=conns,
            scanned_at=now,
        )
