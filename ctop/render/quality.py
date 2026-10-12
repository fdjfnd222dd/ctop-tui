"""Network-quality lines for the Network panel: router and internet ping, then Wi-Fi.

Pure: PingData and WifiData in, rich Text lines out. Least important section is last,
so the panel can slice `[:n]` when it has little height.
"""
from __future__ import annotations

from typing import Callable, Optional, Sequence

from rich.text import Text

from ctop.models import PingData, PingResult, WifiData
from ctop.ui import DIM, FG, fit, grad, runs

LOST_MARK = "×"                  # one cell wide; a visible gap in a sparkline
LEVELS = "▁▂▃▄▅▆▇█"              # no space: an idle line still shows a baseline
QUALITY_COLORS = {"Excellent": "#56d364", "Good": "#a3d95b", "Fair": "#f2cc60",
                  "Weak": "#ff9e64", "Poor": "#ff7b72"}
LOST_COLOR = "#ff7b72"
OK_COLOR = "#56d364"
WARN_COLOR = "#f2cc60"
LABEL_W = 10                     # widest label "Internet" plus padding
SPARK_MAX_W = 60                 # wider sparklines add no information
SPARK_MIN_W = 8                  # narrower sparklines are just noise
PING_SCALE_MS = 150.0            # 150 ms or worse is drawn red
PING_FLOOR_MS = 50.0             # the ping sparkline never scales below this


def sparkline(values: "Sequence[Optional[float]]", vmax: float, width: int,
              color_of: "Callable[[float], str]") -> Text:
    """Bar sparkline of the newest `width` values; a lost sample (None) is a red gap."""
    if width <= 0:
        return Text(no_wrap=True)
    vmax = vmax if vmax > 0 else 1.0
    vals = list(values)[-width:]
    pairs = [(" ", "")] * (width - len(vals))       # pad on the left, so new data enters on the right
    for v in vals:
        if v is None:
            pairs.append((LOST_MARK, f"bold {LOST_COLOR}"))
        else:
            level = max(1, min(8, round(v / vmax * 8)))
            pairs.append((LEVELS[level - 1], color_of(v)))
    return runs(pairs)


def _dim(text: str) -> Text:
    return Text(text, style=DIM, no_wrap=True)


def _spark_row(values: "Sequence[Optional[float]]", vmax: float, spark_w: int,
               color_of: "Callable[[float], str]") -> Text:
    row = Text(" " * LABEL_W, no_wrap=True)
    row.append_text(sparkline(values, vmax, spark_w, color_of))
    return row


def _ping_color(v: float) -> str:
    return grad(min(v / PING_SCALE_MS, 1.0))


def _wifi_color(v: float) -> str:
    return grad(1.0 - v / 100.0)


def _ping_row(label: str, r: PingResult) -> Text:
    row = Text(no_wrap=True)
    row.append(f"{label:<{LABEL_W}}", DIM)
    if r.ms is None and not r.history:
        # no samples yet: a loss count would only be noise
        row.append("waiting for first reply", DIM)
        return row
    if r.ms is None:
        row.append("no reply", f"bold {LOST_COLOR}")
    else:
        text = "<1 ms" if r.ms < 1 else f"{r.ms:.0f} ms"
        row.append(text, f"bold {_ping_color(r.ms)}")
    row.append(", ", DIM)
    if r.loss_pct == 0:
        row.append("no packets lost", OK_COLOR)
    else:
        text = "<1% of packets lost" if r.loss_pct < 1 else f"{r.loss_pct:.0f}% of packets lost"
        row.append(text, WARN_COLOR if r.loss_pct < 10 else LOST_COLOR)
    return row


def _ping_section(label: str, r: Optional[PingResult], missing: str, w: int) -> "list[Text]":
    if r is None:
        return [_dim(f"{label:<{LABEL_W}}{missing}")]
    row = _ping_row(label, r)
    if w >= 70 and r.avg_ms is not None:
        row.append(f"   average {r.avg_ms:.0f} ms", DIM)
    lines = [row]
    spark_w = min(w - LABEL_W, SPARK_MAX_W)
    if spark_w >= SPARK_MIN_W:
        # scale to the worst recent reply, but never below 50 ms so a quiet line stays low
        valid = [v for v in r.history if v is not None]
        vmax = max(PING_FLOOR_MS, max(valid, default=0.0))
        lines.append(_spark_row(r.history, vmax, spark_w, _ping_color))
    return lines


def _wifi_section(wf: Optional[WifiData], w: int) -> "list[Text]":
    if wf is None:
        return [_dim(f"{'Wi-Fi':<{LABEL_W}}not connected")]
    row = Text(no_wrap=True)
    row.append(f"{'Wi-Fi':<{LABEL_W}}", DIM)
    if wf.quality in QUALITY_COLORS:
        row.append(wf.quality, f"bold {QUALITY_COLORS[wf.quality]}")
    else:
        row.append("signal unknown", DIM)
    if wf.signal_dbm is not None:
        row.append(f", {wf.signal_dbm} dBm", DIM)
    if wf.bitrate_mbps is not None:
        speed = f"{wf.bitrate_mbps:.0f}" if wf.bitrate_mbps >= 10 else f"{wf.bitrate_mbps:.1f}"
        row.append(f", link {speed} Mbps", DIM)
    if w >= 60 and wf.ssid:
        row.append("  " + wf.ssid[:20], FG)
    lines = [row]
    spark_w = min(w - LABEL_W, SPARK_MAX_W)
    if spark_w >= SPARK_MIN_W:
        if wf.history:
            lines.append(_spark_row(wf.history, 100.0, spark_w, _wifi_color))
        else:
            lines.append(Text(" " * LABEL_W + "collecting history...", style=DIM, no_wrap=True))
    return lines


def quality_lines(ping: Optional[PingData], wifi: Optional[WifiData], w: int) -> "list[Text]":
    """Router, Internet and Wi-Fi lines (0 to 6), each cropped to exactly `w` cells."""
    if w < 1:
        return []
    lines: list = []
    if ping is None:
        lines.append(_dim("network checks are starting..."))
    else:
        lines += _ping_section("Router", ping.router, "no default gateway found", w)
        lines += _ping_section("Internet", ping.internet, "not measured yet", w)
    lines += _wifi_section(wifi, w)
    return [fit(t, w) for t in lines]
