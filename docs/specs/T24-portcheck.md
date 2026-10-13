# T24 - Port check tool (only the ports the user picked)

## 1 Purpose
`run_ports(target, ports)` tells, for each port the user selected (max 32), whether `target` accepts a TCP connection on it:
**open** (handshake completed - the socket is closed at once and no data is sent), **closed** (the host answered "no", RST),
**filtered** (no answer within the timeout - usually a firewall), or **error** (unreachable network/host).
This is a user-initiated reachability check of a handful of ports, never a sweep: the 32-port cap is enforced here.

## 2 Files
Create `ctop/tools/portcheck.py`, `tests/test_portcheck.py`. Read `docs/specs/NET-COMMON.md`, `ctop/tools/__init__.py`.
For name resolution reuse `resolve_target` from `ctop.tools.trace` ONLY lazily inside `run_ports` (T23 is built in parallel):
`from ctop.tools.trace import resolve_target` inside a try; on ImportError fall back to a private copy (prefer AF_INET, validate with
`ctop.collectors.ping.valid_target`). Tests must pass whether or not trace.py exists: always inject `resolver` in tests.

## 3 Public API
```python
MAX_PORTS = 32
WORKERS = 8
SERVICES = {21: "FTP", 22: "SSH", 23: "Telnet", 25: "SMTP", 53: "DNS", 80: "HTTP", 110: "POP3", 123: "NTP", 139: "NetBIOS",
            143: "IMAP", 443: "HTTPS", 445: "SMB (file sharing)", 465: "SMTPS", 548: "AFP", 554: "RTSP (cameras)", 587: "Mail submit",
            631: "IPP (printer)", 853: "DNS over TLS", 993: "IMAPS", 995: "POP3S", 1883: "MQTT", 3306: "MySQL", 3389: "Remote Desktop",
            5000: "UPnP / dev server", 5353: "mDNS", 5432: "PostgreSQL", 5900: "VNC", 6379: "Redis", 8008: "Chromecast", 8009: "Chromecast",
            8080: "HTTP alt", 8443: "HTTPS alt", 8888: "HTTP alt", 9100: "Printer (raw)", 32400: "Plex", 51820: "WireGuard"}
PRESETS = (("Common", (22, 53, 80, 443, 445, 3389, 8080)),
           ("Web", (80, 443, 8080, 8443)),
           ("Remote access", (22, 23, 3389, 5900)),
           ("File sharing", (21, 139, 445, 548)),
           ("Home devices", (554, 631, 8008, 8009, 9100, 1883)))
def service_name(port: int) -> str            # SERVICES.get(port, "")
def parse_ports(text: str) -> Tuple[Tuple[int, ...], Optional[str]]
    # "22, 80 443 8000-8003" -> ((22, 80, 443, 8000, 8001, 8002, 8003), None); sorted unique.
    # errors (return ((), "plain reason")): empty; non-number; port outside 1..65535; range start > end; more than MAX_PORTS ports
    # ("At most 32 ports at a time"). Separators: commas and/or whitespace.
def tcp_probe(address: str, port: int, timeout: float = 1.5, family: int = socket.AF_INET, clock=time.perf_counter) -> Tuple[str, Optional[float]]
    # create_connection-like: socket(family, SOCK_STREAM), settimeout, connect_ex/connect; close immediately.
    # success -> ("open", ms); ConnectionRefusedError / errno ECONNREFUSED -> ("closed", ms); socket.timeout -> ("filtered", None);
    # EHOSTUNREACH/ENETUNREACH/other OSError -> ("error", None)
def run_ports(target, ports, timeout=1.5, on_update=None, cancel=None, resolver=None, probe=None) -> PortCheckResult
```

## 4 run_ports
1. Validate count (more than MAX_PORTS or empty -> result with error, done). Resolve the target (`resolver` injected or the trace one) ->
   (address, family); failure -> error result.
2. Emit a result with every port `PortResult(port, service_name(port), "pending")`.
3. `concurrent.futures.ThreadPoolExecutor(max_workers=min(WORKERS, len(ports)))` running `probe(address, port, timeout, family)`
   (default `tcp_probe`). As each finishes replace that port's entry and emit (ports keep their sorted order).
   If `cancel` is set: stop submitting/collecting, mark unfinished ports "pending" -> leave as is, set error "cancelled", `shutdown(wait=False, cancel_futures=...)`
   is 3.9+ - fine.
4. Final result `done=True`.

## 5 Tests (`tests/test_portcheck.py`)
* parse_ports: mixed separators and a range; duplicates removed; 0, 65536, "abc", "9-3", 33 ports, "" -> errors with reasons.
* service_name 443 "HTTPS", 12345 "".
* run_ports with fake probe {22: closed, 80: open, 443: open, 3389: filtered} and fake resolver -> states, order, done, on_update first emits all pending.
* too many ports -> error, probe never called. Resolver failure -> error.
* cancel set before start -> error "cancelled", done.
* tcp_probe with a fake socket factory? Keep tcp_probe testable: add `sock_factory=socket.socket` param; fake whose `connect_ex` returns 0 / errno.ECONNREFUSED,
  or whose `connect_ex` raises `socket.timeout` -> right states; the fake records `close()` was called; `send`/`sendall` are never called.
