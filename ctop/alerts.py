"""Plain-English alerts from the current RAM, battery, heat, fan, Wi-Fi and ping numbers.

Pure: no I/O, no state, no logging. Debouncing is the caller's job.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from ctop.models import Alert, Battery, PingData, PingResult, Thermal, WifiData


@dataclass(frozen=True)
class Thresholds:
    ram_warn: float = 85.0          # percent, trigger when >=
    ram_crit: float = 93.0
    swap_warn: float = 60.0
    swap_crit: float = 85.0
    bat_warn_pct: int = 20          # trigger when <= (only while Discharging)
    bat_crit_pct: int = 8
    drain_warn_w: float = 18.0      # trigger when watts >= (only while Discharging)
    health_info_pct: float = 70.0   # trigger when wh_full/wh_design*100 <
    cpu_warn_c: float = 85.0
    cpu_crit_c: float = 95.0
    fan_idle_cpu_c: float = 75.0    # all fans at 0 rpm while CPU >= this
    ping_loss_warn: float = 10.0
    ping_loss_crit: float = 50.0
    router_loss_warn: float = 10.0
    latency_info_ms: float = 150.0
    ping_min_samples: int = 10      # ignore loss/latency until history has this many entries


@dataclass(frozen=True)
class AlertInput:                   # every field optional: None = "not available, skip its rules"
    ram_pct: Optional[float] = None
    swap_pct: Optional[float] = None   # caller passes None when the machine has no swap
    battery: Optional[Battery] = None
    thermal: Optional[Thermal] = None
    cpu_temp: Optional[float] = None   # wins over thermal.cpu_temp when not None
    ping: Optional[PingData] = None
    wifi: Optional[WifiData] = None


# Worst level first in the output; ties keep rule order.
_RANK = {"crit": 0, "warn": 1, "info": 2}


def _cpu_temp(inp: AlertInput) -> Optional[float]:
    # An explicit reading from the caller beats the thermal collector's number.
    if inp.cpu_temp is not None:
        return inp.cpu_temp
    if inp.thermal is not None:
        return inp.thermal.cpu_temp
    return None


def _enough(r: PingResult, t: Thresholds) -> bool:
    # One or two pings are noise (one lost reply = 100 %), so wait for a full window.
    return len(r.history) >= t.ping_min_samples


def _ram(inp: AlertInput, t: Thresholds) -> Optional[Alert]:
    if inp.ram_pct is None:
        return None
    if inp.ram_pct >= t.ram_crit:
        return Alert("crit", "ram-high", f"Memory is almost full ({inp.ram_pct:.0f}%) - the system may start closing programs.")
    if inp.ram_pct >= t.ram_warn:
        return Alert("warn", "ram-high", f"Memory is {inp.ram_pct:.0f}% full.")
    return None


def _swap(inp: AlertInput, t: Thresholds) -> Optional[Alert]:
    if inp.swap_pct is None:
        return None
    if inp.swap_pct >= t.swap_crit:
        return Alert("crit", "swap-high", f"Swap is {inp.swap_pct:.0f}% full - the computer will feel stuck.")
    if inp.swap_pct >= t.swap_warn:
        return Alert("warn", "swap-high", f"Swap is {inp.swap_pct:.0f}% full - memory is under pressure.")
    return None


def _battery_low(inp: AlertInput, t: Thresholds) -> Optional[Alert]:
    b = inp.battery
    # Only while discharging: a charging pack at 5 % is on its way up, not a warning.
    if b is None or b.status != "Discharging" or b.pct is None:
        return None
    if b.pct <= t.bat_crit_pct:
        return Alert("crit", "battery-low", f"Battery is at {b.pct}% - plug in now.")
    if b.pct <= t.bat_warn_pct:
        return Alert("warn", "battery-low", f"Battery is at {b.pct}% - plug in soon.")
    return None


def _battery_drain(inp: AlertInput, t: Thresholds) -> Optional[Alert]:
    b = inp.battery
    if b is None or b.status != "Discharging" or b.watts is None or b.watts < t.drain_warn_w:
        return None
    left = ""
    if b.wh_now is not None and b.watts > 0:
        # Minutes at the current draw, rounded down; show hours once it is an hour or more.
        m = int(b.wh_now / b.watts * 60)
        span = f"{m // 60} h {m % 60:02d} min" if m >= 60 else f"{m} min"
        left = f" - about {span} left"
    return Alert("warn", "battery-drain", f"Battery is draining fast ({b.watts:.1f} W){left}.")


def _cpu_hot(inp: AlertInput, t: Thresholds) -> Optional[Alert]:
    temp = _cpu_temp(inp)
    if temp is None:
        return None
    if temp >= t.cpu_crit_c:
        return Alert("crit", "cpu-hot", f"CPU is at {temp:.0f}°C - close to its limit. Close heavy programs.")
    if temp >= t.cpu_warn_c:
        return Alert("warn", "cpu-hot", f"CPU is hot ({temp:.0f}°C).")
    return None


def _fans_idle(inp: AlertInput, t: Thresholds) -> Optional[Alert]:
    temp = _cpu_temp(inp)
    th = inp.thermal
    # Zero fans reported is not "all fans stopped", so it never fires.
    if th is None or not th.fans or temp is None or temp < t.fan_idle_cpu_c:
        return None
    # Laptop fans legitimately sit at 0 rpm when cool; the CPU temperature gate covers that.
    if any(f.rpm != 0 for f in th.fans):
        return None
    return Alert("warn", "fans-idle", f"Fans are not spinning while the CPU is at {temp:.0f}°C.")


def _wifi_weak(inp: AlertInput, t: Thresholds) -> Optional[Alert]:
    w = inp.wifi
    if w is None or w.quality not in ("Weak", "Poor"):
        return None
    if w.signal_dbm is not None:
        detail = f" ({w.signal_dbm} dBm)"
    elif w.signal_pct is not None:
        detail = f" ({w.signal_pct}%)"
    else:
        detail = ""
    return Alert("warn", "wifi-weak", f"Wi-Fi signal is {w.quality.lower()}{detail} - move closer to the router.")


def _router_loss(inp: AlertInput, t: Thresholds) -> Optional[Alert]:
    p = inp.ping
    r = p.router if p is not None else None
    if r is None or not _enough(r, t) or r.loss_pct < t.router_loss_warn:
        return None
    return Alert("warn", "router-loss", f"Router is dropping packets ({r.loss_pct:.0f}% lost) - check Wi-Fi signal or cable.")


def _internet_loss(inp: AlertInput, t: Thresholds) -> Optional[Alert]:
    p = inp.ping
    r = p.internet if p is not None else None
    if r is None or not _enough(r, t):
        return None
    if r.loss_pct >= t.ping_loss_crit:
        return Alert("crit", "internet-loss", f"Internet is mostly unreachable ({r.loss_pct:.0f}% of pings lost).")
    if r.loss_pct >= t.ping_loss_warn:
        return Alert("warn", "internet-loss", f"Internet connection is dropping packets ({r.loss_pct:.0f}% lost).")
    return None


def _battery_health(inp: AlertInput, t: Thresholds) -> Optional[Alert]:
    # Runs on any battery status: wear does not depend on whether the charger is plugged in.
    b = inp.battery
    if b is None or b.wh_full is None or b.wh_design is None or b.wh_full <= 0 or b.wh_design <= 0:
        return None
    ratio = b.wh_full / b.wh_design * 100
    # Above 110 % the firmware numbers are bogus; say nothing rather than guess.
    if ratio > 110 or ratio >= t.health_info_pct:
        return None
    return Alert("info", "battery-health", f"Battery holds only {ratio:.0f}% of its original capacity.")


def _internet_slow(inp: AlertInput, t: Thresholds) -> Optional[Alert]:
    p = inp.ping
    r = p.internet if p is not None else None
    if r is None or not _enough(r, t) or r.avg_ms is None:
        return None
    # Lossy links get the loss alert instead; "slow" would double-report the same problem.
    if r.avg_ms < t.latency_info_ms or r.loss_pct >= t.ping_loss_warn:
        return None
    return Alert("info", "internet-slow", f"Internet is slow to respond (average {r.avg_ms:.0f} ms).")


# Rule order is the tie-break order inside each level.
_RULES = [
    _ram,
    _swap,
    _battery_low,
    _battery_drain,
    _cpu_hot,
    _fans_idle,
    _wifi_weak,
    _router_loss,
    _internet_loss,
    _battery_health,
    _internet_slow,
]


def evaluate(inp: AlertInput, thresholds: Optional[Thresholds] = None) -> List[Alert]:
    t = thresholds if thresholds is not None else Thresholds()
    alerts: List[Alert] = []
    for rule in _RULES:
        a = rule(inp, t)
        if a is not None:
            alerts.append(a)
    alerts.sort(key=lambda a: _RANK[a.level])
    return alerts
