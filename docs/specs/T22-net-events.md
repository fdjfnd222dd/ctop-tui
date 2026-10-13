# T22 - Network event log ("why did my internet just drop?")

## 1 Purpose
`EventLog` watches the snapshots ctop already has (Wi-Fi link, ping, default route / IP, NetworkManager state, VPN) once per UI tick and
records CHANGES as short timestamped `NetEvent`s: drops, reconnects, roaming to another access point, IP or gateway changes, ping spikes
and packet loss starting/ending, captive portal, VPN up/down. Kept in memory only (max 200), never written to disk.

## 2 Files
Create `ctop/netevents.py`, `tests/test_netevents.py`. Read `docs/specs/NET-COMMON.md`. Pure Python, no I/O, no threads.

## 3 Public API
```python
class EventLog:
    MAX = 200
    def __init__(self): ...
    def observe(self, now: float, wifi=None, link=None, ping=None, net=None, nm=None, vpn=None) -> None: ...
    @property
    def events(self) -> Tuple[NetEvent, ...]: ...   # newest LAST, a new tuple only when something was added (cache it)
    def add(self, now: float, level: str, text: str) -> None: ...   # also used by the app for "You connected to X" style action results
```
Arguments are the latest snapshots, each may be None: `wifi: WifiData`, `link: LinkInfo`, `ping: PingData`,
`net: dict` (the app's `Sampler.net`: keys "iface", "ip", "gateway"), `nm: NmState`, `vpn: VpnStatus`.
A None argument means "no new information" -> do not compare that source this time (do NOT log a drop because a collector is not ready).

## 4 Rules (each compares with the previous non-None value of that source; the very first observation only records state, no events)
| Change | Level | Text |
|---|---|---|
| wifi.ssid None -> "X" | info | `Connected to X` |
| wifi.ssid "X" -> None | warn | `Wi-Fi dropped (was X)` |
| wifi.ssid "X" -> "Y" | info | `Switched Wi-Fi from X to Y` |
| link.bssid A -> B (both not None, same ssid) | info | `Roamed to another access point (5 GHz, channel 36)` using link.band/channel |
| link.band changes while bssid same | info | `Moved to <band>` |
| wifi.quality drops into "Weak"/"Poor" from better | warn | `Wi-Fi signal became weak (-78 dBm)` (dbm if known) ; back to Fair+ -> info `Wi-Fi signal recovered` |
| net["ip"] A -> B (both not None) | info | `IP address changed: A -> B` |
| net["ip"] None -> A | info | `Got an IP address: A` |
| net["ip"] A -> None | warn | `Lost the IP address` |
| net["gateway"] A -> B (both not None) | info | `Router changed: A -> B` |
| ping.internet: loss episode | warn on start, info on end | start when the last 3 samples of `history` are all None and the previous observation was not in an episode: `Internet stopped answering`; end when the latest sample is a reply: `Internet back after 42 s` (seconds since start) |
| ping.internet spike | warn | latest ms > max(150, 4 x avg_ms) and avg_ms not None: `Lag spike: 480 ms to the internet (normally 21 ms)`; at most one spike event per 60 s |
| ping.router loss episode | warn/info | same as internet, texts `Router stopped answering` / `Router back after N s` |
| nm.connectivity changes to "portal" | warn | `Captive portal: this network wants a login page` |
| nm.connectivity "full" -> "limited"/"none" | warn | `Internet access lost (connected to the network, but no internet)` ; back to "full" -> info `Internet access is back` |
| nm.wifi_enabled True -> False | info | `Wi-Fi turned off` ; False -> True info `Wi-Fi turned on` |
| vpn.tailscale "Running" <-> other | info | `Tailscale connected` / `Tailscale disconnected` |
| a VpnTunnel appears up / goes down or disappears | info | `VPN wg0 up` / `VPN wg0 down` |
Debounce: the same text is not logged twice within 10 s. Times are passed in (`now`), never read from the clock.

## 5 Tests (`tests/test_netevents.py`)
One test per table row (build the dataclasses from `ctop/models.py` directly), plus:
* first observation logs nothing; None arguments never log; MAX cap keeps the newest 200; `events` returns the same tuple object
  when nothing changed; debounce; spike rate limit (two spikes 30 s apart -> one event, 61 s apart -> two); loss episode end text
  shows the elapsed seconds.
