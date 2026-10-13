"""Port check for the Network page: is a TCP port open on a target, for the few ports the user picked.

This is a reachability check, not a scan. Each port gets one TCP handshake that is closed at once,
with no data sent. At most MAX_PORTS ports are allowed per run, and the result says what the
answer means in plain words: open, closed (the host said no), filtered (no answer, usually a firewall),
or error (the network or host could not be reached).
"""
from __future__ import annotations

import errno
import re
import socket
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from typing import Callable, Dict, Optional, Tuple

from ctop.collectors.ping import valid_target
from ctop.models import PortCheckResult, PortResult
from ctop.tools import cancelled, emit

MAX_PORTS = 32
WORKERS = 8
POLL_STEP = 0.1                 # how often a running check looks at the cancel flag

SERVICES = {21: "FTP", 22: "SSH", 23: "Telnet", 25: "SMTP", 53: "DNS", 80: "HTTP", 110: "POP3", 123: "NTP",
            139: "NetBIOS", 143: "IMAP", 443: "HTTPS", 445: "SMB (file sharing)", 465: "SMTPS", 548: "AFP",
            554: "RTSP (cameras)", 587: "Mail submit", 631: "IPP (printer)", 853: "DNS over TLS", 993: "IMAPS",
            995: "POP3S", 1883: "MQTT", 3306: "MySQL", 3389: "Remote Desktop", 5000: "UPnP / dev server",
            5353: "mDNS", 5432: "PostgreSQL", 5900: "VNC", 6379: "Redis", 8008: "Chromecast", 8009: "Chromecast",
            8080: "HTTP alt", 8443: "HTTPS alt", 8888: "HTTP alt", 9100: "Printer (raw)", 32400: "Plex",
            51820: "WireGuard"}

PRESETS = (("Common", (22, 53, 80, 443, 445, 3389, 8080)),
           ("Web", (80, 443, 8080, 8443)),
           ("Remote access", (22, 23, 3389, 5900)),
           ("File sharing", (21, 139, 445, 548)),
           ("Home devices", (554, 631, 8008, 8009, 9100, 1883)))

_DIGITS = re.compile(r"\d+")
_SPLIT = re.compile(r"[,\s]+")

# errno values that mean "no answer in time" rather than "refused" or "no route"
_FILTERED_ERRNOS = {errno.ETIMEDOUT, errno.EAGAIN, errno.EWOULDBLOCK, errno.EINPROGRESS}


def service_name(port: int) -> str:
    return SERVICES.get(port, "")


def parse_ports(text: str) -> Tuple[Tuple[int, ...], Optional[str]]:
    """'22, 80 443 8000-8003' -> ((22, 80, 443, 8000, 8001, 8002, 8003), None). Errors come back as a reason."""
    tokens = [t for t in _SPLIT.split((text or "").strip()) if t]
    if not tokens:
        return (), "Type at least one port"
    ports = set()
    for token in tokens:
        if "-" in token:
            start_s, _, end_s = token.partition("-")
            if not (_DIGITS.fullmatch(start_s) and _DIGITS.fullmatch(end_s)):
                return (), "Not a port range: %s" % token
            start, end = int(start_s), int(end_s)
            if start > end:
                return (), "Range start is above its end: %s" % token
            # Check the size before expanding, so "1-65535" costs nothing.
            if len(ports) + (end - start + 1) > MAX_PORTS:
                return (), "At most %d ports at a time" % MAX_PORTS
            numbers = range(start, end + 1)
        else:
            if not _DIGITS.fullmatch(token):
                return (), "Not a port number: %s" % token
            numbers = (int(token),)
        for n in numbers:
            if not 1 <= n <= 65535:
                return (), "Ports must be between 1 and 65535: %d" % n
            ports.add(n)
        if len(ports) > MAX_PORTS:
            return (), "At most %d ports at a time" % MAX_PORTS
    return tuple(sorted(ports)), None


