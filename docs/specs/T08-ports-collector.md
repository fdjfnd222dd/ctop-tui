# T08 - Listening ports + active connections

## 1 Purpose
`read_listeners()` and `read_connections()` turn the kernel's socket table (via `psutil.net_connections`) into the frozen `Listener` / `Connection` rows the Security page shows, with a plain "who can reach this?" exposure value. `PortsCollector` refreshes both every 5 s from ONE socket scan.

## 2 Files
Create `ctop/collectors/ports.py`, `tests/test_ports.py`. READ: `docs/ARCHITECTURE.md`, `ctop/models.py` (`Listener`, `Connection`), `ctop/collectors/base.py`. Touch nothing else.

## 3 Public API
```python
from __future__ import annotations
from typing import Callable, Iterable, Optional, Tuple
from ctop.collectors.base import Collector
from ctop.models import Connection, Listener

MAX_CONNECTIONS = 200
MAX_LISTENERS = 300

def is_private_ip(text: str) -> bool: ...                      # see rules; never raises (bad text -> False)
def exposure_of(address: str) -> str: ...                      # "local" | "network" | "specific"

class ProcessNames:                                            # pid -> process name, cached
    def __init__(self, ttl: float = 60.0, clock: Optional[Callable[[], float]] = None,
                 lookup: Optional[Callable[[int], str]] = None) -> None: ...   # lookup default: psutil.Process(pid).name()
    def __call__(self, pid: Optional[int]) -> Optional[str]: ...

def read_listeners(conns: Optional[Iterable] = None,
                   name_of: Optional[Callable[[Optional[int]], Optional[str]]] = None) -> Tuple[Listener, ...]: ...
def read_connections(conns: Optional[Iterable] = None,
                     name_of: Optional[Callable[[Optional[int]], Optional[str]]] = None) -> Tuple[Connection, ...]: ...

class PortsCollector(Collector):
    name = "ports"
    interval = 5.0
    def __init__(self, conns_fn: Optional[Callable[[], Iterable]] = None) -> None: ...
    def refresh(self) -> None: ...      # self.data = (listeners, connections)   <- a plain 2-tuple of tuples
```
`conns=None` -> `psutil.net_connections(kind="inet")` (catch `psutil.Error`/`OSError` -> empty). `name_of=None` -> a module-level shared `ProcessNames()`. `conns_fn=None` -> `lambda: psutil.net_connections(kind="inet")`. `PortsCollector.refresh` calls `conns_fn()` ONCE, converts to a list, passes it to both readers. Python 3.9 syntax only.

## 4 Data sources
`psutil.net_connections(kind="inet")` returns namedtuples `(fd, family, type, laddr, raddr, status, pid)`. It works without root on Linux; `pid` is `None` for other users' sockets. `laddr`/`raddr` are `addr(ip, port)` tuples; `raddr` is an EMPTY tuple `()` when unconnected. `type` is `socket.SOCK_STREAM` (tcp) or `socket.SOCK_DGRAM` (udp); skip any other type. `status` is `"LISTEN"` (`psutil.CONN_LISTEN`), `"ESTABLISHED"`, `"TIME_WAIT"`, ... and `"NONE"` for UDP. Tests build fakes with `collections.namedtuple("C", "fd family type laddr raddr status pid")` and `Addr = namedtuple("Addr", "ip port")`. Anonymised example rows:
```
tcp  laddr=(0.0.0.0, 8080)        raddr=()                   LISTEN       pid=4242
tcp  laddr=(::, 8080)             raddr=()                   LISTEN       pid=4242
tcp  laddr=(127.0.0.1, 631)       raddr=()                   LISTEN       pid=None
udp  laddr=(0.0.0.0, 5353)        raddr=()                   NONE         pid=4343
tcp  laddr=(192.0.2.5, 51234)     raddr=(203.0.113.9, 443)   ESTABLISHED  pid=4545
```
**Address helpers.** `norm(ip)`: strip a `%scope` suffix (`fe80::1%wlan0`), parse with `ipaddress.ip_address`; an IPv4-mapped IPv6 (`::ffff:127.0.0.1`) is unwrapped with `.ipv4_mapped`; unparsable -> treat as non-loopback/non-private.
**Exposure** (`exposure_of(address)`): loopback (`127.0.0.0/8`, `::1`) -> `"local"`; unspecified (`0.0.0.0`, `::`) -> `"network"`; anything else -> `"specific"`. The returned `Listener.address` keeps the kernel's text (`"0.0.0.0"`, `"::"`, `"127.0.0.1"`).
**Private address** (`is_private_ip`): True when inside any of `10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 169.254.0.0/16, 127.0.0.0/8, 100.64.0.0/10 (CGNAT, also Tailscale), ::1/128, fc00::/7, fe80::/10`. Do NOT use `ip.is_private` - Python counts the documentation ranges `192.0.2.0/24`, `198.51.100.0/24`, `203.0.113.0/24` as private, and 100.64/10 as not private; use an explicit list of `ipaddress.ip_network` objects and `ip in net` (match family first, `ip.version == net.version`).
**Process name** (`ProcessNames`): `None` pid -> `None`; cache `pid -> (name, expires)` for `ttl` seconds (clock injectable); on miss call `psutil.Process(pid).name()`; any `psutil.Error` -> `None` (cache that too, so a vanished pid is not retried each refresh); when the dict exceeds 2000 entries clear it.

