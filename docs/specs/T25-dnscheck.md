# T25 - DNS timing tool

## 1 Purpose
`run_dns()` measures how fast each DNS server this computer uses answers, next to three well-known public ones, and says in one
sentence whether DNS is the reason pages feel slow. Pure Python DNS over UDP (no `dig` needed).

## 2 Files
Create `ctop/tools/dnscheck.py`, `tests/test_dnscheck.py`. Read `docs/specs/NET-COMMON.md`, `ctop/tools/__init__.py`. Reuse `run_cmd` from `ctop.collectors.wifi`.

## 3 Public API
```python
PUBLIC = (("1.1.1.1", "Cloudflare"), ("9.9.9.9", "Quad9"), ("8.8.8.8", "Google"))
KNOWN = {"1.1.1.1": "Cloudflare", "1.0.0.1": "Cloudflare", "9.9.9.9": "Quad9", "149.112.112.112": "Quad9",
         "8.8.8.8": "Google", "8.8.4.4": "Google"}
NAMES = ("example.com", "wikipedia.org", "debian.org")     # one query per name = 3 tries per server
def build_query(name: str, qid: int) -> bytes             # header id, flags 0x0100 (RD), qdcount 1; QNAME labels; QTYPE A (1), QCLASS IN (1)
def parse_reply(data: bytes, qid: int) -> Optional[int]   # rcode if id matches and QR bit set, else None
def read_resolv(text: str) -> List[str]                   # "nameserver X" lines, order kept, strip "%iface"? keep as is
def parse_resolvectl(text: str) -> List[str]              # `resolvectl dns`: "Global: 192.0.2.1" / "Link 3 (wlan0): 192.0.2.1 2001:db8::1"
def parse_nmcli_dns(text: str) -> List[str]               # `nmcli -t -f IP4.DNS,IP6.DNS device show`: "IP4.DNS[1]:192.0.2.1"
def servers_in_use(resolv_path="/etc/resolv.conf", runner=None) -> List[str]
def label_for(server: str, gateway: Optional[str]) -> str # "your router" | KNOWN name | "local cache" (127.x, ::1) | "configured"
def query_once(server: str, name: str, timeout=1.0, sock_factory=None, clock=time.perf_counter) -> Optional[float]   # ms or None
def run_dns(gateway=None, on_update=None, cancel=None, servers=None, query=None) -> DnsReport
```

## 4 Details
* `servers_in_use`: read resolv.conf nameservers. If the list is only stub addresses (127.0.0.53 / 127.0.0.54 = systemd-resolved)
  -> try `runner(["resolvectl", "dns"], 3)` and use its addresses (Global first, then links); if that fails try
  `runner(["nmcli", "-t", "-f", "IP4.DNS,IP6.DNS", "device", "show"], 3)`. Keep other 127.x (dnsmasq) as "local cache".
  Dedupe preserving order, drop invalid IPs (validate with `ipaddress`; strip a `%zone` suffix before validating), max 4.
* `query_once`: UDP socket of the right family (`ipaddress.ip_address(server).version`), settimeout, `sendto(query, (server, 53))`,
  `recvfrom(512)` until a reply with the matching id arrives or the timeout passes; rcode 0 or 3 (NXDOMAIN still proves the server works) -> ms.
  qid from `int.from_bytes(os.urandom(2), "big")`. Close the socket always. Any OSError -> None.
* `run_dns`: `servers` = in-use list (default `servers_in_use()`) followed by PUBLIC ones not already in it. Rows: `DnsTiming(server, label, in_use)`.
  Emit the initial rows (ms None, tries 0). Query servers concurrently (one thread per server, max 7), names sequentially per server;
  after each answer update that row (answered, tries, ms = median of answered) and emit. Cancel checked between queries.
  error "no answer" when all tries failed.
* verdict (done): best_in_use = fastest in-use with answers; best_public = fastest public with answers.
  - no in-use servers found -> "Could not find which DNS server this computer uses."
  - every in-use server failed -> "Your DNS server is not answering - that alone breaks browsing. Switching DNS should fix it."
  - best_in_use.ms > 2 * best_public.ms and the difference > 40 ms -> "Your DNS takes {a:.0f} ms; {label} takes {b:.0f} ms. Switching DNS would make pages start faster."
  - else -> "Your DNS is fine ({a:.0f} ms)."
  - no public answered but in-use did -> "Your DNS works ({a:.0f} ms); public servers did not answer (blocked network?)."

## 5 Tests (`tests/test_dnscheck.py`)
* build_query("example.com", 0x1234) exact bytes: header `12 34 01 00 00 01 00 00 00 00 00 00`, then `07 example 03 com 00`, `00 01 00 01`.
* parse_reply: matching id + QR -> rcode; wrong id -> None; no QR bit -> None; short data -> None.
* read_resolv with comments and `options`; parse_resolvectl sample with Global and two links; parse_nmcli_dns sample.
* servers_in_use: stub-only resolv + resolvectl answer -> real servers; stub + resolvectl failing + nmcli answer -> nmcli servers; `fe80::1%wlan0` accepted; garbage dropped.
* label_for: gateway match "your router", 1.0.0.1 "Cloudflare", 127.0.0.1 "local cache", other "configured".
* query_once with a fake socket returning a matching reply -> float; timeout -> None; socket closed in both.
* run_dns with injected `servers=["192.0.2.1"]` and fake `query(server, name)` returning 120 ms for 192.0.2.1 and 14 ms for 1.1.1.1 etc. -> rows in order
  (in-use first), median right, verdict mentions switching; all in-use None -> "not answering" verdict; cancel -> done promptly.
