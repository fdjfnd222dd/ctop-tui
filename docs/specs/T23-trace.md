# T23 - Traceroute tool (no root, built in)

## 1 Purpose
`run_trace(target)` finds every router between this computer and `target` with round-trip times, without root and without
installing anything. It uses the same trick as `tracepath`: plain UDP sockets with a small TTL and Linux's `IP_RECVERR` error
queue, which hands an unprivileged program the ICMP "time exceeded" reply and the address of the router that sent it.
All hops are probed at once (one socket per TTL), so a whole trace takes ~2 s per round instead of ~30 s.
Fallback: the `tracepath` command when the built-in way is impossible.

## 2 Files
Create `ctop/tools/trace.py`, `tests/test_trace.py`. Read `docs/specs/NET-COMMON.md` and `ctop/tools/__init__.py`.
Reuse `valid_target` from `ctop.collectors.ping`.

## 3 Public API
```python
MAX_HOPS = 30
PROBES = 3                      # rounds; each round probes every TTL once
BASE_PORT = 33434
ROUND_WAIT = 1.5                # seconds to wait for answers after the last probe of a round
SEND_GAP = 0.015                # seconds between probes (routers rate-limit ICMP; a burst shows false "* * *")

def classify(ip: str, target_ip: Optional[str] = None) -> str
    # "target" if ip == target_ip; "home" for 10/8, 172.16/12, 192.168/16, 169.254/16, fc00::/7, fe80::/10;
    # "isp" for 100.64.0.0/10; "internet" for any other valid address; "unknown" for None/invalid
def resolve_target(target: str, resolver=None) -> Tuple[str, int]
    # (ip, family). resolver default socket.getaddrinfo(target, None, 0, socket.SOCK_DGRAM). Prefer AF_INET results.
    # raises ValueError with plain text: "Not a valid address or name" (valid_target fails), "Could not find that name" (gaierror/empty)
def parse_recverr(cmsg_level: int, cmsg_type: int, data: bytes, family: int) -> Optional[Tuple[str, str]]
    # -> (offender_ip, "hop" | "reached" | "unreachable") or None if not an ICMP error record
def parse_tracepath(text: str) -> List[Tuple[int, Optional[str], Optional[float], bool]]   # (ttl, ip, ms, reached)
def run_trace(target, max_hops=MAX_HOPS, probes=PROBES, on_update=None, cancel=None,
              resolver=None, prober_factory=None, tracepath_runner=None, clock=None) -> TraceResult
```

