# T05 - Ping collector (router + internet)

## 1 Purpose
`PingCollector` sends one ICMP echo every 3 s to the default gateway and to one internet address, and publishes latency, loss and jitter over the last 120 samples as `PingData`. It is the ONLY place in ctop allowed to send packets.

## 2 Files
Create `ctop/collectors/ping.py`, `tests/test_ping.py`. READ: `docs/ARCHITECTURE.md`, `ctop/models.py` (`PingResult`, `PingData`), `ctop/collectors/base.py`, and `default_route()` in `ctop/system.py` (returns `(iface, gateway)`, either may be `None`; do NOT import `ctop.system` at module top level - take it as the default of `gateway_fn` lazily, see below). Touch nothing else.

## 3 Public API
```python
from __future__ import annotations
from typing import Callable, Dict, Mapping, Optional, Tuple
from collections import deque
from ctop.collectors.base import Collector
from ctop.models import PingData, PingResult

DEFAULT_TARGET = "1.1.1.1"
HISTORY = 120

def parse_ping_output(text: str) -> Optional[float]: ...          # first "time=12.3 ms" -> 12.3, else None
def valid_target(target: str) -> bool: ...                         # see rules below
def tcp_connect_ms(target: str, port: int = 443, timeout: float = 1.0,
                   connect: Callable = None) -> Optional[float]: ...   # connect defaults to socket.create_connection
def ping_once(target: str, timeout: float = 1.0, run: Callable = None,
              have_ping: Optional[bool] = None, tcp: Callable = None) -> Optional[float]: ...
def summarize(label: str, target: str, samples) -> PingResult: ...  # pure: samples = iterable of Optional[float]

class PingCollector(Collector):
    name = "ping"
    interval = 3.0
    def __init__(self, gateway_fn: Optional[Callable[[], Tuple[Optional[str], Optional[str]]]] = None,
                 runner: Optional[Callable[[str], Optional[float]]] = None,
                 environ: Optional[Mapping[str, str]] = None) -> None: ...
    def refresh(self) -> None: ...        # self.data = PingData(router=..., internet=...)
```
`gateway_fn=None` -> use `ctop.system.default_route` (import it inside `__init__`). `runner(target) -> Optional[float]` returns the round-trip in ms or `None` for lost; default is `ping_once`. `environ=None` -> `os.environ`. Python 3.9 syntax only.

## 4 Data sources
**Command** (exactly this argv, never through a shell): `["ping", "-n", "-c", "1", "-W", "1", target]` with `subprocess.run(..., capture_output=True, text=True, timeout=2.5, stdin=subprocess.DEVNULL, env={**os.environ, "LC_ALL": "C"})`. `-n` = no DNS lookups, `-W 1` = wait 1 s (iputils, busybox and inetutils all take seconds). Replies on every Linux ping flavour (anonymised):
```
64 bytes from 192.0.2.1: icmp_seq=1 ttl=64 time=3.41 ms          (iputils / inetutils)
64 bytes from 192.0.2.1: seq=0 ttl=64 time=0.321 ms              (busybox)
--- 192.0.2.1 ping statistics ---
1 packets transmitted, 0 received, 100% packet loss, time 0ms    <- lost: NO "time=" token
From 192.0.2.5 icmp_seq=1 Destination Host Unreachable           <- lost
```
`parse_ping_output`: regex `time[=<]\s*([0-9]+(?:\.[0-9]+)?)\s*ms` (case-sensitive, first match) -> `float`; no match, empty or non-string -> `None`. The summary line `time 0ms` and `Time to live exceeded` must NOT match.

**Target safety.** `valid_target(t)`: non-empty, length <= 253, matches `^[A-Za-z0-9][A-Za-z0-9.:\-]*$` (so it can never start with `-`, which would turn it into a ping option like `-f`). Invalid `CTOP_PING_TARGET` -> silently use `DEFAULT_TARGET`.

**Environment.** Read once in `__init__`: `CTOP_PING_TARGET` (internet target, default `1.1.1.1`), `CTOP_NO_PING` (value `"1"` -> never ping the internet, `PingData.internet` stays `None`; the router is still pinged because it is on the local network).

**Fallback when ICMP is impossible** (in `ping_once`): (a) `have_ping` false (`shutil.which("ping") is None`), or `run` raises `FileNotFoundError`, or (b) the process exits non-zero and stderr/stdout contains `not permitted` or `Permission denied` (containers without `CAP_NET_RAW`) -> `tcp_connect_ms(target)`. A plain `100% packet loss` (exit 1) is a real loss and returns `None` WITHOUT trying TCP. `tcp_connect_ms` opens a TCP connection to `target:443`, returns elapsed ms (`time.perf_counter`) on success; `ConnectionRefusedError` also counts as a reply (the host answered with RST) and returns the elapsed ms; timeout or any other `OSError` -> `None`. It closes the socket immediately and sends no data. `subprocess.TimeoutExpired` / other errors -> `None`.

**Per-target state.** `self._hist: Dict[str, deque]` mapping target -> `deque(maxlen=120)` of `Optional[float]`, newest last. When the gateway address changes (Wi-Fi switched) delete the old gateway deque so a new router starts clean; the internet deque survives. Gateway `None` (no route) -> `router=None` and no router ping.

**Statistics** in `summarize` (history = the deque contents):
* `ms` = last sample (may be `None`); `history` = tuple of the samples; `loss_pct` = `100.0 * lost / len` (`0.0` when empty); `avg_ms` = mean of non-None samples or `None`; `jitter_ms` = mean absolute difference between consecutive REPLIES (lost samples removed first) or `None` with fewer than 2 replies.
Example: samples `(10.0, None, 14.0, 12.0)` -> loss 25.0, avg 12.0, jitter `(4+2)/2 = 3.0`, ms 12.0.
Labels: `"Router"`, `"Internet"`; `target` is the pinged address string.

