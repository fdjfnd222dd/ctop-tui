"""Network doctor: walks the chain from this computer to the internet and says where it breaks.

Order: link, IP address, router, DNS, internet, login page. Each step is shown as it runs.
Every probe is injectable, so the tests never touch the network or run a command.
"""
from __future__ import annotations

import socket
import threading
import time
from typing import Callable, Dict, Optional, Tuple

from ctop.collectors.ping import ping_once, tcp_connect_ms
from ctop.collectors.wifi import run_cmd
from ctop.models import Diagnosis, DoctorStep
from ctop.tools import cancelled, emit

STEPS = (("link", "Network link"), ("address", "IP address"), ("router", "Router"), ("dns", "Name lookup (DNS)"),
         ("internet", "Internet"), ("portal", "Login page check"))
_NAMES = dict(STEPS)
_ROUTER_PINGS = 3
_DNS_NAMES = ("example.com", "debian.org")
_INTERNET_HOSTS = (("1.1.1.1", 443), ("9.9.9.9", 443))
_DNS_TIMEOUT = 7.0               # getaddrinfo has no timeout of its own; > 5 s so a single retry still counts
_DNS_STALL_MS = 1000.0           # slower than this is the resolver waiting out a timeout, not a slow server
_CONNECTIVITY_CMD = ["nmcli", "networking", "connectivity", "check"]
_CONNECTIVITY_TIMEOUT = 6
_CONNECTIVITY_WORDS = frozenset(("full", "portal", "limited", "none", "unknown"))
_ROUTER_SLOW_MS = 50.0
_DNS_SLOW_MS = 300.0
_INTERNET_SLOW_MS = 150.0
_WEAK_WORDS = ("Weak", "Poor")
# A wrong or missing address means nothing further down can be tested usefully.
_HARD_STEPS = ("link", "address")
_SKIP_HARD = "skipped - fix the step above first"
_SKIP_CANCEL = "skipped - check was stopped"


def _default_ping(target: str) -> Optional[float]:
    return ping_once(target)


def _default_tcp(host: str, port: int) -> Optional[float]:
    return tcp_connect_ms(host, port, timeout=2.0)


def _default_resolve(name: str, family: int = 0) -> Optional[float]:
    """Milliseconds for a name lookup, or None. Runs in a daemon thread so a hung resolver cannot stall us."""
    box: Dict[str, float] = {}

    def work() -> None:
        start = time.perf_counter()
        try:
            socket.getaddrinfo(name, 443, family)
        except OSError:
            return
        box["ms"] = (time.perf_counter() - start) * 1000.0

    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    thread.join(_DNS_TIMEOUT)
    return box.get("ms")


def _default_connectivity() -> Optional[str]:
    # Asks NetworkManager to re-check. This sends one small HTTP request to the distro's
    # connectivity-check URL, the same request the desktop makes on its own.
    text = run_cmd(_CONNECTIVITY_CMD, _CONNECTIVITY_TIMEOUT)
    if text is None:
        return None
    word = text.strip().lower()
    return word if word in _CONNECTIVITY_WORDS else None


def _default_link_up(iface: str) -> Optional[bool]:
    try:
        import psutil  # lazy: only the doctor needs it
        stats = psutil.net_if_stats().get(iface)
    except Exception:
        return None
    return None if stats is None else bool(stats.isup)


class _Context:
    """Snapshots from the app plus the probes, and the numbers the final verdict quotes."""

    def __init__(self, net, nm, wifi, ping, resolve, tcp, connectivity, link_up) -> None:
        self.net = net or {}
        self.nm = nm
        self.wifi = wifi
        self.ping = ping
        self.resolve = resolve
        self.tcp = tcp
        self.connectivity = connectivity
        self.link_up = link_up
        self.router_ms: Optional[float] = None
        self.dns_ms: Optional[float] = None
        self.internet_ms: Optional[float] = None
        self.router_silent = False      # every router ping was lost (the router may just ignore ping)
        self.cancel = None              # set by _run; long steps stop early when it is set
        self.resolve4 = None            # IPv4-only lookup, used to explain a stalled lookup


def _step_link(ctx: _Context) -> Tuple[str, str, Optional[str]]:
    iface = ctx.net.get("iface")
    nm, wifi = ctx.nm, ctx.wifi
    if not iface:
        if nm is not None and nm.wifi_enabled is False:
            return "fail", "Wi-Fi is turned off", "wifi-on"
        saved_wifi = nm is not None and any(c.kind == "wifi" for c in nm.saved)
        return "fail", "Not connected to any network", ("reconnect" if saved_wifi else None)
    if ctx.link_up(iface) is False:
        return "fail", f"{iface} link is down", "reconnect"
    if wifi is not None and wifi.quality in _WEAK_WORDS:
        dbm = wifi.signal_dbm
        where = f" ({dbm} dBm)" if dbm is not None else ""
        return "warn", f"weak Wi-Fi signal{where} - move closer to the router", None
    if wifi is not None and wifi.ssid:
        dbm = wifi.signal_dbm
        where = f" ({dbm} dBm)" if dbm is not None else ""
        return "ok", f"Wi-Fi {wifi.ssid}, signal {wifi.quality}{where}", None
    return "ok", f"{iface} is up", None


