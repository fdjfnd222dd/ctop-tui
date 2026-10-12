# T06 - Wi-Fi collector

## 1 Purpose
`WifiCollector` reports the active Wi-Fi link (SSID, signal, quality word, bit rate, channel, security) plus a 120-sample signal history, using cheap kernel/`iw` reads every 5 s and the heavier `nmcli` only every 30 s. `data` is `None` when the machine has no Wi-Fi interface at all.

## 2 Files
Create `ctop/collectors/wifi.py`, `tests/test_wifi.py`. READ: `docs/ARCHITECTURE.md`, `ctop/models.py` (`WifiData`), `ctop/collectors/base.py`, `default_route()` in `ctop/system.py` (import lazily inside `__init__`). Touch nothing else.

## 3 Public API
```python
from __future__ import annotations
from typing import Callable, Dict, List, Optional, Tuple
from ctop.collectors.base import Collector
from ctop.models import WifiData

def quality_from_dbm(dbm: Optional[int]) -> str: ...      # >=-50 Excellent, >=-60 Good, >=-70 Fair, >=-80 Weak, else Poor; None -> "Unknown"
def quality_from_pct(pct: Optional[int]) -> str: ...      # >=75 Excellent, >=55 Good, >=40 Fair, >=25 Weak, else Poor; None -> "Unknown"
def pct_from_dbm(dbm: int) -> int: ...                    # clamp(2*(dbm+100), 0, 100)   (-50 -> 100, -100 -> 0)
def channel_from_freq(freq_mhz: Optional[int]) -> Optional[int]: ...
def parse_proc_wireless(text: str) -> Dict[str, Dict[str, Optional[float]]]: ...  # iface -> {"link":..,"dbm":..}
def parse_iw_link(text: str) -> Dict[str, object]: ...    # keys: connected, ssid, freq_mhz, signal_dbm, bitrate_mbps
def split_nmcli_line(line: str) -> List[str]: ...         # terse line -> fields, honouring "\:" and "\\"
def parse_nmcli_wifi(text: str) -> Optional[Dict[str, object]]: ...  # the "*" (in use) row: ssid, channel, freq_mhz, rate_mbps, signal_pct, security
def run_cmd(argv: List[str], timeout: float) -> Optional[str]: ...    # stdout or None (missing tool / timeout / error)

class WifiCollector(Collector):
    name = "wifi"
    interval = 5.0
    def __init__(self, net_root: str = "/sys/class/net", proc_wireless: str = "/proc/net/wireless",
                 runner: Optional[Callable[[List[str], float], Optional[str]]] = None,
                 clock: Optional[Callable[[], float]] = None,
                 default_iface_fn: Optional[Callable[[], Optional[str]]] = None) -> None: ...
    def refresh(self) -> None: ...        # self.data = WifiData(...) or None
```
`runner=None` -> `run_cmd`; `clock=None` -> `time.monotonic`; `default_iface_fn=None` -> `lambda: default_route()[0]` (lazy import). Python 3.9 syntax only.

## 4 Data sources
**Interface discovery** (each refresh, cheap): names in `<net_root>` that have a `wireless` sub-directory or a `phy80211` entry; plus names in `/proc/net/wireless`. If none -> `self.data = None`. If several, pick: the default-route interface if it is Wi-Fi, else the first whose `iw link` says connected, else the first in sorted order. Validate the name with `^[A-Za-z0-9_.\-]{1,15}$` before putting it into any argv.

**1 `/proc/net/wireless`** (every refresh, plain file read). Anonymised:
```
Inter-| sta-|   Quality        |   Discarded packets ...
 face | tus | link level noise |  nwid  crypt   frag  retry   misc | beacon | 22
 wlan0: 0000   69.  -41.  -256        0      0      0     27     12        0
```
Skip the first two lines; per line `iface = first token minus ':'`; `link` = 3rd field minus trailing '.', `level` = 4th field minus trailing '.'. `dbm`: float value `v`; if `v > 0` and `v <= 255` treat as unsigned wrap: `v -= 256`; if the result is still `>= 0` -> `None`. `link` is out of 70: `pct = round(min(link,70)/70*100)`.

