"""Demo data for the Network page (`ctop --demo`).

Like ctop/demo.py: made-up, gently moving numbers, documentation addresses only. Nothing here reads
the machine, changes a setting or sends a packet - the fake tools only wait and publish snapshots.
"""
from __future__ import annotations

import math
import time
from typing import Dict

from ctop.collectors.base import Collector
from ctop.models import (DnsReport, DnsTiming, Diagnosis, DoctorStep, FoundDevice, LanScanResult, LinkInfo,
                         NmState, PortCheckResult, PortResult, SavedConnection, SpeedResult, TraceHop, TraceResult,
                         VpnPeer, VpnStatus, VpnTunnel, WifiNetwork)

STEP = 0.15             # seconds between fake progress updates; tests set it to 0


def _pause(cancel) -> bool:
    """Wait one step; True when the person pressed Esc."""
    if STEP <= 0:
        return bool(cancel and cancel.is_set())
    if cancel is not None:
        return cancel.wait(STEP)
    time.sleep(STEP)
    return False


def _emit(on_update, r):
    if on_update is not None:
        try:
            on_update(r)
        except Exception:
            pass


def _wave(period, phase=0.0):
    return math.sin(time.time() / period * 2 * math.pi + phase)


# ---- collectors --------------------------------------------------------------
_NETS = (  # ssid, last octet of bssid, channel, freq, signal, security, saved, aps
    ("ExampleNet", 1, 36, 5180, 82, "WPA2 WPA3", True, 2),
    ("ExampleNet-Guest", 2, 6, 2437, 70, "WPA2", False, 1),
    ("Neighbour Net", 3, 6, 2437, 58, "WPA2", True, 1),
    ("Cafe: Free", 4, 1, 2412, 44, "", False, 1),
    ("Library WiFi", 5, 11, 2462, 39, "802.1X", False, 3),
    ("PrinterSetup-01", 6, 6, 2437, 33, "", False, 1),
    ("Upstairs 5G", 7, 149, 5745, 31, "WPA2", False, 1),
    ("", 8, 11, 2462, 22, "WPA2", False, 1),
    ("Phone hotspot", 9, 44, 5220, 0, "WPA2", True, 1),
)
_UUID = "3f0c1e2a-1111-2222-3333-44445555666{}"


class _DemoNm(Collector):
    name, interval = "nmstate", 8.0

    def refresh(self):
        now = time.time()
        nets = []
        for i, (ssid, b, ch, f, sig, sec, saved, aps) in enumerate(_NETS):
            if sig == 0:
                continue                                    # the hotspot is saved but not in range
            pct = max(5, min(99, int(sig + 4 * _wave(40, i))))
            nets.append(WifiNetwork(ssid, f"02:00:00:AA:BB:{b:02X}", ch, f, "5 GHz" if f > 5000 else "2.4 GHz",
                                    1170 if f > 5000 else 270, pct, sec, ssid == "ExampleNet", saved, aps))
        nets.sort(key=lambda n: (not n.in_use, -(n.signal_pct or 0)))
        saved = (
            SavedConnection("ExampleNet", _UUID.format(1), "wifi", "wlan0", True, "activated", True, 0, now, "ExampleNet"),
            SavedConnection("Phone hotspot", _UUID.format(2), "wifi", None, False, "", True, 5, now - 5 * 3600, "Phone hotspot"),
            SavedConnection("Neighbour Net", _UUID.format(3), "wifi", None, False, "", True, 0, now - 2 * 86400, "Neighbour Net"),
            SavedConnection("Office VPN", _UUID.format(4), "vpn", None, False, "", False, 0, now - 9 * 86400),
        )
        self.data = NmState(True, "", True, True, None, "connected", "full", "wlan0", tuple(nets), saved, now)


class _DemoLink(Collector):
    name, interval = "wifilink", 5.0

    def refresh(self):
        self.data = LinkInfo("wlan0", True, "02:00:00:AA:BB:01", 5180, 36, "5 GHz", 80, int(-52 + 3 * _wave(30)),
                             960.8, 720.6, "Wi-Fi 6", 20.0, "02:00:00:aa:bb:cc", True, 1500)


