# T09 - LAN devices from the neighbour table

## 1 Purpose
`read_devices()` lists the other machines this computer has recently talked to on the local network, taken PASSIVELY from the kernel's neighbour (ARP/NDP) cache, with gateway and "private Wi-Fi address" flags. It never probes, scans or looks up anything.

## 2 Files
Create `ctop/collectors/lan.py`, `tests/test_lan.py`. READ: `docs/ARCHITECTURE.md` (principle 2: passive), `ctop/models.py` (`Device`), `ctop/collectors/base.py`, `default_route()` in `ctop/system.py` (lazy import inside `LanCollector.__init__`). Touch nothing else.

## 3 Public API
```python
from __future__ import annotations
from typing import Callable, List, Optional, Tuple
from ctop.collectors.base import Collector
from ctop.models import Device

# raw row used between parsers and read_devices: (ip, iface, mac_lowercase, state_uppercase)
Row = Tuple[str, str, str, str]

def parse_neigh_json(text: str) -> List[Row]: ...                 # `ip -j neigh` output; [] on any JSON error
def parse_neigh_text(text: str) -> List[Row]: ...                 # `ip neigh` output
def parse_proc_arp(text: str) -> List[Row]: ...                   # /proc/net/arp contents
def is_random_mac(mac: str) -> bool: ...                          # locally-administered bit

def read_devices(neigh_json: Optional[str] = None, gateway_ip: Optional[str] = None,
                 runner: Optional[Callable[[List[str], float], Optional[str]]] = None,
                 arp_path: str = "/proc/net/arp") -> Tuple[Device, ...]: ...

class LanCollector(Collector):
    name = "lan"
    interval = 10.0
    def __init__(self, gateway_fn: Optional[Callable[[], Tuple[Optional[str], Optional[str]]]] = None,
                 runner: Optional[Callable[[List[str], float], Optional[str]]] = None) -> None: ...
    def refresh(self) -> None: ...      # self.data = read_devices(gateway_ip=gateway_fn()[1], runner=...)
```
`neigh_json` is the RAW TEXT of `ip -j neigh` (not parsed). When it is given, no command and no file is touched (tests use this). When `None`: run the commands below through `runner` (default `run_cmd(argv, timeout) -> Optional[str]`: `subprocess.run(argv, capture_output=True, text=True, timeout=timeout, stdin=DEVNULL, env LC_ALL=C)`, stdout or `None` on `OSError`/timeout; define it in this file). `gateway_fn=None` -> `ctop.system.default_route`. Python 3.9 syntax only.

## 4 Data sources (first one that yields at least one row wins)
1. `["ip", "-j", "neigh", "show"]`, timeout 2 s. Anonymised JSON:
```
[{"dst":"192.0.2.1","dev":"wlan0","lladdr":"00:00:5e:00:53:01","state":["REACHABLE"]},
 {"dst":"192.0.2.23","dev":"wlan0","lladdr":"02:00:00:aa:bb:cc","state":["STALE"]},
 {"dst":"192.0.2.77","dev":"wlan0","state":["FAILED"]},
 {"dst":"fe80::1","dev":"wlan0","lladdr":"00:00:5e:00:53:01","router":null,"state":["STALE"]}]
```
Row = `(dst, dev, lladdr.lower(), state[0].upper())`; entries lacking `dst`, `dev` or `lladdr` are dropped by the parser (`state` may be a list or a string; missing -> `"UNKNOWN"`).
2. `["ip", "neigh", "show"]` (old iproute2 without `-j`, or JSON unparsable), same timeout. One entry per line:
```
192.0.2.1 dev wlan0 lladdr 00:00:5e:00:53:01 REACHABLE
192.0.2.9 dev wlan0  FAILED
192.0.2.10 dev wlan0 INCOMPLETE
fe80::1 dev wlan0 lladdr 00:00:5e:00:53:01 router STALE
```
Tokens: `ip = t[0]`; `iface` = token after `dev`; `mac` = token after `lladdr` (line dropped if absent); `state` = last token if it is all letters, else `"UNKNOWN"`.
3. `/proc/net/arp` (`arp_path`), IPv4 only, no command needed:
```
IP address       HW type     Flags       HW address            Mask     Device
192.0.2.1        0x1         0x2         00:00:5e:00:53:01     *        wlan0
```
Skip the header; columns `ip, _, flags, mac, _, iface`. `flags & 0x4` -> `"PERMANENT"`, `flags & 0x2` -> `"STALE"` (the file does not say more; STALE = "known, not freshly verified"), flags `0x0` (incomplete) -> dropped.

**Filtering** (all sources): drop state `FAILED` or `INCOMPLETE`; drop MAC `00:00:00:00:00:00` and `ff:ff:ff:ff:ff:ff`; drop multicast/loopback IPs (`ipaddress`: `is_multicast`, `is_loopback`); drop unparsable IPs; drop interfaces named `lo`.
**One row per device:** group the remaining rows by `(mac, iface)` (a phone seen as `192.0.2.23` plus two IPv6 addresses is ONE device). Representative IP priority: equals `gateway_ip`; else IPv4 (lowest numeric); else the lowest IPv6. `state` = that representative's state.
**Fields:** `is_gateway = (ip == gateway_ip)` (string equality, `gateway_ip` may be `None`); `random_mac = is_random_mac(mac)` = `int(mac.split(":")[0], 16) & 0x02 != 0` (False for malformed text). Note `02:00:00:aa:bb:cc` IS random; `00:00:5e:00:53:01` is not.
**Order:** gateway first; then by `(ip_version, int(ipaddress.ip_address(ip)))` ascending (so `192.0.2.9` before `192.0.2.23`); ties by `iface`. Cap at 256 rows.

