"""Security page, part B: devices this computer talked to, and the "How exposed am I?" verdict.

Pure functions: model in, rich Text lines out. Nothing here reads the system;
the collectors already did that.
"""
from __future__ import annotations

import ipaddress
import textwrap
from typing import List, Optional, Sequence, Tuple

from rich.text import Text

from ctop.models import Device, SecurityData, WifiData
from ctop.ui import DIM, FG, fit, kv

GREEN = "#56d364"
YELLOW = "#f2cc60"
RED = "#ff7b72"
CYAN = "#39c5cf"
PURPLE = "#bc8cff"

_DEAD_STATES = ("FAILED", "INCOMPLETE")       # neighbour entries the kernel gave up on
_LEVELS = ("LOW", "MEDIUM", "HIGH")
_LEVEL_STYLE = {
    "LOW": (GREEN, " - looks fine"),
    "MEDIUM": (YELLOW, " - worth a look"),
    "HIGH": (RED, " - needs attention"),
}
_FOOTER = "Not a security audit."
_NOTE_WIDE = "Only devices this computer talked to recently. Nothing is scanned."
_NOTE_NARROW = "Only recent contacts. Nothing scanned."
_HEADERS = {"ip": "IP address", "mac": "MAC", "role": "Role", "state": "State", "tag": ""}


def _t(s: str, style: Optional[str] = None) -> Text:
    return Text(s, style=style, no_wrap=True)


def _cut(s: str, n: int) -> str:
    """Shorten to n cells with an ellipsis. A column with no room shows nothing."""
    if n <= 0:
        return ""
    if len(s) <= n:
        return s
    return s[: n - 1] + "…"


def _cell(s: str, n: int) -> str:
    return _cut(s, n).ljust(max(n, 0))


