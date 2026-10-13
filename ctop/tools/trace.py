"""Traceroute for the Network page: every router between this computer and a target, no root needed.

The trick is the one `tracepath` uses. A plain UDP socket with a small TTL dies at the router
that sees the TTL run out, and Linux's IP_RECVERR queue hands the unprivileged program the ICMP
"time exceeded" reply together with the router's address. All TTLs are probed at once (one socket
per TTL), so a whole round takes about ROUND_WAIT seconds instead of one timeout per hop.
If the built-in way is impossible (not Linux, no permission) the `tracepath` command is used.
"""
from __future__ import annotations

import ipaddress
import re
import select
import socket
import struct
import time
from collections import Counter
from typing import Callable, List, Optional, Tuple

from ctop.collectors.ping import valid_target
from ctop.collectors.wifi import run_cmd
from ctop.models import TraceHop, TraceResult
from ctop.tools import cancelled, emit

MAX_HOPS = 30
PROBES = 3                      # rounds; each round probes every TTL once
BASE_PORT = 33434
ROUND_WAIT = 1.5                # seconds to wait for answers after the last probe of a round
SEND_GAP = 0.015                # seconds between probes; routers rate-limit ICMP, a burst shows false "* * *"
EMIT_GAP = 0.1                  # at most ~10 progress updates a second

# Python's socket module lacks most of these names; the numbers are the Linux values.
IP_RECVERR = getattr(socket, "IP_RECVERR", 11)
IPV6_RECVERR = getattr(socket, "IPV6_RECVERR", 25)
IPV6_UNICAST_HOPS = getattr(socket, "IPV6_UNICAST_HOPS", 16)
MSG_ERRQUEUE = getattr(socket, "MSG_ERRQUEUE", 0x2000)
SOL_IP = 0
SOL_IPV6 = 41
SO_EE_ORIGIN_ICMP = 2
SO_EE_ORIGIN_ICMP6 = 3

