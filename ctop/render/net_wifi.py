"""Network page boxes A: Wi-Fi list, saved networks & VPN, your Wi-Fi link, and the events log.

Pure: model objects in, rich Text lines out. No I/O, no clock reads (the caller passes `now`).
Every line is no_wrap and at most `w` cells; at most `h` lines are returned.
"""
from __future__ import annotations

import time
from typing import List, Optional, Sequence, Tuple

from rich.text import Text

from ctop.models import (ChannelReport, LinkInfo, NetEvent, NmState, SavedConnection, VpnStatus, WifiData,
                         WifiNetwork)
from ctop.ui import DIM, FG, TRACK, fit, grad

CURSOR_ON = "on #1f3550"         # focused box: cursor row background
CURSOR_OFF = "on #1a1f27"        # unfocused box: cursor row background
GREEN = "#56d364"
YELLOW = "#f2cc60"
RED = "#ff7b72"
BLUE = "#4ea1ff"
CYAN = "#39c5cf"
BARS = "▂▄▆█"
ELLIPSIS = "…"

# Wi-Fi list columns: (width, gap before). "name" is the flexible one.
_WIDTHS = {"sig": 6, "count": 3, "status": 12, "band": 7, "ch": 4, "sec": 10}
_GAP = {"sig": 0, "name": 0, "count": 0, "status": 2, "band": 2, "ch": 2, "sec": 2}

# Saved list: dot(2) name(flex) kind(9) auto(6) ago(5), gaps as in _saved_row.
_KIND_WORD = {"wifi": "Wi-Fi", "ethernet": "Cable", "vpn": "VPN", "wireguard": "WireGuard", "other": "Other"}
_SAVED_FIXED = 2 + 2 + 9 + 2 + 6 + 1 + 5     # everything except the name column
_VPN_MAX_LINES = 3

# Link quality word -> colour. Unknown quality is drawn dim, not as a warning.
_QUALITY_COLOR = {"Excellent": GREEN, "Good": GREEN, "Fair": YELLOW, "Weak": RED, "Poor": RED}


# ---- small helpers ----------------------------------------------------------
def _dim(text: str, style: str = DIM) -> Text:
    return Text(text, style=style, no_wrap=True)


def _finish(lines: Sequence[Text], w: int, h: int) -> List[Text]:
    """Cut to h lines, pad/crop to w cells, and make sure nothing wraps."""
    if w < 1 or h < 1:
        return []
    out = []
    for line in list(lines)[:h]:
        t = fit(line, w)
        t.no_wrap = True
        t.overflow = "crop"
        out.append(t)
    return out


def _cut(t: Text, w: int) -> Text:
    """Shorten to w cells with an ellipsis mark, padded to exactly w."""
    if w <= 0:
        return Text("", no_wrap=True)
    t = t.copy()
    if t.cell_len > w:
        t.truncate(w, overflow="ellipsis")
    return fit(t, w)


def _state_lines(nm: Optional[NmState]) -> Optional[List[Text]]:
    """Plain-words message when Wi-Fi cannot show networks; None when it can."""
    if nm is None:
        return [_dim("looking for Wi-Fi networks…")]
    if not nm.available:
        reason = nm.reason or "Network info is not available."
        return [_dim(reason), _dim("Wi-Fi controls need NetworkManager.")]
    if nm.wifi_device is None:
        return [_dim("No Wi-Fi hardware found.")]
    if nm.wifi_hw is False:
        return [_dim("Wi-Fi is blocked by a hardware switch or airplane key.")]
    if nm.wifi_enabled is False:
        return [_dim("Wi-Fi is off.  Press w to turn it on.", YELLOW)]
    return None


def _signal_word_style(q: str) -> str:
    return _QUALITY_COLOR.get(q, DIM)


# ---- public helpers ---------------------------------------------------------
def signal_bars(pct: Optional[int]) -> Text:
    """Four cells of rising bars; the filled part is coloured by strength (green = strong)."""
    if pct is None or pct <= 0:
        filled = 0
    elif pct >= 75:
        filled = 4
    elif pct >= 50:
        filled = 3
    elif pct >= 25:
        filled = 2
    else:
        filled = 1
    colour = grad(1.0 - min(max(pct, 0), 100) / 100.0) if pct is not None else TRACK
    t = Text(no_wrap=True)
    for i, glyph in enumerate(BARS):
        t.append(glyph, colour if i < filled else TRACK)
    return t


