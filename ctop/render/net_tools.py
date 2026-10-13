"""Network page Tools box: the tool menu on the left, the result of the last tool on the right.

Pure: result objects in, rich Text lines out. The traceroute view is the showpiece: one row per
hop, coloured by where the hop sits (home / provider / internet / target), with a latency bar.
Every line is no_wrap and at most `w` cells; at most `h` lines are returned.
"""
from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

from rich.text import Text

from ctop.models import (Diagnosis, DnsReport, LanScanResult, PortCheckResult, SpeedResult, TraceResult)
from ctop.ui import DIM, FG, fit, graph, grad, hcat, meter

MenuItem = Tuple[str, str, str]      # (key, label, shortcut); key "" = separator row, never the cursor

GREEN = "#56d364"
YELLOW = "#f2cc60"
RED = "#ff7b72"
BLUE = "#4ea1ff"
CYAN = "#39c5cf"
ORANGE = "#d9a441"
CURSOR_ON = "on #1f3550"
CURSOR_OFF = "on #1a1f27"
DOWN_COLOR = "#6fb3ff"
UP_COLOR = "#f08fd8"

KIND_COLOR = {"home": GREEN, "isp": ORANGE, "internet": BLUE, "target": CYAN, "unknown": DIM}
KIND_WORD = {"home": "home", "isp": "provider", "internet": "internet", "target": "target", "unknown": ""}
FIX_HINT = {"reconnect": "Fix: Tools > Fix Wi-Fi (reconnect)",
            "dns": "Fix: Saved box > Enter on your network > DNS",
            "portal": "Fix: open a web browser and log in on the page it shows",
            "wifi-on": "Fix: press w to turn Wi-Fi on"}

_IP_MAX = 39                         # longest IPv6 text; longer ones are cut
_PORT_STATE = {
    # state -> (glyph, glyph colour, word, detail-when-no-ms)
    "open": ("●", GREEN, "open", ""),
    "closed": ("○", DIM, "closed", "(refused)"),
    "filtered": ("◌", YELLOW, "no answer", "(firewall?)"),
    "error": ("✗", RED, "unreachable", ""),
    "pending": ("…", CYAN, "checking", ""),
}
_STAGE_WORD = {"starting": "measuring ping", "latency": "measuring ping", "download": "downloading",
               "upload": "uploading"}
_LEVEL_COLOR = {"ok": GREEN, "warn": YELLOW, "fail": RED}
_STEP_ICON = {"ok": ("✓", GREEN, ""), "warn": ("!", YELLOW, "bold "), "fail": ("✗", RED, "bold "),
              "running": ("◐", CYAN, ""), "pending": ("·", DIM, ""), "skip": ("·", DIM, "")}


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


