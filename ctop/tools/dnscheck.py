"""DNS timing tool: how fast each DNS server this computer uses answers, next to three public ones.

Pure Python DNS over UDP (a tiny A-record query), so `dig` is not needed. Each server gets one
query per test name; the row shows the median of the answered tries. The verdict says in one
sentence whether DNS is what makes pages feel slow.
"""
from __future__ import annotations

import ipaddress
import os
import re
import socket
import struct
import threading
import time
from typing import Callable, Dict, List, Optional, Sequence

from ctop.collectors.wifi import run_cmd
from ctop.models import DnsReport, DnsTiming
from ctop.tools import cancelled, emit

PUBLIC = (("1.1.1.1", "Cloudflare"), ("9.9.9.9", "Quad9"), ("8.8.8.8", "Google"))
KNOWN = {"1.1.1.1": "Cloudflare", "1.0.0.1": "Cloudflare", "9.9.9.9": "Quad9", "149.112.112.112": "Quad9",
         "8.8.8.8": "Google", "8.8.4.4": "Google"}
# Three names, so each server gets three tries; a single lucky answer is not enough.
NAMES = ("example.com", "wikipedia.org", "debian.org")
# systemd-resolved listens on these; they hide the real servers from resolv.conf.
_STUBS = frozenset(("127.0.0.53", "127.0.0.54"))
_MAX_SERVERS = 4
_HEADER_FLAGS_RD = 0x0100
_RCODE_OK = (0, 3)            # NXDOMAIN (3) still proves the server works
_RECV_SIZE = 512
_CMD_TIMEOUT = 3

_ZONE_RE = re.compile(r"%[^%\s]*$")
_NMCLI_DNS_RE = re.compile(r"^IP[46]\.DNS\[\d+\]$")


def build_query(name: str, qid: int) -> bytes:
    """A standard recursive A-record query for `name`."""
    header = struct.pack(">HHHHHH", qid & 0xFFFF, _HEADER_FLAGS_RD, 1, 0, 0, 0)
    labels = b"".join(bytes([len(p)]) + p.encode("ascii") for p in name.rstrip(".").split("."))
    # QTYPE A (1) and QCLASS IN (1).
    return header + labels + b"\x00" + struct.pack(">HH", 1, 1)


def parse_reply(data: bytes, qid: int) -> Optional[int]:
    """Response code when the reply belongs to this query, else None."""
    if len(data) < 12:
        return None
    reply_id, flags = struct.unpack(">HH", data[:4])
    if reply_id != (qid & 0xFFFF) or not flags & 0x8000:
        return None
    return flags & 0x000F


def _clean_ip(text: str) -> Optional[str]:
    """Validated address without any %zone suffix, or None for garbage."""
    candidate = _ZONE_RE.sub("", text.strip())
    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        return None
    return candidate


