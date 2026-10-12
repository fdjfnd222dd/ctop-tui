"""Ping collector: latency, loss and jitter to the default gateway and one internet address.

This is the only ctop code allowed to send packets: one echo per destination per refresh.
"""
from __future__ import annotations

import functools
import os
import re
import shutil
import socket
import subprocess
import time
from collections import deque
from typing import Callable, Dict, Mapping, Optional, Tuple

from ctop.collectors.base import Collector
from ctop.models import PingData, PingResult

DEFAULT_TARGET = "1.1.1.1"
HISTORY = 120

# ping -W 1 can take about a second; this cap stops a hung ping process.
_RUN_TIMEOUT = 2.5
_TIME_RE = re.compile(r"time[=<]\s*([0-9]+(?:\.[0-9]+)?)\s*ms")
# A leading "-" would be read by ping as an option (for example -f), so it is refused.
_TARGET_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9.:\-]*")
# Containers without CAP_NET_RAW say one of these; a plain loss does not.
_PERMISSION_MARKERS = ("not permitted", "Permission denied")


def parse_ping_output(text: str) -> Optional[float]:
    """First round-trip time in ms from ping output, or None when there was no reply."""
    if not isinstance(text, str):
        return None
    m = _TIME_RE.search(text)
    return float(m.group(1)) if m else None


def valid_target(target: str) -> bool:
    return isinstance(target, str) and len(target) <= 253 and _TARGET_RE.fullmatch(target) is not None


def tcp_connect_ms(target: str, port: int = 443, timeout: float = 1.0,
                   connect: Callable = None) -> Optional[float]:
    """Round trip of a TCP handshake, used when ICMP is not allowed.

    A refused connection still means the host answered (RST), so it counts as a reply.
    The socket is closed at once; no data is sent.
    """
    if connect is None:
        connect = socket.create_connection
    start = time.perf_counter()
    try:
        sock = connect((target, port), timeout)
    except ConnectionRefusedError:
        pass
    except OSError:
        return None
    else:
        sock.close()
    return (time.perf_counter() - start) * 1000.0


def ping_once(target: str, timeout: float = 1.0, run: Callable = None,
              have_ping: Optional[bool] = None, tcp: Callable = None) -> Optional[float]:
    """One echo to `target`: round trip in ms, or None when lost.

    Falls back to a TCP handshake when ICMP cannot be sent at all (no ping binary,
    or no CAP_NET_RAW). A plain loss (exit 1) is a real loss, so it does not fall back.
    """
    if tcp is None:
        tcp = functools.partial(tcp_connect_ms, timeout=timeout)
    if have_ping is None:
        have_ping = shutil.which("ping") is not None
    if not have_ping:
        return tcp(target)
    if run is None:
        run = subprocess.run
    # -n: no reverse DNS lookup. -W 1: wait at most one second for the reply.
    argv = ["ping", "-n", "-c", "1", "-W", "1", target]
    # LC_ALL=C keeps the reply words in English so the regex matches on any locale.
    try:
        proc = run(argv, capture_output=True, text=True, timeout=_RUN_TIMEOUT,
                   stdin=subprocess.DEVNULL, env={**os.environ, "LC_ALL": "C"})
    except FileNotFoundError:
        return tcp(target)
    except (subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode == 0:
        return parse_ping_output(proc.stdout)
    text = proc.stdout + proc.stderr
    if any(marker in text for marker in _PERMISSION_MARKERS):
        return tcp(target)
    return None


def summarize(label: str, target: str, samples) -> PingResult:
    """Statistics over a history of round trips (None = lost), newest last."""
    hist = tuple(samples)
    replies = [s for s in hist if s is not None]
    loss = 100.0 * (len(hist) - len(replies)) / len(hist) if hist else 0.0
    avg = sum(replies) / len(replies) if replies else None
    # Jitter needs two replies in a row; lost samples are dropped first so a gap
    # does not count as a jump.
    if len(replies) >= 2:
        diffs = [abs(b - a) for a, b in zip(replies, replies[1:])]
        jitter = sum(diffs) / len(diffs)
    else:
        jitter = None
    return PingResult(label=label, target=target, ms=hist[-1] if hist else None,
                      avg_ms=avg, jitter_ms=jitter, loss_pct=loss, history=hist)


class PingCollector(Collector):
    """Publishes PingData every 3 s; the router is pinged on the LAN, the internet target once."""

    name = "ping"
    interval = 3.0

    def __init__(self, gateway_fn: Optional[Callable[[], Tuple[Optional[str], Optional[str]]]] = None,
                 runner: Optional[Callable[[str], Optional[float]]] = None,
                 environ: Optional[Mapping[str, str]] = None) -> None:
        super().__init__()
        if gateway_fn is None:
            # Lazy import: ctop.system reads /proc, which tests must never touch.
            from ctop.system import default_route
            gateway_fn = default_route
        env = os.environ if environ is None else environ
        target = env.get("CTOP_PING_TARGET", DEFAULT_TARGET)
        # A bad value falls back quietly rather than reaching the ping command line.
        self._internet = target if valid_target(target) else DEFAULT_TARGET
        self._ping_internet = env.get("CTOP_NO_PING") != "1"
        self._gateway_fn = gateway_fn
        self._runner = runner if runner is not None else ping_once
        self._gateway: Optional[str] = None
        self._hist: Dict[str, deque] = {}

    def refresh(self) -> None:
        _, gateway = self._gateway_fn()
        if gateway != self._gateway:
            # A new router (Wi-Fi switched) starts with an empty history.
            self._hist.pop(self._gateway, None)
            self._gateway = gateway
        router = self._sample("Router", gateway) if gateway is not None else None
        internet = self._sample("Internet", self._internet) if self._ping_internet else None
        self.data = PingData(router=router, internet=internet)

    def _sample(self, label: str, target: str) -> PingResult:
        hist = self._hist.setdefault(target, deque(maxlen=HISTORY))
        hist.append(self._runner(target))
        return summarize(label, target, hist)