**2 `iw dev <if> link`** (every refresh if `iw` exists; timeout 2 s; `runner` returns `None` when the binary is missing). Connected output (anonymised):
```
Connected to 02:00:00:aa:bb:cc (on wlan0)
	SSID: ExampleNet
	freq: 5180.0
	signal: -42 dBm
	rx bitrate: 390.0 MBit/s VHT-MCS 8 80MHz short GI VHT-NSS 1
	tx bitrate: 866.7 MBit/s VHT-MCS 9 80MHz short GI VHT-NSS 2
```
Disconnected: `Not connected.` -> `connected=False`. Rules: `SSID:` = everything after `SSID: ` (may contain spaces); `freq` -> `int(float(x))`; `signal` -> `int` of the number before ` dBm`; bitrate = `tx bitrate` number, else `rx bitrate`, as `float`. NEVER store the BSSID (the MAC after "Connected to") anywhere.

**3 `nmcli`** (security + fallbacks; at most every 30 s, cached; timeout 4 s):
`["nmcli", "-t", "--escape", "yes", "-f", "IN-USE,SSID,CHAN,FREQ,RATE,SIGNAL,SECURITY", "dev", "wifi", "list", "ifname", <if>, "--rescan", "no"]`. `--rescan no` is mandatory: without it NetworkManager may start an active channel scan (battery + latency cost). Output, one AP per line, `:` separated with `\:` escaping:
```
*:ExampleNet:36:5180 MHz:866 Mbit/s:78:WPA2 WPA3
 :Neighbour Net:44:5220 MHz:540 Mbit/s:62:WPA2
 :Open Cafe:1:2412 MHz:130 Mbit/s:55:
*:Cafe\: Free:6:2437 MHz:130 Mbit/s:60:WPA1 WPA2
```
`split_nmcli_line`: scan characters; backslash + next char -> that char (so `\:` -> `:`, `\\` -> `\`); unescaped `:` splits. `parse_nmcli_wifi` returns the dict for the first row whose field 0 is `*`, else `None`. `freq_mhz` = leading integer of `"5180 MHz"`; `rate_mbps` = leading number of `"866 Mbit/s"` (this is the AP's MAXIMUM rate, so it is only a last-resort bitrate); `signal_pct` = int; `security`: split the field on spaces, map `WPA1`->`WPA`, drop empty and `--`, join with one space; empty result -> `"none"`.
Cache: `self._nm` (dict or None) and `self._nm_at` (clock value). Re-run `nmcli` when `clock() - _nm_at >= 30`, or the SSID/interface changed since the cached run. If `runner` returns `None` (no NetworkManager: Debian server, `iwd`, `wpa_supplicant` only) keep `security=None` and do not retry more often than every 30 s.

**Merge precedence per field:** dbm: iw > /proc. ssid: iw > nmcli. freq: iw > nmcli. bitrate: iw > nmcli `rate_mbps`. channel: `channel_from_freq(freq)` > nmcli `CHAN`. security: nmcli only. `signal_pct`: `pct_from_dbm(dbm)` if dbm known, else /proc link pct, else nmcli SIGNAL. `quality`: `quality_from_dbm` if dbm known, else `quality_from_pct(signal_pct)`.
`channel_from_freq`: 2412-2472 -> `(f-2407)//5`; 2484 -> 14; 5160-5885 -> `(f-5000)//5`; 5955-7115 -> `(f-5950)//5`; anything else/None -> `None`. (5180 -> 36, 5680 -> 136, 2437 -> 6, 5955 -> 1.)

**Result.** Interface present but not connected (iw says `Not connected.`, or no signal anywhere) -> `WifiData(iface, None, None, None, None, None, None, None, "Unknown", history=<old history>)`. History: `deque(maxlen=120)` of `signal_pct`, appended only when `signal_pct` is not None; `WifiData.history = tuple(deque)`.

## 5 Edge cases & distro differences
* No Wi-Fi hardware / desktop / VM (`eth0` only): `data is None`.
* Debian/Ubuntu: `iw` may not be installed (netinst) -> `/proc/net/wireless` still gives signal; SSID/rate then come from nmcli or stay `None`. `/proc/net/wireless` can be empty on kernels without WEXT compat; then everything depends on iw/nmcli.
* Arch with `iwd`/`wpa_supplicant` without NetworkManager: no security info (`None`); iw still works. `iwctl` is out of scope.
* USB dongles / predictable names (`wlp3s0`, `wlx...`) are found through the sysfs `wireless` directory, not by a `wl*` name guess.
* SSID may contain spaces, colons, UTF-8 or be hidden (empty) -> empty SSID becomes `None`. Do not log or print it anywhere except in the model.
* Airplane mode (rfkill) = connected False. `iw` unauthorised or failing -> `None` output -> fall through.

## 6 Performance & safety limits
Cheap path per refresh: one tiny file read + one `iw` process (~5 ms). `nmcli` at most 2 per minute (~30 ms each). All subprocesses via `run_cmd` with `timeout=`, `stdin=DEVNULL`, `env LC_ALL=C`. No scanning (`--rescan no`, never `iw scan`, never `nmcli dev wifi rescan`), no writes, no connecting/disconnecting.

## 7 Test plan (`tests/test_wifi.py`) - fake `runner`, tmp `net_root`, never call real iw/nmcli
* `test_quality_from_dbm_boundaries`: -50 Excellent, -51 Good, -60 Good, -61 Fair, -70 Fair, -71 Weak, -80 Weak, -81 Poor, None Unknown.
* `test_quality_from_pct_boundaries`: 75 Excellent, 74 Good, 55 Good, 54 Fair, 40 Fair, 39 Weak, 25 Weak, 24 Poor.
* `test_pct_from_dbm`: -42 -> 100, -75 -> 50, -100 -> 0, -120 -> 0.
* `test_channel_from_freq`: 2412->1, 2437->6, 2484->14, 5180->36, 5680->136, 5955->1, 1000->None, None->None.
* `test_parse_proc_wireless`: the sample above -> `{"wlan0": {"link": 69.0, "dbm": -41.0}}`; text with only the two header lines -> `{}`; unsigned level `216.` -> dbm -40.
* `test_parse_iw_link_connected`: the sample above -> connected True, ssid "ExampleNet", freq 5180, signal -42, bitrate 866.7.
* `test_parse_iw_link_not_connected`: `"Not connected.\n"` -> `connected` False and no ssid.
* `test_parse_iw_link_ssid_with_spaces_and_rx_only`: `SSID: My Home Net`, only an `rx bitrate: 144.4 MBit/s` line -> ssid "My Home Net", bitrate 144.4.
* `test_split_nmcli_line_escapes`: `r"*:Cafe\: Free:6:2437 MHz:130 Mbit/s:60:WPA1 WPA2"` -> 7 fields, field 1 `"Cafe: Free"`.
* `test_parse_nmcli_in_use_row`: the 4-line sample -> the `*` row of "ExampleNet" (security "WPA2 WPA3", channel 36, freq 5180, rate 866.0, signal_pct 78); only rows starting with space -> `None`; open network row with `*` and empty security -> `"none"`; `WPA1 WPA2` -> `"WPA WPA2"`.
* `test_no_wifi_interface_gives_none`: net_root containing only `eth0/` (no `wireless` dir) and a proc file with just headers -> after `run()` `data is None`, `error is None`.
* `test_connected_full_merge`: net_root with `wlan0/wireless/`; runner returns the iw sample for `iw`, the nmcli sample for `nmcli`; expect iface "wlan0", ssid "ExampleNet", dbm -42, `signal_pct==100`, quality "Excellent", bitrate 866.7, freq 5180, channel 36, security "WPA2 WPA3".
* `test_proc_only_when_iw_and_nmcli_missing`: runner always `None`; proc sample level -61, link 40 -> dbm -61, quality "Fair", ssid None, security None, bitrate None.
* `test_nmcli_is_cached_for_30_seconds`: fake clock; five refreshes at t=0,5,10,15,20 -> nmcli called once; refresh at t=31 -> called again (count 2); `iw` called every refresh.
* `test_nmcli_argv_never_rescans`: the recorded nmcli argv contains `"--rescan"` followed by `"no"` and the interface name as a separate element.
* `test_disconnected_keeps_iface_and_unknown_quality`: iw returns `"Not connected.\n"`, nmcli `None`, proc has no level -> `quality=="Unknown"`, `ssid is None`.
* `test_history_capped_and_newest_last`: 130 refreshes with rising signals -> `len(history)==120`, last element equals the latest `signal_pct`.
* `test_invalid_iface_name_is_not_used`: net_root dir named `x;rm` containing `wireless/` -> ignored (`data is None`).

## 8 Done when
- [ ] `python -m unittest tests.test_wifi -v` passes; no real subprocess or `/sys` access in tests.
- [ ] `python -m pyflakes ctop/collectors/wifi.py tests/test_wifi.py` clean.
- [ ] No new dependencies; Python 3.9 syntax.
- [ ] Only the two listed files created; no BSSID/MAC stored or printed anywhere.
- [ ] `nmcli` is never run without `--rescan no`.
