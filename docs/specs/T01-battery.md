# T01 - Portable battery reader

## 1 Purpose
`read_battery()` turns whatever the kernel exposes under `/sys/class/power_supply` into one `Battery` snapshot (or `None` on a desktop/VM), no matter whether the pack reports charge (uAh) or energy (uWh), and current x voltage or power directly.
It replaces the Dell-specific battery code in `ctop/system.py` (do NOT edit that file).

## 2 Files
Create `ctop/battery.py`, `tests/test_battery.py`. READ first: `docs/ARCHITECTURE.md`, `ctop/models.py` (class `Battery`). Touch nothing else.

## 3 Public API
```python
from __future__ import annotations
from typing import List, Optional
from ctop.models import Battery

POWER_SUPPLY = "/sys/class/power_supply"

def read_battery(root: str = POWER_SUPPLY) -> Optional[Battery]: ...
```
Private helpers (suggested): `_read(path) -> Optional[str]` (strip; `None` on any `OSError`), `_int(path) -> Optional[int]` (`None` on `ValueError`/missing), `_read_one(dirpath) -> Optional[dict]`, `_combine(list_of_dict) -> Battery`.
Python 3.9: no `X | None`, no `match`. No third-party imports (not even psutil). Never raise: wrap the whole body so any unexpected `OSError/ValueError/ArithmeticError` yields `None`.

## 4 Data sources
Every file is a one-line text file in `<root>/<supply>/`. Anonymised sample of a real laptop pack (`BAT0`):
```
type=Battery  status=Discharging  present=1  capacity=87  cycle_count=0  technology=Li-poly
charge_full_design=3333000  charge_full=3333000  charge_now=2889000   (uAh)
current_now=1389000 (uA)  voltage_now=12112000 (uV)  voltage_min_design=11400000 (uV)
```
Other drivers use `energy_full_design / energy_full / energy_now` (uWh) and/or `power_now` (uW).

**Which directories count.** Iterate `sorted(os.listdir(root))`; skip a directory unless ALL hold: `type` file reads `Battery` (case-insensitive); `scope` file is absent or not `Device` (wireless mice/keyboards/headsets/phones show up as `type=Battery, scope=Device` - they are NOT the laptop pack); `present` file is absent or not `0` (empty ThinkPad bay). `AC`, `ADP1`, `ucsi-*` (type Mains/USB) never count. Do not require the name to start with `BAT`.

**Per-battery fields** (all optional, `None` when missing/unparsable):
* `volts` = `voltage_now / 1e6`.
* `wh_design`, `wh_full`, `wh_now`: if `energy_*` exists: `uWh / 1e6`. Else if `charge_*` exists: `uAh * V / 1e12` where `V` (uV) = `voltage_min_design`, else `voltage_now`; if neither voltage exists the three values are `None` (never invent a voltage). Prefer `energy_*` when both families exist. A value `<= 0` becomes `None`.
* `watts` (magnitude, never negative): `power_now / 1e6` if present; else `abs(current_now * voltage_now) / 1e12` if both present; else `None`. `0` is a valid result (idle/full).
* `status`: map case-insensitively to exactly one of `Charging, Discharging, Full, Not charging, Unknown`; anything else or missing -> `Unknown`.
* `pct`: `capacity` clamped to 0..100; if missing and `wh_now` and `wh_full` known -> `round(100 * wh_now / wh_full)` clamped; else `None`.
* `cycles`: `cycle_count` if `>= 0` else `None` (0 is legitimate on a new pack).