def _window(n: int, cursor: int, rows: int) -> Tuple[int, int]:
    """(start, end) slice that keeps the cursor on screen, near the middle when scrolling."""
    if n <= 0 or rows <= 0:
        return (0, 0)
    if n <= rows:
        return (0, n)
    cursor = min(max(cursor, 0), n - 1)
    start = min(max(cursor - rows // 2, 0), n - rows)
    return (start, start + rows)


def _ms(v: Optional[float]) -> str:
    if v is None:
        return "-"
    return "<1 ms" if v < 1 else f"{v:.0f} ms"


# ---- menu -------------------------------------------------------------------
def menu_lines(items: Sequence[MenuItem], cursor: int, focused: bool, running: Optional[str],
               w: int, h: int) -> List[Text]:
    if w < 1 or h < 1:
        return []
    out: List[Text] = []
    start, end = _window(len(items), cursor, h)
    style = CURSOR_ON if focused else CURSOR_OFF
    for i in range(start, end):
        key, label, shortcut = items[i]
        if key == "":
            out.append(_dim("─" * w))
            continue
        row = Text(no_wrap=True)
        if running is not None and key == running:
            row.append("◐ ", f"bold {CYAN}")
        else:
            row.append("  ")
        right = shortcut or ""
        room = max(0, w - row.cell_len - len(right) - (1 if right else 0))
        label_t = _cut(_dim(label, FG), room)
        row.append_text(label_t)
        if right:
            row.append(" ")
            row.append(right, DIM)
        row = fit(row, w)
        if i == cursor:
            row.stylize(style, 0, len(row))
        out.append(row)
    return _finish(out, w, h)


# ---- idle guide -------------------------------------------------------------
_GUIDE = [
    ("", "Pick a tool on the left, Enter runs it."),
    ("Doctor", " - finds where your connection breaks"),
    ("Trace & ports", " - the path to any address, and which ports answer"),
    ("Speed test", " - download, upload and lag under load (~35 MB)"),
    ("DNS timing", " - is name lookup slowing you down?"),
    ("Find devices", " - everything on your home network"),
    ("", ""),
    ("", "Tools send traffic only when you start them. Esc stops a running tool."),
]


def idle_lines(w: int, h: int) -> List[Text]:
    out = []
    for name, rest in _GUIDE:
        t = Text(no_wrap=True)
        if name:
            t.append(name, FG)
            t.append(rest, DIM)
        else:
            t.append(rest, DIM)
        out.append(t)
    return _finish(out, w, h)


# ---- trace (the map) --------------------------------------------------------
def _trace_header(trace: TraceResult, w: int) -> Text:
    addr = trace.address or trace.target
    left = f"Path to {addr}"
    if trace.address and trace.target and trace.target != trace.address:
        left += f" ({trace.target})"
    head = Text(no_wrap=True)
    head.append(left, f"bold {FG}")

    last = trace.hops[-1].ttl if trace.hops else 0
    if trace.error:
        status, style = trace.error, f"bold {RED}"
    elif trace.reached:
        status, style = f"reached in {last} hops", f"bold {GREEN}"
    elif trace.done:
        status = (f"stopped at hop {last} - the rest does not answer" if trace.hops
                  else "stopped - no hops answered")
        style = YELLOW
    else:
        status, style = f"tracing… hop {len(trace.hops) + 1}", CYAN

    room = max(0, w - len(status) - 2)
    if head.cell_len > room:
        head.truncate(max(room, 0), overflow="ellipsis")
    pad = max(1, w - head.cell_len - len(status))
    head.append(" " * pad)
    head.append(status, style)
    return head


def _hop_row(hop, ipw: int, bar_w: int, max_avg: float, w: int, kind_w: int = 9) -> Text:
    row = Text(no_wrap=True)
    if hop.reached:
        glyph, gcol = "◆", KIND_COLOR["target"]
    elif hop.ip is None:
        glyph, gcol = "○", DIM
    else:
        glyph, gcol = "●", KIND_COLOR.get(hop.kind, DIM)
    row.append(" ")
    row.append(glyph, f"bold {gcol}" if glyph != "○" else DIM)
    row.append(f" {hop.ttl:>2} ", DIM)
    if hop.ip is None:
        row.append(_cut(_dim("no answer"), ipw))
    else:
        row.append(_cut(_dim(hop.ip, FG), ipw))
    row.append(" ")
    if kind_w:
        word = KIND_WORD.get(hop.kind, "") or ("target" if hop.reached else "")
        row.append(f"{word:<{kind_w}}", DIM)
        row.append(" ")

    avg = hop.avg_ms
    if avg is None:
        # no reply at all: the "* * *" marks are the whole story for this hop
        row.append(" " * bar_w)
        row.append(f"{'* * *':>7}", DIM)
        return fit(row, w)
    length = max(1, round(avg / max_avg * bar_w)) if max_avg > 0 else 1
    length = min(length, bar_w)
    row.append("━" * length, grad(min(avg / 150.0, 1.0)))
    row.append(" " * (bar_w - length))
    row.append(f"{_ms(avg):>7}", FG)
    loss = hop.loss_pct
    if loss > 0:
        row.append(f" {loss:.0f}% lost", YELLOW)
    if hop.reached:
        row.append(" ✓", f"bold {GREEN}")
    return fit(row, w)


def trace_lines(trace: Optional[TraceResult], w: int, h: int) -> List[Text]:
    if w < 1 or h < 1:
        return []
    if trace is None:
        return _finish([_dim("No trace yet.")], w, h)

    lines = [_trace_header(trace, w)]
    if trace.error and not trace.hops:
        return _finish(lines + [Text(trace.error, style=RED, no_wrap=True)], w, h)

    lines.append(Text(" you", style=f"bold {FG}", no_wrap=True))
    lines.append(_dim("  │"))

    # Room left for hop rows; a trace still running keeps one row for the "…" marker.
    room = h - len(lines) - (0 if trace.done else 1)
    hops = list(trace.hops)
    if hops and room > 0:
        ips = [len(hp.ip) for hp in hops if hp.ip]
        ipw = min(_IP_MAX, max(ips) if ips else len("no answer"))
        ipw = max(ipw, len("no answer"))
        # prefix up to the bar (indent, glyph, ttl, ip, kind), then the ms, loss and tick columns
        # narrow box: the "home/provider" word goes first, the ms column never
        kind_w = 8 if w >= 50 else 0
        fixed = 1 + 1 + 4 + ipw + 1 + (kind_w + 1 if kind_w else 0)
        tail = 7 + (9 if any(hp.loss_pct > 0 for hp in hops) else 0) + (2 if trace.reached else 0)
        bar_w = max(3, w - fixed - tail)
        avgs = [hp.avg_ms for hp in hops if hp.avg_ms is not None]
        max_avg = max(avgs) if avgs else 0.0

        if len(hops) <= room:
            shown = [(hp, False) for hp in hops]
        else:
            # Too many hops: keep the first few, a "more" marker, then the last hop.
            head = hops[: room - 2] if room >= 3 else []
            tail = [hops[-1]]
            hidden = len(hops) - len(head) - len(tail)
            shown = [(hp, False) for hp in head] + [(None, hidden)] + [(hp, False) for hp in tail]

        for hp, more in shown:
            if hp is None:
                lines.append(_dim(f"  ⋮  {more} more hops"))
            else:
                lines.append(_hop_row(hp, ipw, bar_w, max_avg, w, kind_w))
    if not trace.done and not trace.error:
        lines.append(_dim("  …"))
    return _finish(lines, w, h)


# ---- ports ------------------------------------------------------------------
def ports_lines(pc: Optional[PortCheckResult], w: int, h: int) -> List[Text]:
    if w < 1 or h < 1:
        return []
    if pc is None:
        return _finish([_dim("No port check yet.")], w, h)

    counts = {}
    for r in pc.results:
        counts[r.state] = counts.get(r.state, 0) + 1
    parts = []
    if counts.get("open"):
        parts.append((f"{counts['open']} open", GREEN))
    if counts.get("closed"):
        parts.append((f"{counts['closed']} closed", DIM))
    if counts.get("filtered"):
        parts.append((f"{counts['filtered']} no answer", YELLOW))
    if counts.get("error"):
        parts.append((f"{counts['error']} unreachable", RED))
    if counts.get("pending"):
        parts.append((f"{counts['pending']} checking…", CYAN))

    head = Text(no_wrap=True)
    head.append(f"Ports on {pc.address or pc.target}", f"bold {FG}")
    right = Text(no_wrap=True)
    for i, (txt, style) in enumerate(parts):
        if i:
            right.append(" · ", DIM)
        right.append(txt, style)
    if pc.error and not pc.results:
        right = Text(pc.error, style=RED, no_wrap=True)
    gap = max(2, w - head.cell_len - right.cell_len)
    head.append(" " * gap)
    head.append_text(right)
    lines = [head]

    ordered = sorted(pc.results, key=lambda r: (r.state != "open", r.port))
    rows = []
    for r in ordered:
        glyph, gcol, word, nodata = _PORT_STATE.get(r.state, _PORT_STATE["error"])
        row = Text(no_wrap=True)
        row.append(glyph, f"bold {gcol}" if r.state == "open" else gcol)
        row.append(" ")
        row.append(f"{r.port:<6}", FG)
        row.append(_cut(_dim(r.service or "", FG), 16))
        row.append("  ")
        row.append(f"{word:<11}", gcol if r.state != "closed" else DIM)
        if r.state == "open" and r.ms is not None:
            row.append(f"{_ms(r.ms):>8}", FG)
        elif nodata:
            row.append(nodata, DIM)
        rows.append(row)

    room = h - 1
    if len(rows) + 2 <= h:
        lines.extend(rows)
        lines.append(_dim("Open = a program on that device accepts connections there."))
    else:
        lines.extend(rows[: max(0, room)])
    return _finish(lines, w, h)


# ---- target: trace + ports together ----------------------------------------
def target_lines(trace: Optional[TraceResult], ports: Optional[PortCheckResult], w: int, h: int) -> List[Text]:
    if w < 1 or h < 1:
        return []
    if trace is None and ports is None:
        return _finish([_dim("No trace or port check yet.")], w, h)
    if ports is None:
        return trace_lines(trace, w, h)
    if trace is None:
        return ports_lines(ports, w, h)

    if w >= 96:
        left = trace_lines(trace, w - 38, h)
        right = ports_lines(ports, 36, h)
        return _finish(hcat([left, right], [w - 38, 36]), w, h)

    # Stacked: ports get their own rows (about half the box at most), the trace the rest.
    port_rows = len(ports_lines(ports, w, h))
    reserved = min(port_rows + 1, h // 2)
    trace_h = h - reserved
    out = trace_lines(trace, w, trace_h) + ports_lines(ports, w, reserved)
    return _finish(out, w, h)


# ---- DNS timing -------------------------------------------------------------
def dns_lines(report: Optional[DnsReport], w: int, h: int) -> List[Text]:
    if w < 1 or h < 1:
        return []
    if report is None:
        return _finish([_dim("No DNS check yet.")], w, h)

    head = Text(no_wrap=True)
    head.append("DNS timing", f"bold {FG}")
    status = "done" if report.done else "testing…"
    gap = max(2, w - head.cell_len - len(status))
    head.append(" " * gap)
    head.append(status, DIM if report.done else CYAN)
    lines = [head]

    answered = [t.ms for t in report.timings if t.ms is not None]
    max_ms = max(answered) if answered else 0.0
    bar_w = max(4, min(16, w - 2 - 13 - 14 - 2 - 9 - 2 - 7))
    for t in report.timings:
        row = Text(no_wrap=True)
        row.append("● ", FG)
        row.append(_cut(_dim(t.server, FG), 13))
        row.append(_cut(_dim(t.label, DIM), 14))
        if t.ms is None:
            row.append(" " * bar_w)
            row.append(f"{'no answer':>9}", f"bold {RED}")
        else:
            length = max(1, round(t.ms / max_ms * bar_w)) if max_ms > 0 else 1
            length = min(length, bar_w)
            row.append("━" * length, grad(min(t.ms / 150.0, 1.0)))
            row.append(" " * (bar_w - length))
            row.append(f"{_ms(t.ms):>9}", FG)
        row.append("  ")
        if t.in_use:
            row.append("in use", CYAN)
        lines.append(row)

    tail: List[Text] = []
    if report.verdict:
        v = report.verdict
        if "not answering" in v:
            colour = RED
        elif "Switching" in v:
            colour = YELLOW
        elif "fine" in v:
            colour = GREEN
        else:
            colour = FG
        tail = [Text("", no_wrap=True), Text(v, style=colour, no_wrap=True)]
    # The verdict is the point of the box, so the timing rows give way to it when h is small.
    keep = max(0, h - len(tail))
    return _finish(lines[:keep] + tail, w, h)


# ---- speed test -------------------------------------------------------------
def speed_lines(sr: Optional[SpeedResult], w: int, h: int) -> List[Text]:
    if w < 1 or h < 1:
        return []
    if sr is None:
        return _finish([_dim("No speed test yet.")], w, h)

    head = Text(no_wrap=True)
    head.append("Speed test  ", f"bold {FG}")
    stage = sr.stage
    if stage == "done":
        head.append("✓ done", f"bold {GREEN}")
    elif stage == "cancelled":
        head.append("cancelled", YELLOW)
    elif stage == "error":
        head.append("error", f"bold {RED}")
        if sr.error:
            head.append(f": {sr.error}", RED)
    else:
        head.append("▸ " + _STAGE_WORD.get(stage, stage), CYAN)
        head.append("   ")
        head.append_text(meter(sr.progress, 14))
        head.append(f" {round(sr.progress * 100)}%", FG)

    grade = sr.grade or "-"
    grade_style = GREEN if grade in ("A", "B") else YELLOW if grade == "C" else RED if grade in ("D", "F") else DIM
    down = "-" if sr.down_mbps is None else f"{sr.down_mbps:.1f} Mbps"
    up = "-" if sr.up_mbps is None else f"{sr.up_mbps:.1f} Mbps"
    lines = [head]
    lines.append(_kv_row("↓ Download", down, DOWN_COLOR))

    graph_rows = 3 if h >= 9 else (1 if h >= 6 else 0)
    if graph_rows:
        colour = UP_COLOR if stage == "upload" else DOWN_COLOR
        vals = sr.samples
        vmax = max(vals) if vals else 1.0
        for row in graph(vals, vmax, max(1, w - 2), graph_rows, lambda f: colour):
            t = Text("  ", no_wrap=True)
            t.append_text(row)
            lines.append(t)

    lines.append(_kv_row("↑ Upload", up, UP_COLOR))
    ping = Text(no_wrap=True)
    ping.append("Ping  ", DIM)
    ping.append(f"idle {_ms(sr.idle_ms)} → busy {_ms(sr.loaded_ms)}", FG)
    ping.append("   lag under load: ", DIM)
    ping.append(grade, f"bold {grade_style}")
    lines.append(ping)

    mb = sr.bytes_used / 1e6
    lines.append(_dim(f"Used {mb:.0f} MB of data.  Server: speed.cloudflare.com"))
    return _finish(lines, w, h)


def _kv_row(label: str, value: str, colour: str) -> Text:
    t = Text(no_wrap=True)
    t.append(f"{label:<12}", colour)
    t.append(value, f"bold {FG}")
    return t


# ---- network doctor ---------------------------------------------------------
def doctor_lines(dx: Optional[Diagnosis], w: int, h: int) -> List[Text]:
    if w < 1 or h < 1:
        return []
    if dx is None:
        return _finish([_dim("No checks run yet.")], w, h)

    head = Text(no_wrap=True)
    head.append("Network doctor", f"bold {FG}")
    status = "done" if dx.done else "checking…"
    gap = max(2, w - head.cell_len - len(status))
    head.append(" " * gap)
    head.append(status, DIM if dx.done else CYAN)

    rows = []
    for step in dx.steps:
        glyph, gcol, weight = _STEP_ICON.get(step.status, _STEP_ICON["pending"])
        row = Text(no_wrap=True)
        row.append("  ")
        row.append(glyph, f"{weight}{gcol}")
        row.append(" ")
        row.append(_cut(_dim(step.name, FG), 18))
        detail_style = DIM if step.status in ("ok", "skip", "pending", "running") else _LEVEL_COLOR.get(step.status, DIM)
        row.append(step.detail or "", detail_style)
        rows.append(row)

    tail: List[Text] = []
    if dx.verdict:
        tail += [Text("", no_wrap=True), Text(dx.verdict, style=_LEVEL_COLOR.get(dx.level, FG), no_wrap=True)]
    if dx.suggestion and dx.suggestion in FIX_HINT:
        tail.append(Text(FIX_HINT[dx.suggestion], style=CYAN, no_wrap=True))

    room = h - 1 - len(tail)
    if room < len(rows):
        rows = rows[: max(0, room)]
    lines = [head] + rows + tail
    return _finish(lines, w, h)


# ---- device finder ----------------------------------------------------------
def lan_lines(ls: Optional[LanScanResult], w: int, h: int) -> List[Text]:
    if w < 1 or h < 1:
        return []
    if ls is None:
        return _finish([_dim("No device scan yet.")], w, h)

    head = Text(no_wrap=True)
    head.append(f"Devices on {ls.subnet}" if ls.subnet else "Devices on this network", f"bold {FG}")
    if ls.done:
        prog = f"found {len(ls.devices)}"
        head.append(" " * max(2, w - head.cell_len - len(prog)))
        head.append(prog, FG)
    else:
        prog = f"probing {ls.probed}/{ls.total}"
        head.append(" " * max(2, w - head.cell_len - len(prog) - 11))
        head.append_text(meter(ls.probed / ls.total if ls.total else 0.0, 10))
        head.append(" " + prog, CYAN)
    lines = [head]
    if ls.error:
        lines.append(Text(ls.error, style=RED, no_wrap=True))

    for d in ls.devices:
        row = Text(no_wrap=True)
        row.append(f"{d.ip:<16}", FG)
        row.append(f"{d.mac or '-':<19}", DIM if not d.mac else FG)
        tags = []
        if d.is_gateway:
            tags.append(("router", ORANGE))
        if d.is_me:
            tags.append(("this computer", CYAN))
        if d.random_mac:
            tags.append(("private MAC", DIM))
        for i, (txt, style) in enumerate(tags):
            if i:
                row.append("  ")
            row.append(txt, style)
        lines.append(row)

    footer = "Sent one small packet to each address; nothing else was probed."
    if len(lines) + 1 <= h:
        lines.append(_dim(footer))
    return _finish(lines, w, h)


# ---- plain message ----------------------------------------------------------
def message_lines(lines: Sequence[str], w: int, h: int) -> List[Text]:
    out = []
    for i, line in enumerate(lines):
        out.append(Text(str(line), style=f"bold {FG}" if i == 0 else FG, no_wrap=True))
    return _finish(out, w, h)


# ---- dispatcher -------------------------------------------------------------
def _kind_ok(value, cls) -> bool:
    return value is None or isinstance(value, cls)


def tool_view_lines(kind: Optional[str], result, w: int, h: int) -> List[Text]:
    """Pick the right view for the tool that ran last. Never raises: bad input is one dim line."""
    try:
        if w < 1 or h < 1:
            return []
        if kind is None:
            return idle_lines(w, h)
        if kind == "target":
            if isinstance(result, tuple) and len(result) == 2 and _kind_ok(result[0], TraceResult) \
                    and _kind_ok(result[1], PortCheckResult):
                return target_lines(result[0], result[1], w, h)
        elif kind == "trace" and _kind_ok(result, TraceResult):
            return trace_lines(result, w, h)
        elif kind == "ports" and _kind_ok(result, PortCheckResult):
            return ports_lines(result, w, h)
        elif kind == "dns" and _kind_ok(result, DnsReport):
            return dns_lines(result, w, h)
        elif kind == "speed" and _kind_ok(result, SpeedResult):
            return speed_lines(result, w, h)
        elif kind == "doctor" and _kind_ok(result, Diagnosis):
            return doctor_lines(result, w, h)
        elif kind == "lan" and _kind_ok(result, LanScanResult):
            return lan_lines(result, w, h)
        elif kind == "message" and isinstance(result, (tuple, list)) \
                and all(isinstance(s, str) for s in result):
            return message_lines(result, w, h)
        return _finish([_dim("Nothing to show.")], w, h)
    except Exception:            # a renderer bug must never take the page down
        return _finish([_dim("Could not show this result.")], w, h)
