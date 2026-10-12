# T04 - Alert rules

## 1 Purpose
`evaluate()` is a pure function that turns the current RAM/swap/battery/thermal/network numbers into a short list of plain-English `Alert`s, worst first. It does no I/O, keeps no state, and never raises on missing (`None`) inputs.

## 2 Files
Create `ctop/alerts.py`, `tests/test_alerts.py`. READ: `docs/ARCHITECTURE.md`, `ctop/models.py` (`Alert`, `Battery`, `Thermal`, `Fan`, `PingData`, `PingResult`, `WifiData`). Touch nothing else.

## 3 Public API
```python
from __future__ import annotations
from dataclasses import dataclass
from typing import List, Optional
from ctop.models import Alert, Battery, PingData, Thermal, WifiData

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

def evaluate(inp: AlertInput, thresholds: Optional[Thresholds] = None) -> List[Alert]: ...
```
`thresholds=None` means `Thresholds()`. Python 3.9 syntax only. Write one small private function per rule (`_ram(inp, t) -> Optional[Alert]`, ...) and call them from `evaluate` through a module list `_RULES`.

## 4 Rules (exact keys, levels and texts)
One alert per key at most (the worse level wins). `X` in a text is the number formatted exactly as shown. The degree sign is `°C`. Use plain ASCII hyphen " - ".
| key | fires when | level | text |
|---|---|---|---|
| `ram-high` | `ram_pct >= ram_crit` | crit | `Memory is almost full ({ram:.0f}%) - the system may start closing programs.` |
| | `ram_pct >= ram_warn` | warn | `Memory is {ram:.0f}% full.` |
| `swap-high` | `swap_pct >= swap_crit` | crit | `Swap is {swap:.0f}% full - the computer will feel stuck.` |
| | `swap_pct >= swap_warn` | warn | `Swap is {swap:.0f}% full - memory is under pressure.` |
| `battery-low` | `battery.status == "Discharging"` and `pct` not None and `pct <= bat_crit_pct` | crit | `Battery is at {pct}% - plug in now.` |
| | same, `pct <= bat_warn_pct` | warn | `Battery is at {pct}% - plug in soon.` |
| `battery-drain` | `status == "Discharging"`, `watts` not None, `watts >= drain_warn_w` | warn | `Battery is draining fast ({w:.1f} W){left}.` |
| `cpu-hot` | `temp >= cpu_crit_c` | crit | `CPU is at {t:.0f}°C - close to its limit. Close heavy programs.` |
| | `temp >= cpu_warn_c` | warn | `CPU is hot ({t:.0f}°C).` |
| `fans-idle` | `thermal.fans` non-empty, every `rpm == 0`, `temp >= fan_idle_cpu_c` | warn | `Fans are not spinning while the CPU is at {t:.0f}°C.` |
| `wifi-weak` | `wifi.quality in ("Weak","Poor")` | warn | `Wi-Fi signal is {quality.lower()}{detail} - move closer to the router.` |
| `router-loss` | `ping.router` not None, `len(history) >= ping_min_samples`, `loss_pct >= router_loss_warn` | warn | `Router is dropping packets ({loss:.0f}% lost) - check Wi-Fi signal or cable.` |
| `internet-loss` | `ping.internet`, enough samples, `loss_pct >= ping_loss_crit` | crit | `Internet is mostly unreachable ({loss:.0f}% of pings lost).` |
| | enough samples, `loss_pct >= ping_loss_warn` | warn | `Internet connection is dropping packets ({loss:.0f}% lost).` |
| `battery-health` | `wh_full` and `wh_design` not None, both `> 0`, `wh_full/wh_design*100 < health_info_pct` (ALWAYS evaluated, any status) | info | `Battery holds only {ratio:.0f}% of its original capacity.` |
| `internet-slow` | `ping.internet`, enough samples, `avg_ms` not None, `avg_ms >= latency_info_ms`, and `loss_pct < ping_loss_warn` | info | `Internet is slow to respond (average {avg:.0f} ms).` |
Details:
* `temp` = `inp.cpu_temp` if not None else `inp.thermal.cpu_temp` if thermal is not None else None; rules `cpu-hot` and `fans-idle` skip when `temp is None`.
* `left` (battery-drain): empty string unless `battery.wh_now` is not None and `watts > 0`; then `minutes = int(wh_now / watts * 60)` and `left = " - about " + ("{m//60} h {m%60:02d} min" if minutes >= 60 else "{m} min") + " left"`. Example: `Battery is draining fast (21.3 W) - about 1 h 24 min left.` (30.0 Wh at 21.3 W).
* `detail` (wifi-weak): `f" ({dbm} dBm)"` if `signal_dbm` not None, else `f" ({pct}%)"` if `signal_pct` not None, else `""`. Example: `Wi-Fi signal is weak (-76 dBm) - move closer to the router.`
* `battery-health` sanity: if the ratio is `> 110` ignore it (bogus firmware numbers).
* "Plain English": no jargon, no exclamation marks, no trailing spaces, each text one line.