**Combining several packs** (ThinkPads have BAT0 + BAT1; sum, don't pick one):
* `wh_now/wh_full/wh_design`: sum of the packs that report it; `None` if no pack does.
* `watts`: sum of reported values, `None` if none. `volts`: first non-None. `cycles`: max of non-None.
* `pct`: `round(100 * sum_now / sum_full)` clamped when both sums exist, else rounded mean of reported `pct`s, else `None`.
* `status`: any `Discharging` -> `Discharging`; elif any `Charging` -> `Charging`; elif all packs `Full` -> `Full`; elif any `Not charging` -> `Not charging`; else `Unknown`.
Return `None` when no directory qualifies, the root does not exist, or the root is unreadable.

## 5 Edge cases & distro differences
* Desktop PC, server, most VMs: only `AC` or nothing -> `None` (UI says "no battery").
* Dell/Lenovo/HP/Asus differ in charge vs energy; Apple Silicon (Asahi) and Chromebooks may expose only `capacity`+`status` -> `Battery(pct, status, None, None, None, None, None, None)`.
* `current_now` may be signed (negative while discharging) or unsigned: always `abs`.
* Some firmware reports `energy_full > energy_full_design` after calibration; keep the numbers as is (alerts handle ratios).
* Garbage text in a file (`"abc"`, empty file, directory instead of file) -> that field `None`, never a crash.
* Arch and Debian/Ubuntu expose the same sysfs; nothing distro specific, and no root is needed (do not read `serial_number`, `manufacturer`, `model_name`).

## 6 Performance & safety limits
Reads at most ~20 tiny files per pack, ~0.2 ms total, no subprocess, no sleeping, no caching (caller decides the rate). Read-only; never writes `charge_control_*` or anything else.

## 7 Test plan (`tests/test_battery.py`, `unittest`)
Helper: `make(root, name, **files)` creates `root/name/` and writes each kwarg as `name=value\n` (`tempfile.TemporaryDirectory()` for the root; use `os.makedirs`).
* `test_charge_based_pack`: `BAT0` with `type=Battery status=Discharging present=1 capacity=87 charge_full_design=3333000 charge_full=3333000 charge_now=2889000 current_now=1389000 voltage_now=12112000 voltage_min_design=11400000 cycle_count=0` -> `pct==87`, `status=="Discharging"`, `wh_now≈32.93` (assertAlmostEqual places=2; 2.889 Ah x 11.4 V), `wh_full≈38.0`, `wh_design≈38.0`, `watts≈16.82` (1.389 A x 12.112 V), `volts≈12.112`, `cycles==0`.
* `test_energy_based_pack_prefers_power_now`: `energy_full_design=50000000 energy_full=40000000 energy_now=20000000 power_now=9000000 current_now=1 voltage_now=11000000 status=Charging capacity=50` -> `wh_design==50.0`, `wh_full==40.0`, `wh_now==20.0`, `watts==9.0`, `status=="Charging"`.
* `test_negative_current_is_magnitude`: `current_now=-2000000 voltage_now=10000000` -> `watts==20.0`.
* `test_no_power_supply_dir_returns_none`: root that does not exist -> `None`.
* `test_ac_only_returns_none`: `AC` with `type=Mains online=1` -> `None`.
* `test_wireless_peripheral_is_ignored`: `hid-0018-battery-7` with `type=Battery scope=Device capacity=40 status=Discharging` only -> `None`; adding a real `BAT0` returns the BAT0 numbers.
* `test_absent_bay_is_skipped`: `BAT1` with `type=Battery present=0 capacity=0` -> `None`.
* `test_two_packs_are_combined`: BAT0 `energy_now=10000000 energy_full=20000000 energy_full_design=25000000 power_now=4000000 status=Discharging cycle_count=100`, BAT1 `energy_now=20000000 energy_full=20000000 energy_full_design=25000000 power_now=1000000 status=Full cycle_count=300` -> `wh_now==30.0`, `wh_full==40.0`, `wh_design==50.0`, `watts==5.0`, `pct==75`, `status=="Discharging"`, `cycles==300`.
* `test_capacity_only_driver`: `type=Battery capacity=64 status=Charging` -> `pct==64`, every other numeric field `None`.
* `test_unknown_status_string`: `status=Weird` -> `"Unknown"`; missing status file -> `"Unknown"`.
* `test_capacity_is_clamped`: `capacity=101` -> `100`.
* `test_pct_computed_when_capacity_missing`: `charge_now=1500000 charge_full=3000000 voltage_min_design=11000000 type=Battery` -> `pct==50`.
* `test_garbage_values_do_not_crash`: `capacity=abc charge_full= current_now=x` -> returns a `Battery` with `pct is None` and no exception.
* `test_zero_full_energy_is_none`: `type=Battery energy_full=0 energy_now=0 capacity=55` -> `wh_full is None` and `pct==55` (taken from `capacity`).
* `test_zero_cycles_is_kept`: `cycle_count=0` -> `cycles==0` (not None).

## 8 Done when
- [ ] `python -m unittest tests.test_battery -v` passes (all tests above, no real `/sys` access in tests).
- [ ] `python -m pyflakes ctop/battery.py tests/test_battery.py` is clean.
- [ ] No new dependencies; no 3.10+ syntax; `from __future__ import annotations` present.
- [ ] Only `ctop/battery.py` and `tests/test_battery.py` were created/changed.
- [ ] No real serials, MACs or hostnames anywhere in fixtures.
