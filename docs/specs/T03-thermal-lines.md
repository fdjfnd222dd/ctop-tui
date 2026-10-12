# T03 - Thermal lines for the CPU panel

## 1 Purpose
Turn a `Thermal` snapshot into at most 4 short coloured lines (CPU temperature, fan speed, hottest
non-CPU part) for the right-hand block of the CPU panel. Pure function, no I/O, no system calls.

## 2 Files
Create: `ctop/render/thermal.py`, `tests/test_render_thermal.py`.
READ first: `docs/ARCHITECTURE.md` (render contract), `ctop/models.py` (`Thermal`, `TempReading`, `Fan`), `ctop/ui.py` (`grad`, `kv`, `fit`, `DIM`, `FG`).
Touch nothing else. Do NOT edit `ctop/panels/cpu.py` (the maintainer wires it in).

## 3 Public API
```python
from __future__ import annotations
from typing import Optional
from rich.text import Text
from ctop.models import Thermal, TempReading

def thermal_lines(th: Optional[Thermal], w: int) -> "list[Text]": ...      # 0..4 lines
def heat(celsius: float, reading: Optional[TempReading] = None) -> float: ...  # 0.0 .. 1.0
```
Python 3.9: no `X | Y` types, no `match`. Use `"list[Text]"` as a string annotation (allowed with the `__future__` import) or `typing.List`.

## 4 Visual design
Every line is `ui.kv(label, value, style)` with the default label width 11, so the columns line up with the
panel's existing "Speed" and "Load" lines. Lines are in this order (the maintainer slices `[:budget]`
from the END, so the least important line must be last):

1. CPU temperature  2. one line per fan (max 2 fans)  3. hottest non-CPU part.

Mock, width 40 (lines are padded to w by `fit`; shown here without padding):
```
Temp       62 °C  warm
Fan        2400 rpm (46% of max)
Hot spot   58 °C  Disk (nvme)
```
Mock, width 90 (identical, lines do not stretch). Two fans, no hot spot, CPU hot:
```
Temp       91 °C  very hot
Fan 1      4800 rpm (96% of max)
Fan 2      4750 rpm (95% of max)
```
Missing data (each is ONE dim line, style `DIM`, exact wording):
```
Temp       not reported                    (th.cpu_temp is None)
Fan        no fan reported                 (th.fans is empty)
temperature sensors not read yet           (th is None; this is the ONLY line returned)
```
**Colour scale (decided):** `t = heat(c, reading)` and colour `grad(t)`.
`heat` = `(c - 40) / (top - 40)` clamped to 0..1, where `top` is the sensor's own `crit` if it is set,
else its own `high` if set, else `95.0`; if `top < 50` (nonsense value) use `95.0`. For the CPU line `reading` is the
`TempReading` with `kind == "cpu"` and the highest `celsius` (None if there is none, then `top = 95.0`).
For the Hot spot line `reading` is that part's own reading.
Why: a Ryzen and a Xeon get different warning points, and the sensor knows best; 40 C is "idle cool" on every laptop.
Word after the CPU number (two spaces before it, coloured like the number): `heat` < 0.35 `cool`, < 0.60 `warm`, < 0.85 `hot`, else `very hot`.
Number style: `bold` + grad colour: `f"bold {grad(t)}"`. The word uses plain `grad(t)`.

Fan line: label is `"Fan"` when there is exactly one fan, else `fan.label[:10]`. Value `f"{rpm} rpm"`; if `max_rpm` is
truthy append `f" ({pct}% of max)"`, `pct = min(100, round(rpm * 100 / max_rpm))`; rpm colour `grad(pct/100)` (FG if no max_rpm).
Width fallback: if the finished line is wider than `w`, rebuild it with `f" ({pct}%)"` instead of `" (N% of max)"`.
`rpm == 0`: value `0 rpm (stopped)`; colour `#56d364` when `cpu_temp is None or cpu_temp < 80`, otherwise bold red `#ff7b72`.

