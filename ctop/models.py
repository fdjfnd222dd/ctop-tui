"""Plain data shapes passed from collectors to renderers.

This file is the CONTRACT between the data layer (ctop/collectors, ctop/battery.py,
ctop/alerts.py) and the drawing layer (ctop/render). Collectors build these;
renderers only read them. Both sides must treat them as immutable snapshots:
a collector publishes a NEW object each refresh and never edits one in place,
so the UI thread can read it at any moment without locks.

A field that is None means "not available on this machine" (no fan, no Wi-Fi,
no permission). Renderers must handle None gracefully - never crash, never print "None".
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Tuple


# ---- battery ---------------------------------------------------------------
@dataclass(frozen=True)
class Battery:
    pct: Optional[int]                 # 0-100, None if unknown
    status: str                        # "Charging" | "Discharging" | "Full" | "Not charging" | "Unknown"
    watts: Optional[float]             # magnitude of power flowing, W (None if the pack does not report it)
    volts: Optional[float]
    wh_now: Optional[float]            # energy left, Wh
    wh_full: Optional[float]           # energy when full today, Wh
    wh_design: Optional[float]         # energy when full new, Wh
    cycles: Optional[int]


# ---- thermals --------------------------------------------------------------
@dataclass(frozen=True)
class TempReading:
    label: str                         # human label: "CPU", "Disk (nvme)", "Wi-Fi card", "Board"
    celsius: float
    kind: str                          # "cpu" | "disk" | "wifi" | "gpu" | "board" | "other"
    high: Optional[float] = None       # sensor's own "getting hot" mark, deg C
    crit: Optional[float] = None       # sensor's own "danger" mark, deg C


@dataclass(frozen=True)
class Fan:
    label: str                         # "Fan 1" if the driver gives no label
    rpm: int
    max_rpm: Optional[int] = None


@dataclass(frozen=True)
class Thermal:
    temps: Tuple[TempReading, ...] = ()
    fans: Tuple[Fan, ...] = ()
    cpu_temp: Optional[float] = None   # the one number the CPU panel shows
    hottest: Optional[TempReading] = None


# ---- network quality -------------------------------------------------------
@dataclass(frozen=True)
class PingResult:
    label: str                         # "Router" | "Internet"
    target: str                        # IP that was pinged
    ms: Optional[float]                # latest round trip, None = lost / unreachable
    avg_ms: Optional[float]            # mean of replies in `history`
    jitter_ms: Optional[float]         # mean absolute difference between consecutive replies
    loss_pct: float                    # % of `history` that got no reply (0-100)
    history: Tuple[Optional[float], ...] = ()   # newest last, max 120, None = lost


@dataclass(frozen=True)
class PingData:
    router: Optional[PingResult] = None     # None when there is no default gateway
    internet: Optional[PingResult] = None


@dataclass(frozen=True)
class WifiData:
    iface: str
    ssid: Optional[str]
    signal_pct: Optional[int]          # 0-100
    signal_dbm: Optional[int]          # e.g. -58
    bitrate_mbps: Optional[float]      # link speed
    freq_mhz: Optional[int]
    channel: Optional[int]
    security: Optional[str]            # e.g. "WPA2", "WPA3", "WPA2 WPA3", "none", None if unknown
    quality: str                       # "Excellent" | "Good" | "Fair" | "Weak" | "Poor" | "Unknown"
    history: Tuple[int, ...] = ()      # signal_pct samples, newest last, max 120


# ---- security / cyber networking page --------------------------------------
@dataclass(frozen=True)
class Listener:
    proto: str                         # "tcp" | "udp"
    address: str                       # bind address as the kernel reports it, "0.0.0.0", "::", "127.0.0.1", ...
    port: int
    pid: Optional[int]                 # None when the owner is another user's process we may not inspect
    process: Optional[str]
    exposure: str                      # "local" (only this computer) | "network" (every network interface) | "specific" (one interface address)


@dataclass(frozen=True)
class Connection:
    proto: str                         # "tcp" | "udp"
    local_port: int
    remote_ip: str
    remote_port: int
    status: str                        # "ESTABLISHED", "TIME_WAIT", ...
    pid: Optional[int]
    process: Optional[str]
    remote_is_private: bool            # RFC1918 / link-local / loopback / CGNAT 100.64/10 / ULA


@dataclass(frozen=True)
class Device:
    ip: str
    mac: str
    iface: str
    state: str                         # "REACHABLE" | "STALE" | "DELAY" | "PROBE" | "FAILED" | "PERMANENT" ...
    is_gateway: bool
    random_mac: bool                   # locally-administered bit set => phone/laptop "private Wi-Fi address"


@dataclass(frozen=True)
class FirewallInfo:
    backend: Optional[str]             # "ufw" | "firewalld" | "nftables" | None when nothing is detected
    active: Optional[bool]             # None = could not tell
    default_incoming: Optional[str]    # "deny" | "reject" | "allow" | None = unknown
    rules_visible: bool                # False: rule list needs root, we only know the summary
    rules: Tuple[str, ...] = ()
    note: str = ""                     # plain-language caveat for the page, e.g. "rule list needs root"


@dataclass(frozen=True)
class SecurityData:
    listeners: Tuple[Listener, ...] = ()
    connections: Tuple[Connection, ...] = ()
    devices: Tuple[Device, ...] = ()
    firewall: FirewallInfo = field(default_factory=lambda: FirewallInfo(None, None, None, False))
    collected_at: float = 0.0          # time.time()


# ---- program details popup -------------------------------------------------
@dataclass(frozen=True)
class ProcessDetail:
    pid: int
    name: str
    cmdline: str                       # joined, truncated to 300 chars
    user: Optional[str]
    ram_bytes: int
    threads: Optional[int]
    open_files: Optional[int]
    started: Optional[float]           # epoch seconds
    status: str                        # psutil status: "running", "sleeping", ...


@dataclass(frozen=True)
class ProgramDetail:
    name: str
    total_processes: int
    processes: Tuple[ProcessDetail, ...] = ()        # biggest RAM first, at most 15
    connections: Tuple[Connection, ...] = ()          # belonging to this program, at most 30
    listeners: Tuple[Listener, ...] = ()
    oldest_started: Optional[float] = None
    total_ram_bytes: int = 0
    total_threads: int = 0


# ---- alerts ----------------------------------------------------------------
@dataclass(frozen=True)
class Alert:
    level: str                         # "info" | "warn" | "crit"
    key: str                           # stable id, e.g. "ram-high", "battery-low"
    text: str                          # one short plain-English sentence