def _step_address(ctx: _Context) -> Tuple[str, str, Optional[str]]:
    ip = ctx.net.get("ip")
    # 169.254.x.x is the self-assigned address a device picks when the router gave it none.
    if not ip or ip.startswith("169.254."):
        return "fail", "No IP address from the router (DHCP)", "reconnect"
    return "ok", ip, None


def _step_router(ctx: _Context) -> Tuple[str, str, Optional[str]]:
    gateway = ctx.net.get("gateway")
    if not gateway:
        return "fail", "No router (default gateway) set", "reconnect"
    replies = []
    for _ in range(_ROUTER_PINGS):
        if cancelled(ctx.cancel):
            break                       # the caller drops this step's result once it sees the cancel
        replies.append(ctx.ping(gateway))
    got = [r for r in replies if r is not None]
    if not got:
        ctx.router_silent = True
        return "fail", "Router is not answering", "reconnect"
    avg = sum(got) / len(got)
    ctx.router_ms = avg
    lost = len(replies) - len(got)
    if lost:
        return "warn", f"Router lost {lost} of {_ROUTER_PINGS} replies - weak or busy Wi-Fi", None
    if avg > _ROUTER_SLOW_MS:
        return "warn", f"Router answers slowly ({avg:.0f} ms) - usually weak Wi-Fi", None
    return "ok", f"{avg:.0f} ms", None


def _step_dns(ctx: _Context) -> Tuple[str, str, Optional[str]]:
    times = []
    for name in _DNS_NAMES:
        if cancelled(ctx.cancel):
            break
        times.append(ctx.resolve(name))
    got = [t for t in times if t is not None]
    best = min(got) if got else None
    if (best is None or best > _DNS_STALL_MS) and ctx.resolve4 is not None and not cancelled(ctx.cancel):
        # A normal lookup asks for IPv4 and IPv6 at once. Some routers and DNS filters drop one of
        # the pair, so every lookup waits out a 5 s timeout although the server itself is fast.
        v4 = ctx.resolve4(_DNS_NAMES[0])
        if v4 is not None and v4 < _DNS_SLOW_MS:
            wait = f"{best / 1000:.0f} s" if best is not None else "more than 7 s"
            return ("warn", f"Every lookup waits {wait}: the IPv4+IPv6 lookup pair times out once "
                    f"(IPv4 alone: {v4:.0f} ms). Common router/DNS-filter bug", "dns")
    if best is None:
        return "fail", "Names do not resolve - DNS is broken", "dns"
    ctx.dns_ms = best
    if best > _DNS_SLOW_MS:
        return "warn", f"Name lookups are slow ({best:.0f} ms)", "dns"
    return "ok", f"{best:.0f} ms", None


def _step_internet(ctx: _Context, router_status: str) -> Tuple[str, str, Optional[str]]:
    ms = None
    for host, port in _INTERNET_HOSTS:
        ms = ctx.tcp(host, port)
        if ms is not None:
            break
    if ms is None:
        if router_status == "fail":
            return "fail", "No internet beyond your router", "reconnect"
        return ("fail", "Your router is up but the internet is not - the problem is likely your provider or modem",
                None)
    ctx.internet_ms = ms
    if ms > _INTERNET_SLOW_MS:
        return "warn", f"Internet is slow to answer ({ms:.0f} ms)", None
    return "ok", f"{ms:.0f} ms", None


def _step_portal(ctx: _Context) -> Tuple[str, str, Optional[str]]:
    word = ctx.connectivity()
    if word == "portal":
        return "fail", "This network wants you to log in (hotel/cafe page)", "portal"
    if word == "limited":
        return "warn", "NetworkManager reports limited internet", None
    if word == "full":
        return "ok", "No login page", None
    return "skip", "Could not check", None


def _verdict(steps: Tuple[DoctorStep, ...], suggestions: Dict[str, Optional[str]],
             ctx: _Context) -> Tuple[str, str, Optional[str]]:
    by_key = {s.key: s for s in steps}
    fails = [s for s in steps if s.status == "fail"]
    if fails:
        pick = fails[0]
        # A login page is the usual cause of a failed internet check, so it is named instead.
        if pick.key == "internet" and by_key["portal"].status == "fail":
            pick = by_key["portal"]
        return "fail", "Problem: " + pick.detail, suggestions.get(pick.key)
    warns = [s for s in steps if s.status == "warn"]
    if warns:
        pick = warns[0]
        return "warn", "Works, but: " + pick.detail, suggestions.get(pick.key)
    return ("ok", f"Everything looks healthy: router {ctx.router_ms:.0f} ms, DNS {ctx.dns_ms:.0f} ms, "
                  f"internet {ctx.internet_ms:.0f} ms.", None)