## 5 Edge cases & distro differences
* Debian/Ubuntu/Arch all ship `ip` (iproute2); minimal containers may not -> fall through to `/proc/net/arp`; if that is also missing return `()`.
* The table only holds hosts this machine already exchanged packets with (and entries age out after minutes) - it is NOT a full inventory. A comment in the code must say so; never "improve" it by pinging.
* Entries need no root. `ip neigh` can show IPv6 router-advertisement entries; they merge into the IPv4 row of the same MAC, or stand alone when no IPv4 exists.
* Virtual interfaces (`virbr0`, `docker0`, `tailscale0`) appear as ordinary `iface` values; keep them.
* VPN-only or offline machine: empty table -> `()`.
* Privacy: MACs are shown only on-screen; never write them to disk or logs.

## 6 Performance & safety limits
One `ip` process per refresh (~3 ms), every 10 s. Strictly read-only and passive: NO ARP requests, NO ping sweeps, NO nmap/arp-scan, NO mDNS/NetBIOS/DNS or MAC-vendor lookups (no `OUI` database, no network name resolution). Timeouts on every subprocess. Do not retry in a loop.

## 7 Test plan (`tests/test_lan.py`) - pass text in; never run `ip`, never read the real `/proc/net/arp`
Constants `GW="192.0.2.1"`, `JSON=<the 4-entry sample above>`.
* `test_json_basic`: `read_devices(JSON, GW)` -> 2 devices: `Device("192.0.2.1","00:00:5e:00:53:01","wlan0","REACHABLE",True,False)` first, then `Device("192.0.2.23","02:00:00:aa:bb:cc","wlan0","STALE",False,True)`; the FAILED entry and the IPv6 duplicate of the gateway MAC are absent.
* `test_skips_failed_incomplete_and_macless`: JSON with `FAILED`, `INCOMPLETE` and an entry without `lladdr` -> `()`.
* `test_random_mac_bit`: `is_random_mac("02:00:00:aa:bb:cc")` True; `"00:00:5e:00:53:01"` False; `"da:a1:19:00:00:01"` True (0xda & 2); `"d8:a1:19:00:00:01"` False; `"junk"` False.
* `test_gateway_first_then_numeric_ip_order`: four entries `192.0.2.100`, `192.0.2.9`, `192.0.2.1`, `192.0.2.50` (each with its own MAC) and `gateway_ip="192.0.2.50"` -> order `.50`, `.1`, `.9`, `.100`.
* `test_ipv6_only_device_is_kept`: a MAC that only has `2001:db8::5` -> one device with that IPv6 address; sorted after all IPv4 rows.
* `test_same_mac_collapses_prefers_ipv4`: `fe80::2`, `2001:db8::2`, `192.0.2.2` same MAC+iface -> one device `192.0.2.2`.
* `test_same_mac_on_two_interfaces_is_two_devices`: same MAC on `wlan0` and `eth0` -> 2 devices.
* `test_state_is_list_or_string`: `"state":"STALE"` and `"state":["STALE"]` both -> `"STALE"`; lowercase `"stale"` -> `"STALE"`.
* `test_invalid_json_falls_back_to_text`: `read_devices(neigh_json="not json", ...)` with `runner` returning the text sample for `["ip","neigh","show"]` -> the text rows are used.
* `test_text_parser`: the 4-line text sample -> rows for `192.0.2.1` (REACHABLE) and `fe80::1` (STALE) only; `router` token ignored.
* `test_proc_arp_parser`: the sample plus a `0x0` flags row -> exactly one row with state `"STALE"`; a `0x6` flags row -> `"PERMANENT"`.
* `test_fallback_chain_with_runner`: `neigh_json=None`; fake runner returns `None` for both `ip` commands and `arp_path` points at a tmp file with the arp sample -> one device from the file.
* `test_nothing_available`: runner `None`, `arp_path` nonexistent -> `()`.
* `test_multicast_and_loopback_ignored`: JSON entries for `224.0.0.251`, `127.0.0.1`, and MAC `ff:ff:ff:ff:ff:ff` -> `()`.
* `test_gateway_none`: `gateway_ip=None` -> no device has `is_gateway`.
* `test_collector_uses_gateway_fn`: `LanCollector(gateway_fn=lambda: ("wlan0", GW), runner=fake)` where fake returns `JSON` for the `-j` command; after `run()` `data[0].is_gateway` True, `error is None`, `name=="lan"`, `interval==10.0`.
* `test_runner_argv`: the first command recorded is exactly `["ip","-j","neigh","show"]` with timeout `2`.

## 8 Done when
- [ ] `python -m unittest tests.test_lan -v` passes with no subprocess and no real `/proc` read.
- [ ] `python -m pyflakes ctop/collectors/lan.py tests/test_lan.py` clean.
- [ ] No new dependencies; Python 3.9 syntax.
- [ ] Only the two listed files created; the file contains no code that sends packets or resolves names.
- [ ] Fixtures use only documentation IPs (`192.0.2.x`, `2001:db8::`) and MACs `02:00:00:aa:bb:cc` / `00:00:5e:00:53:xx`.
