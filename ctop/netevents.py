"""Network event log: "why did my internet just drop?" in plain sentences.

observe() compares each new snapshot with the previous one and records only the changes.
Kept in memory (never written to disk), and it never reads the clock: the caller passes `now`.
"""
from __future__ import annotations

from collections import deque
from typing import Dict, Optional, Tuple

from ctop.models import LinkInfo, NetEvent, NmState, PingData, PingResult, VpnStatus, WifiData

_QUALITY_RANK = {"Poor": 0, "Weak": 1, "Fair": 2, "Good": 3, "Excellent": 4}
_LOSS_RUN = 3              # this many lost samples in a row start a "stopped answering" episode
_SPIKE_FLOOR_MS = 150.0    # a spike must be at least this slow...
_SPIKE_FACTOR = 4.0        # ...and this many times slower than normal
_SPIKE_GAP_S = 60.0        # at most one spike event per this many seconds
_DEBOUNCE_S = 10.0         # the same sentence is not logged twice within this window
_PRUNE_AT = 256            # keep the debounce table small


def _known(band: Optional[str]) -> bool:
    return band not in (None, "?", "")


def _where(link: LinkInfo) -> str:
    parts = []
    if _known(link.band):
        parts.append(link.band)
    if link.channel is not None:
        parts.append("channel %d" % link.channel)
    return " (%s)" % ", ".join(parts) if parts else ""


