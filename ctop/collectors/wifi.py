"""Wi-Fi link collector: signal, quality word, bit rate, channel and security.

Cheap reads (/proc/net/wireless and `iw`) run on every refresh. `nmcli` is the
heavy one, so it runs at most every 30 s: it is the only source of the security
type and it is slow to answer.
"""
from __future__ import annotations

import os
import re
import subprocess
import time
from collections import deque
from typing import Callable, Dict, List, Optional

from ctop.collectors.base import Collector
from ctop.models import WifiData

# Kernel interface names are at most 15 chars; checked before any name reaches argv.
_IFACE_OK = re.compile(r"[A-Za-z0-9_.\-]{1,15}")
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")
_NM_FIELDS = "IN-USE,SSID,CHAN,FREQ,RATE,SIGNAL,SECURITY"
_NM_EVERY = 30.0
_NM_TIMEOUT = 4.0
_IW_TIMEOUT = 2.0
_HISTORY = 120

_DBM_STEPS = ((-50, "Excellent"), (-60, "Good"), (-70, "Fair"), (-80, "Weak"))
_PCT_STEPS = ((75, "Excellent"), (55, "Good"), (40, "Fair"), (25, "Weak"))


def quality_from_dbm(dbm: Optional[int]) -> str:
    if dbm is None:
        return "Unknown"
    for floor, word in _DBM_STEPS:
        if dbm >= floor:
            return word
    return "Poor"


def quality_from_pct(pct: Optional[int]) -> str:
    if pct is None:
        return "Unknown"
    for floor, word in _PCT_STEPS:
        if pct >= floor:
            return word
    return "Poor"


def pct_from_dbm(dbm: int) -> int:
    # Linear map used by NetworkManager: -100 dBm is nothing, -50 dBm is full.
    return max(0, min(100, 2 * (dbm + 100)))


def channel_from_freq(freq_mhz: Optional[int]) -> Optional[int]:
    f = freq_mhz
    if f is None:
        return None
    if 2412 <= f <= 2472:
        return (f - 2407) // 5
    if f == 2484:
        return 14
    if 5160 <= f <= 5885:
        return (f - 5000) // 5
    if 5955 <= f <= 7115:
        return (f - 5950) // 5
    return None


def _lead_number(text: str) -> Optional[float]:
    """First number in the text ('-42 dBm' -> -42.0), or None."""
    m = _NUMBER.match(text.strip())
    return float(m.group(0)) if m else None


def _int_or_none(value: Optional[float]) -> Optional[int]:
    return None if value is None else int(value)


def _first(*values):
    # `or` would skip a real 0 reading, so test for None explicitly.
    for value in values:
        if value is not None:
            return value
    return None


def parse_proc_wireless(text: str) -> Dict[str, Dict[str, Optional[float]]]:
    out: Dict[str, Dict[str, Optional[float]]] = {}
    for line in text.splitlines()[2:]:          # first two lines are headers
        try:
            fields = line.split()
            iface = fields[0].rstrip(":")
            link = float(fields[2].rstrip("."))
            dbm: Optional[float] = float(fields[3].rstrip("."))
        except (ValueError, IndexError):
            continue
        # Some drivers report the level as an unsigned byte: 216 means -40 dBm.
        if 0 < dbm <= 255:
            dbm -= 256
        if dbm >= 0:
            dbm = None                          # 0 or still positive: no real reading
        out[iface] = {"link": link, "dbm": dbm}
    return out


def parse_iw_link(text: str) -> Dict[str, object]:
    out: Dict[str, object] = {"connected": False, "ssid": None, "freq_mhz": None,
                              "signal_dbm": None, "bitrate_mbps": None}
    # Never read the "Connected to <BSSID>" line beyond this check: the AP MAC is not stored.
    if not text.lstrip().startswith("Connected to"):
        return out
    out["connected"] = True
    tx: Optional[float] = None
    rx: Optional[float] = None
    for line in (ln.strip() for ln in text.splitlines()):
        if line.startswith("SSID:"):
            out["ssid"] = line[len("SSID:"):].strip() or None
        elif line.startswith("freq:"):
            out["freq_mhz"] = _int_or_none(_lead_number(line[len("freq:"):]))
        elif line.startswith("signal:"):
            out["signal_dbm"] = _int_or_none(_lead_number(line[len("signal:"):]))
        elif line.startswith("tx bitrate:"):
            tx = _lead_number(line[len("tx bitrate:"):])
        elif line.startswith("rx bitrate:"):
            rx = _lead_number(line[len("rx bitrate:"):])
    # Transmit rate is the one that matters for "how fast is my link"; receive is the fallback.
    out["bitrate_mbps"] = _first(tx, rx)
    return out


def split_nmcli_line(line: str) -> List[str]:
    # nmcli -t escapes ':' and '\' inside a field with a backslash.
    fields: List[str] = []
    cur: List[str] = []
    escaped = False
    for ch in line:
        if escaped:
            cur.append(ch)
            escaped = False
        elif ch == "\\":
            escaped = True
        elif ch == ":":
            fields.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    fields.append("".join(cur))
    return fields


def parse_nmcli_wifi(text: str) -> Optional[Dict[str, object]]:
    for line in text.splitlines():
        # Padding keeps a short row from raising; missing trailing fields read as empty.
        f = split_nmcli_line(line) + [""] * 7
        if f[0] != "*":                         # "*" marks the AP we are connected to
            continue
        words = [("WPA" if w == "WPA1" else w) for w in f[6].split(" ") if w not in ("", "--")]
        return {
            "ssid": f[1],
            "channel": _int_or_none(_lead_number(f[2])),
            "freq_mhz": _int_or_none(_lead_number(f[3])),
            "rate_mbps": _lead_number(f[4]),    # the AP's maximum rate, last resort only
            "signal_pct": _int_or_none(_lead_number(f[5])),
            "security": " ".join(words) or "none",
        }
    return None


