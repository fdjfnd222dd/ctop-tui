"""Portable battery reader: one Battery snapshot from /sys/class/power_supply.

Charge-based (uAh) and energy-based (uWh) packs both work, and so do drivers
that report power directly or only current x voltage. Returns None on a desktop,
a VM, or any read problem. Never raises.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from ctop.models import Battery

POWER_SUPPLY = "/sys/class/power_supply"

# Every numeric file ctop may need. Missing files simply read as None.
_INT_FILES = (
    "present", "capacity", "cycle_count", "voltage_now", "voltage_min_design",
    "current_now", "power_now",
    "energy_full_design", "energy_full", "energy_now",
    "charge_full_design", "charge_full", "charge_now",
)
_STATUSES = {s.lower(): s for s in ("Charging", "Discharging", "Full", "Not charging")}


def _read(path: str) -> Optional[str]:
    try:
        with open(path) as f:
            return f.read().strip()
    except (OSError, ValueError):  # ValueError: non-text bytes in a sysfs file
        return None


def _int(path: str) -> Optional[int]:
    text = _read(path)
    if text is None:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _positive(value: float) -> Optional[float]:
    # Zero or negative energy means "no data" here, not an empty pack.
    return value if value > 0 else None


def _clamp(value: float) -> int:
    return max(0, min(100, round(value)))


def _energy_wh(raw: Dict[str, Optional[int]], stem: str) -> Optional[float]:
    # energy_* is already in uWh. charge_* is uAh and needs a voltage to become Wh.
    energy = raw["energy_" + stem]
    if energy is not None:
        return _positive(energy / 1e6)
    charge = raw["charge_" + stem]
    # Design voltage is used on purpose: it gives a stable Wh rating for the pack.
    volt = raw["voltage_min_design"]
    if volt is None:
        volt = raw["voltage_now"]
    if charge is None or volt is None:
        return None
    return _positive(charge * volt / 1e12)


def _watts(raw: Dict[str, Optional[int]]) -> Optional[float]:
    # Magnitude only: the sign of current depends on the driver, not on the pack.
    if raw["power_now"] is not None:
        return abs(raw["power_now"]) / 1e6
    if raw["current_now"] is not None and raw["voltage_now"] is not None:
        return abs(raw["current_now"] * raw["voltage_now"]) / 1e12
    return None


def _read_one(dirpath: str) -> Optional[Dict[str, Any]]:
    if (_read(os.path.join(dirpath, "type")) or "").lower() != "battery":
        return None
    # Wireless mice and headsets report themselves as Battery with scope=Device.
    if (_read(os.path.join(dirpath, "scope")) or "").lower() == "device":
        return None
    # An empty ThinkPad bay still has a Battery directory, marked present=0.
    raw = {name: _int(os.path.join(dirpath, name)) for name in _INT_FILES}
    if raw["present"] == 0:
        return None

    wh_now = _energy_wh(raw, "now")
    wh_full = _energy_wh(raw, "full")
    pct = raw["capacity"]
    if pct is not None:
        pct = _clamp(pct)
    elif wh_now is not None and wh_full is not None:
        pct = _clamp(100 * wh_now / wh_full)

    cycles = raw["cycle_count"]
    if cycles is not None and cycles < 0:
        cycles = None
    volts = None if raw["voltage_now"] is None else raw["voltage_now"] / 1e6

    status = _read(os.path.join(dirpath, "status")) or ""
    return {
        "pct": pct,
        "status": _STATUSES.get(status.lower(), "Unknown"),
        "watts": _watts(raw),
        "volts": volts,
        "wh_now": wh_now,
        "wh_full": wh_full,
        "wh_design": _energy_wh(raw, "full_design"),
        "cycles": cycles,
    }


def _sum(values: List[Optional[float]]) -> Optional[float]:
    known = [v for v in values if v is not None]
    return sum(known) if known else None


def _combined_status(statuses: List[str]) -> str:
    # Discharging wins: if any pack is draining, the laptop is on battery.
    if "Discharging" in statuses:
        return "Discharging"
    if "Charging" in statuses:
        return "Charging"
    if all(s == "Full" for s in statuses):
        return "Full"
    if "Not charging" in statuses:
        return "Not charging"
    return "Unknown"


def _combine(packs: List[Dict[str, Any]]) -> Battery:
    # Two packs are one battery: sum the energy so the percentage matches the total.
    now = _sum([p["wh_now"] for p in packs])
    full = _sum([p["wh_full"] for p in packs])
    if now is not None and full is not None:
        pct: Optional[int] = _clamp(100 * now / full)
    else:
        reported = [p["pct"] for p in packs if p["pct"] is not None]
        pct = _clamp(sum(reported) / len(reported)) if reported else None

    volts = [p["volts"] for p in packs if p["volts"] is not None]
    cycles = [p["cycles"] for p in packs if p["cycles"] is not None]
    return Battery(
        pct=pct,
        status=_combined_status([p["status"] for p in packs]),
        watts=_sum([p["watts"] for p in packs]),
        volts=volts[0] if volts else None,
        wh_now=now,
        wh_full=full,
        wh_design=_sum([p["wh_design"] for p in packs]),
        cycles=max(cycles) if cycles else None,
    )


def read_battery(root: str = POWER_SUPPLY) -> Optional[Battery]:
    try:
        packs: List[Dict[str, Any]] = []
        for name in sorted(os.listdir(root)):
            one = _read_one(os.path.join(root, name))
            if one is not None:
                packs.append(one)
        return _combine(packs) if packs else None
    except (OSError, ValueError, ArithmeticError):  # e.g. OverflowError on absurd sysfs numbers
        return None