def read_resolv(text: str) -> List[str]:
    """Nameserver lines of resolv.conf, in file order. Comments and options are skipped."""
    out: List[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line[0] in "#;":
            continue
        parts = line.split()
        if len(parts) >= 2 and parts[0] == "nameserver":
            out.append(parts[1])
    return out


def parse_resolvectl(text: str) -> List[str]:
    """Addresses from `resolvectl dns`: 'Global: a b' and 'Link 3 (wlan0): a b' lines."""
    out: List[str] = []
    for line in text.splitlines():
        _, sep, rest = line.partition(":")
        if not sep:
            continue
        for tok in rest.split():
            ip = _clean_ip(tok)
            if ip:
                out.append(ip)
    return out


def parse_nmcli_dns(text: str) -> List[str]:
    """Addresses from `nmcli -t -f IP4.DNS,IP6.DNS device show`: 'IP4.DNS[1]:192.0.2.1' lines."""
    out: List[str] = []
    for line in text.splitlines():
        key, sep, value = line.partition(":")
        if not sep or not _NMCLI_DNS_RE.match(key):
            continue
        ip = _clean_ip(value)
        if ip:
            out.append(ip)
    return out


def _dedupe_valid(addresses: Sequence[str]) -> List[str]:
    seen: List[str] = []
    for addr in addresses:
        ip = _clean_ip(addr)
        if ip and ip not in seen:
            seen.append(ip)
    return seen[:_MAX_SERVERS]


def servers_in_use(resolv_path: str = "/etc/resolv.conf", runner: Optional[Callable] = None) -> List[str]:
    """The DNS servers this computer asks. Asks the network manager when resolv.conf only has stubs."""
    if runner is None:
        runner = run_cmd
    try:
        with open(resolv_path, "r", encoding="utf-8", errors="replace") as fh:
            listed = read_resolv(fh.read())
    except OSError:
        listed = []
    real = [s for s in _dedupe_valid(listed) if s not in _STUBS]
    if real:
        return real
    # Only the systemd-resolved stub is listed, so the real servers are one command away.
    # The stub itself still answers queries, so it is the last resort.
    text = runner(["resolvectl", "dns"], _CMD_TIMEOUT)
    found = _dedupe_valid(parse_resolvectl(text)) if text else []
    if not found:
        text = runner(["nmcli", "-t", "-f", "IP4.DNS,IP6.DNS", "device", "show"], _CMD_TIMEOUT)
        found = _dedupe_valid(parse_nmcli_dns(text)) if text else []
    return found or _dedupe_valid(listed)


def label_for(server: str, gateway: Optional[str]) -> str:
    if gateway is not None and server == gateway:
        return "your router"
    if server in KNOWN:
        return KNOWN[server]
    try:
        ip = ipaddress.ip_address(server)
    except ValueError:
        return "configured"
    if ip.is_loopback:
        return "local cache"
    return "configured"


def _family_for(server: str) -> Optional[int]:
    try:
        version = ipaddress.ip_address(server).version
    except ValueError:
        return None
    return socket.AF_INET6 if version == 6 else socket.AF_INET


def query_once(server: str, name: str, timeout: float = 1.0, sock_factory: Optional[Callable] = None,
               clock: Callable[[], float] = time.perf_counter) -> Optional[float]:
    """Milliseconds for one answer from `server` to a query for `name`, or None (no answer or error).

    sock_factory(family) returns a UDP socket-like object; the default is a real socket.
    """
    family = _family_for(server)
    if family is None:
        return None
    if sock_factory is None:
        sock_factory = lambda fam: socket.socket(fam, socket.SOCK_DGRAM)
    qid = int.from_bytes(os.urandom(2), "big")
    query = build_query(name, qid)
    sock = None
    try:
        sock = sock_factory(family)
        start = clock()
        deadline = start + timeout
        sock.sendto(query, (server, 53))
        # Stray replies (wrong id) are ignored until the time limit passes.
        while True:
            remaining = deadline - clock()
            if remaining <= 0:
                return None
            sock.settimeout(remaining)
            data, _ = sock.recvfrom(_RECV_SIZE)
            rcode = parse_reply(data, qid)
            if rcode in _RCODE_OK:
                return (clock() - start) * 1000.0
    except OSError:
        return None
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass


def _median(values: List[float]) -> Optional[float]:
    if not values:
        return None
    s = sorted(values)
    mid = len(s) // 2
    return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2.0


def _verdict(rows: Sequence[DnsTiming]) -> str:
    in_use = [r for r in rows if r.in_use]
    public = [r for r in rows if not r.in_use]
    if not in_use:
        return "Could not find which DNS server this computer uses."
    answered_in = [r for r in in_use if r.ms is not None]
    if not answered_in:
        return ("Your DNS server is not answering - that alone breaks browsing. "
                "Switching DNS should fix it.")
    best_in = min(answered_in, key=lambda r: r.ms)
    answered_pub = [r for r in public if r.ms is not None]
    if not answered_pub:
        return f"Your DNS works ({best_in.ms:.0f} ms); public servers did not answer (blocked network?)."
    best_pub = min(answered_pub, key=lambda r: r.ms)
    a, b = best_in.ms, best_pub.ms
    if a > 2 * b and a - b > 40:
        return (f"Your DNS takes {a:.0f} ms; {best_pub.label} takes {b:.0f} ms. "
                "Switching DNS would make pages start faster.")
    return f"Your DNS is fine ({a:.0f} ms)."


def run_dns(gateway: Optional[str] = None, on_update: Optional[Callable] = None,
            cancel=None, servers: Optional[Sequence[str]] = None,
            query: Optional[Callable] = None) -> DnsReport:
    """Time every in-use DNS server plus the public ones. Never raises."""
    try:
        return _run(gateway, on_update, cancel, servers, query)
    except Exception as exc:  # the page must always get a finished report
        return DnsReport(done=True, verdict=f"DNS check failed: {str(exc)[:80]}")


def _run(gateway, on_update, cancel, servers, query) -> DnsReport:
    if query is None:
        query = query_once
    in_use = list(servers) if servers is not None else servers_in_use()
    order: List[str] = []
    for s in in_use:
        if s not in order:
            order.append(s)
    in_use_set = set(order)
    for addr, _ in PUBLIC:
        if addr not in order:
            order.append(addr)

    lock = threading.Lock()
    answers: Dict[str, List[Optional[float]]] = {s: [] for s in order}

    def make_rows(done: bool) -> DnsReport:
        rows = []
        for s in order:
            got = answers[s]
            ok = [v for v in got if v is not None]
            ms = _median(ok)
            failed_all = len(got) == len(NAMES) and not ok
            rows.append(DnsTiming(server=s, label=label_for(s, gateway), in_use=s in in_use_set,
                                  ms=ms, answered=len(ok), tries=len(got),
                                  error="no answer" if failed_all else None))
        rows_t = tuple(rows)
        verdict = _verdict(rows_t) if done else ""
        return DnsReport(timings=rows_t, done=done, verdict=verdict)

    emit(on_update, make_rows(False))

    def worker(server: str) -> None:
        for name in NAMES:
            if cancelled(cancel):
                return
            try:
                ms = query(server, name)
            except Exception:
                ms = None           # a broken probe counts as one lost try, never a crash
            with lock:
                answers[server].append(ms)
                snapshot = make_rows(False)
            if not cancelled(cancel):
                emit(on_update, snapshot)

    threads = [threading.Thread(target=worker, args=(s,), daemon=True) for s in order]
    for t in threads:
        t.start()
    # Wait in short slices so a cancel is noticed within about 50 ms.
    for t in threads:
        while t.is_alive():
            if cancelled(cancel):
                break
            t.join(0.05)
        if cancelled(cancel):
            break

    if cancelled(cancel):
        with lock:
            report = make_rows(True)
        cancelled_report = DnsReport(timings=report.timings, done=True, verdict="Cancelled.")
        emit(on_update, cancelled_report)
        return cancelled_report

    with lock:
        final = make_rows(True)
    emit(on_update, final)
    return final
