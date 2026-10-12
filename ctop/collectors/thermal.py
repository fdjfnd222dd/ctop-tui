"""Fans and temperatures from hwmon, with thermal zones as the CPU fallback.

Reads only small sysfs files: no subprocess, and no psutil.sensors_temperatures()
(it costs ~50 ms because it reads every sensor). Chips are identified by their
`name` file, because the hwmonN numbers change between boots.
"""
from __future__ import annotations

import os
import re
from collections import Counter
from dataclasses import replace
from typing import List, NamedTuple, Optional

from ctop.collectors.base import Collector
from ctop.models import Fan, TempReading, Thermal

HWMON = "/sys/class/hwmon"
THERMAL = "/sys/class/thermal"

# The panel lists CPU first and board sensors last.
_KIND_ORDER = ("cpu", "gpu", "disk", "wifi", "board", "other")
_CPU_TEMP1 = ("cpu_thermal", "cpu-thermal", "soc_thermal", "bcm2835_thermal")  # ARM / Raspberry Pi
_ZONE_CPU = ("x86_pkg_temp", "cpu-thermal", "cpu_thermal", "soc_thermal", "bcm2835_thermal", "k10temp")
_WIFI = ("iwlwifi", "ath9k", "ath10k", "ath11k", "ath12k", "mt76", "mt79", "rtw", "brcmfmac")
_BOARD = ("dell_smm", "thinkpad", "applesmc", "asus", "hp", "nct", "it87")


class _Sensor(NamedTuple):
    k: int                             # the N in tempN_input
    label: str                         # "" when the driver gives no readable label
    celsius: float                     # only sane values are kept
    high: Optional[float]
    crit: Optional[float]


def _read(path: str) -> Optional[str]:
    # None on any failure; a sensor that vanishes mid-scan is simply skipped.
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read().strip()
    except OSError:                    # includes IsADirectoryError (dell_smm fan labels)
        return None


def _int(path: str) -> Optional[int]:
    text = _read(path)
    try:
        return int(text)
    except (TypeError, ValueError):    # TypeError: the file could not be read at all
        return None


def _celsius(raw: Optional[int]) -> Optional[float]:
    # 0, 255000 and -128000 are the drivers' "no sensor" markers.
    if raw is None or raw == 0:
        return None
    c = raw / 1000.0
    return c if -30 < c < 150 else None


def _limit(raw: Optional[int]) -> Optional[float]:
    # The sensor's own max/crit. Junk such as the nvme 65261850 is dropped.
    if raw is None:
        return None
    c = raw / 1000.0
    return c if 0 < c <= 150 else None


def _numbered(root: str, prefix: str) -> List[str]:
    """Entries named <prefix><N> under root, sorted by N (hwmon9 before hwmon10)."""
    try:
        names = os.listdir(root)
    except OSError:
        return []
    hits = [n for n in names if re.fullmatch(prefix + r"[0-9]+", n)]
    return sorted(hits, key=lambda n: int(n[len(prefix):]))


def _indices(path: str, kind: str) -> List[int]:
    """The K of every <kind>K_input file in one chip dir, ascending."""
    try:
        names = os.listdir(path)
    except OSError:
        return []
    pat = re.compile(kind + r"([0-9]+)_input$")
    return sorted(int(m.group(1)) for m in map(pat.match, names) if m)


def _valid_sensors(path: str) -> List[_Sensor]:
    out = []
    for k in _indices(path, "temp"):
        c = _celsius(_int(f"{path}/temp{k}_input"))
        if c is not None:
            out.append(_Sensor(k, _read(f"{path}/temp{k}_label") or "", c,
                               _limit(_int(f"{path}/temp{k}_max")),
                               _limit(_int(f"{path}/temp{k}_crit"))))
    return out


def _reading(label: str, s: _Sensor, kind: str) -> TempReading:
    return TempReading(label, s.celsius, kind, s.high, s.crit)


def _temp1(valid: List[_Sensor]) -> Optional[_Sensor]:
    return next((s for s in valid if s.k == 1), None)


def _pick(valid: List[_Sensor], labels: tuple) -> Optional[_Sensor]:
    # Labels are tried in the order given, so the first preference wins.
    for want in labels:
        for s in valid:
            if s.label == want:
                return s
    return None


def _one(valid: List[_Sensor], label: str, kind: str, labels: tuple = ()) -> List[TempReading]:
    # Single-sensor chips: a preferred label if there is one, else temp1.
    s = _pick(valid, labels) or _temp1(valid)
    return [_reading(label, s, kind)] if s else []


def _coretemp(valid: List[_Sensor]) -> List[TempReading]:
    # Each socket gets a "CPU" label; _unique() turns the second into "CPU 2".
    pkgs = [s for s in valid if s.label.startswith("Package id")]
    if pkgs:
        return [_reading("CPU", s, "cpu") for s in pkgs]
    cores = [s for s in valid if s.label.startswith("Core ")]
    if cores:
        return [_reading("CPU", max(cores, key=lambda s: s.celsius), "cpu")]
    if not any(s.label for s in valid):
        return _one(valid, "CPU", "cpu")
    return []