## 4 How the built-in prober works (Linux)
Constants (define them with fallbacks, Python's socket module lacks most):
`IP_RECVERR = getattr(socket, "IP_RECVERR", 11)`, `IPV6_RECVERR = getattr(socket, "IPV6_RECVERR", 25)`,
`IP_TTL = socket.IP_TTL` (2), `IPV6_UNICAST_HOPS = getattr(socket, "IPV6_UNICAST_HOPS", 16)`, `MSG_ERRQUEUE = getattr(socket, "MSG_ERRQUEUE", 0x2000)`,
`SOL_IP = 0`, `SOL_IPV6 = 41`, `SO_EE_ORIGIN_ICMP = 2`, `SO_EE_ORIGIN_ICMP6 = 3`.

`class UdpProber` (the real one; `prober_factory(family)` returns one):
* `send(ttl: int, port: int) -> int` : new `socket(family, SOCK_DGRAM)`, set non-blocking, set TTL (`SOL_IP, IP_TTL` or `SOL_IPV6, IPV6_UNICAST_HOPS`),
  enable RECVERR (`SOL_IP, IP_RECVERR, 1` or `SOL_IPV6, IPV6_RECVERR, 1`), `connect((addr, port))`, `send(b"ctop-trace")`, remember
  `fd -> (ttl, sent_at)`, register the fd in a `select.poll()` for `POLLIN | POLLERR`. Returns the ttl.
* `wait(timeout: float) -> List[Tuple[int, Optional[str], str, float]]` : one `poll(timeout_ms)`; for every ready fd read the error
  queue with `sock.recvmsg(256, 512, MSG_ERRQUEUE)` (catch `BlockingIOError`/`OSError` -> skip; an `ECONNREFUSED` raised by a plain
  `recv` means reached with unknown offender = the target). For each ancillary record call `parse_recverr`; append
  `(ttl, ip, kind, (now - sent_at) * 1000)`; then close and unregister that socket (one answer per probe).
* `close()` closes every remaining socket. Must be called in a `finally`.
* The constructor takes `(family, addr)`; creating the first socket or setting RECVERR may raise `OSError` -> the caller falls back.

`parse_recverr`: accept only (SOL_IP, IP_RECVERR) for AF_INET and (SOL_IPV6, IPV6_RECVERR) for AF_INET6, and `len(data) >= 16`.
`ee_errno, ee_origin, ee_type, ee_code, _pad, ee_info, ee_data = struct.unpack_from("=IBBBBII", data, 0)` (16 bytes).
The offender address follows at offset 16: IPv4 `sockaddr_in` -> address bytes `data[20:24]`; IPv6 `sockaddr_in6` -> `data[24:40]`
(use `socket.inet_ntop`). If the offender family field (`struct.unpack_from("=H", data, 16)`) is 0 -> ip None.
IPv4 (origin 2): type 11 -> "hop"; type 3 code 3 (port unreachable) -> "reached"; type 3 other codes -> "unreachable".
IPv6 (origin 3): type 3 -> "hop"; type 1 code 4 -> "reached"; type 1 other -> "unreachable". Other origins -> None.

## 5 run_trace algorithm
1. `resolve_target` (ValueError -> return `TraceResult(target, error=str(e), done=True)` via emit).
2. Emit `TraceResult(target, address, hops=(), method="udp")` right away so the UI shows "tracing 203.0.113.5 ...".
3. Make the prober; if it raises `OSError` -> tracepath fallback (section 6).
4. For round r in 0..probes-1: limit = known reached ttl (if found) else max_hops. For ttl 1..limit: `send(ttl, BASE_PORT + r * max_hops + ttl)`,
   then `wait(SEND_GAP)` (collect early answers while sending; this also paces probes). Then keep calling `wait(0.1)` until
   `ROUND_WAIT` seconds have passed since the last send, every probe of the round answered, or cancelled.
   Record answers into per-ttl lists (index r); a probe with no answer is None. When a "reached" answer arrives at ttl T, the
   destination is T: remember the smallest such T. Emit an updated result whenever new answers came in (max ~10/s).
   `prober.close()` after each round (finally).
5. Build hops for ttl 1..end where end = reached ttl, else (last ttl that had any reply) + 1 (one "no answer" hop shows where it stopped),
   capped at max_hops. hop.ip = most common replying ip for that ttl; kind via `classify(ip, address)`; reached flag for ttl == reached.
   "unreachable" answers count as replies for that ttl and end the trace at that ttl (the route is blocked there).
6. Final: `done=True`, `reached=bool(reached ttl)`. Cancelled -> `done=True`, `error="cancelled"` keeping hops so far.

## 6 tracepath fallback
`tracepath_runner(argv, timeout) -> Optional[str]`, default `ctop.collectors.wifi.run_cmd`. argv `["tracepath", "-n", "-m", str(max_hops), address]`
(add `"-6"` for IPv6 is NOT needed; modern tracepath picks). timeout 40. Output lines:
```
 1?: [LOCALHOST]                      pmtu 1500
 1:  192.0.2.1                                             3.123ms
 2:  no reply
 3:  198.51.100.9                                         12.400ms asymm  4
 9:  203.0.113.5                                          24.100ms reached
     Resume: pmtu 1500 hops 9 back 9
```
Skip `[LOCALHOST]` and `Resume`. Duplicate ttl lines: keep the first reply. rtts = (ms,) one probe. method="tracepath".
None output -> `error="Tracing needs Linux UDP error reporting or the tracepath tool"`.

## 7 Tests (`tests/test_trace.py`) - no real sockets
* classify: 192.168.1.1 home, 10.0.0.1 home, 100.64.3.1 isp, 203.0.113.5 internet, target match, None unknown.
* resolve_target: "-f" -> ValueError; fake resolver raising `socket.gaierror` -> "Could not find"; mixed v6/v4 results -> picks v4.
* parse_recverr: build bytes with `struct.pack("=IBBBBII", 113, 2, 11, 0, 0, 0, 0) + struct.pack("=HH4s8x", socket.AF_INET, 0, socket.inet_aton("198.51.100.9"))`
  -> ("198.51.100.9", "hop"); type 3 code 3 -> reached; IPv6 type 3 with sockaddr_in6 `struct.pack("=HHI16sI", AF_INET6, 0, 0, inet_pton(...), 0)` -> hop;
  wrong level -> None; short data -> None.
* run_trace with a FakeProber (send records, wait returns scripted answers: ttl1 192.0.2.1 hop 2 ms, ttl2 none, ttl3 198.51.100.9 hop, ttl4 203.0.113.5 reached):
  hops 1..4, hop 2 ip None with all rtts None, hop 4 reached and kind "target", result reached True, method "udp", rtts length == probes,
  later rounds only probe up to ttl 4, on_update called several times with increasing hop info, done True at end.
  Use an injected clock that advances per call so ROUND_WAIT ends without sleeping.
* prober_factory raising OSError -> tracepath fallback with fake runner and the sample above -> hops parsed, reached at 9, method "tracepath".
* cancel set before start -> returns quickly with error "cancelled".
* never more than max_hops hops; unreachable answer ends the trace.
