"""Demo mode: `ctop --demo` runs the whole dashboard on made-up, gently moving numbers.

Why it exists: so anyone can look at ctop (and so the README screenshots can be
made) without exposing a real machine's programs, network names or addresses.

Safety: demo mode never reads the system for what it shows and never stops a
program (the app refuses the kill in demo mode; the fake programs have no
process ids at all). Every address is from the reserved documentation ranges.
"""
from __future__ import annotations

import math
import random
import time
from collections import deque
from types import SimpleNamespace

from ctop.collectors.base import Collector
from ctop.models import (Battery, Connection, Device, Fan, FirewallInfo, Listener, PingData,
                         PingResult, ProcessDetail, ProgramDetail, SecurityData, TempReading,
                         Thermal, WifiData)
from ctop.system import Group, Sampler
from ctop.ui import HIST

GIB = 1024 ** 3
MIB = 1024 ** 2

# name, RAM in GiB, process count, base CPU %, (pids are fake: members stay empty)
PROGRAMS = [
    ("Firefox", 2.41, 28, 14.0), ("Code", 0.92, 9, 6.0), ("Slack", 0.61, 6, 2.0),
    ("Spotify", 0.43, 5, 3.0), ("Thunderbird", 0.31, 2, 0.6), ("LibreOffice", 0.28, 3, 0.2),
    ("node", 0.19, 4, 1.4), ("python3", 0.12, 2, 0.8), ("Plasma (desktop)", 0.15, 1, 1.2),
    ("KWin (desktop)", 0.09, 1, 1.5), ("Konsole", 0.04, 1, 0.3), ("pipewire", 0.03, 2, 0.6),
    ("NetworkManager", 0.02, 1, 0.0), ("systemd", 0.02, 3, 0.0), ("tailscaled", 0.02, 1, 0.1),
    ("kwrite", 0.02, 1, 0.0), ("polkitd", 0.01, 1, 0.0), ("dbus-broker", 0.01, 3, 0.0),
    ("wireplumber", 0.01, 1, 0.0), ("upowerd", 0.01, 1, 0.0), ("tlp-pd", 0.01, 1, 0.0),
]
RAM_SCALE = 0.72        # keeps the demo around 65% full, so no scary banner in screenshots
SYSTEM_GIB = 0.9        # kernel / drivers: used RAM that is not a program


def _wave(t, period, phase=0.0):
    return math.sin(t / period * 2 * math.pi + phase)


