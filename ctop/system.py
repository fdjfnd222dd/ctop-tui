"""Data collection: CPU, memory, per-program grouping, network and battery. No UI in here."""
import os
import socket
import struct
import time
from collections import deque
from typing import List, NamedTuple, Tuple

import psutil

from ctop.battery import read_battery
from ctop.ui import HIST

POWER_SUPPLY = "/sys/class/power_supply"
PSS_THRESHOLD = 30 * 1024 * 1024  # only processes above this get the exact (slower) PSS read

# --------------------------------------------------------------------------
# Data collection - no UI in here
# --------------------------------------------------------------------------
FRIENDLY = {"claude": "Claude Code", "firefox": "Firefox", "chrome": "Chrome", "kwin_wayland": "KWin (desktop)",
            "plasmashell": "Plasma (desktop)", "Xwayland": "XWayland", "konsole": "Konsole"}
INTERPRETERS = ("python", "node", "bash", "sh", "java", "perl", "ruby")


def prog_name(info):
    """Group key for a process: all Firefox tabs -> 'Firefox', scripts -> script name."""
    name = info.get("name") or "?"
    exe = (info.get("exe") or "").replace(" (deleted)", "")
    base = os.path.basename(exe)
    if base.startswith("python"):
        args = (info.get("cmdline") or [])[1:]
        it = iter(args)
        for a in it:
            if a == "-m":
                return next(it, name)
            if not a.startswith("-"):
                return os.path.basename(a)
        return name
    # Claude Code installs as ".../versions/2.1.296" - a version number is no name
    if base.replace(".", "").isdigit():
        parts = [x for x in exe.split("/") if x and x != "versions" and not x.replace(".", "").isdigit()]
        name = parts[-1] if parts else name
    elif base and not base.startswith(INTERPRETERS):
        name = base
    return FRIENDLY.get(name, name)


def _status_mem(pid):
    """Anonymous + shared-memory RSS from /proc/PID/status: cheap, readable for any user."""
    anon = shm = 0
    with open(f"/proc/{pid}/status") as f:
        for line in f:
            if line.startswith("RssAnon:"):
                anon = int(line.split()[1])
            elif line.startswith("RssShmem:"):
                shm = int(line.split()[1])
    return (anon + shm) * 1024


def read_proc_mem(pid):
    """Bytes this process really costs.

    Small processes: the cheap status number is plenty accurate. Big ones
    (Firefox tabs, Claude) share pages with siblings, so there we pay for PSS,
    which splits shared pages fairly. That keeps the scan around 30 ms instead
    of ~250 ms - this matters because ctop itself must not drain the battery.
    """
    try:
        rough = _status_mem(pid)
    except OSError:
        return 0
    if rough < PSS_THRESHOLD:
        return rough
    try:
        anon = shm = 0
        with open(f"/proc/{pid}/smaps_rollup") as f:
            for line in f:
                if line.startswith("Pss_Anon:"):
                    anon = int(line.split()[1])
                elif line.startswith("Pss_Shmem:"):
                    shm = int(line.split()[1])
        return (anon + shm) * 1024
    except OSError:                         # another user's process: keep the rough number
        return rough


def find_cpu_temp_file():
    """Path of the CPU package temperature file, found once at start-up.

    psutil.sensors_temperatures() reads every sensor in the machine (~50 ms),
    far too heavy to run every second, so we locate the single file we need.
    """
    base = "/sys/class/hwmon"
    try:
        for d in sorted(os.listdir(base)):
            with open(f"{base}/{d}/name") as f:
                if f.read().strip() != "coretemp":
                    continue
            for n in sorted(x for x in os.listdir(f"{base}/{d}") if x.endswith("_label")):
                with open(f"{base}/{d}/{n}") as f:
                    if f.read().startswith("Package"):
                        return f"{base}/{d}/{n.replace('_label', '_input')}"
            return f"{base}/{d}/temp1_input"
    except OSError:
        pass
    return None


