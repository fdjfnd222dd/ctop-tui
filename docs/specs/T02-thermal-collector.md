# T02 - Fans + temperatures collector

## 1 Purpose
`read_thermal()` reads CPU/disk/Wi-Fi/GPU/board temperatures and fan speeds from hwmon (with thermal zones as a CPU fallback) into one frozen `Thermal`, on any Intel/AMD/ARM Linux box. `ThermalCollector` publishes it every 2 s.

## 2 Files
Create `ctop/collectors/thermal.py`, `tests/test_thermal.py`. READ: `docs/ARCHITECTURE.md`, `ctop/models.py` (`TempReading`, `Fan`, `Thermal`), `ctop/collectors/base.py`. Touch nothing else.

## 3 Public API
```python
from __future__ import annotations
from typing import Optional
from ctop.collectors.base import Collector
from ctop.models import Fan, TempReading, Thermal

HWMON = "/sys/class/hwmon"
THERMAL = "/sys/class/thermal"

def read_thermal(hwmon_root: str = HWMON, thermal_root: str = THERMAL) -> Thermal: ...

class ThermalCollector(Collector):
    name = "thermal"
    interval = 2.0
    def __init__(self, hwmon_root: str = HWMON, thermal_root: str = THERMAL) -> None: ...
    def refresh(self) -> None: ...        # self.data = read_thermal(...)  (a NEW object each call)
```
Never use `psutil.sensors_temperatures()` (50 ms; reads everything). Python 3.9 syntax only. `read_thermal` never raises: a missing/unreadable root gives `Thermal()`.

## 4 Data sources
Each `<hwmon_root>/hwmonN/` has `name` plus `tempK_input` (millidegrees C), optional `tempK_label`, `tempK_max`, `tempK_crit`, and `fanK_input` (rpm), optional `fanK_label`, `fanK_max`. The number N changes between boots: identify chips ONLY by the `name` file; walk dirs in natural order (`hwmon9` before `hwmon10`: sort by the integer suffix). Anonymised real examples:
```
coretemp: temp1_label="Package id 0" temp1_input=44000 temp1_max=100000 temp1_crit=100000 ; temp2_label="Core 0" temp2_input=43000
nvme:     temp1_label=Composite temp1_input=38850 temp1_max=80850 temp1_crit=81850 ; temp2_label="Sensor 1" temp2_input=38850 temp2_max=65261850
dell_smm: temp1_input=42000 temp2_input=38000 (no labels) ; fan1_input=0 fan1_max=4900 ; reading fan1_label raises OSError (EINVAL)
iwlwifi_1: temp1_input=38000      acpitz: temp1_input=25000      pch_cannonlake: temp1_input=38000
k10temp (AMD): temp1_label=Tctl temp1_input=62000 ; temp2_label=Tccd1 ; temp3_label=Tdie temp3_input=52000
```
**Value rules.** `celsius = raw / 1000.0`. Keep a temperature only if `raw != 0` and `-30 < celsius < 150` (0 / 255000 / -128000 mean "no sensor"). `high` = `tempK_max`, `crit` = `tempK_crit` (same K), each `None` unless `0 < value/1000 <= 150` (the nvme `65261850` is junk -> `None`). Any unreadable/unparsable file -> that value is skipped, never an exception.