class DemoSampler(Sampler):
    """A Sampler whose numbers come from smooth waves instead of the machine."""

    def __init__(self):
        super().__init__()
        self._rng = random.Random(7)
        self._t0 = time.time()
        self.temp_file = None
        total = int(7.4 * GIB)
        self._total = total
        self.net = {"iface": "wlan0", "gateway": "192.0.2.1", "ip": "192.0.2.23",
                    "dns": ["192.0.2.1", "198.51.100.53"], "ssid": "ExampleNet",
                    "ifaces": [("tailscale0", "VPN", True, "203.0.113.7"),
                               ("virbr0", "VM bridge", False, "192.168.122.1"),
                               ("wlan0", "Wi-Fi", True, "192.0.2.23")]}
        self.boot_totals = (int(41.2 * GIB), int(3.1 * GIB))
        self.cursor = None
        # pre-fill the graphs so a screenshot is never an empty chart
        for k in range(HIST):
            self._advance(self._t0 - (HIST - k))
        self.sample_fast()
        self.scan_procs()

    # ---- one point in time -> every live number
    def _advance(self, t):
        x = t - self._t0 + 40
        self.cores = [max(2.0, min(97.0, 22 + 16 * _wave(x, 11, i) + 9 * _wave(x, 3.3, i * 1.9)
                                   + self._rng.uniform(-3, 3))) for i in range(8)]
        self.cpu_hist.append(sum(self.cores) / len(self.cores))
        burst = max(0.0, _wave(x, 37)) ** 2
        self.down = 4 + 55 * burst + 6 * abs(_wave(x, 5)) + self._rng.uniform(0, 2)
        self.up = 0.4 + 5 * burst + 0.8 * abs(_wave(x, 7)) + self._rng.uniform(0, 0.3)
        self.down_hist.append(self.down)
        self.up_hist.append(self.up)
        watts = 7.2 + 2.2 * _wave(x, 29) + 1.0 * _wave(x, 7) + self._rng.uniform(-0.3, 0.3)
        self.watt_hist.append(max(2.0, watts))
        return watts

    def sample_fast(self):
        watts = self._advance(time.time())
        self.freq = 1.8 + 1.4 * (sum(self.cores) / len(self.cores)) / 100
        self.temp = 52 + 0.18 * sum(self.cores) / len(self.cores) * 3 + 2 * _wave(time.time(), 17)
        if self.thermal is not None and self.thermal.cpu_temp is not None:
            self.temp = self.thermal.cpu_temp
        self.load = (1.2 + 0.4 * _wave(time.time(), 40), 1.1, 0.9)
        prog_total = sum(p[1] for p in PROGRAMS) * RAM_SCALE + SYSTEM_GIB
        used = int(prog_total * GIB)
        self.vm = SimpleNamespace(total=self._total, available=self._total - used, cached=int(1.6 * GIB),
                                  percent=used / self._total * 100)
        swap_total = 4 * GIB
        self.swap = SimpleNamespace(total=swap_total, used=int(0.4 * GIB), percent=10.0)
        self.boot_totals = (self.boot_totals[0] + int(self.down * 125000), self.boot_totals[1] + int(self.up * 125000))
        pct = 78
        self.battery = Battery(pct=pct, status="Discharging", watts=max(2.0, watts), volts=11.8,
                               wh_now=pct / 100 * 35.7, wh_full=35.7, wh_design=38.0, cycles=212)
        self.bat = {"pct": pct, "status": "Discharging", "watts": self.battery.watts,
                    "wh_now": self.battery.wh_now, "wh_full": 35.7, "wh_design": 38.0, "cycles": 212}

    def scan_procs(self):
        t = time.time() - self._t0
        groups = []
        for i, (name, gib, count, cpu) in enumerate(PROGRAMS):
            jitter = 1 + 0.015 * _wave(t, 23, i)
            groups.append(Group(name, int(gib * RAM_SCALE * GIB * jitter), count, [],
                                max(0.0, cpu * (1 + 0.5 * _wave(t, 9, i * 2.3)))))
        self.groups = sorted(groups, key=lambda g: -g.bytes)

    def scan_net(self):                        # the network facts above are fixed
        pass


# --------------------------------------------------------------------------
# Fake collectors: same shape as the real ones, data made up
# --------------------------------------------------------------------------
class DemoCollector(Collector):
    """Publishes whatever `make()` returns, every `interval` seconds."""

    def __init__(self, name, make, interval=2.0):
        super().__init__()
        self.name = name
        self.interval = interval
        self._make = make
        self.refresh()                          # data is there from the first frame
        self.last_run = time.time()

    def refresh(self):
        self.data = self._make()


def _thermal(rng):
    cpu = 55 + 6 * _wave(time.time(), 21) + rng.uniform(-1, 1)
    temps = (TempReading("CPU", cpu, "cpu", 90.0, 100.0),
             TempReading("Disk (nvme)", 41.0, "disk", 75.0, 85.0),
             TempReading("Wi-Fi card", 44.0, "wifi", None, None))
    return Thermal(temps=temps, fans=(Fan("Fan 1", 2400 + int(150 * _wave(time.time(), 13)), 5200),),
                   cpu_temp=cpu, hottest=temps[0])


def _ping_factory(rng):
    router, inet = deque(maxlen=120), deque(maxlen=120)
    for k in range(120):
        router.append(max(0.8, 2.2 + rng.uniform(-0.8, 1.6)))
        inet.append(None if k == 61 else max(8.0, 18 + 5 * _wave(k, 20) + rng.uniform(-3, 5)))

    def make():
        router.append(max(0.8, 2.2 + rng.uniform(-0.8, 1.6)))
        inet.append(max(8.0, 18 + rng.uniform(-3, 6)))

        def result(label, target, hist):
            got = [v for v in hist if v is not None]
            jit = sum(abs(a - b) for a, b in zip(got, got[1:])) / max(len(got) - 1, 1)
            return PingResult(label, target, hist[-1], sum(got) / len(got), jit,
                              100.0 * (len(hist) - len(got)) / len(hist), tuple(hist))
        return PingData(router=result("Router", "192.0.2.1", router),
                        internet=result("Internet", "198.51.100.53", inet))
    return make