_HOME_NETS = tuple(ipaddress.ip_network(n) for n in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16", "fc00::/7", "fe80::/10"))
_ISP_NETS = (ipaddress.ip_network("100.64.0.0/10"),)     # carrier-grade NAT: the ISP's own network

# tracepath lines look like " 3:  198.51.100.9   12.400ms asymm  4" or " 2:  no reply"
_TRACEPATH_LINE = re.compile(r"^\s*(\d+)\??:\s*(.*)$")
_TRACEPATH_MS = re.compile(r"(\d+(?:\.\d+)?)ms")
_ERR_HEADER = struct.Struct("=IBBBBII")                  # sock_extended_err, 16 bytes


def classify(ip: Optional[str], target_ip: Optional[str] = None) -> str:
    """Which part of the path a router belongs to: home LAN, ISP, internet, or the target."""
    if ip is None:
        return "unknown"
    if target_ip is not None and ip == target_ip:
        return "target"
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return "unknown"
    if any(addr in net for net in _HOME_NETS):
        return "home"
    if any(addr in net for net in _ISP_NETS):
        return "isp"
    return "internet"


def resolve_target(target: str, resolver: Optional[Callable] = None) -> Tuple[str, int]:
    """(ip, family) for a typed host name or address. Prefers IPv4, which home routers handle best."""
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


def parse_recverr(cmsg_level: int, cmsg_type: int, data: bytes, family: int) -> Optional[Tuple[Optional[str], str]]:
    """Turn one IP_RECVERR control message into (router_ip, "hop" | "reached" | "unreachable").

    Returns None for control messages that are not ICMP errors (wrong level, too short, other origin).
    """
    if family == socket.AF_INET:
        if (cmsg_level, cmsg_type) != (SOL_IP, IP_RECVERR):
            return None
    elif family == socket.AF_INET6:
        if (cmsg_level, cmsg_type) != (SOL_IPV6, IPV6_RECVERR):
            return None
    else:
        return None
    if len(data) < 16:
        return None
    _errno, origin, etype, code, _pad, _info, _extra = _ERR_HEADER.unpack_from(data, 0)

    # The router's address follows as a sockaddr; family 0 means the kernel did not record one.
    ip = None
    if len(data) >= 18:
        sa_family = struct.unpack_from("=H", data, 16)[0]
        if sa_family == socket.AF_INET and len(data) >= 24:
            ip = socket.inet_ntop(socket.AF_INET, data[20:24])
        elif sa_family == socket.AF_INET6 and len(data) >= 40:
            ip = socket.inet_ntop(socket.AF_INET6, data[24:40])

    if origin == SO_EE_ORIGIN_ICMP:
        if etype == 11:                                  # time exceeded: a router along the way
            return ip, "hop"
        if etype == 3:
            return (ip, "reached") if code == 3 else (ip, "unreachable")   # 3/3 = port unreachable = target
        return None
    if origin == SO_EE_ORIGIN_ICMP6:
        if etype == 3:                                   # IPv6 time exceeded
            return ip, "hop"
        if etype == 1:
            return (ip, "reached") if code == 4 else (ip, "unreachable")
        return None
    return None


def parse_tracepath(text: str) -> List[Tuple[int, Optional[str], Optional[float], bool]]:
    """Parse `tracepath -n` output into (ttl, ip, ms, reached). The first reply per TTL wins."""
    rows = []
    seen = set()
    for line in text.splitlines():
        m = _TRACEPATH_LINE.match(line)
        if not m:
            continue                                     # "Resume: ..." and blank lines
        ttl = int(m.group(1))
        rest = m.group(2).strip()
        if ttl in seen or rest.startswith("[LOCALHOST]"):
            continue                                     # "1?: [LOCALHOST] pmtu" is this computer itself
        seen.add(ttl)
        if rest.startswith("no reply") or not rest:
            rows.append((ttl, None, None, False))
            continue
        tokens = rest.split()
        ip = tokens[0]
        try:
            ipaddress.ip_address(ip)
        except ValueError:
            rows.append((ttl, None, None, False))
            continue
        ms_match = _TRACEPATH_MS.search(rest)
        ms = float(ms_match.group(1)) if ms_match else None
        rows.append((ttl, ip, ms, "reached" in tokens))
    return rows


class UdpProber:
    """Sends one tiny UDP packet per TTL and reads the router's answer from the error queue.

    Constructed per round. Creating a socket or setting RECVERR raises OSError (the caller falls back
    to tracepath); a probe that cannot be sent just gets no answer.
    """

    def __init__(self, family: int, addr: str, clock: Callable[[], float] = time.monotonic):
        self.family = family
        self.addr = addr
        self._clock = clock
        self._socks = {}                                 # fd -> (socket, ttl, sent_at)
        self._poll = select.poll()

    def send(self, ttl: int, port: int) -> int:
        sock = socket.socket(self.family, socket.SOCK_DGRAM)
        try:
            sock.setblocking(False)
            if self.family == socket.AF_INET:
                sock.setsockopt(SOL_IP, socket.IP_TTL, ttl)
                sock.setsockopt(SOL_IP, IP_RECVERR, 1)
            else:
                sock.setsockopt(SOL_IPV6, IPV6_UNICAST_HOPS, ttl)
                sock.setsockopt(SOL_IPV6, IPV6_RECVERR, 1)
        except OSError:
            sock.close()
            raise
        try:
            sock.connect((self.addr, port))
            sent_at = self._clock()
            sock.send(b"ctop-trace")
        except OSError:
            sock.close()                                 # no route or refused: this TTL just gets no answer
            return ttl
        fd = sock.fileno()
        self._socks[fd] = (sock, ttl, sent_at)
        self._poll.register(fd, select.POLLIN | select.POLLERR)
        return ttl

    def wait(self, timeout: float) -> List[Tuple[int, Optional[str], str, float]]:
        """Wait up to `timeout` seconds; return (ttl, router_ip, kind, ms) for every answer that came in."""
        if not self._socks:
            time.sleep(timeout)                          # nothing to poll: pace the caller without spinning
            return []
        out = []
        for fd, ev in self._poll.poll(int(timeout * 1000)):
            entry = self._socks.get(fd)
            if entry is None:
                continue
            sock, ttl, sent_at = entry
            answer = None
            try:
                _data, ancdata, _flags, _addr = sock.recvmsg(256, 512, MSG_ERRQUEUE)
            except OSError:
                ancdata = []
            for level, ctype, cdata in ancdata:
                answer = parse_recverr(level, ctype, cdata, self.family)
                if answer is not None:
                    break
            if answer is None and ev & select.POLLIN:
                try:
                    sock.recv(1)
                except ConnectionRefusedError:
                    answer = (None, "reached")           # plain recv says "refused": the target answered
                except OSError:
                    pass
            if answer is None:
                if ev & (select.POLLERR | select.POLLHUP | select.POLLNVAL) and not ancdata:
                    self._drop(fd)                       # errored with nothing readable: poll would return at once forever
                continue
            ip, kind = answer
            out.append((ttl, ip, kind, (self._clock() - sent_at) * 1000.0))
            self._drop(fd)                               # one answer per probe
        return out

    def _drop(self, fd: int) -> None:
        self._poll.unregister(fd)                        # unregister before close: fd numbers get reused
        entry = self._socks.pop(fd, None)
        if entry is not None:
            entry[0].close()

    def close(self) -> None:
        for fd in list(self._socks):
            self._drop(fd)


def _snapshot(target, address, replies, probes, max_hops, reached_ttl, done, error, method):
    """Build a TraceResult from everything heard so far. replies[ttl][round] = (ip, kind, ms) or None."""
    heard = [t for t, rounds in replies.items() if any(rounds)]
    unreach = [t for t in heard if any(a and a[1] == "unreachable" for a in replies[t])]
    if reached_ttl:
        end = reached_ttl
    else:
        end = min(max_hops, (max(heard) + 1) if heard else 1)
    if unreach:
        end = min(end, min(unreach))                     # a blocked route ends the trace at that hop

    hops = []
    for ttl in range(1, end + 1):
        rounds = replies.get(ttl, [None] * probes)
        rtts = tuple(a[2] if a else None for a in rounds)
        ips = [a[0] for a in rounds if a and a[0]]
        ip = Counter(ips).most_common(1)[0][0] if ips else None
        reached = ttl == reached_ttl
        if reached:
            ip = ip or address
            kind = "target"
        else:
            kind = classify(ip, address)
        hops.append(TraceHop(ttl=ttl, ip=ip, rtts=rtts, kind=kind, reached=reached))
    return TraceResult(target=target, address=address, hops=tuple(hops), done=done,
                       reached=bool(reached_ttl), method=method, error=error)


def _tracepath(target, address, max_hops, runner, on_update) -> TraceResult:
    """Fallback: ask the tracepath command. One probe per hop, so every hop shows one RTT."""
    runner = runner or run_cmd
    out = runner(["tracepath", "-n", "-m", str(max_hops), address], 40)
    if out is None:
        result = TraceResult(target=target, address=address, done=True, method="tracepath",
                             error="Tracing needs Linux UDP error reporting or the tracepath tool")
        emit(on_update, result)
        return result
    hops = []
    reached_ttl = None
    for ttl, ip, ms, reached in parse_tracepath(out):
        if reached and reached_ttl is None:
            reached_ttl = ttl
        kind = "target" if reached else classify(ip, address)
        hops.append(TraceHop(ttl=ttl, ip=ip, rtts=(ms,), kind=kind, reached=reached))
    result = TraceResult(target=target, address=address, hops=tuple(hops), done=True,
                         reached=reached_ttl is not None, method="tracepath")
    emit(on_update, result)
    return result


def run_trace(target, max_hops=MAX_HOPS, probes=PROBES, on_update=None, cancel=None,
              resolver=None, prober_factory=None, tracepath_runner=None, clock=None) -> TraceResult:
    """Trace the route to `target`. Never raises; problems come back in `result.error`.

    prober_factory(family, address) returns an object with send/wait/close (default: UdpProber).
    """
    clock = clock or time.monotonic
    make = prober_factory or (lambda family, addr: UdpProber(family, addr, clock))

    try:
        address, family = resolve_target(target, resolver)
    except ValueError as e:
        result = TraceResult(target=target, error=str(e), done=True)
        emit(on_update, result)
        return result

    emit(on_update, TraceResult(target=target, address=address, method="udp"))
    if cancelled(cancel):
        result = TraceResult(target=target, address=address, done=True, error="cancelled")
        emit(on_update, result)
        return result

    replies = {ttl: [None] * probes for ttl in range(1, max_hops + 1)}
    reached_ttl = None
    error = None
    last_emit = 0.0

    for r in range(probes):
        if cancelled(cancel):
            error = "cancelled"
            break
        # Once the destination is known, deeper TTLs cannot add anything: probe only up to it.
        limit = reached_ttl or max_hops
        prober = None
        try:
            prober = make(family, address)
            sent = []
            answered = set()
            last_send = clock()

            def absorb(got):
                nonlocal reached_ttl
                new = False
                for ttl, ip, kind, ms in got:
                    if kind == "hop" and ip is None:
                        continue                         # a reply without a router address is no hop
                    if kind == "reached" and ip is None:
                        ip = address
                    if ttl in answered:
                        continue
                    answered.add(ttl)
                    replies[ttl][r] = (ip, kind, ms)
                    if kind == "reached" and (reached_ttl is None or ttl < reached_ttl):
                        reached_ttl = ttl
                    new = True
                return new

            for ttl in range(1, limit + 1):
                if cancelled(cancel) or (reached_ttl and ttl > reached_ttl):
                    break                                # the destination is known: deeper TTLs are pointless
                prober.send(ttl, BASE_PORT + r * max_hops + ttl)
                sent.append(ttl)
                last_send = clock()
                if absorb(prober.wait(SEND_GAP)) and clock() - last_emit >= EMIT_GAP:
                    emit(on_update, _snapshot(target, address, replies, probes, max_hops, reached_ttl, False, None, "udp"))
                    last_emit = clock()

            # Wait for the stragglers, but no longer than ROUND_WAIT after the last probe.
            while (not cancelled(cancel) and len(answered) < len(sent)
                   and clock() - last_send < ROUND_WAIT):
                if absorb(prober.wait(0.1)) and clock() - last_emit >= EMIT_GAP:
                    emit(on_update, _snapshot(target, address, replies, probes, max_hops, reached_ttl, False, None, "udp"))
                    last_emit = clock()
        except OSError:
            if r == 0:
                return _tracepath(target, address, max_hops, tracepath_runner, on_update)
            break                                        # later rounds failing keeps what we already have
        finally:
            if prober is not None:
                prober.close()

    if cancelled(cancel):
        error = "cancelled"
    result = _snapshot(target, address, replies, probes, max_hops, reached_ttl, True, error, "udp")
    emit(on_update, result)
    return result