def _amd(valid: List[_Sensor]) -> List[TempReading]:
    # Tdie is the real die reading. Tctl can carry a vendor offset, so it comes second.
    s = _pick(valid, ("Tdie", "Tctl"))
    if s is None:
        s = next((x for x in valid if x.k == 1 and not x.label.startswith("Tccd")), None)
    return [_reading("CPU", s, "cpu")] if s else []


def _board(valid: List[_Sensor]) -> List[TempReading]:
    # Labelled sensors keep their own name. Unnamed ones collapse to one "Board"
    # reading (the hottest), since dell_smm and friends expose many of them.
    out = [_reading(s.label, s, "board") for s in valid if s.label]
    bare = [s for s in valid if not s.label]
    if bare:
        out.append(_reading("Board", max(bare, key=lambda s: s.celsius), "board"))
    return out


def _chip_temps(path: str, name: str, ordinal: int, total: int) -> List[TempReading]:
    """Temperatures for one hwmon chip, chosen by its driver name."""
    if name == "coretemp":
        return _coretemp(_valid_sensors(path))
    if name in ("k10temp", "zenpower"):
        return _amd(_valid_sensors(path))
    if name in _CPU_TEMP1:
        return _one(_valid_sensors(path), "CPU", "cpu")
    if name in ("nvme", "drivetemp"):
        # Several disks are numbered in directory order: "Disk 1 (nvme)", "Disk 2 (nvme)".
        label = f"Disk ({name})" if total == 1 else f"Disk {ordinal} ({name})"
        return _one(_valid_sensors(path), label, "disk", ("Composite",))
    if name.startswith(_WIFI):
        return _one(_valid_sensors(path), "Wi-Fi card", "wifi")
    if name in ("amdgpu", "radeon", "nouveau"):
        return _one(_valid_sensors(path), "GPU", "gpu", ("edge",))
    if name.startswith(_BOARD):
        return _board(_valid_sensors(path))
    if name == "acpitz":
        return _one(_valid_sensors(path), "System (ACPI)", "board")
    if name.startswith("pch_"):
        return _one(_valid_sensors(path), "Chipset", "board")
    return []                          # unknown chip: temps are noise, its fans still count


def _chip_fans(path: str) -> List[Fan]:
    out = []
    for k in _indices(path, "fan"):
        rpm = _int(f"{path}/fan{k}_input")
        if rpm is None or rpm < 0:     # 0 is real: a stopped fan
            continue
        label = _read(f"{path}/fan{k}_label") or f"Fan {k}"
        mx = _int(f"{path}/fan{k}_max")
        out.append(Fan(label, rpm, mx if mx is not None and mx > 0 else None))
    return out


def _zone_cpu(root: str) -> List[TempReading]:
    # Used only when hwmon gave no CPU reading. acpitz is skipped: often a constant 25 C.
    for z in _numbered(root, "thermal_zone"):
        base = f"{root}/{z}"
        if _read(f"{base}/type") in _ZONE_CPU:
            c = _celsius(_int(f"{base}/temp"))
            if c is not None:
                return [TempReading("CPU", c, "cpu")]
    return []


def _unique(labels: List[str]) -> List[str]:
    """Give repeated labels a " 2", " 3" suffix, in order of appearance."""
    used = set()
    out = []
    for lab in labels:
        cand, n = lab, 1
        while cand in used:
            n += 1
            cand = f"{lab} {n}"
        used.add(cand)
        out.append(cand)
    return out


def read_thermal(hwmon_root: str = HWMON, thermal_root: str = THERMAL) -> Thermal:
    """Never raises: a missing or unreadable root gives Thermal()."""
    chips = [(f"{hwmon_root}/{d}", _read(f"{hwmon_root}/{d}/name") or "")
             for d in _numbered(hwmon_root, "hwmon")]
    totals = Counter(name for _, name in chips)
    seen: Counter = Counter()
    temps: List[TempReading] = []
    fans: List[Fan] = []
    for path, name in chips:
        seen[name] += 1
        temps += _chip_temps(path, name, seen[name], totals[name])
        fans += _chip_fans(path)
    if not any(t.kind == "cpu" for t in temps):
        temps += _zone_cpu(thermal_root)

    labels = _unique([t.label for t in temps])
    temps = sorted((replace(t, label=lab) for t, lab in zip(temps, labels)),
                   key=lambda t: _KIND_ORDER.index(t.kind))   # stable: keeps dir order per kind
    fan_labels = _unique([f.label for f in fans])
    fans = [replace(f, label=lab) for f, lab in zip(fans, fan_labels)]

    cpus = [t.celsius for t in temps if t.kind == "cpu"]
    return Thermal(
        temps=tuple(temps),
        fans=tuple(fans),
        cpu_temp=max(cpus) if cpus else None,
        hottest=max(temps, key=lambda t: t.celsius, default=None),
    )


class ThermalCollector(Collector):
    name = "thermal"
    interval = 2.0

    def __init__(self, hwmon_root: str = HWMON, thermal_root: str = THERMAL) -> None:
        super().__init__()
        self.hwmon_root = hwmon_root
        self.thermal_root = thermal_root

    def refresh(self) -> None:
        self.data = read_thermal(self.hwmon_root, self.thermal_root)
