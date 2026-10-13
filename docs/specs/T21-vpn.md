# T21 - VPN / Tailscale status collector

## 1 Purpose
`VpnCollector` publishes a `VpnStatus`: is Tailscale installed and running (and which peers are online), plus every VPN-like
tunnel interface that exists (WireGuard `wg*`, Tailscale `tailscale*`, OpenVPN/other `tun*`, `tap*`, `ppp*`). Read-only.
(NetworkManager VPN profiles come from T18's saved list; turning things on/off is T19.)

## 2 Files
Create `ctop/collectors/vpn.py`, `tests/test_vpn.py`. Read `docs/specs/NET-COMMON.md`. Reuse `run_cmd` from `ctop.collectors.wifi`.

## 3 Public API
```python
TAILSCALE = ["tailscale", "status", "--json"]
def parse_tailscale(text: str) -> Tuple[Optional[str], Optional[str], Optional[str], Tuple[VpnPeer, ...]]
    # (backend_state, self_ip, tailnet_name, peers)
def tunnels(addrs=None, stats=None) -> Tuple[VpnTunnel, ...]   # defaults psutil.net_if_addrs(), psutil.net_if_stats()

class VpnCollector(Collector):
    name = "vpn"
    interval = 15.0
    def __init__(self, runner=None, which=shutil.which, addrs_fn=None, stats_fn=None): ...
    def refresh(self) -> None: ...
```

## 4 Sources
* `which("tailscale")` is None -> `tailscale=None` and no command run. Else `runner(TAILSCALE, 3.0)`; None -> tailscale `"unknown"`.
* JSON keys used (all optional, never KeyError): `BackendState` (e.g. "Running", "Stopped", "NeedsLogin", "Starting"),
  `Self.TailscaleIPs[0]`, `CurrentTailnet.Name` (or `MagicDNSSuffix` when no CurrentTailnet), `Peer` (dict id -> peer) with
  `HostName`, `DNSName`, `TailscaleIPs`, `OS`, `Online`, `ExitNode`. Peer name = HostName, else first label of DNSName, else "?".
  Peer ip = first of TailscaleIPs that has no ":" (prefer IPv4), else first, else None. Bad JSON -> ("unknown", None, None, ()).
  Peers sorted: online first, then name (case-insensitive). Cap 50.
* tunnels: interface names matching `^(wg|tailscale|tun|tap|ppp|proton|nordlynx|mullvad)` ; kind: `wg`/`nordlynx`/`mullvad` -> "WireGuard",
  `tailscale` -> "Tailscale", `tun`/`tap` -> "OpenVPN/tun", else "VPN". up = stats[name].isup; ip = first AF_INET address, else first AF_INET6
  without "%". Sorted by name.

## 5 Tests (`tests/test_vpn.py`)
* parse_tailscale on a small JSON (Running, self ip 100.64.0.1, 3 peers: one online exit node with v4+v6 ips, one offline, one with DNSName only)
  -> ordering, names, ip choice, exit_node flag, tailnet name.
* "Stopped" JSON with no Peer key -> peers ().
* bad JSON -> "unknown".
* collector: which returns None -> tailscale None and runner never called; runner None -> "unknown".
* tunnels with fake addrs/stats objects (use `types.SimpleNamespace(family=socket.AF_INET, address=...)`, `SimpleNamespace(isup=True)`):
  wg0 up with ip, tailscale0 down, eth0 ignored, kinds right.
