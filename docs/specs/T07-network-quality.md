# T07 - Network quality lines (ping + Wi-Fi) for the Network panel

## 1 Purpose
Show, in plain words, "is my network healthy": how fast the router and the internet answer, whether
packets are being lost, and how good the Wi-Fi signal is, each with a tiny history sparkline.
Pure function over `PingData` and `WifiData`. No I/O.

## 2 Files
Create: `ctop/render/quality.py`, `tests/test_render_quality.py`.
READ first: `docs/ARCHITECTURE.md`, `ctop/models.py` (`PingData`, `PingResult`, `WifiData`), `ctop/ui.py` (`grad`, `runs`, `fit`, `DIM`, `FG`).
Touch nothing else (the maintainer edits `ctop/panels/network.py`).

## 3 Public API
```python
from __future__ import annotations
from typing import Callable, Optional, Sequence
from rich.text import Text
from ctop.models import PingData, WifiData

LOST_MARK = "×"                       # U+00D7, one cell wide
LEVELS = "▁▂▃▄▅▆▇█"                   # 8 levels, no space
QUALITY_COLORS = {"Excellent": "#56d364", "Good": "#a3d95b", "Fair": "#f2cc60",
                  "Weak": "#ff9e64", "Poor": "#ff7b72"}      # anything else (Unknown) -> DIM
LOST_COLOR = "#ff7b72"

def sparkline(values: "Sequence[Optional[float]]", vmax: float, width: int,
              color_of: "Callable[[float], str]") -> Text: ...
def quality_lines(ping: Optional[PingData], wifi: Optional[WifiData], w: int) -> "list[Text]": ...  # 0..6 lines
```
Python 3.9 only: no `X | Y`, no `match`.

## 4 Visual design
`sparkline(values, vmax, width, color_of)` returns a Text of EXACTLY `width` cells (build with `ui.runs`):
take the last `width` values; if fewer, pad on the LEFT with plain spaces. For each value:
`None` -> `LOST_MARK` styled `bold LOST_COLOR` (a gap you can see); otherwise
`level = max(1, min(8, round(v / vmax * 8)))` -> `LEVELS[level-1]` styled `color_of(v)`. `vmax <= 0` -> treat as 1.0. `width <= 0` -> empty Text.
Why a floor of 1: an idle 0 ms line must still show a baseline, not vanish.

Label column is 10 cells: `"Router    "`, `"Internet  "`, `"Wi-Fi     "` (label dim `DIM`). Sparkline lines are
indented by 10 spaces; their width is `min(w - 10, 60)`; if that is `< 8` the sparkline lines are omitted.
Ping scale: `vmax = max(50.0, max(valid samples))`; colour of a bar `grad(min(v / 150.0, 1.0))` (150 ms = red).
Wi-Fi sparkline: values are signal %, `vmax = 100.0`, colour `grad(1 - v / 100.0)`.

Ping line (`PingResult r`, label L): after the label, in this order, comma separated, plain words:
- latest: `r.ms` not None -> `f"{r.ms:.0f} ms"` (`"<1 ms"` if `r.ms < 1`), bold, colour `grad(min(r.ms/150,1))`;
  `r.ms` None and `r.history` non-empty -> `no reply` bold `LOST_COLOR`; `r.ms` None and history empty -> the whole value is dim `waiting for first reply` (no loss text).
- loss: `loss_pct == 0` -> `no packets lost` (colour `#56d364`); `0 < loss_pct < 10` -> `"<1% of packets lost"` if under 1 else `f"{loss_pct:.0f}% of packets lost"` (colour `#f2cc60`); `>= 10` same wording, colour `#ff7b72`.
- if `w >= 70` and `avg_ms` is not None append `f"   average {avg_ms:.0f} ms"` in `DIM`.
Wi-Fi line: `quality` word bold in `QUALITY_COLORS[quality]` (`Unknown` -> `DIM`, text `signal unknown`), then
`, -58 dBm` (only if `signal_dbm` not None), `, link 144 Mbps` (only if `bitrate_mbps` not None; `.0f`, or `.1f` when < 10), in `DIM`;
if `w >= 60` and `ssid` is a non-empty string append `"  " + ssid[:20]` in `FG`. No history -> sparkline line is dim `collecting history...`.

