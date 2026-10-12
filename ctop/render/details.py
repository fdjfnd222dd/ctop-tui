"""Program details popup (Enter key): plain-words lines for one program. Pure, no I/O."""
from __future__ import annotations

import time
from typing import Callable, List, Optional, Sequence

from rich.text import Text

from ctop.models import Listener, ProcessDetail, ProgramDetail
from ctop.ui import DIM, FG, fit, fmt_gb, kv

INTERNET = "#4ea1ff"
LOCAL = "#56d364"
WARN = "#f2cc60"
LOCKED = "#8b5a5a"
HEAD_STYLE = "bold " + DIM
FOOT = "Esc closes   x x stops this program"
FOOT_LOCKED = "Esc closes   this program is locked and cannot be stopped"

# (example lines per section, blank lines between blocks). Richest first; the first that fits wins.
_CHOICES = [(3, True), (2, True), (1, True), (1, False), (0, False)]
# Sort key for listeners: the most exposed one is shown for each port.
_EXPOSURE_RANK = {"network": 0, "specific": 1, "local": 2}
_WHO = {"local": ("only this computer", LOCAL),
        "network": ("everyone on your network", WARN),
        "specific": ("one network address", WARN)}


def detail_lines(detail: ProgramDetail, w: int, h: int,
                 now: Optional[float] = None, locked: bool = False) -> List[Text]:
    if w < 1 or h <= 0:
        return []
    if now is None:
        now = time.time()
    head = _head(detail, now)
    foot = Text(FOOT_LOCKED, style=LOCKED, no_wrap=True) if locked else Text(FOOT, style=DIM, no_wrap=True)
    n_proc = len(detail.processes)
    for ex, blanks in _CHOICES:
        # Process rows give way last-in, first-out: fewer rows only once examples are already trimmed.
        for rows in range(min(15, n_proc), min(2, n_proc) - 1, -1):
            blocks = [head, _processes(detail, rows, w), _connections(detail, ex, w),
                      _listening(detail.listeners, ex), [foot]]
            lines = _join(blocks, blanks)
            if len(lines) <= h:
                return [fit(t, w) for t in lines]
    # Nothing fits: keep the top of the head as room allows, but the footer is always last.
    return [fit(t, w) for t in head[: h - 1] + [foot]]


def _kv(label: str, value: str, style: str = FG) -> Text:
    return kv(label, value, style, lw=12)


def _age(seconds: float) -> str:
    # At most two units, so the Running line stays short and readable at a glance.
    s = max(int(seconds), 0)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m"
    if s < 86400:
        return f"{s // 3600}h {(s % 3600) // 60:02d}m"
    return f"{s // 86400}d {(s % 86400) // 3600}h"


def _head(d: ProgramDetail, now: float) -> List[Text]:
    n = d.total_processes
    title = Text(no_wrap=True)
    title.append(d.name, "bold")
    title.append(f"   {n} process{'' if n == 1 else 'es'}", DIM)
    if d.oldest_started is None:
        running = _kv("Running", "unknown", DIM)
    else:
        running = _kv("Running", _age(now - d.oldest_started))
    running.append("  (oldest process)", DIM)
    return [title, _kv("RAM", fmt_gb(d.total_ram_bytes), "bold"),
            _kv("Threads", str(d.total_threads)), running]


def _processes(d: ProgramDetail, rows: int, w: int) -> List[Text]:
    # Threads and Files only fit on a wide popup; narrow ones keep PID, RAM and the command.
    wide = w >= 50
    out = [_proc_header(wide)]
    out.extend(_proc_row(p, wide, w) for p in d.processes[:rows])
    if d.total_processes > rows:
        out.append(Text(f"... +{d.total_processes - rows} more", style=DIM, no_wrap=True))
    return out


def _proc_header(wide: bool) -> Text:
    t = Text(no_wrap=True)
    t.append(f"{'PID':<7} {'RAM':>9}", HEAD_STYLE)
    if wide:
        t.append(f" {'Threads':>7} {'Files':>5}", HEAD_STYLE)
    t.append(" Command", HEAD_STYLE)
    return t


def _proc_row(p: ProcessDetail, wide: bool, w: int) -> Text:
    t = Text(no_wrap=True)
    t.append(f"{p.pid:<7} ", DIM)
    t.append(f"{fmt_gb(p.ram_bytes):>9}", FG)
    if wide:
        t.append(f" {_num(p.threads):>7} {_num(p.open_files):>5}", DIM)
    cmd_w = max(w - (32 if wide else 18), 0)
    t.append(" " + _cut(p.cmdline or p.name, cmd_w), FG)
    return t


def _num(v: Optional[int]) -> str:
    return "-" if v is None else str(v)


def _cut(s: str, width: int) -> str:
    if len(s) <= width:
        return s
    return s[: max(width - 1, 0)] + "…"


def _connections(d: ProgramDetail, ex: int, w: int) -> List[Text]:
    conns = d.connections
    n = len(conns)
    if n == 0:
        return [_kv("Connections", "none open", DIM)]
    pub = sum(1 for c in conns if not c.remote_is_private)
    priv = n - pub
    if w >= 70:
        summary = f"{n} open: {pub} to the internet, {priv} on your local network"
    else:
        summary = f"{n} open ({pub} internet, {priv} local)"
    # Public endpoints first: they are the ones worth a second look.
    ends = {}
    for c in conns:
        ends.setdefault((c.remote_ip, c.remote_port), c.remote_is_private)
    ordered = sorted(ends.items(), key=lambda item: item[1])
    return [_kv("Connections", summary)] + _capped(ordered, ex, _endpoint_row)


def _endpoint_row(item) -> Text:
    (ip, port), private = item
    addr = f"[{ip}]:{port}" if ":" in ip else f"{ip}:{port}"
    t = Text(no_wrap=True)
    t.append("  " + addr.ljust(26), FG)
    if private:
        t.append("local network", LOCAL)
    else:
        t.append("internet", INTERNET)
    return t


def _listening(listeners: Sequence[Listener], ex: int) -> List[Text]:
    if not listeners:
        return [_kv("Listening", "no open ports", DIM)]
    ranked = sorted(listeners, key=lambda x: _EXPOSURE_RANK.get(x.exposure, 3))
    ports = {}
    for x in ranked:
        ports.setdefault((x.proto, x.port), x)
    items = list(ports.values())
    n = len(items)
    return [_kv("Listening", f"{n} port{'' if n == 1 else 's'}")] + _capped(items, ex, _listen_row)


def _listen_row(x: Listener) -> Text:
    who, color = _WHO.get(x.exposure, ("unknown", DIM))
    t = Text(no_wrap=True)
    t.append(f"  {x.proto.upper()} {x.port}".ljust(13), FG)
    t.append(who, color)
    return t


def _capped(items: Sequence, ex: int, make: Callable[..., Text]) -> List[Text]:
    # When items overflow, the last of the ex slots becomes the "+N more" count.
    if len(items) <= ex:
        return [make(x) for x in items]
    shown = max(ex - 1, 0)
    out = [make(x) for x in items[:shown]]
    if ex > 0:
        out.append(Text(f"  ... +{len(items) - shown} more", style=DIM, no_wrap=True))
    return out


def _join(blocks: List[List[Text]], blanks: bool) -> List[Text]:
    lines: List[Text] = []
    for i, block in enumerate(blocks):
        if i and blanks:
            lines.append(Text(no_wrap=True))
        lines.extend(block)
    return lines
