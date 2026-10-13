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
    captured_at: Optional[float] = None               # time.time() when collected; the popup shows its age


# ---- alerts ----------------------------------------------------------------
@dataclass(frozen=True)
class Alert:
    level: str                         # "info" | "warn" | "crit"
    key: str                           # stable id, e.g. "ram-high", "battery-low"
    text: str                          # one short plain-English sentence


# ---- network page (page 3) -------------------------------------------------
# Everything below is shown on the Network page. Collectors in ctop/collectors/
# (nmstate, wifilink, vpn) and the on-demand tools in ctop/tools/ build these.
@dataclass(frozen=True)
class WifiNetwork:
    """One SSID seen in the last Wi-Fi scan (several access points of one SSID are merged)."""
    ssid: str                          # "" = hidden network
    bssid: str                         # strongest access point, upper case "AA:BB:CC:DD:EE:FF"
    channel: Optional[int]
    freq_mhz: Optional[int]
    band: str                          # "2.4 GHz" | "5 GHz" | "6 GHz" | "?"
    rate_mbps: Optional[int]           # the access point's maximum rate
    signal_pct: Optional[int]          # 0-100
    security: str                      # "WPA2", "WPA WPA2", "WPA3", "802.1X", ... ; "" = open network
    in_use: bool                       # this computer is connected to it right now
    saved: bool                        # a saved profile exists for this SSID
    aps: int = 1                       # how many access points broadcast this SSID (mesh / extenders)


@dataclass(frozen=True)
class SavedConnection:
    """One NetworkManager profile (a remembered Wi-Fi, a cable, a VPN...)."""
    name: str
    uuid: str
    kind: str                          # "wifi" | "ethernet" | "vpn" | "wireguard" | "other"
    device: Optional[str]              # interface it is active on, None when inactive
    active: bool
    state: str                         # "activated" | "activating" | "deactivating" | "" when inactive
    autoconnect: bool
    priority: int                      # higher wins when several saved networks are in range
    last_used: Optional[float]         # epoch seconds, None = never
    ssid: Optional[str] = None         # Wi-Fi profiles only; may differ from `name`


@dataclass(frozen=True)
class NmState:
    """What NetworkManager knows: radios, nearby networks, saved profiles."""
    available: bool                    # False: no nmcli / NetworkManager not running
    reason: str = ""                   # plain words for the page when not available
    wifi_enabled: Optional[bool] = None
    wifi_hw: Optional[bool] = None     # False = hardware switch / rfkill block
    wwan_enabled: Optional[bool] = None
    state: str = ""                    # nmcli general STATE: "connected", "disconnected", "asleep", ...
    connectivity: str = ""             # "full" | "limited" | "portal" | "none" | "unknown"
    wifi_device: Optional[str] = None  # e.g. "wlan0", None = no Wi-Fi hardware
    networks: Tuple[WifiNetwork, ...] = ()     # in-use first, then strongest first
    saved: Tuple[SavedConnection, ...] = ()    # active first, then most recently used
    scanned_at: float = 0.0            # time.time() of this snapshot


@dataclass(frozen=True)
class LinkInfo:
    """Details of the Wi-Fi link this computer is on (from `iw` and /sys)."""
    iface: str
    connected: bool
    bssid: Optional[str] = None        # the access point you are attached to
    freq_mhz: Optional[int] = None
    channel: Optional[int] = None
    band: str = "?"
    width_mhz: Optional[int] = None    # 20 / 40 / 80 / 160 / 320
    signal_dbm: Optional[int] = None
    rx_mbps: Optional[float] = None
    tx_mbps: Optional[float] = None
    standard: Optional[str] = None     # "Wi-Fi 4" (HT) | "Wi-Fi 5" (VHT) | "Wi-Fi 6" (HE) | "Wi-Fi 7" (EHT) | None
    tx_power_dbm: Optional[float] = None
    mac: Optional[str] = None
    random_mac: Optional[bool] = None  # locally administered address = privacy MAC
    mtu: Optional[int] = None


@dataclass(frozen=True)
class ChannelLoad:
    band: str                          # "2.4 GHz" | "5 GHz" | "6 GHz"
    channel: int
    networks: int                      # access points heard on this channel
    strongest_pct: int                 # strongest of them, 0-100
    mine: bool                         # the network you are on uses this channel


@dataclass(frozen=True)
class ChannelReport:
    loads: Tuple[ChannelLoad, ...] = ()        # sorted by band, then channel
    tip: str = ""                      # one plain sentence, "" when nothing useful to say


@dataclass(frozen=True)
class VpnPeer:
    name: str
    ip: Optional[str]
    os: str
    online: bool
    exit_node: bool                    # traffic is going out through this peer right now


@dataclass(frozen=True)
class VpnTunnel:
    name: str                          # interface name, e.g. "wg0", "tun0", "tailscale0"
    kind: str                          # "WireGuard" | "Tailscale" | "OpenVPN/tun" | "VPN"
    up: bool
    ip: Optional[str]