def _wifi_factory(rng):
    hist = deque((int(82 + 5 * _wave(k, 30) + rng.uniform(-3, 3)) for k in range(120)), maxlen=120)

    def make():
        hist.append(int(82 + 5 * _wave(time.time(), 30) + rng.uniform(-3, 3)))
        return WifiData("wlan0", "ExampleNet", hist[-1], -52, 433.3, 5180, 36, "WPA2", "Excellent", tuple(hist))
    return make


def _security():
    ls = (Listener("tcp", "0.0.0.0", 8000, 4310, "node", "network"),
          Listener("udp", "0.0.0.0", 5353, 812, "avahi-daemon", "network"),
          Listener("tcp", "192.0.2.23", 53, 1190, "dnsmasq", "specific"),
          Listener("tcp", "127.0.0.1", 5432, 1201, "postgres", "local"),
          Listener("tcp", "127.0.0.1", 631, 905, "cupsd", "local"),
          Listener("tcp", "127.0.0.1", 8080, 4122, "python3", "local"))
    cs = tuple(
        Connection("tcp", 40000 + i, ip, 443, "ESTABLISHED", 4000 + i, proc, False)
        for i, (proc, ip) in enumerate([
            ("firefox", "203.0.113.10"), ("firefox", "203.0.113.11"), ("firefox", "198.51.100.20"),
            ("firefox", "203.0.113.12"), ("slack", "198.51.100.80"), ("slack", "198.51.100.81"),
            ("spotify", "203.0.113.90"), ("code", "198.51.100.44"), ("thunderbird", "203.0.113.33")])
    ) + (Connection("tcp", 41000, "192.0.2.77", 631, "ESTABLISHED", 4400, "cups-browsed", True),)
    devs = (Device("192.0.2.1", "00:00:5e:00:53:01", "wlan0", "REACHABLE", True, False),
            Device("192.0.2.14", "02:00:00:aa:bb:14", "wlan0", "REACHABLE", False, True),
            Device("192.0.2.31", "00:00:5e:00:53:31", "wlan0", "STALE", False, False),
            Device("192.0.2.40", "02:00:00:aa:bb:40", "wlan0", "STALE", False, True))
    fw = FirewallInfo("ufw", True, "deny", True,
                      ("22/tcp ALLOW IN 192.0.2.0/24", "8000/tcp ALLOW IN 192.0.2.0/24",
                       "5353/udp ALLOW IN Anywhere"), "")
    return SecurityData(ls, cs, devs, fw, time.time())


def make_demo_collectors():
    rng = random.Random(11)
    sec = _security()
    return {
        "thermal": DemoCollector("thermal", lambda: _thermal(rng), 2.0),
        "ping": DemoCollector("ping", _ping_factory(rng), 3.0),
        "wifi": DemoCollector("wifi", _wifi_factory(rng), 5.0),
        "ports": DemoCollector("ports", lambda: (sec.listeners, sec.connections), 5.0),
        "lan": DemoCollector("lan", lambda: sec.devices, 10.0),
        "firewall": DemoCollector("firewall", lambda: sec.firewall, 30.0),
    }


def demo_detail(name, group):
    """A believable ProgramDetail for the Enter pop-up (fake processes, fake pids)."""
    now = time.time()
    n = min(group.count, 15)
    share = group.bytes // max(group.count, 1)
    procs = tuple(
        ProcessDetail(pid=4100 + i * 7, name=name.lower(),
                      cmdline=(f"/usr/lib/{name.lower()}/{name.lower()} --type=renderer --tab={i}" if i else
                               f"/usr/lib/{name.lower()}/{name.lower()}"),
                      user="demo", ram_bytes=int(share * (1.8 if i == 0 else 0.9)), threads=24 - i,
                      open_files=60 - i * 2, started=now - 3 * 3600 - i * 60, status="sleeping")
        for i in range(n))
    conns = tuple(Connection("tcp", 40000 + i, f"203.0.113.{10 + i}", 443, "ESTABLISHED", 4100, name.lower(), False)
                  for i in range(min(8, group.count)))
    listeners = (Listener("tcp", "127.0.0.1", 8080, 4100, name.lower(), "local"),)
    return ProgramDetail(name=name, total_processes=group.count, processes=procs, connections=conns,
                         listeners=listeners, oldest_started=now - 3 * 3600,
                         total_ram_bytes=group.bytes, total_threads=sum(p.threads or 0 for p in procs),
                         captured_at=now)