class _DemoVpn(Collector):
    name, interval = "vpn", 15.0

    def refresh(self):
        peers = (VpnPeer("laptop-2", "100.64.0.8", "linux", True, False),
                 VpnPeer("phone", "100.64.0.9", "android", True, False),
                 VpnPeer("home-server", "100.64.0.10", "linux", False, False),
                 VpnPeer("tablet", "100.64.0.11", "iOS", False, False))
        self.data = VpnStatus("Running", "100.64.0.7", "example.ts.net", peers,
                              (VpnTunnel("tailscale0", "Tailscale", True, "100.64.0.7"),))


def make_demo_net_collectors() -> Dict[str, Collector]:
    return {"nmstate": _DemoNm(), "wifilink": _DemoLink(), "vpn": _DemoVpn()}


# ---- fake change layer -------------------------------------------------------
class DemoNetctl:
    """Same functions as ctop.netctl; every change only pretends."""

    def __init__(self):
        from ctop import netctl
        self._real = netctl
        self.DNS_PRESETS = netctl.DNS_PRESETS
        self.valid_password = netctl.valid_password
        self.parse_dns_list = netctl.parse_dns_list

    def _ok(self, what):
        return self._real.Outcome(True, f"Demo mode: pretended to {what} - nothing was changed.")

    def get_dns(self, uuid, run=None):
        return (("192.0.2.1",), False)

    def __getattr__(self, name):
        if not hasattr(self._real, name) or name.startswith("_"):
            raise AttributeError(name)
        words = name.replace("_", " ")
        return lambda *a, **kw: self._ok(words)


# ---- fake tools --------------------------------------------------------------
_PATH = (("192.0.2.1", "home", 2.1), ("100.64.0.1", "isp", 9.8), (None, "unknown", None),
         ("198.51.100.9", "internet", 14.2), ("198.51.100.17", "internet", 16.0),
         ("198.51.100.33", "internet", 19.5), ("198.51.100.41", "internet", 22.7), ("203.0.113.5", "target", 24.3))


def demo_trace(target, max_hops=30, probes=3, on_update=None, cancel=None, **kw) -> TraceResult:
    address = target if target[:1].isdigit() else "203.0.113.5"
    hops = []
    r = TraceResult(target, address, (), method="udp")
    _emit(on_update, r)
    for ttl, (ip, kind, ms) in enumerate(_PATH, 1):
        if _pause(cancel):
            r = TraceResult(target, address, tuple(hops), True, False, "udp", "cancelled")
            _emit(on_update, r)
            return r
        if ip is None:
            rtts = (None,) * probes
        else:
            ip = address if kind == "target" else ip
            rtts = tuple(None if (ttl == 4 and p == 1) else round(ms + 0.7 * p, 1) for p in range(probes))
        hops.append(TraceHop(ttl, ip, rtts, kind, kind == "target"))
        r = TraceResult(target, address, tuple(hops), False, False, "udp")
        _emit(on_update, r)
    r = TraceResult(target, address, tuple(hops), True, True, "udp")
    _emit(on_update, r)
    return r


_SERVICES = {22: "SSH", 80: "HTTP", 443: "HTTPS", 3389: "Remote Desktop", 445: "SMB (file sharing)", 53: "DNS", 8080: "HTTP alt"}


def demo_ports(target, ports, timeout=1.5, on_update=None, cancel=None, **kw) -> PortCheckResult:
    address = target if target[:1].isdigit() else "203.0.113.5"
    res = {p: PortResult(p, _SERVICES.get(p, ""), "pending") for p in ports}
    _emit(on_update, PortCheckResult(target, address, tuple(res.values())))
    for i, p in enumerate(ports):
        if _pause(cancel):
            r = PortCheckResult(target, address, tuple(res.values()), True, "cancelled")
            _emit(on_update, r)
            return r
        state = "open" if p in (80, 443) else "closed" if p == 22 else "filtered"
        res[p] = PortResult(p, _SERVICES.get(p, ""), state, 11.0 + 3 * i if state != "filtered" else None)
        _emit(on_update, PortCheckResult(target, address, tuple(res.values())))
    r = PortCheckResult(target, address, tuple(res.values()), True)
    _emit(on_update, r)
    return r


def demo_dns(gateway=None, on_update=None, cancel=None, **kw) -> DnsReport:
    rows = [("192.0.2.1", "your router", True, 38.0), ("1.1.1.1", "Cloudflare", False, 12.0),
            ("9.9.9.9", "Quad9", False, 15.0), ("8.8.8.8", "Google", False, 18.0)]
    out = [DnsTiming(s, l, u) for s, l, u, _ in rows]
    _emit(on_update, DnsReport(tuple(out)))
    for i, (s, l, u, ms) in enumerate(rows):
        if _pause(cancel):
            break
        out[i] = DnsTiming(s, l, u, ms, 3, 3)
        _emit(on_update, DnsReport(tuple(out)))
    r = DnsReport(tuple(out), True, "Your DNS is fine (38 ms).")
    _emit(on_update, r)
    return r