**read_listeners.** Keep: TCP with `status == "LISTEN"`; UDP with empty `raddr` and `laddr.port != 0` (unconnected bound sockets). For each: `proto`, `address=laddr.ip`, `port=laddr.port`, `pid`, `process=name_of(pid)`, `exposure=exposure_of(laddr.ip)`.
Merge (one row per service, not per socket): group key = `(proto, port, process, addr_key)` where `addr_key` = `"*"` for `0.0.0.0` or `::`, `"lo"` for any loopback, otherwise the literal normalised address text. Within a group: `pid` = the smallest non-None pid (else `None`); `address` = the IPv4 member's text if the group has one, else the IPv6 text (so the `0.0.0.0` + `::` pair on port 8080 becomes ONE `Listener("tcp","0.0.0.0",8080,4242,"<name>","network")`). Several worker processes sharing one inherited socket (same process name) therefore collapse into one row. Rows whose `process` is `None` still merge by `(proto, port, None, addr_key)`. Sort by `(port, proto, address)`; cap `MAX_LISTENERS`.

**read_connections.** Keep rows with a non-empty `raddr` and `status != "LISTEN"`, TCP or UDP. Drop when BOTH local and remote address are loopback (use `norm`). Build `Connection(proto, local_port=laddr.port, remote_ip=raddr.ip (text as given, scope stripped), remote_port=raddr.port, status=<psutil status as is, "NONE" stays "NONE">, pid, process, remote_is_private=is_private_ip(raddr.ip))`. Sort: status rank `ESTABLISHED`=0, `SYN_SENT`/`SYN_RECV`=1, `NONE`=2, everything else (CLOSE_WAIT, FIN_WAIT*, ...)=3, `TIME_WAIT`=9; then rows with a process before `None`; then `process` text, `remote_ip`, `remote_port`. Cap at `MAX_CONNECTIONS` AFTER sorting (so the cap drops the least interesting rows).

## 5 Edge cases & distro differences
* Same code on Arch and Debian/Ubuntu; no root. Ubuntu snap/flatpak apps may report names like `firefox` or `Web Content`; names come from `psutil.Process.name()` (kernel `comm`, 15 chars max, so long names look cut - acceptable).
* Another user's or root's sockets: `pid=None`, `process=None`. systemd-resolved's `127.0.0.53:53` -> `local`. Docker/libvirt bridges show `specific` (e.g. `192.0.2.1`).
* Dual-stack: a service bound only to `::` (Linux dual-stack default) is `network` and appears once; bound only to `::1` is `local`.
* Process vanished between scan and name lookup -> `process=None`, no exception.
* Thousands of TIME_WAIT sockets (busy web server): sorted last and capped to 200.
* Connections to the router/phone on the LAN have `remote_is_private=True`; to a documentation-range or public address `False`.

## 6 Performance & safety limits
`psutil.net_connections` takes about 15-50 ms (it walks `/proc/*/fd`), so it runs once per 5 s refresh and never more; never call it from the UI thread (the collector runs on a worker thread). No subprocess, no `ss`, no DNS or whois lookups, no sending packets, read-only. Name cache keeps `psutil.Process` lookups to at most a few per refresh.

