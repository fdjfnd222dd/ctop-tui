# T28 - Device finder (opt-in sweep of your own home network)

## 1 Purpose
The Security page lists only devices this computer already talked to (passive). `run_lanscan()` is the opt-in, one-key version:
it sends ONE tiny UDP packet to every address of your own local subnet (max 254 addresses), which makes the kernel ask "who has this
address?" (ARP) for each; then it reads the kernel's neighbour table to list every device that answered. No root, no raw sockets,
no port scanning. It refuses anything that is not a private home-sized network.

## 2 Files
Create `ctop/tools/lanscan.py`, `tests/test_lanscan.py`. Read `docs/specs/NET-COMMON.md`, `ctop/tools/__init__.py`,
`ctop/collectors/lan.py` (`read_devices`, `is_random_mac`), `ctop/system.py` (`default_route`).

## 3 Public API
```python
MAX_HOSTS = 254
SEND_GAP = 0.004        # seconds between packets: 254 packets take ~1 s, gentle on cheap routers
SETTLE = 2.0            # seconds to let ARP replies arrive after the last packet
DISCARD_PORT = 9        # the "discard" service; nothing is expected to answer, the ARP lookup is the point
def scan_subnet(ip: str, netmask: str) -> Tuple[Optional[ipaddress.IPv4Network], Optional[str]]
    # (network, None) or (None, reason). Rules: IPv4 only; the network from ip/netmask; if its prefix is shorter than /24 use the /24
    # that contains ip (cap 254 hosts); refuse unless ip is in 10/8, 172.16/12, 192.168/16 or 100.64/10 -> "Only private home networks can be scanned".
    # Doc ranges 192.0.2.0/24, 198.51.100.0/24, 203.0.113.0/24 are ALSO accepted (so tests and demo can use them).
def run_lanscan(on_update=None, cancel=None, route=None, addrs=None, send=None, neigh=None, sleep=None, own_mac=None) -> LanScanResult
```
Injected (real defaults): `route() -> (iface, gateway)` = `ctop.system.default_route`; `addrs()` = `psutil.net_if_addrs()`;
`send(ip)` = one shared non-blocking `socket(AF_INET, SOCK_DGRAM)` doing `sendto(b"", (ip, DISCARD_PORT))`, OSError ignored, closed at the end;
`neigh(gateway) -> Tuple[Device, ...]` = `ctop.collectors.lan.read_devices(gateway_ip=gateway)`; `sleep(s)` = `cancel.wait(s)` if cancel else `time.sleep(s)`;
`own_mac(iface)` = read `/sys/class/net/<iface>/address`.

## 4 run_lanscan
1. route -> iface None -> error "Not connected to a network". Find the iface's AF_INET address + netmask in `addrs()`; missing -> error "No IPv4 address on <iface>".
2. `scan_subnet` -> error reason if refused. Emit `LanScanResult(subnet=str(net), total=hosts, probed=0)`.
3. For each host address except our own: `send(str(host))`, `sleep(SEND_GAP)`; every 16 hosts emit progress (`probed`). Cancel -> stop sending, go to step 4 anyway (show what we have) with error "cancelled".
4. `sleep(SETTLE)` (unless cancelled), then `neigh(gateway)`: keep devices whose ip is inside the subnet; build `FoundDevice(ip, mac, random_mac, is_gateway)`.
   Add ourselves: `FoundDevice(own_ip, own_mac(iface), is_random_mac(mac), False, is_me=True)`. Sort: gateway, me, then numeric ip.
   Emit final `done=True`, `probed=total`.

## 5 Tests (`tests/test_lanscan.py`)
* scan_subnet: 192.168.1.23/255.255.255.0 -> /24; 10.1.2.3/255.255.0.0 -> 10.1.2.0/24; 203.0.113.9/255.255.255.0 accepted; 8.8.8.8/255.255.255.0 refused;
  192.168.1.5/255.255.255.252 -> /30 (2 hosts).
* run_lanscan with fakes (iface wlan0, 192.0.2.23/24, gateway 192.0.2.1; neigh returns gateway, two devices in subnet and one outside) -> send called 253 times (not for own ip),
  devices: gateway first, me second, outside device dropped; progress emits; sleep never really sleeps.
* no route -> error; refused subnet -> error, send never called; cancel after 20 sends -> error "cancelled", done True, fewer than 253 sends.