def demo_speed(on_update=None, cancel=None, **kw) -> SpeedResult:
    r = SpeedResult("latency", 0.05)
    _emit(on_update, r)
    if _pause(cancel):
        return _cancelled(on_update, r)
    samples = []
    for i in range(12):
        if _pause(cancel):
            return _cancelled(on_update, r)
        samples.append(min(245.0, 40.0 + 30 * i))
        r = SpeedResult("download", 0.1 + 0.5 * i / 12, 14.0, None, samples[-1], None, int(2.1e6 * (i + 1)),
                        samples=tuple(samples))
        _emit(on_update, r)
    up = []
    for i in range(8):
        if _pause(cancel):
            return _cancelled(on_update, r)
        up.append(min(38.0, 8.0 + 6 * i))
        r = SpeedResult("upload", 0.6 + 0.4 * i / 8, 14.0, 48.0, 245.3, up[-1], int(25e6 + 0.8e6 * (i + 1)),
                        samples=tuple(up))
        _emit(on_update, r)
    r = SpeedResult("done", 1.0, 14.0, 48.0, 245.3, 38.1, 31_000_000, "B", tuple(up))
    _emit(on_update, r)
    return r


def _cancelled(on_update, r):
    r = SpeedResult("cancelled", r.progress, r.idle_ms, r.loaded_ms, r.down_mbps, r.up_mbps, r.bytes_used, None, r.samples)
    _emit(on_update, r)
    return r


_DOCTOR = (("link", "Network link", "Wi-Fi ExampleNet, signal Good (-52 dBm)"), ("address", "IP address", "192.0.2.23"),
           ("router", "Router", "2 ms"), ("dns", "Name lookup (DNS)", "18 ms"), ("internet", "Internet", "21 ms"),
           ("portal", "Login page check", "No login page"))


def demo_doctor(net=None, nm=None, wifi=None, on_update=None, cancel=None, **kw) -> Diagnosis:
    steps = [DoctorStep(k, n, "pending") for k, n, _ in _DOCTOR]
    for i, (k, n, d) in enumerate(_DOCTOR):
        steps[i] = DoctorStep(k, n, "running")
        _emit(on_update, Diagnosis(tuple(steps)))
        if _pause(cancel):
            steps = [s if s.status in ("ok",) else DoctorStep(s.key, s.name, "skip") for s in steps]
            r = Diagnosis(tuple(steps), True, "ok", "Cancelled")
            _emit(on_update, r)
            return r
        steps[i] = DoctorStep(k, n, "ok", d)
    r = Diagnosis(tuple(steps), True, "ok", "Everything looks healthy: router 2 ms, DNS 18 ms, internet 21 ms.")
    _emit(on_update, r)
    return r


_DEVICES = (("192.0.2.1", "02:00:00:aa:bb:01", False, True, False), ("192.0.2.23", "02:00:00:aa:bb:cc", True, False, True),
            ("192.0.2.31", "02:00:00:aa:bb:31", False, False, False), ("192.0.2.40", "06:00:00:aa:bb:40", True, False, False),
            ("192.0.2.52", "02:00:00:aa:bb:52", False, False, False), ("192.0.2.77", "0a:00:00:aa:bb:77", True, False, False),
            ("192.0.2.90", "02:00:00:aa:bb:90", False, False, False))


def demo_lanscan(on_update=None, cancel=None, **kw) -> LanScanResult:
    total = 253
    for probed in range(0, total, 32):
        _emit(on_update, LanScanResult("192.0.2.0/24", (), probed, total))
        if _pause(cancel):
            r = LanScanResult("192.0.2.0/24", (), probed, total, True, "cancelled")
            _emit(on_update, r)
            return r
    devs = tuple(FoundDevice(ip, mac, rnd, gw, me) for ip, mac, rnd, gw, me in _DEVICES)
    r = LanScanResult("192.0.2.0/24", devs, total, total, True)
    _emit(on_update, r)
    return r


__all__ = ["STEP", "DemoNetctl", "make_demo_net_collectors", "demo_trace", "demo_ports", "demo_dns",
           "demo_speed", "demo_doctor", "demo_lanscan"]