**Chip -> readings table** (one `TempReading` per row unless stated):
| hwmon `name` | what to read | label | kind |
|---|---|---|---|
| `coretemp` | every input whose label starts `Package id` (first -> "CPU", second socket -> "CPU 2"); if none, the max of the `Core *` inputs; if no labels at all, `temp1_input` | CPU | cpu |
| `k10temp`, `zenpower` | label `Tdie` if present, else `Tctl`, else `temp1_input`; ignore `Tccd*` | CPU | cpu |
| `cpu_thermal`, `cpu-thermal`, `soc_thermal`, `bcm2835_thermal` (ARM/Pi) | `temp1_input` | CPU | cpu |
| `nvme` | label `Composite`, else `temp1_input`; ignore `Sensor N`. One chip -> "Disk (nvme)"; several -> "Disk 1 (nvme)", "Disk 2 (nvme)" in dir order | | disk |
| `drivetemp` (SATA) | `temp1_input`; label "Disk (sata)" (numbered like nvme if several) | | disk |
| name starts with `iwlwifi`, `ath9k`, `ath10k`, `ath11k`, `ath12k`, `mt76`, `mt79`, `rtw`, `brcmfmac` | `temp1_input` | Wi-Fi card | wifi |
| `amdgpu`, `radeon`, `nouveau` | label `edge`, else `temp1_input` | GPU | gpu |
| `dell_smm`, `thinkpad`, `applesmc`, `asus*`, `hp*`, `nct*`, `it87*` | labelled temps: use the label text, kind board. UNLABELLED temps: ONE reading = the maximum of them | Board | board |
| `acpitz` | `temp1_input` | System (ACPI) | board |
| name starts with `pch_` | `temp1_input` | Chipset | board |
| anything else | no temperatures (noise), but still look at its fans | | |
If two readings end up with the same label, add " 2", " 3" to the later ones.

**Thermal-zone fallback** (only when NO `cpu` reading exists after hwmon): scan `<thermal_root>/thermal_zone*` (natural order), read `type` and `temp` (millidegrees). Accept the first zone whose type is `x86_pkg_temp`, `cpu-thermal`, `cpu_thermal`, `soc_thermal`, `bcm2835_thermal` or `k10temp` -> `TempReading("CPU", c, "cpu")` (no high/crit). Ignore `acpitz` here (often a constant 25 C).

**Fans.** For every chip, for every `fanK_input`: `rpm = int(text)`; skip if unparsable or `< 0`. `0` is valid (fan stopped) and MUST be kept. Label: contents of `fanK_label` stripped if readable and non-empty, else `f"Fan {K}"`. `max_rpm` = `fanK_max` if `> 0` else `None`. Disambiguate duplicate labels with " 2". Order: dir order, then K.

**Result.** `temps` sorted by kind rank cpu < gpu < disk < wifi < board < other (stable, keeps dir order inside a kind). `cpu_temp` = max celsius of `kind=="cpu"` readings or `None`. `hottest` = reading with the max celsius over all temps, `None` if none. `fans` as above. All tuples.

## 5 Edge cases & distro differences
* Intel (coretemp) vs AMD (k10temp: `Tctl` can carry a +offset on some Ryzen, hence prefer `Tdie`) vs Raspberry Pi / ARM (`cpu_thermal`).
* Dell: `dell_smm` temps are unlabelled and fan label reads fail with `OSError`; ThinkPad: `thinkpad` chip has up to 8 unlabelled temps + `fan1`, HP/Asus similar. Treat the label read failing as "no label".
* Desktop PC: Super-I/O chips (`nct6775`, `it8728`) may have many fans, some stuck at 0 - report them all.
* VM / container / no sensors: empty roots -> `Thermal()` (all fields default). Do not invent data.
* `hwmonN/device/` sub-dir layouts of very old kernels: ignore. A sensor dir that disappears mid-read (USB, hot-unplug) -> skip it.
* No root needed on Arch or Debian/Ubuntu; files are world-readable.

## 6 Performance & safety limits
Budget well under 5 ms per refresh: about 30-50 tiny file reads, no subprocess, no globbing of `/sys/devices`. `dell_smm` fan reads trigger firmware calls that stall a CPU for a few ms, so read each fan file exactly once per refresh and never faster than the 2 s interval. Read-only: never touch `pwm*`, `fan*_target`, `*_enable`.

