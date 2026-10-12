"""CPU panel thermal lines: CPU temperature, fan speed, hottest non-CPU part.

Pure functions only (no I/O, no psutil). Input is a Thermal snapshot from the collector.
"""
from __future__ import annotations

from typing import Optional

from rich.cells import cell_len
from rich.text import Text

from ctop.models import Fan, TempReading, Thermal
from ctop.ui import DIM, FG, fit, grad, kv

DEFAULT_TOP = 95.0      # used when the sensor gives no warning or danger mark of its own
IDLE_C = 40.0           # "idle cool" on every laptop; the colour scale starts here
HOT_SPOT_MIN_C = 50.0   # below this a non-CPU part is not worth a line


def heat(celsius: float, reading: Optional[TempReading] = None) -> float:
    """0.0 (idle cool) .. 1.0 (at the sensor's own limit) for colouring."""
    # Each sensor knows its own limits (a Ryzen and a Xeon differ), so use them when given.
    top = DEFAULT_TOP
    if reading is not None:
        if reading.crit is not None:
            top = reading.crit
        elif reading.high is not None:
            top = reading.high
    if top < 50.0:      # a nonsense limit would make the whole scale meaningless
        top = DEFAULT_TOP
    return min(max((celsius - IDLE_C) / (top - IDLE_C), 0.0), 1.0)


def _cpu_word(t: float) -> str:
    if t < 0.35:
        return "cool"
    if t < 0.60:
        return "warm"
    if t < 0.85:
        return "hot"
    return "very hot"


def _cpu_line(celsius: float, cpu_reading: Optional[TempReading]) -> Text:
    t = heat(celsius, cpu_reading)
    line = kv("Temp", f"{celsius:.0f} °C", f"bold {grad(t)}")
    line.append(f"  {_cpu_word(t)}", grad(t))
    return line


def _fan_line(label: str, fan: Fan, cpu_temp: Optional[float], w: int) -> Text:
    if fan.rpm == 0:
        # A stopped fan is only a problem when the CPU is actually hot.
        style = "#56d364" if cpu_temp is None or cpu_temp < 80 else "bold #ff7b72"
        return kv(label, "0 rpm (stopped)", style)
    if not fan.max_rpm:
        return kv(label, f"{fan.rpm} rpm", FG)
    pct = min(100, round(fan.rpm * 100 / fan.max_rpm))
    style = grad(pct / 100)
    line = kv(label, f"{fan.rpm} rpm ({pct}% of max)", style)
    if cell_len(line.plain) > w:
        # Narrow panel: the shorter form keeps the percentage visible.
        line = kv(label, f"{fan.rpm} rpm ({pct}%)", style)
    return line


def _hot_spot(th: Thermal) -> Optional[Text]:
    parts = [r for r in th.temps if r.kind != "cpu"]
    if not parts:
        return None
    hot = max(parts, key=lambda r: r.celsius)
    # Silence is correct when nothing is hot: show it only if it is hot or hotter than the CPU.
    hotter_than_cpu = th.cpu_temp is not None and hot.celsius > th.cpu_temp
    if hot.celsius < HOT_SPOT_MIN_C and not hotter_than_cpu:
        return None
    t = heat(hot.celsius, hot)
    line = kv("Hot spot", f"{hot.celsius:.0f} °C", f"bold {grad(t)}")
    line.append(f"  {hot.label}", DIM)
    return line


def thermal_lines(th: Optional[Thermal], w: int) -> "list[Text]":
    if w < 1:
        return []
    if th is None:
        return [fit(Text("temperature sensors not read yet", style=DIM, no_wrap=True), w)]

    lines = []
    cpu_readings = [r for r in th.temps if r.kind == "cpu"]
    cpu_reading = max(cpu_readings, key=lambda r: r.celsius, default=None)
    if th.cpu_temp is None:
        lines.append(kv("Temp", "not reported", DIM))
    else:
        lines.append(_cpu_line(th.cpu_temp, cpu_reading))

    if not th.fans:
        lines.append(kv("Fan", "no fan reported", DIM))
    else:
        # Two fans at most, so the block stays within four lines.
        single = len(th.fans) == 1
        for fan in th.fans[:2]:
            label = "Fan" if single else fan.label[:10]
            lines.append(_fan_line(label, fan, th.cpu_temp, w))

    hot = _hot_spot(th)
    if hot is not None:
        lines.append(hot)
    return [fit(line, w) for line in lines]