def default_route():
    """(interface, gateway) of the lowest-metric default route, or (None, None)."""
    best = None
    try:
        with open("/proc/net/route") as f:
            next(f)
            for line in f:
                p = line.split()
                if p[1] == "00000000" and int(p[3], 16) & 2:
                    metric = int(p[6])
                    if best is None or metric < best[0]:
                        gw = socket.inet_ntoa(struct.pack("<L", int(p[2], 16)))
                        best = (metric, p[0], gw)
    except (OSError, ValueError, IndexError):
        pass
    return (best[1], best[2]) if best else (None, None)


def iface_kind(name):
    for prefix, kind in (("wl", "Wi-Fi"), ("en", "Ethernet"), ("eth", "Ethernet"),
                         ("tailscale", "VPN"), ("tun", "VPN"), ("wg", "VPN"),
                         ("virbr", "VM bridge"), ("docker", "Container"), ("br", "Bridge")):
        if name.startswith(prefix):
            return kind
    return "Other"


class Group(NamedTuple):
    """One program = all processes that share a name (every Firefox tab is one Group)."""
    name: str
    bytes: int            # real RAM cost (PSS-based, see read_proc_mem)
    count: int            # how many processes
    members: List[Tuple[int, float]]   # [(pid, start_time)] - what the kill safety net checks
    cpu: float            # percent of ONE core, like top: 100 = one core fully busy