def run_cmd(argv: List[str], timeout: float) -> Optional[str]:
    # LC_ALL=C keeps iw/nmcli output in English so the parsers above match.
    env = dict(os.environ, LC_ALL="C")
    try:
        p = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                           stderr=subprocess.DEVNULL, env=env, timeout=timeout, text=True)
    except (OSError, subprocess.SubprocessError):
        return None                             # tool missing or too slow
    return p.stdout if p.returncode == 0 else None


class WifiCollector(Collector):
    name = "wifi"
    interval = 5.0

    def __init__(self, net_root: str = "/sys/class/net", proc_wireless: str = "/proc/net/wireless",
                 runner: Optional[Callable[[List[str], float], Optional[str]]] = None,
                 clock: Optional[Callable[[], float]] = None,
                 default_iface_fn: Optional[Callable[[], Optional[str]]] = None) -> None:
        super().__init__()
        if default_iface_fn is None:
            # Lazy import: tests inject the function and never load ctop.system.
            from ctop.system import default_route
            default_iface_fn = lambda: default_route()[0]
        self.net_root = net_root
        self.proc_wireless = proc_wireless
        self._run = runner or run_cmd
        self._clock = clock or time.monotonic
        self._default_iface = default_iface_fn
        self._history: deque = deque(maxlen=_HISTORY)
        self._nm: Optional[Dict[str, object]] = None
        self._nm_key: Optional[tuple] = None
        self._nm_at: Optional[float] = None

    def refresh(self) -> None:
        proc_text = self._read_proc()
        cands = self._candidates(proc_text)
        if not cands:
            self.data = None                    # no Wi-Fi hardware at all
            return
        iface = self._pick(cands)
        iw = self._iw_link(iface)               # None when iw is missing or failing
        if iw is not None and not iw["connected"]:
            self.data = self._idle(iface)
            return
        iw = iw or {}
        now = self._clock()
        proc = parse_proc_wireless(proc_text).get(iface, {})
        link = proc.get("link")
        dbm = _first(iw.get("signal_dbm"), proc.get("dbm"))
        dbm = None if dbm is None else int(dbm)
        nm = self._nmcli(iface, iw.get("ssid"), now) or {}
        if dbm is not None:
            signal_pct: Optional[int] = pct_from_dbm(dbm)
        elif link is not None:
            signal_pct = round(min(link, 70) / 70 * 100)    # /proc link is out of 70
        else:
            signal_pct = nm.get("signal_pct")
        if dbm is None and signal_pct is None:
            self.data = self._idle(iface)       # associated but nothing measurable
            return
        if signal_pct is not None:
            self._history.append(signal_pct)
        freq = _first(iw.get("freq_mhz"), nm.get("freq_mhz"))
        self.data = WifiData(
            iface=iface,
            ssid=_first(iw.get("ssid"), nm.get("ssid")) or None,
            signal_pct=signal_pct,
            signal_dbm=dbm,
            bitrate_mbps=_first(iw.get("bitrate_mbps"), nm.get("rate_mbps")),
            freq_mhz=freq,
            channel=_first(channel_from_freq(freq), nm.get("channel")),
            security=nm.get("security"),
            quality=quality_from_dbm(dbm) if dbm is not None else quality_from_pct(signal_pct),
            history=tuple(self._history),
        )

    def _idle(self, iface: str) -> WifiData:
        return WifiData(iface, None, None, None, None, None, None, None, "Unknown",
                        history=tuple(self._history))

    def _read_proc(self) -> str:
        try:
            with open(self.proc_wireless) as f:
                return f.read()
        except OSError:
            return ""

    def _candidates(self, proc_text: str) -> List[str]:
        names = set(parse_proc_wireless(proc_text))
        try:
            entries = os.listdir(self.net_root)
        except OSError:
            entries = []
        for name in entries:
            base = os.path.join(self.net_root, name)
            # sysfs marks a Wi-Fi card with a "wireless" dir (WEXT) or a "phy80211" link (cfg80211).
            if os.path.exists(os.path.join(base, "wireless")) or os.path.exists(os.path.join(base, "phy80211")):
                names.add(name)
        return sorted(n for n in names if _IFACE_OK.fullmatch(n))

    def _pick(self, cands: List[str]) -> str:
        if len(cands) == 1:
            return cands[0]
        default = self._default_iface()
        if default in cands:                    # the interface that actually carries traffic
            return default
        for name in cands:
            iw = self._iw_link(name)
            if iw and iw["connected"]:
                return name
        return cands[0]

    def _iw_link(self, iface: str) -> Optional[Dict[str, object]]:
        out = self._run(["iw", "dev", iface, "link"], _IW_TIMEOUT)
        return None if out is None else parse_iw_link(out)

    def _nmcli(self, iface: str, ssid: Optional[str], now: float) -> Optional[Dict[str, object]]:
        key = (iface, ssid)
        if self._nm_at is None or now - self._nm_at >= _NM_EVERY or key != self._nm_key:
            # --rescan no: without it NetworkManager starts an active channel scan (battery and latency cost).
            out = self._run(["nmcli", "-t", "--escape", "yes", "-f", _NM_FIELDS,
                             "dev", "wifi", "list", "ifname", iface, "--rescan", "no"], _NM_TIMEOUT)
            self._nm = None if out is None else parse_nmcli_wifi(out)
            self._nm_at = now                   # also stops retrying every refresh when nmcli is absent
            self._nm_key = key
        return self._nm