## 5 Edge cases & distro differences
* Desktop PC / VM: `battery`, `thermal`, `wifi`, `ping` are None -> only RAM/swap rules can fire; `evaluate(AlertInput())` returns `[]`.
* Laptop with no fan driver (`thermal.fans == ()`) -> `fans-idle` never fires (zero fans is not "all fans stopped"). Dell/ThinkPad fans legitimately sit at 0 rpm when cool; that is why the CPU must be `>= 75` degrees.
* Charging / Full / Not charging battery at 5% -> no `battery-low` and no `battery-drain`.
* Fresh start-up: ping history has 1-3 samples (one lost reply = 100%); `ping_min_samples` stops false alarms for the first ~30 s.
* Thresholds are inclusive (`>=` / `<=`). `evaluate` is stateless: debouncing/hysteresis is the caller's business.

## 6 Performance & safety limits
Microseconds, no I/O, no logging, no globals mutated, no imports beyond `dataclasses`, `typing` and `ctop.models`. Never raise: all attribute access is guarded by `is not None`.

## 7 Test plan (`tests/test_alerts.py`)
Build inputs with the model dataclasses; helper `keys(alerts)` -> `[a.key for a in alerts]`, `by(alerts, key)` -> the alert or None.
* `test_empty_input_gives_no_alerts`: `evaluate(AlertInput()) == []`.
* `test_ram_levels`: 84.9 -> none; 85 -> warn text `Memory is 85% full.`; 93 -> crit and text contains `94%` for 93.6; only one `ram-high` alert.
* `test_swap_levels`: 59.9 none; 60 warn; 85 crit.
* `test_battery_low_only_when_discharging`: `Battery(pct=7,status="Discharging",...)` -> crit `Battery is at 7% - plug in now.`; pct 20 -> warn; pct 21 -> none; same pct 7 with status "Charging" -> no `battery-low`.
* `test_battery_drain_text_with_time_left`: `watts=21.3, wh_now=30.0, Discharging` -> text `Battery is draining fast (21.3 W) - about 1 h 24 min left.`; with `wh_now=None` -> `Battery is draining fast (21.3 W).`; `watts=17.9` -> none; `Charging` at 25 W -> none.
* `test_battery_drain_minutes_only`: `watts=20.0, wh_now=10.0` -> `about 30 min left`.
* `test_battery_health_info`: `wh_full=31.0, wh_design=50.0` -> info `Battery holds only 62% of its original capacity.`; `wh_full=35.0` (70%) -> none; works while "Charging"; `wh_design=None` -> none; `wh_full=60, wh_design=50` -> none.
* `test_cpu_heat`: `cpu_temp=84.9` none; 85 warn `CPU is hot (85°C).`; 95 crit; `cpu_temp=None` falls back to `Thermal(cpu_temp=90.0)` -> warn; explicit `cpu_temp=50` beats thermal 99 -> none.
* `test_fans_idle`: `Thermal(fans=(Fan("Fan 1",0),), cpu_temp=78.0)` -> warn `Fans are not spinning while the CPU is at 78°C.`; one fan at 1200 rpm -> none; `fans=()` -> none; temp 74 -> none.
* `test_wifi_weak_and_poor`: quality "Weak" with dbm -76 -> text `Wi-Fi signal is weak (-76 dBm) - move closer to the router.`; "Poor" with only pct 20 -> `... is poor (20%) - ...`; "Fair"/"Unknown" -> none.
* `test_ping_needs_enough_samples`: internet `loss_pct=100.0` with history of 5 lost samples -> no alert; with 10 -> crit.
* `test_internet_loss_levels`: 9.9 none; 10 warn `Internet connection is dropping packets (10% lost).`; 50 crit `Internet is mostly unreachable (50% of pings lost).`
* `test_router_loss`: 10 -> warn text contains `Router is dropping packets (10% lost)`; `router=None` -> none.
* `test_internet_slow_info`: `avg_ms=210.0, loss 0` -> info `Internet is slow to respond (average 210 ms).`; same with `loss_pct=20` -> only `internet-loss` (no slow alert); `avg_ms=149` -> none.
* `test_sorted_crit_then_warn_then_info`: input producing crit ram, warn cpu, info battery-health, crit battery-low -> levels list is `["crit","crit","warn","info"]` and the two crits are in rule order (ram-high before battery-low).
* `test_custom_thresholds`: `Thresholds(ram_warn=50.0)` with ram 60 -> warn.
* `test_all_texts_are_single_line_ascii_except_degree`: collect every alert from a "worst case" input; each `text` has no newline and ends with `.`.

## 8 Done when
- [ ] `python -m unittest tests.test_alerts -v` passes.
- [ ] `python -m pyflakes ctop/alerts.py tests/test_alerts.py` clean.
- [ ] No new dependencies; Python 3.9 syntax; `models.py` untouched.
- [ ] Only the two listed files created.
- [ ] Alert keys and texts match section 4 character for character (renderers and tests depend on them).