## 7 Test plan (`tests/test_ports.py`) - fake namedtuple connections only, fake `name_of`
`NAMES = {4242: "webdev", 4343: "avahi-daemon", 4545: "firefox"}.get`.
* `test_exposure_rules`: `"127.0.0.1"`, `"::1"`, `"::ffff:127.0.0.1"` -> local; `"0.0.0.0"`, `"::"` -> network; `"192.0.2.5"`, `"2001:db8::5"`, `"fe80::1%wlan0"` -> specific.
* `test_is_private_ip`: `10.1.2.3`, `172.16.0.1`, `172.31.255.1`, `192.168.1.5`, `169.254.3.3`, `100.64.0.1`, `100.127.255.1`, `fd12::1`, `fe80::1`, `::1` True; `172.32.0.1`, `100.128.0.1`, `203.0.113.9`, `198.51.100.1`, `2001:db8::1`, `"garbage"`, `""` False.
* `test_tcp_listener_dual_stack_pair_merges`: the `0.0.0.0:8080` + `:::8080` rows (same pid) -> exactly one `Listener("tcp","0.0.0.0",8080,4242,"webdev","network")`.
* `test_ipv6_only_listener_keeps_ipv6_address`: only `::` port 9000 -> one row with `address=="::"`.
* `test_loopback_and_specific_not_merged_with_wildcard`: `127.0.0.1:631` and `0.0.0.0:631` same pid -> two rows (exposure local and network).
* `test_udp_listener_only_when_unconnected`: UDP unconnected port 5353 -> listener `Listener("udp","0.0.0.0",5353,4343,"avahi-daemon","network")`; UDP with `raddr=Addr("203.0.113.9",53)` -> not a listener but a connection; UDP `port 0` -> skipped.
* `test_pid_none_gives_none_process`: row `127.0.0.1:631 pid=None` -> `pid is None`, `process is None`, exposure local.
* `test_workers_sharing_a_socket_collapse`: pids 10 and 11 (use `name_of=lambda pid: "webdev"`) on `0.0.0.0:80` -> one row with `pid==10`.
* `test_listener_sort_by_port`: ports 9000, 22, 631 -> output order 22, 631, 9000.
* `test_connections_basic_fields`: the ESTABLISHED sample -> `Connection("tcp",51234,"203.0.113.9",443,"ESTABLISHED",4545,"firefox",False)`.
* `test_connections_drop_loopback_pairs_and_listen`: loopback<->loopback ESTABLISHED, a LISTEN row, and an unconnected UDP row are all absent; a loopback->`192.0.2.5` row is kept.
* `test_connection_private_flag`: remote `192.168.1.20` -> True; `100.64.0.9` -> True; `203.0.113.9` -> False.
* `test_connection_sort_and_cap`: 250 rows (mix of ESTABLISHED and TIME_WAIT, TIME_WAIT first in input) -> `len==200`, first row ESTABLISHED, every ESTABLISHED row kept before any TIME_WAIT.
* `test_other_socket_types_ignored`: a row with `type=3` (raw) is skipped by both readers.
* `test_process_names_cache`: `ProcessNames(ttl=60, clock=fake)` with a monkeypatched/injected lookup counter (give `ProcessNames` an optional `lookup` constructor argument defaulting to the psutil call): two calls for pid 7 within 30 s -> lookup called once; call after 61 s -> twice; lookup raising -> `None` and cached.
* `test_collector_scans_once_and_returns_tuple`: `PortsCollector(conns_fn=counter)` with a fake list; after `run()` `data` is a 2-tuple `(listeners, connections)` of tuples, `conns_fn` was called exactly once, `error is None`.
* `test_empty_input`: `read_listeners([], NAMES)` and `read_connections([], NAMES)` -> `()`.

## 8 Done when
- [ ] `python -m unittest tests.test_ports -v` passes with no real sockets scanned (all inputs injected).
- [ ] `python -m pyflakes ctop/collectors/ports.py tests/test_ports.py` clean.
- [ ] No new dependencies (psutil + stdlib `ipaddress`); Python 3.9 syntax.
- [ ] Only the two listed files created.
- [ ] No real IPs, hostnames or process names from a developer machine in fixtures.