## 5 Edge cases & distro differences
* Debian/Ubuntu: `ping` is `iputils-ping` (cap_net_raw file capability) - works unprivileged. Minimal Debian/containers may lack the binary or the capability -> the TCP fallback above (router `:443` usually answers RST, which is fine).
* Arch: iputils, same flags. Busybox/Alpine: same argv works.
* No default route / offline: router `None`; internet pings return instantly with an error text -> `None` samples (shows 100% loss, which is true).
* IPv6 targets (`CTOP_PING_TARGET=2001:db8::1`) work because modern `ping` auto-selects v6; the default gateway from `default_route` is IPv4 only.
* Hostnames in `CTOP_PING_TARGET` are allowed by `valid_target` but cause one DNS lookup per ping; document "use an IP" in a comment.
* `CTOP_NO_PING=1` is for privacy/air-gapped use: zero packets leave the LAN.

## 6 Performance & safety limits
Two sequential pings per refresh, each at most ~1 s (worst case ~2.5 s with timeout); CPU cost is the fork/exec only (~2 ms). Sequential on purpose: no threads, no pool. Exactly two destinations, ever: the gateway and one internet address; one packet each per 3 s. No reverse DNS, no scanning, no writes. Never keep a subprocess alive between refreshes.

## 7 Test plan (`tests/test_ping.py`) - never run a real ping or open a real socket
* `test_parse_iputils_reply`: `"64 bytes from 192.0.2.1: icmp_seq=1 ttl=64 time=3.41 ms\n"` -> `3.41`.
* `test_parse_busybox_reply`: `"64 bytes from 192.0.2.1: seq=0 ttl=64 time=0.321 ms"` -> `0.321`.
* `test_parse_lost_outputs`: the `100% packet loss` block, the `Destination Host Unreachable` line, `""`, and `None` -> all `None`.
* `test_summarize_stats`: `(10.0, None, 14.0, 12.0)` -> `loss_pct==25.0`, `avg_ms==12.0`, `jitter_ms==3.0`, `ms==12.0`, `history==(10.0,None,14.0,12.0)`.
* `test_summarize_empty_and_all_lost`: `()` -> loss 0.0, avg None, jitter None, ms None; `(None,None)` -> loss 100.0, avg None.
* `test_valid_target`: `"1.1.1.1"`, `"2001:db8::1"`, `"example.net"` valid; `"-f"`, `"--help"`, `""`, `"a b"`, `"1.1.1.1;ls"` invalid.
* `test_collector_pings_both_targets`: `PingCollector(gateway_fn=lambda: ("wlan0","192.0.2.1"), runner=fake, environ={})` where `fake` records targets and returns 2.0 for the gateway, 20.0 for `1.1.1.1`; after `run()`: `data.router.target=="192.0.2.1"`, label `"Router"`, `data.internet.target=="1.1.1.1"`, label `"Internet"`, `error is None`, targets recorded == `["192.0.2.1","1.1.1.1"]`.
* `test_env_target_override`: `environ={"CTOP_PING_TARGET":"198.51.100.7"}` -> internet target `198.51.100.7`; `{"CTOP_PING_TARGET":"-f"}` -> falls back to `1.1.1.1`.
* `test_no_ping_env`: `environ={"CTOP_NO_PING":"1"}` -> `data.internet is None`, router still present, fake never called with the internet target.
* `test_no_gateway`: `gateway_fn=lambda: (None, None)` -> `data.router is None`, internet present.
* `test_loss_is_recorded`: fake returns `None` three times -> `internet.loss_pct==100.0`, `ms is None`, `len(history)==3`.
* `test_history_capped_at_120`: call `refresh()` 130 times -> `len(data.internet.history)==120`.
* `test_gateway_change_resets_router_history`: gateway `192.0.2.1` for 5 refreshes then `198.51.100.1` -> router history length 1, internet history length 6.
* `test_refresh_publishes_new_object`: two refreshes give `data` objects that are not the same instance.
* `test_ping_once_uses_tcp_when_binary_missing`: `ping_once("192.0.2.1", have_ping=False, tcp=lambda t: 7.5)` -> `7.5`, and with `run=` raising `FileNotFoundError` and `have_ping=True` -> also `7.5`.
* `test_ping_once_parses_run_output`: fake `run` returning an object with `.returncode=0, .stdout="...time=3.41 ms", .stderr=""` -> `3.41`; with `returncode=1, stdout="100% packet loss"` -> `None` and the tcp fake is NOT called.
* `test_ping_once_permission_error_falls_back`: `returncode=2, stderr="ping: socket: Operation not permitted"` -> tcp fake value.
* `test_ping_once_argv`: capture the argv passed to fake `run`: equals `["ping","-n","-c","1","-W","1","192.0.2.1"]`, and `timeout` kwarg is 2.5.
* `test_tcp_connect_refused_counts_as_reply`: fake `connect` raising `ConnectionRefusedError` -> a float `>= 0`; raising `socket.timeout` -> `None`.

## 8 Done when
- [ ] `python -m unittest tests.test_ping -v` passes; the tests open no sockets and spawn no process.
- [ ] `python -m pyflakes ctop/collectors/ping.py tests/test_ping.py` clean.
- [ ] No new dependencies; Python 3.9 syntax; `from __future__ import annotations`.
- [ ] Only the two listed files created; `ctop/system.py` and `models.py` untouched.
- [ ] Only the gateway and one internet target are ever contacted.