def ago(ts: Optional[float], now: float) -> str:
    """How long ago, in the shortest plain form: never / now / 12m / 5h / 3d."""
    if ts is None:
        return "never"
    d = now - ts
    if d < 60:
        return "now"
    if d < 3600:
        return f"{int(d // 60)}m"
    if d < 86400:
        return f"{int(d // 3600)}h"
    return f"{int(d // 86400)}d"


def window(n: int, cursor: int, rows: int) -> Tuple[int, int]:
    """(start, end) slice of n items that keeps `cursor` on screen, near the middle when scrolling."""
    if n <= 0 or rows <= 0:
        return (0, 0)
    if n <= rows:
        return (0, n)
    cursor = min(max(cursor, 0), n - 1)
    start = min(max(cursor - rows // 2, 0), n - rows)
    return (start, start + rows)


# ---- Wi-Fi list -------------------------------------------------------------
def _wifi_plan(w: int) -> Tuple[List[str], int]:
    """Which columns fit at this width, and how wide the flexible name column is."""
    cols = ["sig", "name", "count", "status", "band", "ch", "sec"]

    def name_w(c: List[str]) -> int:
        return w - sum(_WIDTHS[k] + _GAP[k] for k in c if k != "name")

    # Security goes first when narrow, then band; the name keeps room to read.
    if name_w(cols) < 16 and "sec" in cols:
        cols.remove("sec")
    if name_w(cols) < 12 and "band" in cols:
        cols.remove("band")
    return cols, max(1, name_w(cols))


def _compose(cells: dict, cols: List[str], name_w: int) -> Text:
    out = Text(no_wrap=True)
    for k in cols:
        if _GAP[k]:
            out.append(" " * _GAP[k])
        width = name_w if k == "name" else _WIDTHS[k]
        out.append_text(fit(cells[k], width))
    return out


def _wifi_row(net: WifiNetwork, cols: List[str], name_w: int) -> Text:
    if net.in_use:
        status = Text("✓ connected", style=f"bold {GREEN}", no_wrap=True)
    elif net.saved:
        status = Text("saved", style=BLUE, no_wrap=True)
    else:
        status = Text("", no_wrap=True)
    if net.ssid:
        name = _dim(net.ssid, FG) if not net.in_use else _dim(net.ssid, f"bold {FG}")
    else:
        name = _dim("(hidden network)")
    if not net.security:
        sec = Text("open ⚠", style=YELLOW, no_wrap=True)
    elif "802.1X" in net.security:
        sec = _dim("enterprise", FG)
    else:
        sec = _dim(net.security, FG)
    cells = {
        "sig": signal_bars(net.signal_pct),
        "name": _cut(name, name_w),
        "count": _dim(f"×{net.aps}") if net.aps > 1 else _dim(""),
        "status": status,
        "band": _dim(net.band, FG),
        "ch": _dim(("-" if net.channel is None else str(net.channel)).rjust(4), FG),
        "sec": sec,
    }
    return _compose(cells, cols, name_w)


def wifi_list_lines(nm: Optional[NmState], cursor: int, focused: bool, w: int, h: int) -> List[Text]:
    if w < 1 or h < 1:
        return []
    state = _state_lines(nm)
    if state is not None:
        return _finish(state, w, h)
    nets = nm.networks
    if not nets:
        return _finish([_dim("No networks found yet - press r to scan.")], w, h)

    cols, name_w = _wifi_plan(w)
    header = _compose({
        "sig": _dim("Signal"), "name": _dim("Network"), "count": _dim(""),
        "status": _dim("Status"), "band": _dim("Band"), "ch": _dim("Ch".rjust(4)),
        "sec": _dim("Security"),
    }, cols, name_w)
    out = [header]
    rows = h - 1                       # the header takes one line
    start, end = window(len(nets), cursor, rows)
    style = CURSOR_ON if focused else CURSOR_OFF
    for i in range(start, end):
        line = _wifi_row(nets[i], cols, name_w)
        line = fit(line, w)
        if i == cursor:
            line.stylize(style, 0, len(line))
        out.append(line)
    return _finish(out, w, h)


# ---- saved networks & VPN ---------------------------------------------------
def _saved_row(c: SavedConnection, cursor_on: bool, focused: bool, w: int, now: float) -> Text:
    name_w = max(1, w - _SAVED_FIXED)
    if c.active and c.state == "activating":
        dot = Text("◐ ", style=YELLOW, no_wrap=True)
    elif c.active:
        dot = Text("● ", style=f"bold {GREEN}", no_wrap=True)
    else:
        dot = _dim("○ ")
    name = c.name
    if c.priority:
        name = f"{name} p{c.priority}"
    row = Text(no_wrap=True)
    row.append_text(dot)
    row.append_text(_cut(_dim(name, FG), name_w))
    row.append("  ")
    row.append(f"{_KIND_WORD.get(c.kind, 'Other'):<9}", DIM)
    row.append("  ")
    row.append(f"{'auto' if c.autoconnect else 'manual':<6}", DIM if c.autoconnect else YELLOW)
    row.append(" ")
    when = "now" if c.active else ago(c.last_used, now)
    row.append(f"{when:>5}", DIM)
    row = fit(row, w)
    if cursor_on:
        row.stylize(CURSOR_ON if focused else CURSOR_OFF, 0, len(row))
    return row


def _vpn_block(vpn: Optional[VpnStatus], w: int) -> List[Text]:
    """The rule and the VPN lines under the saved list. Empty when there is nothing to say."""
    if vpn is None:
        return []
    lines: List[Text] = []
    ts = vpn.tailscale
    if ts is not None:
        t = Text(no_wrap=True)
        t.append(f"{'Tailscale':<11}", DIM)
        if ts == "Running":
            t.append("● on", f"bold {GREEN}")
            if vpn.tailscale_ip:
                t.append(f"   {vpn.tailscale_ip}", FG)
            total = len(vpn.peers)
            online = sum(1 for p in vpn.peers if p.online)
            if total:
                t.append(f"  {online}/{total} online", DIM)
        elif ts == "NeedsLogin":
            t.append("○ needs login", YELLOW)
        else:
            t.append(f"○ off ({ts})", DIM)
        lines.append(t)
    for tun in vpn.tunnels:
        if len(lines) >= _VPN_MAX_LINES:
            break
        if not tun.up or tun.name.lower().startswith("tailscale"):
            continue
        t = Text(no_wrap=True)
        t.append(f"{tun.name}  {tun.kind}  up  ", FG)
        t.append(tun.ip or "-", FG)
        lines.append(t)
    lines = lines[:_VPN_MAX_LINES]
    if not lines:
        return []
    return [_dim("─" * w)] + lines


def saved_lines(nm: Optional[NmState], vpn: Optional[VpnStatus], cursor: int, focused: bool,
                w: int, h: int, now: float) -> List[Text]:
    if w < 1 or h < 1:
        return []
    block = _vpn_block(vpn, w)
    # The VPN block is always visible, so it is reserved first; the list gets what is left.
    block = block[:max(0, h - 1)]
    rows = h - len(block)

    if nm is None:
        body = [_dim("looking for Wi-Fi networks…")]
    elif not nm.available:
        body = [_dim(nm.reason or "Network info is not available.")]
    elif not nm.saved:
        body = [_dim("No saved networks yet.")]
    else:
        body = []
        start, end = window(len(nm.saved), cursor, rows)
        for i in range(start, end):
            body.append(_saved_row(nm.saved[i], i == cursor, focused, w, now))
    return _finish(body[:rows] + block, w, h)


# ---- your Wi-Fi link --------------------------------------------------------
def _link_row(label: str, value: Text) -> Text:
    t = Text(no_wrap=True)
    t.append(f"{label:<9}", DIM)
    t.append_text(value)
    return t


def _nearby_row(report: Optional[ChannelReport]) -> Optional[Text]:
    if report is None or not report.loads:
        return _link_row("Nearby", _dim("no scan data yet"))
    t = Text(no_wrap=True)
    t.append(f"{'Nearby':<9}", DIM)
    band = None
    for load in report.loads:
        if load.band != band:
            if band is not None:
                t.append("   ")
            band = load.band
            t.append(f"{band.replace(' GHz', '')}: ", DIM)
        else:
            t.append("  ")
        count_style = RED if load.networks >= 8 else YELLOW if load.networks >= 5 else FG
        t.append(f"{load.channel}·", FG)
        t.append(str(load.networks), count_style)
        if load.mine:
            t.append("◆", f"bold {CYAN}")
    return t


def link_lines(link: Optional[LinkInfo], wifi: Optional[WifiData], report: Optional[ChannelReport],
               aps: Optional[int], w: int, h: int) -> List[Text]:
    if w < 1 or h < 1:
        return []
    if link is None:
        return _finish([_dim("No Wi-Fi link.")], w, h)

    mac_line = None
    if link.mac:
        mac_line = _link_row("Your MAC", Text("private (random) ✓", FG) if link.random_mac
                             else _dim("hardware address"))
    if not link.connected:
        lines = [_dim("Wi-Fi is not connected.")]
        if mac_line is not None:
            lines.append(mac_line)
        return _finish(lines, w, h)

    # Line 1: network name, then band, channel, width, Wi-Fi generation.
    fields = []
    if link.band and link.band != "?":
        fields.append(link.band)
    channel = link.channel if link.channel is not None else (wifi.channel if wifi else None)
    if channel is not None:
        fields.append(f"ch {channel}")
    width_field = f"{link.width_mhz} MHz" if link.width_mhz else None
    standard = link.standard
    name = wifi.ssid if wifi is not None and wifi.ssid else ""

    def head_line(with_width: bool) -> Text:
        parts = fields + ([width_field] if with_width and width_field else []) + ([standard] if standard else [])
        t = Text(no_wrap=True)
        if name:
            t.append(name, f"bold {FG}")
            if parts:
                t.append(" · ", DIM)
        if parts:
            t.append(" · ".join(parts), FG)
        elif not name:
            t.append("Wi-Fi link", FG)
        return t

    # The channel width is the least useful part of the header: drop it before the
    # standard ("Wi-Fi 5") or the channel gets cut off on a narrow box.
    head = head_line(True)
    if head.cell_len > w:
        head = head_line(False)

    lines = [head]

    # Line 2: signal in dBm and the quality word from the Wi-Fi reading.
    sig = Text(no_wrap=True)
    sig.append("-" if link.signal_dbm is None else f"{link.signal_dbm} dBm", FG)
    quality = wifi.quality if wifi is not None else "Unknown"
    if quality and quality != "Unknown":
        sig.append("  ")
        sig.append(quality, f"bold {_signal_word_style(quality)}")
    lines.append(_link_row("Signal", sig))

    # Line 3: current download and upload speeds.
    rate = Text(no_wrap=True)
    rx = "-" if link.rx_mbps is None else f"{link.rx_mbps:.0f} Mbps"
    tx = "-" if link.tx_mbps is None else f"{link.tx_mbps:.0f} Mbps"
    rate.append(f"↓ {rx}", FG)
    rate.append("  ")
    rate.append(f"↑ {tx}", FG)
    lines.append(_link_row("Link", rate))

    # Line 4: the access point you are attached to, and how many share the name.
    router = Text(no_wrap=True)
    router.append(link.bssid or "-", FG)
    if aps and aps > 1:
        router.append(f"  ({aps} access points)", DIM)
    lines.append(_link_row("Router", router))

    if mac_line is not None:
        lines.append(mac_line)

    # Power and MTU share one line; skip it only when neither is known.
    power = Text(no_wrap=True)
    if link.tx_power_dbm is not None:
        power.append(f"{link.tx_power_dbm:.0f} dBm transmit", FG)
    if link.mtu is not None:
        if power.plain:
            power.append("   ")
        power.append(f"MTU {link.mtu}", FG)
    if power.plain:
        lines.append(_link_row("Power", power))

    lines.append(_nearby_row(report))

    tip = report.tip if report is not None else ""
    if tip:
        # the tip is the box's one sentence of advice: wrap it onto two lines rather than cut it
        words, cur = f"Tip: {tip}".split(), ""
        for word in words:
            if cur and len(cur) + 1 + len(word) > w:
                lines.append(Text(cur, style=f"italic {DIM}", no_wrap=True))
                cur = word
            else:
                cur = f"{cur} {word}" if cur else word
        if cur:
            lines.append(Text(cur, style=f"italic {DIM}", no_wrap=True))
    return _finish(lines, w, h)


# ---- events -----------------------------------------------------------------
_EVENT_ICON = {"info": ("·", BLUE, ""), "warn": ("!", YELLOW, "bold "), "crit": ("✗", RED, "bold ")}
_OLD_AFTER = 20 * 3600


def events_lines(events: Sequence[NetEvent], w: int, h: int, now: float) -> List[Text]:
    if w < 1 or h < 1:
        return []
    if not events:
        return _finish([_dim("Nothing yet - drops, roaming and lag spikes show up here.")], w, h)

    # The events tuple is oldest first; the page shows newest first.
    newest_first = list(reversed(events))
    overflow = len(newest_first) > h
    shown = newest_first[: h - 1] if overflow else newest_first
    out = []
    for ev in shown:
        fmt = "%a %H:%M" if now - ev.ts > _OLD_AFTER else "%H:%M:%S"
        row = Text(no_wrap=True)
        row.append(time.strftime(fmt, time.localtime(ev.ts)), DIM)
        row.append("  ")
        icon, colour, weight = _EVENT_ICON.get(ev.level, _EVENT_ICON["info"])
        row.append(icon, f"{weight}{colour}")
        row.append(" ")
        row.append(ev.text, FG)
        out.append(row)
    if overflow:
        out.append(_dim(f"… {len(newest_first) - len(shown)} older"))
    return _finish(out, w, h)