def _worst(steps: Tuple[DoctorStep, ...]) -> str:
    statuses = {s.status for s in steps}
    if "fail" in statuses:
        return "fail"
    if "warn" in statuses:
        return "warn"
    return "ok"


def run_doctor(net: Optional[dict] = None, nm=None, wifi=None, on_update: Optional[Callable] = None,
               cancel: Optional[threading.Event] = None, ping: Optional[Callable] = None,
               resolve: Optional[Callable] = None, tcp: Optional[Callable] = None,
               connectivity: Optional[Callable] = None, link_up: Optional[Callable] = None,
               resolve4: Optional[Callable] = None) -> Diagnosis:
    """Run every step in order and return the diagnosis. Never raises."""
    try:
        return _run(net, nm, wifi, on_update, cancel, ping, resolve, tcp, connectivity, link_up, resolve4)
    except Exception as exc:  # the page must always get a finished diagnosis
        steps = tuple(DoctorStep(key, name, "skip", "check failed") for key, name in STEPS)
        return Diagnosis(steps=steps, done=True, level="warn",
                         verdict=f"The check failed: {str(exc)[:80]}", suggestion=None)


def _run(net, nm, wifi, on_update, cancel, ping, resolve, tcp, connectivity, link_up, resolve4=None) -> Diagnosis:
    ctx = _Context(net, nm, wifi,
                   ping or _default_ping,
                   resolve or _default_resolve,
                   tcp or _default_tcp,
                   connectivity or _default_connectivity,
                   link_up or _default_link_up)
    ctx.cancel = cancel
    # the IPv4-only follow-up explains a stalled lookup; tests inject it explicitly
    if resolve4 is None and resolve is None:
        resolve4 = lambda name: _default_resolve(name, socket.AF_INET)   # noqa: E731
    ctx.resolve4 = resolve4
    order = [key for key, _ in STEPS]
    results: Dict[str, DoctorStep] = {key: DoctorStep(key, name, "pending") for key, name in STEPS}
    suggestions: Dict[str, Optional[str]] = {}

    def snapshot(done: bool = False, verdict: str = "", level: Optional[str] = None,
                 suggestion: Optional[str] = None) -> Diagnosis:
        steps = tuple(results[k] for k in order)
        return Diagnosis(steps=steps, done=done, level=level or _worst(steps), verdict=verdict,
                         suggestion=suggestion)

    def show() -> None:
        emit(on_update, snapshot())

    hard_failed = False
    stopped = False
    for key in order:
        name = _NAMES[key]
        if stopped or cancelled(cancel):
            stopped = True
            results[key] = DoctorStep(key, name, "skip", _SKIP_CANCEL)
            continue
        if hard_failed:
            results[key] = DoctorStep(key, name, "skip", _SKIP_HARD)
            continue
        results[key] = DoctorStep(key, name, "running")
        show()
        if key == "link":
            status, detail, sug = _step_link(ctx)
        elif key == "address":
            status, detail, sug = _step_address(ctx)
        elif key == "router":
            status, detail, sug = _step_router(ctx)
        elif key == "dns":
            status, detail, sug = _step_dns(ctx)
        elif key == "internet":
            status, detail, sug = _step_internet(ctx, results["router"].status)
        else:
            status, detail, sug = _step_portal(ctx)
        if cancelled(cancel):
            # The check was stopped while this step ran: its result is not trusted.
            results[key] = DoctorStep(key, name, "skip", _SKIP_CANCEL)
            stopped = True
            continue
        results[key] = DoctorStep(key, name, status, detail)
        suggestions[key] = sug
        if key in _HARD_STEPS and status == "fail":
            hard_failed = True
        if key == "internet" and status != "fail" and ctx.router_silent and results["router"].status == "fail":
            # Internet works although the router ignores ping: that is normal for some routers.
            results["router"] = DoctorStep("router", _NAMES["router"], "warn", "Router ignores ping (normal for some)")
            suggestions["router"] = None
        show()

    if stopped:
        steps = tuple(results[k] for k in order)
        final = Diagnosis(steps=steps, done=True, level=_worst(steps), verdict="Cancelled", suggestion=None)
        emit(on_update, final)
        return final

    steps = tuple(results[k] for k in order)
    level, verdict, suggestion = _verdict(steps, suggestions, ctx)
    final = Diagnosis(steps=steps, done=True, level=level, verdict=verdict, suggestion=suggestion)
    emit(on_update, final)
    return final