def tcp_probe(address: str, port: int, timeout: float = 1.5, family: int = socket.AF_INET,
              clock: Callable[[], float] = time.perf_counter,
              sock_factory: Callable = socket.socket) -> Tuple[str, Optional[float]]:
    """One TCP handshake, closed at once. Returns (state, ms); ms is None unless the host answered."""
    try:
        sock = sock_factory(family, socket.SOCK_STREAM)
    except OSError:
        return "error", None                             # out of file handles or no such address family
    try:
        sock.settimeout(timeout)
        start = clock()
        try:
            code = sock.connect_ex((address, port))
        except socket.timeout:
            return "filtered", None
        except ConnectionRefusedError:
            return "closed", (clock() - start) * 1000.0
        except OSError:
            return "error", None
        ms = (clock() - start) * 1000.0
        if code == 0:
            return "open", ms
        if code == errno.ECONNREFUSED:
            return "closed", ms
        if code in _FILTERED_ERRNOS:
            return "filtered", None
        return "error", None                             # no route, host down, network unreachable...
    finally:
        sock.close()


def _resolve_fallback(target: str, resolver: Optional[Callable] = None) -> Tuple[str, int]:
    """Same rules as ctop.tools.trace.resolve_target, used only if that module is missing."""
    if not valid_target(target):
        raise ValueError("Not a valid address or name")
    if resolver is None:
        resolver = lambda name: socket.getaddrinfo(name, None, 0, socket.SOCK_DGRAM)  # noqa: E731
    try:
        infos = list(resolver(target))
    except (OSError, UnicodeError):
        raise ValueError("Could not find that name")
    if not infos:
        raise ValueError("Could not find that name")
    v4 = [i for i in infos if i[0] == socket.AF_INET]
    pick = (v4 or infos)[0]
    return pick[4][0], pick[0]


def _resolve(target: str, resolver: Optional[Callable]) -> Tuple[str, int]:
    # Imported here so this module loads even when the trace tool is absent.
    try:
        from ctop.tools.trace import resolve_target
    except ImportError:
        resolve_target = _resolve_fallback
    return resolve_target(target, resolver)


def _snapshot(target, address, ports, results: Dict[int, PortResult], done, error) -> PortCheckResult:
    return PortCheckResult(target=target, address=address,
                           results=tuple(results[p] for p in ports), done=done, error=error)


def run_ports(target, ports, timeout=1.5, on_update=None, cancel=None, resolver=None,
              probe=None) -> PortCheckResult:
    """Check each port in `ports` on `target`. Never raises; problems come back in `result.error`."""
    probe = probe or tcp_probe
    if cancelled(cancel):
        result = PortCheckResult(target=target, error="cancelled", done=True)
        emit(on_update, result)
        return result

    ports = tuple(sorted(set(ports or ())))
    if not ports:
        result = PortCheckResult(target=target, error="Pick at least one port", done=True)
        emit(on_update, result)
        return result
    if len(ports) > MAX_PORTS:
        result = PortCheckResult(target=target, error="At most %d ports at a time" % MAX_PORTS, done=True)
        emit(on_update, result)
        return result
    if any(not 1 <= p <= 65535 for p in ports):
        result = PortCheckResult(target=target, error="Ports must be between 1 and 65535", done=True)
        emit(on_update, result)
        return result

    try:
        address, family = _resolve(target, resolver)
    except ValueError as e:
        result = PortCheckResult(target=target, error=str(e), done=True)
        emit(on_update, result)
        return result

    results = {p: PortResult(p, service_name(p), "pending") for p in ports}
    emit(on_update, _snapshot(target, address, ports, results, False, None))

    error = None
    executor = ThreadPoolExecutor(max_workers=min(WORKERS, len(ports)))
    try:
        futures = {executor.submit(probe, address, p, timeout, family): p for p in ports}
        waiting = set(futures)
        while waiting:
            if cancelled(cancel):
                error = "cancelled"
                break
            finished, waiting = wait(waiting, timeout=POLL_STEP, return_when=FIRST_COMPLETED)
            if not finished:
                continue
            for fut in finished:
                port = futures[fut]
                try:
                    state, ms = fut.result()
                except Exception:
                    state, ms = "error", None           # a broken probe must not sink the other ports
                results[port] = PortResult(port, service_name(port), state, ms)
            emit(on_update, _snapshot(target, address, ports, results, False, None))
    finally:
        executor.shutdown(wait=False, cancel_futures=True)   # unfinished probes stay "pending"

    result = _snapshot(target, address, ports, results, True, error)
    emit(on_update, result)
    return result
