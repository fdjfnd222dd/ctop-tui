# T27 - Network doctor ("why is my internet slow / not working?")

## 1 Purpose
`run_doctor()` checks the chain from this computer to the internet in order - link, IP address, router, DNS, internet, captive portal -
and ends with ONE plain sentence saying where it breaks plus an action the page can offer (reconnect, switch DNS, open the portal login,
turn Wi-Fi on). Each step shows live as it runs.

## 2 Files
Create `ctop/tools/doctor.py`, `tests/test_doctor.py`. Read `docs/specs/NET-COMMON.md`, `ctop/tools/__init__.py`,
`ctop/collectors/ping.py` (`ping_once`, `tcp_connect_ms`).

## 3 Public API
```python
STEPS = (("link", "Network link"), ("address", "IP address"), ("router", "Router"), ("dns", "Name lookup (DNS)"),
         ("internet", "Internet"), ("portal", "Login page check"))
def run_doctor(net=None, nm=None, wifi=None, on_update=None, cancel=None,
               ping=None, resolve=None, tcp=None, connectivity=None, link_up=None) -> Diagnosis
```
Inputs (snapshots the app already has, any may be None): `net` dict with "iface", "ip", "gateway" (the app's `Sampler.net`);
`nm: NmState`; `wifi: WifiData`. Injected probes with real defaults:
* `ping(target) -> Optional[float]` default `ctop.collectors.ping.ping_once`
* `resolve(name) -> Optional[float]` default: `socket.getaddrinfo(name, 443)` run in a daemon thread joined with 4 s timeout; ms or None
* `tcp(host, port) -> Optional[float]` default `ctop.collectors.ping.tcp_connect_ms(host, port, timeout=2.0)`
* `connectivity() -> Optional[str]` default runs `["nmcli", "networking", "connectivity", "check"]` (timeout 10) via `run_cmd`; returns the
  stripped word ("full", "portal", "limited", "none", "unknown") or None. (This asks NetworkManager to re-check; it sends one small HTTP request
  to the distro's connectivity-check URL - the same thing the desktop does on its own.)
* `link_up(iface) -> Optional[bool]` default from `psutil.net_if_stats()`.

## 4 Steps (each: emit "running", then its result; later steps are "skip" with detail "skipped - fix the step above first" when a step fails hard)
* **link**: `nm.wifi_enabled is False` and no other default iface -> fail "Wi-Fi is turned off", suggestion "wifi-on".
  No `net["iface"]` (no default route) -> fail "Not connected to any network" (suggestion "reconnect" if nm has a saved wifi, else None).
  Else ok: "Wi-Fi ExampleNet, signal Good (-58 dBm)" when wifi has an ssid, else "<iface> is up". Wi-Fi quality Weak/Poor -> warn "weak Wi-Fi signal (-78 dBm) - move closer to the router".
* **address**: ip None -> fail "No IP address from the router (DHCP)" suggestion "reconnect"; ip starts with "169.254." -> same fail; else ok "192.0.2.23".
* **router**: gateway None -> fail "No router (default gateway) set" suggestion "reconnect". 3 pings: all None -> fail "Router is not answering" suggestion "reconnect";
  any None -> warn "Router lost 1 of 3 replies - weak or busy Wi-Fi"; avg > 50 ms -> warn "Router answers slowly (64 ms) - usually weak Wi-Fi"; else ok "3 ms".
  (A router that blocks ping but everything after works is fine: if the router fails but internet passes later, downgrade the router step to warn "Router ignores ping (normal for some)".)
* **dns**: resolve("example.com") and resolve("debian.org"): both None -> fail "Names do not resolve - DNS is broken" suggestion "dns"; best > 300 ms -> warn "Name lookups are slow (420 ms)" suggestion "dns"; else ok "18 ms".
  DNS failing does NOT skip internet (we test internet by IP).
* **internet**: tcp("1.1.1.1", 443) then tcp("9.9.9.9", 443) if the first fails: both None -> fail "No internet beyond your router" (suggestion "reconnect" only if router also failed, else None:
  "Your router is up but the internet is not - the problem is likely your provider or modem").  ms > 150 -> warn "Internet is slow to answer (210 ms)"; else ok "21 ms".
* **portal**: connectivity(): "portal" -> fail "This network wants you to log in (hotel/cafe page)" suggestion "portal"; "limited" -> warn "NetworkManager reports limited internet";
  "full" -> ok "No login page"; None/"unknown" -> skip "Could not check".
Verdict (first fail, else first warn, else ok): use the failing/warning step's detail as the verdict sentence, prefixed:
fail -> "Problem: ", warn -> "Works, but: ", all ok -> "Everything looks healthy: router {r} ms, DNS {d} ms, internet {i} ms." level = worst status. suggestion = from the verdict step.
Cancel: mark the running and remaining steps "skip", done True, verdict "Cancelled".

## 5 Tests (`tests/test_doctor.py`) - all probes injected, no network
* all good -> level ok, 6 ok steps, verdict mentions the three ms numbers.
* Wi-Fi off -> link fail, all others skip, suggestion "wifi-on".
* no ip / 169.254 -> address fail, suggestion "reconnect".
* router all lost but internet ok -> router warn "ignores ping", level warn.
* router and internet down -> internet fail with suggestion reconnect.
* router ok, internet down -> verdict mentions provider.
* dns both None -> fail suggestion "dns", internet still tested.
* portal -> suggestion "portal"; connectivity None -> portal skip and level not affected.
* weak wifi -> link warn.
* on_update sees "running" for each step before its result.
* cancel set after the router step (from inside the fake resolve) -> remaining skip, verdict "Cancelled".