## 7 Test plan (`tests/test_thermal.py`)
Helper `chip(root, n, name, **files)` makes `root/hwmonN/` and writes `name` plus each kwarg (`temp1_input="44000"`). To simulate an unreadable label create `fan1_label` as a DIRECTORY (reading raises `IsADirectoryError`, an `OSError`). Use `tempfile.TemporaryDirectory()` for `hwmon_root` and `thermal_root`.
* `test_intel_laptop_full_tree`: chips: 1 acpitz(25000), 4 nvme (Composite 38850 max 80850 crit 81850; Sensor 1 38850 max 65261850), 7 dell_smm (temp1 42000, temp2 38000, fan1_input 0, fan1_max 4900, fan1_label dir), 8 iwlwifi_1 (38000), 9 coretemp (Package id 0 44000 max 100000 crit 100000; Core 0 43000). Expect `cpu_temp==44.0`; `temps[0]` = CPU with `high==100.0, crit==100.0`; labels set == {"CPU","Disk (nvme)","Wi-Fi card","System (ACPI)","Board"}; Board == 42.0; nvme `high==80.85`, `crit==81.85`; no label contains "Sensor"; `fans==(Fan("Fan 1",0,4900),)`; `hottest.label=="CPU"`.
* `test_kind_order`: same tree -> kinds in order cpu, disk, wifi, board, board.
* `test_amd_prefers_tdie`: k10temp with Tctl 62000, Tccd1 55000, Tdie 52000 -> `cpu_temp==52.0`.
* `test_amd_tctl_only`: k10temp with only Tctl 62000 -> `62.0`.
* `test_amd_gpu_edge`: amdgpu with temp1_label=edge 48000, temp2_label=junction 90000 -> one gpu reading 48.0.
* `test_coretemp_without_package_uses_hottest_core`: coretemp with Core 0 50000, Core 1 55000 -> 55.0; coretemp with unlabeled temp1 50000 -> 50.0.
* `test_two_nvme_are_numbered`: hwmon9 and hwmon10 both `nvme` -> labels "Disk 1 (nvme)" (hwmon9) then "Disk 2 (nvme)" (natural sort, not string sort).
* `test_sanity_filters`: coretemp with `temp1_label="Package id 0"`, `temp1_input=255000`; nvme with Composite `-128000`; iwlwifi with `temp1_input=0` -> `temps==()` and `cpu_temp is None`.
* `test_fans_labels_and_zero`: chip `it8728` with fan1_input 1200 + fan1_label "CPU Fan" + fan1_max 3000, fan2_input 0 (no label), fan3_input "abc", fan4_input -1 -> fans `(Fan("CPU Fan",1200,3000), Fan("Fan 2",0,None))`.
* `test_thermal_zone_fallback`: empty hwmon; thermal_zone0 type=acpitz temp=25000, thermal_zone1 type=x86_pkg_temp temp=45000 -> `cpu_temp==45.0`, one reading labelled "CPU".
* `test_thermal_zone_ignored_when_hwmon_has_cpu`: coretemp 44000 + zone x86_pkg_temp 99000 -> `cpu_temp==44.0`, exactly one cpu reading.
* `test_empty_and_missing_roots`: empty dirs and non-existent paths -> `Thermal()`.
* `test_unknown_chip_temps_ignored_but_fans_kept`: chip `weirdchip` with temp1_input 40000, fan1_input 900 -> `temps==()`, one fan.
* `test_collector_publishes_new_object_each_refresh`: `ThermalCollector(h, t)`; `data is None` before; after `run()` data is a `Thermal`, `error is None`, `name=="thermal"`, `interval==2.0`; change a file, run again -> new value and `data is not` the old object.

## 8 Done when
- [ ] `python -m unittest tests.test_thermal -v` passes.
- [ ] `python -m pyflakes ctop/collectors/thermal.py tests/test_thermal.py` clean.
- [ ] No new dependencies, no psutil, Python 3.9 syntax.
- [ ] Only the two listed files created; `models.py` untouched.
- [ ] Fixtures contain no real serials, hostnames or paths.