# ---- devices ---------------------------------------------------------------
def _addr_key(ip: str) -> Tuple[int, int, str]:
    """Numeric order: IPv4 before IPv6, bad strings last. Plain string sort would put .40 before .9."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return (9, 0, ip)
    return (addr.version, int(addr), "")


def _visible(devices: Sequence[Device]) -> List[Device]:
    seen = set()
    kept = []
    for d in devices:
        if d.state in _DEAD_STATES or d.ip in seen:
            continue
        seen.add(d.ip)
        kept.append(d)
    # gateway first, then live neighbours, then by address
    kept.sort(key=lambda d: (not d.is_gateway, d.state != "REACHABLE", _addr_key(d.ip)))
    return kept


def _columns(w: int) -> List[Tuple[str, int]]:
    """(name, width) pairs. In the narrow layout State takes the rest of the line."""
    if w < 62:
        spec = [("ip", 16), ("role", 8), ("state", 0)]
    elif w < 84:
        spec = [("ip", 16), ("mac", 18), ("role", 8), ("state", 14)]
    else:
        spec = [("ip", 16), ("mac", 18), ("role", 8), ("state", 14), ("tag", 15)]
    fixed = sum(n for _, n in spec)
    return [(k, n or w - fixed) for k, n in spec]


def _state_words(state: str) -> Tuple[str, str]:
    if state == "REACHABLE":
        return "active now", GREEN
    if state in ("STALE", "DELAY", "PROBE"):
        return "seen recently", DIM
    if state == "PERMANENT":
        return "fixed entry", DIM
    return state.lower(), DIM


def _device_row(d: Device, cols: List[Tuple[str, int]]) -> Text:
    role, role_style = ("router", "bold " + CYAN) if d.is_gateway else ("device", FG)
    state, state_style = _state_words(d.state)
    cells = {
        "ip": (d.ip, None),
        "mac": (d.mac, None),
        "role": (role, role_style),
        "state": (state, state_style),
        "tag": ("private address" if d.random_mac else "", PURPLE),
    }
    t = Text(no_wrap=True)
    for key, n in cols:
        s, style = cells[key]
        t.append(_cell(s, n), style)
    return t


def _header(cols: List[Tuple[str, int]]) -> Text:
    t = Text(no_wrap=True)
    for key, n in cols:
        t.append(_cell(_HEADERS[key], n), "bold " + DIM)
    return t


def devices_lines(devices: "Sequence[Device]", w: int, h: int) -> "List[Text]":
    if w < 1 or h <= 0:
        return []
    cols = _columns(w)
    # the note needs 4 lines; below that the table itself matters more
    top = ([_t(_NOTE_WIDE if w >= 70 else _NOTE_NARROW, DIM)] if h >= 4 else []) + [_header(cols)]
    rows = [_device_row(d, cols) for d in _visible(devices)]
    if not rows:
        lines = top + [_t("No other devices seen yet.", DIM)]
    elif len(top) + len(rows) <= h:
        lines = top + rows
    else:
        keep = h - len(top) - 1      # one line is reserved for the "+N more" marker
        if keep < 0:
            lines = [_t("... +%d more" % len(rows), DIM)]
        else:
            lines = top + rows[:keep] + [_t("... +%d more" % (len(rows) - keep), DIM)]
    return [fit(t, w) for t in lines[:h]]


# ---- exposure verdict ------------------------------------------------------
def assess(sec: SecurityData, wifi: Optional[WifiData]) -> "Tuple[str, List[str]]":
    """One LOW/MEDIUM/HIGH verdict from fixed rules, so the same input always gives the same answer.

    An unknown firewall counts as MEDIUM: "we could not check" is not "safe".
    """
    n = len({(l.proto, l.port, l.process) for l in sec.listeners if l.exposure == "network"})
    fw = sec.firewall
    on = fw.active is True
    off = fw.active is False
    policy = fw.default_incoming

    if n == 0:
        level = 0
    elif off or (on and policy == "allow"):
        level = 2
    else:
        level = 1

    wsec = wifi.security.strip().lower() if wifi is not None and wifi.security is not None else ""
    open_wifi = wsec in ("none", "open")
    wep = wsec.startswith("wep")
    if open_wifi or wep:
        level = min(level + 1, 2)

    reasons = []
    if n == 0:
        reasons.append("Nothing is listening for connections from other devices on your network.")
    elif n == 1:
        reasons.append("1 program accepts connections from other devices on your network.")
    else:
        reasons.append(f"{n} programs accept connections from other devices on your network.")

    if n > 0 and off:
        reasons.append("The firewall is off, so nothing stops other devices from reaching them.")
    elif n > 0 and on:
        if policy in ("deny", "reject"):
            reasons.append("The firewall is on and blocks anything you have not allowed.")
        elif policy == "allow":
            reasons.append("The firewall is on but lets connections in by default.")
        else:
            reasons.append("The firewall is on, but its default rule is unknown.")
    elif n > 0:
        reasons.append("Could not tell whether a firewall is on.")
    elif off:
        reasons.append("The firewall is off, but nothing is listening for outside connections right now.")

    if open_wifi:
        reasons.append("Your Wi-Fi network has no password, so people nearby can listen in.")
    elif wep:
        reasons.append("Your Wi-Fi uses WEP, an old lock that is easy to break.")

    return _LEVELS[level], reasons


def _firewall_line(fw) -> Text:
    if fw.active is True:
        return kv("Firewall", f"{fw.backend}, on", vstyle=GREEN, lw=10)
    if fw.active is False:
        return kv("Firewall", f"{fw.backend}, off", vstyle=RED, lw=10)
    return kv("Firewall", f"{fw.backend}, state unknown", vstyle=YELLOW, lw=10)


def _incoming_line(fw) -> Text:
    if fw.default_incoming in ("deny", "reject"):
        return kv("Incoming", "blocked unless a rule allows it", lw=10)
    if fw.default_incoming == "allow":
        return kv("Incoming", "allowed by default", vstyle=YELLOW, lw=10)
    return kv("Incoming", "unknown", vstyle=DIM, lw=10)


def summary_lines(sec: SecurityData, wifi: Optional[WifiData], w: int, h: int) -> "List[Text]":
    if w < 1 or h <= 0:
        return []
    level, reasons = assess(sec, wifi)
    colour, suffix = _LEVEL_STYLE[level]
    head = kv("Exposure", level, vstyle=f"bold {colour}", lw=10)
    head.append(suffix, DIM)
    body = [head]

    for reason in reasons:
        pieces = textwrap.wrap(reason, width=max(w - 2, 1))
        body += [_t(("• " if i == 0 else "  ") + p, FG) for i, p in enumerate(pieces)]

    fw = sec.firewall
    if fw.backend is None:
        body.append(kv("Firewall", "none detected", vstyle=DIM, lw=10))
    else:
        body.append(_firewall_line(fw))
        body.append(_incoming_line(fw))
        if fw.rules_visible:
            body.append(kv("Rules", f"{len(fw.rules)} rules", lw=10))
    if fw.note:
        body += [_t(line, DIM) for line in textwrap.wrap(fw.note, width=max(w, 1))]

    # the footer is the disclaimer, so it survives any cut
    footer = _t(_FOOTER, DIM)
    if h == 1:
        lines = [head]
    elif h == 2:
        lines = [head, footer]
    elif len(body) + 1 <= h:
        lines = body + [footer]
    else:
        keep = h - 2                 # room for the "+N more" marker and the footer
        lines = body[:keep] + [_t("... +%d more" % (len(body) - keep), DIM), footer]
    return [fit(t, w) for t in lines]