class EventLog:
    MAX = 200

    def __init__(self):
        self._weak = False
        self._events: deque = deque(maxlen=self.MAX)
        self._cache: Optional[Tuple[NetEvent, ...]] = None
        self._recent: Dict[str, float] = {}      # text -> time it was last logged (debounce)
        self._seen = set()                       # sources that have had their first observation
        self._ssid: Optional[str] = None
        self._quality: Optional[int] = None      # rank from _QUALITY_RANK
        self._bssid: Optional[str] = None
        self._band: Optional[str] = None
        self._ip: Optional[str] = None
        self._gateway: Optional[str] = None
        self._loss: Dict[str, Optional[float]] = {}   # "Internet"/"Router" -> episode start, None = answering
        self._last_spike: Optional[float] = None
        self._wifi_enabled: Optional[bool] = None
        self._connectivity: Optional[str] = None
        self._tailscale: Optional[str] = None
        self._tunnels: Dict[str, bool] = {}      # interface name -> up

    # ---- public -------------------------------------------------------------
    @property
    def events(self) -> Tuple[NetEvent, ...]:
        # Rebuilt only after something was added, so the page can compare by identity.
        if self._cache is None:
            self._cache = tuple(self._events)
        return self._cache

    def add(self, now: float, level: str, text: str) -> None:
        last = self._recent.get(text)
        if last is not None and now - last < _DEBOUNCE_S:
            return
        self._recent[text] = now
        if len(self._recent) > _PRUNE_AT:
            self._recent = {t: ts for t, ts in self._recent.items() if now - ts < _DEBOUNCE_S}
        self._events.append(NetEvent(ts=now, level=level, text=text))
        self._cache = None

    def observe(self, now: float, wifi: Optional[WifiData] = None, link: Optional[LinkInfo] = None,
                ping: Optional[PingData] = None, net: Optional[dict] = None,
                nm: Optional[NmState] = None, vpn: Optional[VpnStatus] = None) -> None:
        # A None argument means "nothing new", so that source is not compared this time.
        ssid_before = self._ssid
        if wifi is not None:
            self._observe_wifi(now, wifi)
        if link is not None:
            same_network = self._ssid is not None and self._ssid == ssid_before
            self._observe_link(now, link, same_network)
        if ping is not None:
            self._observe_ping(now, ping)
        if net is not None:
            self._observe_net(now, net)
        if nm is not None:
            self._observe_nm(now, nm)
        if vpn is not None:
            self._observe_vpn(now, vpn)

    # ---- per source ---------------------------------------------------------
    def _first(self, source: str) -> bool:
        # The first snapshot of a source only records state; comparing needs a previous one.
        first = source not in self._seen
        self._seen.add(source)
        return first

    def _observe_wifi(self, now: float, wifi: WifiData) -> None:
        first = self._first("wifi")
        prev_ssid, prev_quality = self._ssid, self._quality
        self._ssid = wifi.ssid
        if wifi.quality in _QUALITY_RANK:
            self._quality = _QUALITY_RANK[wifi.quality]
        if first:
            return

        if prev_ssid is None and wifi.ssid is not None:
            self.add(now, "info", "Connected to %s" % wifi.ssid)
        elif prev_ssid is not None and wifi.ssid is None:
            self.add(now, "warn", "Wi-Fi dropped (was %s)" % prev_ssid)
        elif prev_ssid is not None and wifi.ssid is not None and prev_ssid != wifi.ssid:
            self.add(now, "info", "Switched Wi-Fi from %s to %s" % (prev_ssid, wifi.ssid))

        new_q = self._quality
        if new_q is None:
            return
        if prev_quality is None:
            self._weak = new_q <= 1                       # first look: remember, do not report
            return
        # Hysteresis: weak below Fair, healthy again only at Good, so a signal sitting on the line stays quiet.
        if not self._weak and new_q <= 1:
            self._weak = True
            if wifi.signal_dbm is not None:
                self.add(now, "warn", "Wi-Fi signal became weak (%d dBm)" % wifi.signal_dbm)
            else:
                self.add(now, "warn", "Wi-Fi signal became weak")
        elif self._weak and new_q >= 3:
            self._weak = False
            self.add(now, "info", "Wi-Fi signal recovered")

    def _observe_link(self, now: float, link: LinkInfo, same_network: bool) -> None:
        first = self._first("link")
        prev_bssid, prev_band = self._bssid, self._band
        self._bssid, self._band = link.bssid, link.band
        if first:
            return

        if prev_bssid is not None and link.bssid is not None:
            if prev_bssid != link.bssid and same_network:
                self.add(now, "info", "Roamed to another access point%s" % _where(link))
            elif (prev_bssid == link.bssid and _known(prev_band) and _known(link.band)
                  and prev_band != link.band):
                self.add(now, "info", "Moved to %s" % link.band)

    def _observe_net(self, now: float, net: dict) -> None:
        first = self._first("net")
        ip, gateway = net.get("ip"), net.get("gateway")
        prev_ip, prev_gw = self._ip, self._gateway
        self._ip, self._gateway = ip, gateway
        if first:
            return

        if prev_ip is None and ip is not None:
            self.add(now, "info", "Got an IP address: %s" % ip)
        elif prev_ip is not None and ip is None:
            self.add(now, "warn", "Lost the IP address")
        elif prev_ip is not None and ip is not None and prev_ip != ip:
            self.add(now, "info", "IP address changed: %s -> %s" % (prev_ip, ip))

        if prev_gw is not None and gateway is not None and prev_gw != gateway:
            self.add(now, "info", "Router changed: %s -> %s" % (prev_gw, gateway))

    def _observe_ping(self, now: float, ping: PingData) -> None:
        for label, result in (("Internet", ping.internet), ("Router", ping.router)):
            if result is not None:
                self._observe_target(now, label, result)

    def _observe_target(self, now: float, label: str, result: PingResult) -> None:
        first = label not in self._loss
        episode = self._loss.get(label)
        history = result.history
        lost_run = len(history) >= _LOSS_RUN and all(x is None for x in history[-_LOSS_RUN:])
        latest = history[-1] if history else result.ms

        if first:
            # Recording state only: we do not know when an episode that is already running began.
            self._loss[label] = now if lost_run else None
            return

        if episode is None:
            if lost_run:
                self._loss[label] = now
                self.add(now, "warn", "%s stopped answering" % label)
            elif label == "Internet":
                self._check_spike(now, result)
        elif latest is not None:
            self._loss[label] = None
            self.add(now, "info", "%s back after %d s" % (label, int(now - episode)))

    def _check_spike(self, now: float, result: PingResult) -> None:
        ms, avg = result.ms, result.avg_ms
        if ms is None or avg is None or ms <= max(_SPIKE_FLOOR_MS, _SPIKE_FACTOR * avg):
            return
        if self._last_spike is not None and now - self._last_spike < _SPIKE_GAP_S:
            return
        self._last_spike = now
        self.add(now, "warn", "Lag spike: %d ms to the internet (normally %d ms)" % (round(ms), round(avg)))

    def _observe_nm(self, now: float, nm: NmState) -> None:
        first = self._first("nm")
        prev_conn, prev_wifi = self._connectivity, self._wifi_enabled
        # While NetworkManager is offline, "none" only means "no link"; the Wi-Fi events already
        # say so, and "connected, but no internet" would be wrong. Keep the last real value.
        offline = nm.state in ("disconnected", "disconnecting", "asleep")
        if nm.connectivity and not offline:
            self._connectivity = nm.connectivity
        if nm.wifi_enabled is not None:
            self._wifi_enabled = nm.wifi_enabled
        if first:
            return

        conn = "" if offline else nm.connectivity
        if prev_conn is not None and conn and conn != prev_conn:
            if conn == "portal":
                self.add(now, "warn", "Captive portal: this network wants a login page")
            elif prev_conn == "full" and conn in ("limited", "none"):
                self.add(now, "warn", "Internet access lost (connected to the network, but no internet)")
            elif conn == "full" and prev_conn in ("limited", "none", "portal"):
                self.add(now, "info", "Internet access is back")

        if prev_wifi is not None and nm.wifi_enabled is not None and prev_wifi != nm.wifi_enabled:
            self.add(now, "info", "Wi-Fi turned on" if nm.wifi_enabled else "Wi-Fi turned off")

    def _observe_vpn(self, now: float, vpn: VpnStatus) -> None:
        first = self._first("vpn")
        prev_ts = self._tailscale
        if vpn.tailscale is not None:
            self._tailscale = vpn.tailscale
        prev_tunnels = self._tunnels
        self._tunnels = {t.name: t.up for t in vpn.tunnels}
        if first:
            return

        if prev_ts is not None and vpn.tailscale is not None:
            was_running = prev_ts == "Running"
            is_running = vpn.tailscale == "Running"
            if was_running != is_running:
                self.add(now, "info", "Tailscale connected" if is_running else "Tailscale disconnected")

        for name, up in self._tunnels.items():
            if name not in prev_tunnels:
                if up:
                    self.add(now, "info", "VPN %s up" % name)
            elif up and not prev_tunnels[name]:
                self.add(now, "info", "VPN %s up" % name)
            elif prev_tunnels[name] and not up:
                self.add(now, "info", "VPN %s down" % name)
        for name, was_up in prev_tunnels.items():
            if was_up and name not in self._tunnels:          # the interface disappeared while up
                self.add(now, "info", "VPN %s down" % name)