Mock, width 40 (all three sections present = 6 lines):
```
Router    2 ms, no packets lost
          ▁▁▂▁▁▁▁▂▁▁▁▁▁▁▁▁▁▁▁▁▂▁▁▁▁▁▁▁▁▁
Internet  18 ms, 3% of packets lost
          ▂▂▃▂×▂▂▃▂▂▂▂▂▂▂▃▂▂▂×▂▂▂▂▂▂▂▂▂▂
Wi-Fi     Good, -58 dBm, link 144 Mbps
          ▆▆▆▅▆▆▆▆▅▅▆▆▆▆▆▆▆▆▆▅▆▆▆▆▆▆▆▆▆▆
```
Mock, width 90 (sparklines are 60 wide, extras appear):
```
Router    2 ms, no packets lost   average 2 ms
          ▁▁▂▁▁▁▁▂▁▁▁▁▁▁▁▁▁▁▁▁▂▁▁▁▁▁▁▁▁▁▁▁▂▁▁▁▁▁▁▁▁▁▁▁▂▁▁▁▁▁▁▁▁▁▁▁▂▁▁▁
Internet  no reply, 12% of packets lost   average 21 ms
          ▂▂▃▂×▂▂▃▂▂▂▂▂▂▂▃▂▂▂×▂▂▂▂▂▂▂▂▂▂▂▂▃▂×▂▂▂▂▂▂▂▂▂▂▂▂▂▂▂▂▂▂▂▂▂▂▂▂×
Wi-Fi     Fair, -71 dBm, link 72 Mbps  ExampleNet
          ▄▄▄▃▄▄▄▄▃▃▄▄▄▄▄▄▄▄▄▃▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▃▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄
```
Missing data, each ONE dim line (`DIM`), exact words:
`ping is None` -> only `network checks are starting...`;
`ping.router is None` -> `Router    no default gateway found`;
`ping.internet is None` -> `Internet  not measured yet`;
`wifi is None` -> `Wi-Fi     not connected`.

## 5 Rules
- Width: every returned `Text` passes through `ui.fit(t, w)` last, so `cell_len(t.plain) == w`; `w < 1` -> `[]`. `no_wrap=True` everywhere (`runs`/`Text(no_wrap=True)`).
- Height: at most 6 lines; a section that is None costs 1 line, a present one 2 (or 1 when its sparkline is omitted for width).
- Order is always Router, Internet, Wi-Fi (the panel may slice `[:n]`; least important is last).
- Never print "None"; never crash on empty `history`, `ms=None`, `avg_ms=None`, `loss_pct=0`.
- No I/O, no clock, no random. Characters: ASCII plus `×`, `▁..█`, `,`.

## 6 Test plan (`tests/test_render_quality.py`)
Fixtures (documentation addresses only): `R = PingResult("Router","192.0.2.1",2.0,2.0,0.4,0.0,(2.0,2.0,3.0))`,
`I = PingResult("Internet","198.51.100.7",18.0,20.0,3.0,3.0,(18.0,None,20.0,19.0))`,
`WF = WifiData("wlan0","ExampleNet",72,-58,144.0,5180,36,"WPA2","Good",(70,72,72))`.
- `test_sparkline_exact_width`: `sparkline([1,2,3], 10, 8, lambda v: "green")` has `cell_len == 8` and starts with 5 spaces.
- `test_sparkline_levels`: values `[0, 50, 100]`, vmax 100, width 3 -> plain `"▁▄█"`.
- `test_sparkline_lost_is_red_gap`: `[5.0, None, 5.0]` -> plain `"▄×▄"` (vmax 10, 5.0 is level 4); assert plain[1] == "×" and a span covers index 1 whose style contains "#ff7b72".
- `test_sparkline_truncates_to_newest`: 100 values, width 10 -> 10 chars, equal to the last 10 values' rendering.
- `test_internet_plain_words`: line starts with `"Internet  18 ms, 3% of packets lost"`.
- `test_router_no_loss`: starts with `"Router    2 ms, no packets lost"`.
- `test_no_reply_wording`: `PingResult("Internet","198.51.100.7",None,20.0,3.0,12.0,(18.0,None))` -> contains `"no reply, 12% of packets lost"`.
- `test_waiting_for_first_reply`: history `()`, `ms=None` -> contains `"waiting for first reply"`.
- `test_wifi_line`: `"Wi-Fi     Good, -58 dBm, link 144 Mbps"` at w=40; at w=90 it also ends (rstrip) with `ExampleNet`.
- `test_wifi_unknown_quality`: quality "Unknown", dbm None -> contains `"signal unknown"`.
- `test_none_sections`: `quality_lines(None, None, 40)` -> `["network checks are starting...", "Wi-Fi     not connected"]` (rstrip); `PingData(None, None)` -> contains the router and internet "none" lines.
- `test_full_is_six_lines_max`: all present -> `len == 6`; at w=12 (sparkline omitted) -> `len == 3`.
- `test_width_guarantee`: for w in (1, 12, 40, 90) all `cell_len(t.plain) == w`, `t.no_wrap`.
- `test_no_None_text`: no line contains "None".

## 7 Done when
- [ ] `python -m unittest tests.test_render_quality -v` passes on Python 3.9+.
- [ ] `python -m pyflakes ctop/render/quality.py tests/test_render_quality.py` is silent.
- [ ] Only the two listed files created; no new dependency.
- [ ] Only documentation addresses and the SSID "ExampleNet" in fixtures.
