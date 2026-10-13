# T18 - NetworkManager state collector (radios, nearby Wi-Fi, saved profiles)

## 1 Purpose
`NmCollector` publishes an `NmState` (see `ctop/models.py`) for the Network page: is NetworkManager there, are the radios on,
which Wi-Fi networks are nearby (from NM's cached scan - it never triggers a scan), and which profiles are saved.
Read-only. Changing anything is T19's job.

## 2 Files
Create `ctop/collectors/nmstate.py`, `tests/test_nmstate.py`. Read `docs/specs/NET-COMMON.md` first.
Reuse (import, do not copy): `split_nmcli_line`, `channel_from_freq`, `run_cmd` from `ctop.collectors.wifi`.

## 3 Public API
```python
GENERAL = ["nmcli", "-t", "-f", "RUNNING,STATE,CONNECTIVITY,WIFI-HW,WIFI,WWAN", "general"]
DEVICES = ["nmcli", "-t", "-f", "DEVICE,TYPE,STATE", "device"]
WIFI_LIST = ["nmcli", "-t", "-f", "IN-USE,BSSID,SSID,CHAN,FREQ,RATE,SIGNAL,SECURITY", "device", "wifi", "list", "--rescan", "no"]
CONNS = ["nmcli", "-t", "-f", "NAME,UUID,TYPE,DEVICE,AUTOCONNECT,AUTOCONNECT-PRIORITY,ACTIVE,STATE,TIMESTAMP", "connection", "show"]
def ssid_argv(uuid: str) -> list: ...   # ["nmcli", "-g", "802-11-wireless.ssid", "connection", "show", "uuid", uuid]
TIMEOUT = 3.0

def band_of(freq_mhz: Optional[int]) -> str: ...       # 2400-2500 "2.4 GHz"; 4900-5925 "5 GHz"; 5926-7125 "6 GHz"; else "?"
def parse_general(text: str) -> dict: ...             # keys running(bool) state connectivity wifi_hw wifi wwan (bool|None each)
def parse_devices(text: str) -> Optional[str]: ...     # first DEVICE whose TYPE == "wifi" (not "wifi-p2p"), else None
def parse_wifi_list(text: str, saved_ssids=frozenset()) -> Tuple[WifiNetwork, ...]: ...
def parse_connections(text: str, ssids: Mapping[str, str] = None) -> Tuple[SavedConnection, ...]: ...
def unescape_value(text: str) -> str: ...              # `-g` output: strip trailing newline, "\:" -> ":", "\\" -> "\"

class NmCollector(Collector):
    name = "nmstate"
    interval = 8.0
    def __init__(self, runner=None, clock=None): ...   # runner(argv, timeout) -> Optional[str]; default run_cmd; clock default time.time
    def refresh(self) -> None: ...
```

## 4 Data sources and parsing
All via `runner(argv, TIMEOUT)`; `None` = command missing/failed.

**general** (one line): `running:connected:full:enabled:enabled:enabled`. The first field must be `running`.
`wifi_hw`/`wifi`/`wwan`: `"enabled"` -> True, `"disabled"` -> False, `"missing"` or anything else -> None.
If GENERAL returns None, or the first field is not `running` -> publish `NmState(available=False, reason=...)` and run nothing else:
reason `"NetworkManager not found - Wi-Fi controls need it (package: network-manager)"` when the command failed,
`"NetworkManager is not running"` when it answered but is not running. Set `scanned_at=clock()`.

**devices**: lines `wlan0:wifi:connected`, `p2p-dev-wlan0:wifi-p2p:disconnected`, `lo:loopback:connected (externally)`.

**wifi list** (only when a Wi-Fi device exists AND wifi is True): lines like
```
*:AA\:BB\:CC\:DD\:EE\:01:ExampleNet:136:5680 MHz:1170 Mbit/s:77:WPA1 WPA2
 :AA\:BB\:CC\:DD\:EE\:02:ExampleNet:1:2412 MHz:270 Mbit/s:59:WPA2
 :AA\:BB\:CC\:DD\:EE\:03:Cafe\: Free:6:2437 MHz:130 Mbit/s:60:
 :AA\:BB\:CC\:DD\:EE\:04::11:2462 MHz:65 Mbit/s:30:WPA2
```
Split with `split_nmcli_line` (pad to 8 fields). BSSID upper case. channel = int of CHAN, else `channel_from_freq(freq)`.
freq = leading int of `"5680 MHz"`, rate = leading int of `"1170 Mbit/s"`, signal = int or None.
security: split on spaces, map `WPA1`->`WPA`, drop `""` and `"--"`; keep `802.1X`, `WPA3`, `OWE`; join with one space; open = `""`.
**Merge by SSID** (non-empty SSIDs): one `WifiNetwork` per SSID taken from its strongest AP (signal), `aps` = number of rows
with that SSID, `in_use` = True if ANY of its rows has `*` (and then the in-use row's bssid/channel/freq/band/rate/signal/security
are used instead of the strongest one). Hidden rows (empty SSID) are merged into ONE entry with ssid `""` (strongest, aps = count).
`saved` = ssid in `saved_ssids`. Order: in_use first, then signal descending (None last), then ssid. Cap at 60 entries.

**connections**: lines `ExampleNet:3f0c1e2a-1111-2222-3333-444455556666:802-11-wireless:wlan0:yes:0:yes:activated:1791910451`.
NAME may contain escaped `\:`; always use `split_nmcli_line`. kind: `802-11-wireless`->`wifi`, `802-3-ethernet`->`ethernet`,
`vpn`->`vpn`, `wireguard`->`wireguard`, anything else -> `other`.
SKIP types `loopback`, `bridge`, `tun`, `bond`, `vlan`, `dummy`, `generic` (not things a person picks).
device `""`/`"--"` -> None. autoconnect = `yes`. priority int (default 0). active = `yes`. state `""`/`"--"` -> `""`.
last_used = int(TIMESTAMP) or None when `0`/empty/bad. ssid = `ssids.get(uuid)` for wifi profiles (None otherwise).
Order: active first, then last_used descending (None last), then name. Cap 40.

**SSID of each saved Wi-Fi profile**: `-g` single value, e.g. `Cafe\: Free\n`. Cache `uuid -> ssid` in the collector for its
lifetime (SSIDs do not change). Each refresh fetches at most 8 uuids not yet cached (wifi kind only); a failed lookup caches the
profile NAME as its ssid (so it is not retried every refresh). The set of saved ssids = cached ssids of the current wifi profiles.
Order of commands in `refresh()`: GENERAL, DEVICES, CONNS, missing ssids, WIFI_LIST (so `saved` flags use fresh ssids);
the published `saved` tuple is built after the lookups so it carries the ssids.

## 5 Edge cases
* Debian 12 ships nmcli 1.42: every field above exists there; do not use newer fields.
* Wi-Fi radio off -> `networks=()`, `wifi_enabled=False`, no WIFI_LIST call. No Wi-Fi device -> `wifi_device=None`, `networks=()`.
* Garbage / short lines are skipped, never raise. SSIDs may contain `:`, `\`, spaces, UTF-8.
* `uuid` must match `^[0-9a-fA-F-]{8,64}$` before it is put in argv; otherwise skip that profile's SSID lookup.

## 6 Performance
Normal refresh = 4 nmcli calls (~12 ms each). SSID lookups are one-time per profile. Never `--rescan yes`, never `wifi rescan`.

## 7 Tests (`tests/test_nmstate.py`) - fake runner keyed on argv, fake clock
* band_of: 2412 "2.4 GHz", 5180 "5 GHz", 5955 "6 GHz", 7115 "6 GHz", None "?".
* parse_general: the sample -> running True, wifi True, wwan True, wifi_hw True; `running:asleep:none:enabled:disabled:missing` -> wifi False, wwan None.
* parse_devices picks `wlan0`, ignores `wifi-p2p`; none -> None.
* parse_wifi_list on the 4-line sample: 3 entries; ExampleNet first, in_use, aps 2, channel 136, band "5 GHz", security "WPA WPA2",
  bssid ending `:01`; "Cafe: Free" security ""; hidden entry ssid "" aps 1; saved flag set when passed `frozenset({"Cafe: Free"})`.
* in_use row wins over a stronger non-in-use AP of the same SSID.
* parse_connections: wifi row parsed fully; loopback skipped; `--` device -> None; TIMESTAMP 0 -> None; ordering rule; escaped `\:` in NAME.
* unescape_value: `"Cafe\\: Free\n"` -> `"Cafe: Free"`.
* collector, NM missing (runner returns None for GENERAL): available False, reason mentions network-manager, exactly 1 runner call.
* collector full path: available True, wifi_device "wlan0", networks merged, saved entry has ssid from the `-g` lookup, `saved` flag on the matching network.
* SSID cache: a second refresh does not call the `-g` command again; at most 8 lookups per refresh with 10 new profiles.
* Wi-Fi disabled: no WIFI_LIST call recorded.
* bad uuid `x;rm` never reaches argv.
* every recorded WIFI_LIST argv contains `"--rescan", "no"`.