Hot spot: among `th.temps` with `kind != "cpu"` take the highest `celsius`. Show it ONLY if its celsius `>= 50.0`
or it is hotter than `th.cpu_temp`. Format: number+`°C` in grad colour (bold), then two spaces and its `label` in `DIM`.
If nothing qualifies, no line (silence is correct: nothing is hot).

## 5 Rules
- Width: after building, pass every line through `ui.fit(line, w)` so `cell_len(t.plain) == w`. If `w < 1` return `[]`.
- Height: never more than 4 lines (2 fans max, then hot spot). `th is None` -> exactly 1 line.
- Every `Text` is created with `no_wrap=True` (kv already does that).
- None handling: `cpu_temp`, `max_rpm`, `high`, `crit` may all be None; never print "None", never crash. `th.fans`/`th.temps` may be empty tuples.
- Characters allowed: ASCII plus `°`. No emoji.
- No I/O, no `time`, no globals that change. Do not import `psutil`.

## 6 Test plan (`tests/test_render_thermal.py`, `unittest`, helper `def L(lines): return [t.plain.rstrip() for t in lines]`)
Fixtures: `CPU = TempReading("CPU", 62.0, "cpu", high=90.0, crit=100.0)`, `SSD = TempReading("Disk (nvme)", 58.0, "disk")`,
`F1 = Fan("Fan 1", 2400, 5200)`.
- `test_none_gives_one_dim_line`: `thermal_lines(None, 40)` has length 1, plain starts with "temperature sensors not read yet".
- `test_cpu_line_text`: `Thermal(temps=(CPU,), cpu_temp=62.0)`; first line is `"Temp       62 °C  warm"`.
- `test_word_very_hot`: `cpu_temp=95.0` with `CPU` (crit 100) -> heat 0.916 -> plain contains "very hot".
- `test_missing_cpu_temp`: `Thermal()` -> lines contain "Temp       not reported" and "Fan        no fan reported".
- `test_fan_percent`: `Thermal(fans=(F1,), cpu_temp=50.0)` -> a line contains "2400 rpm (46% of max)".
- `test_fan_without_max`: `Fan("Fan 1", 3000)` -> "3000 rpm" and no "%" in that line.
- `test_fan_stopped_hot_is_red`: `Fan("Fan 1",0,5200)`, `cpu_temp=85.0` -> line contains "0 rpm (stopped)"; the first span style contains "#ff7b72" (check `t.spans` styles or `t.style`).
- `test_two_fans_use_their_labels`: two fans -> lines start with the fan labels; total lines <= 4.
- `test_hot_spot_shown_when_warm`: temps `(CPU, SSD)` -> a line starts with "Hot spot   58 °C" and ends with "Disk (nvme)" (rstrip).
- `test_hot_spot_hidden_when_cool`: SSD at 38 C and cpu_temp 62 -> no line starts with "Hot spot".
- `test_hot_spot_ignores_cpu_kind`: only a cpu-kind reading -> no "Hot spot".
- `test_heat_uses_sensor_limits`: `heat(70, TempReading("x",70,"cpu",crit=80)) == 0.75`; `heat(70) == (70-40)/55`; `heat(20) == 0.0`; `heat(200) == 1.0`.
- `test_width_guarantee`: for `w in (1, 12, 24, 40, 90)` every line has `cell_len(t.plain) == w` and `t.no_wrap` is True.
- `test_never_more_than_four_lines`: 3 fans + 3 hot parts -> `len <= 4`.
- `test_no_none_word_anywhere`: no `plain` contains "None".

## 7 Done when
- [ ] `python -m unittest tests.test_render_thermal -v` passes on Python 3.9+.
- [ ] `python -m pyflakes ctop/render/thermal.py tests/test_render_thermal.py` prints nothing.
- [ ] Only the two listed files were created or changed; no new dependency.
- [ ] No real hostnames/IPs in the files; labels in tests are the generic ones above.