@dataclass(frozen=True)
class VpnStatus:
    tailscale: Optional[str] = None    # None = not installed; "Running" | "Stopped" | "NeedsLogin" | "Starting" | ...
    tailscale_ip: Optional[str] = None
    tailnet: Optional[str] = None
    peers: Tuple[VpnPeer, ...] = ()    # online first, then by name
    tunnels: Tuple[VpnTunnel, ...] = ()


@dataclass(frozen=True)
class NetEvent:
    ts: float                          # time.time()
    level: str                         # "info" | "warn" | "crit"
    text: str                          # one short plain sentence


# ---- network tools (run only when the user asks) ----------------------------
@dataclass(frozen=True)
class TraceHop:
    ttl: int
    ip: Optional[str]                  # None = no answer from this hop ("* * *")
    rtts: Tuple[Optional[float], ...] = ()   # one per probe, ms, None = lost
    kind: str = "unknown"              # "home" (private LAN) | "isp" (CGNAT / ISP) | "internet" | "target" | "unknown"
    reached: bool = False              # this hop is the destination itself

    @property
    def replies(self) -> Tuple[float, ...]:
        return tuple(r for r in self.rtts if r is not None)

    @property
    def avg_ms(self) -> Optional[float]:
        r = self.replies
        return sum(r) / len(r) if r else None

    @property
    def loss_pct(self) -> float:
        return 100.0 * (len(self.rtts) - len(self.replies)) / len(self.rtts) if self.rtts else 0.0


@dataclass(frozen=True)
class TraceResult:
    target: str                        # what the user typed
    address: Optional[str] = None      # resolved IP
    hops: Tuple[TraceHop, ...] = ()
    done: bool = False
    reached: bool = False
    method: str = ""                   # "udp" (built-in) | "tracepath"
    error: Optional[str] = None        # plain words, e.g. "could not find that name"


@dataclass(frozen=True)
class PortResult:
    port: int
    service: str                       # "HTTPS", "SSH", "" when unknown
    state: str                         # "open" | "closed" | "filtered" | "error" | "pending"
    ms: Optional[float] = None


@dataclass(frozen=True)
class PortCheckResult:
    target: str
    address: Optional[str] = None
    results: Tuple[PortResult, ...] = ()
    done: bool = False
    error: Optional[str] = None


@dataclass(frozen=True)
class DnsTiming:
    server: str                        # IP address
    label: str                         # "your router", "Cloudflare", "Quad9", "Google", "configured"
    in_use: bool                       # this computer uses it right now
    ms: Optional[float] = None         # median of the answered tries
    answered: int = 0
    tries: int = 0
    error: Optional[str] = None


@dataclass(frozen=True)
class DnsReport:
    timings: Tuple[DnsTiming, ...] = ()
    done: bool = False
    verdict: str = ""                  # one plain sentence


@dataclass(frozen=True)
class SpeedResult:
    stage: str = "starting"            # "starting" | "latency" | "download" | "upload" | "done" | "error" | "cancelled"
    progress: float = 0.0              # 0-1 over the whole test
    idle_ms: Optional[float] = None    # latency with an idle line
    loaded_ms: Optional[float] = None  # latency while downloading (bufferbloat)
    down_mbps: Optional[float] = None
    up_mbps: Optional[float] = None
    bytes_used: int = 0                # data spent by the test so far
    grade: Optional[str] = None        # "A" .. "F" for lag under load
    samples: Tuple[float, ...] = ()    # live Mbps readings of the current stage, for the graph
    error: Optional[str] = None


@dataclass(frozen=True)
class DoctorStep:
    key: str                           # "link" | "address" | "router" | "dns" | "internet" | "portal"
    name: str                          # "Wi-Fi link", "IP address", ...
    status: str                        # "pending" | "running" | "ok" | "warn" | "fail" | "skip"
    detail: str = ""


@dataclass(frozen=True)
class Diagnosis:
    steps: Tuple[DoctorStep, ...] = ()
    done: bool = False
    level: str = "ok"                  # "ok" | "warn" | "fail"
    verdict: str = ""                  # one plain sentence
    suggestion: Optional[str] = None   # an action id the page can offer: "reconnect" | "dns" | "portal" | "wifi-on" | None


@dataclass(frozen=True)
class FoundDevice:
    ip: str
    mac: Optional[str]
    random_mac: bool
    is_gateway: bool
    is_me: bool
    ms: Optional[float] = None         # reply time when the device answered a probe


@dataclass(frozen=True)
class LanScanResult:
    subnet: Optional[str] = None       # e.g. "192.0.2.0/24"
    devices: Tuple[FoundDevice, ...] = ()
    probed: int = 0
    total: int = 0
    done: bool = False
    error: Optional[str] = None
