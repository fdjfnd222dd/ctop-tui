# T31 - Demo data for the Network page (`ctop --demo`)

## 1 Purpose
`ctop --demo` must show a convincing Network page with made-up data, read nothing from the machine, change nothing and send no packet.
This module supplies fake collectors, a fake `netctl` and fake tools with the same signatures as the real ones.

## 2 Files
Create `ctop/demo_net.py`, `tests/test_demo_net.py`. Read `ctop/demo.py` (style, documentation addresses), `docs/specs/NET-COMMON.md`,
the models, `ctop/netctl.py` (T19, for `Outcome` and the function names/signatures), and the tool modules in `ctop/tools/` (for each `run_*` signature).
All of those already exist when you start.

## 3 Public API
```python
STEP = 0.15                      # seconds between fake progress updates; tests set it to 0
def make_demo_net_collectors() -> Dict[str, Collector]   # keys "nmstate", "wifilink", "vpn" -> Collector subclasses with refresh() publishing models
class DemoNetctl:                # same public function names and parameters as ctop.netctl; every one returns netctl.Outcome(True, "Demo mode: pretended to <what> - nothing was changed.")
    DNS_PRESETS = netctl.DNS_PRESETS
    def get_dns(self, uuid, run=None): return (("192.0.2.1",), False)
    # plus: valid_password, parse_dns_list delegate to netctl (pure functions)
def demo_trace(target, max_hops=30, probes=3, on_update=None, cancel=None, **kw) -> TraceResult
def demo_ports(target, ports, timeout=1.5, on_update=None, cancel=None, **kw) -> PortCheckResult
def demo_dns(gateway=None, on_update=None, cancel=None, **kw) -> DnsReport
def demo_speed(on_update=None, cancel=None, **kw) -> SpeedResult
def demo_doctor(net=None, nm=None, wifi=None, on_update=None, cancel=None, **kw) -> Diagnosis
def demo_lanscan(on_update=None, cancel=None, **kw) -> LanScanResult
```
Fake tools emit several progressive snapshots, pacing with `_pause()` = `cancel.wait(STEP)` if cancel else `time.sleep(STEP)` (skip when STEP == 0),
and honour cancel exactly like the real ones (TraceResult error "cancelled", SpeedResult stage "cancelled", ...).

## 4 Data (documentation ranges only; SSIDs: ExampleNet, ExampleNet-Guest, Neighbour Net, Cafe: Free, Library WiFi, PrinterSetup-01, "" hidden)
* nmstate: available, wifi on, connectivity "full", wifi_device "wlan0", ~9 networks across 2.4/5 GHz incl. one open, one enterprise ("802.1X"),
  ExampleNet in use on 5 GHz ch 36 with aps 2 and ALSO a 2.4 GHz entry? (no - networks are merged by SSID; give ExampleNet-Guest on 2.4). Signals wobble a few % per refresh.
  saved: ExampleNet (active, wifi), Neighbour Net (wifi, last used 2 days ago), Office VPN (vpn, manual), Phone hotspot (wifi, priority 5). UUIDs valid fake format.
* wifilink: ExampleNet link 5 GHz ch 36, 80 MHz, Wi-Fi 6, -52 dBm wobbling, rx 960 / tx 720, bssid "02:00:00:AA:BB:01", random_mac True, mtu 1500, tx power 20.
* vpn: tailscale "Running", ip 100.64.0.7 is fine in code (not docs), tailnet "example.ts.net", 4 peers (2 online, one exit-node option off), tunnels tailscale0 up.
* demo_trace: 8 hops: 192.0.2.1 (home) -> 100.64.0.1 (isp) -> one no-answer hop -> 198.51.100.9/.17/.33 (internet) -> 203.0.113.5 target reached; ~2-40 ms rising; one hop 33% loss.
  Target "example.org" -> address 203.0.113.5; any IP typed is used as its own address.
* demo_ports: 22 closed, 80 open, 443 open, everything else filtered, ms ~10-30. demo_dns: router 192.0.2.1 ~38 ms in use, Cloudflare 12, Quad9 15, Google 18, verdict as the real rules give.
* demo_speed: idle 14 ms, loaded 48 ms (grade B), down 245 Mbps, up 38 Mbps, bytes_used ~31 MB, samples rising then flat.
* demo_doctor: all ok except portal "full"; verdict healthy. demo_lanscan: 192.0.2.0/24, 7 devices incl. router, this computer, 2 private MACs.

## 5 Tests (`tests/test_demo_net.py`) - set `demo_net.STEP = 0` in setUp
* every collector refresh() publishes the right model type; nmstate has an in-use network and an active saved profile.
* DemoNetctl has every public function of ctop.netctl (compare `dir()` of callables not starting with "_") and they return ok Outcomes mentioning "Demo".
* every demo tool returns done=True (or stage "done") with plausible fields; on_update called more than once; cancel set before start -> cancelled result.
* no socket is opened: patch `socket.socket` to raise during the tests.
* no address outside documentation ranges, 127.x, 100.64.x appears in any string the module produces (scan `repr` of results).