class Sampler:
    """Holds every number the panels draw. Fast stats are sampled on the UI
    thread (they are cheap); slow scans run in background threads."""

    def __init__(self):
        # CPU
        self.cores = []
        self.cpu_hist = deque(maxlen=HIST)
        self.freq = None
        self.temp = None
        self.load = (0, 0, 0)
        psutil.cpu_percent(percpu=True)             # prime: first call is always 0
        self.temp_file = find_cpu_temp_file()
        # memory
        self.vm = psutil.virtual_memory()
        self.swap = psutil.swap_memory()
        self.groups = []                            # [Group] biggest RAM first (the ring always uses this)
        self.sort_key = "ram"                       # how the list is ordered: ram | cpu | name
        self.filter_text = ""                       # search box: keep programs whose name contains this
        self.search_mode = False                    # True while the user is typing a search
        self.paused = False
        self.interval = 1.0                         # seconds between samples (the + / - keys change it)
        self._cpu_prev = {}                         # (pid, start) -> total cpu seconds at last scan
        self._scan_time = None
        self.cursor = None                          # program name currently selected
        self.armed = None                           # program waiting for the 2nd 'x'
        self.armed_at = 0.0
        self.term_sent = {}                         # name -> when a polite stop was sent
        self.procs_busy = False
        self._names = {}                            # (pid, start time) -> program name
        # network
        self.down_hist = deque(maxlen=HIST)
        self.up_hist = deque(maxlen=HIST)
        self.down = self.up = 0.0
        self.net = {}                               # slow info, refreshed by scan_net
        self.net_busy = False
        self._net_last = None
        self.boot_totals = (0, 0)
        # battery (portable reader: ctop/battery.py)
        self.battery = None                         # Battery model or None
        self.bat = {}                               # same numbers as a dict, what the battery panel reads
        self.watt_hist = deque(maxlen=HIST)
        # snapshots published by background collectors (ctop/collectors); None until the first run
        self.thermal = None
        self.ping = None
        self.wifi = None
        self.sec = None                             # SecurityData
        self.alerts = []

    # ---- fast sampling (every 1 s)
    def sample_fast(self):
        now = time.time()
        self.cores = psutil.cpu_percent(percpu=True)
        total = sum(self.cores) / max(len(self.cores), 1)
        self.cpu_hist.append(total)
        try:
            self.freq = psutil.cpu_freq().current / 1000
        except Exception:
            self.freq = None
        if self.thermal is not None and self.thermal.cpu_temp is not None:
            self.temp = self.thermal.cpu_temp
        else:
            try:
                with open(self.temp_file) as f:
                    self.temp = int(f.read()) / 1000
            except (OSError, ValueError, TypeError):
                self.temp = None
        self.load = os.getloadavg()
        self.vm = psutil.virtual_memory()
        self.swap = psutil.swap_memory()

        # network speed on the default interface (falls back to everything but lo)
        counters = psutil.net_io_counters(pernic=True)
        iface = self.net.get("iface")
        if iface in counters:
            cur = (counters[iface].bytes_recv, counters[iface].bytes_sent)
        else:
            cur = (sum(c.bytes_recv for n, c in counters.items() if n != "lo"),
                   sum(c.bytes_sent for n, c in counters.items() if n != "lo"))
        if self._net_last and self._net_last[2] == iface:
            dt = max(now - self._net_last[3], 1e-3)
            self.down = max(cur[0] - self._net_last[0], 0) * 8 / dt / 1e6
            self.up = max(cur[1] - self._net_last[1], 0) * 8 / dt / 1e6
        self._net_last = (cur[0], cur[1], iface, now)
        self.boot_totals = cur
        self.down_hist.append(self.down)
        self.up_hist.append(self.up)

        # battery: read_battery() copes with charge_*/energy_* and current*voltage/power_now packs
        b = read_battery()
        self.battery = b
        if b is None:
            self.bat = {}
        else:
            self.bat = {"pct": b.pct, "status": b.status, "watts": b.watts,
                        "wh_now": b.wh_now or 0.0, "wh_full": b.wh_full or 0.0,
                        "wh_design": b.wh_design or 0.0, "cycles": b.cycles}
            self.watt_hist.append(b.watts or 0.0)

    # ---- slow scans (background threads)
    def scan_procs(self):
        self.procs_busy = True
        try:
            groups, alive = {}, set()
            now, cpu_prev = time.time(), {}
            elapsed = (now - self._scan_time) if self._scan_time else None
            for p in psutil.process_iter():
                try:
                    key = (p.pid, p.create_time())  # create_time guards against pid reuse
                    name = self._names.get(key)
                    if name is None:
                        name = self._names[key] = prog_name(p.as_dict(["name", "exe", "cmdline"]))
                except psutil.Error:
                    continue
                alive.add(key)
                try:                                # CPU use = change in used CPU-seconds / elapsed time
                    ct = p.cpu_times()
                    used = ct.user + ct.system
                    cpu_prev[key] = used
                    before = self._cpu_prev.get(key)
                    cpu = max(used - before, 0) / elapsed * 100 if before is not None and elapsed else 0.0
                except psutil.Error:
                    cpu = 0.0
                mem = read_proc_mem(p.pid)
                if mem <= 0:                        # kernel threads cost no user RAM
                    continue
                g = groups.setdefault(name, [0, 0, [], 0.0])
                g[0] += mem
                g[1] += 1
                g[2].append(key)                    # (pid, start time) - used by the kill safety net
                g[3] += cpu
            for key in set(self._names) - alive:    # forget processes that exited
                del self._names[key]
            self._cpu_prev, self._scan_time = cpu_prev, now
            self.groups = sorted((Group(n, v[0], v[1], v[2], v[3]) for n, v in groups.items()),
                                 key=lambda x: -x.bytes)
        finally:
            self.procs_busy = False

    @property
    def history_minutes(self):
        """How much time the graphs cover at the current refresh speed."""
        return round(HIST * self.interval / 60, 1)

    def visible_groups(self):
        """The program list as the user sees it: filtered by the search box, then sorted."""
        g = self.groups
        if self.filter_text:
            needle = self.filter_text.lower()
            g = [x for x in g if needle in x.name.lower()]
        if self.sort_key == "cpu":
            g = sorted(g, key=lambda x: (-x.cpu, -x.bytes))
        elif self.sort_key == "name":
            g = sorted(g, key=lambda x: x.name.lower())
        return list(g)

    def scan_net(self):
        self.net_busy = True
        try:
            iface, gw = default_route()
            info = {"iface": iface, "gateway": gw, "dns": [], "ip": None,
                    "ifaces": []}
            try:
                with open("/etc/resolv.conf") as f:
                    info["dns"] = [l.split()[1] for l in f if l.startswith("nameserver")]
            except OSError:
                pass
            stats = psutil.net_if_stats()
            for name, addrs in sorted(psutil.net_if_addrs().items()):
                if name == "lo":
                    continue
                v4 = next((a.address for a in addrs if a.family == socket.AF_INET), None)
                up = name in stats and stats[name].isup
                info["ifaces"].append((name, iface_kind(name), up, v4))
                if name == iface:
                    info["ip"] = v4
            self.net = info
        finally:
            self.net_busy = False
