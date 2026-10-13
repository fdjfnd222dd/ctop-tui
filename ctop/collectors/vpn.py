"""VPN status: Tailscale (installed? running? which peers are online) and tunnel interfaces.

Read-only. NetworkManager VPN profiles are listed by the nmstate collector; switching anything is not done here.
"""
from __future__ import annotations

import json
import re
import shutil
import socket
from typing import Callable, Mapping, Optional, Tuple

import psutil

from ctop.collectors.base import Collector
from ctop.collectors.wifi import run_cmd
from ctop.models import VpnPeer, VpnStatus, VpnTunnel

TAILSCALE = ["tailscale", "status", "--json"]
TAILSCALE_TIMEOUT = 3.0
MAX_PEERS = 50

# Interface name prefixes that are VPN tunnels: WireGuard, Tailscale, OpenVPN and friends.
_TUNNEL_NAME = re.compile(r"^(wg|tailscale|tun|tap|ppp|proton|nordlynx|mullvad)")
_WIREGUARD_PREFIXES = ("wg", "nordlynx", "mullvad")


def _text(value) -> Optional[str]:
    return value if isinstance(value, str) else None


def _peer_ip(ips) -> Optional[str]:
    if not isinstance(ips, list):
        return None
    texts = [ip for ip in ips if isinstance(ip, str)]
    # Prefer IPv4: it is the address people read out loud. IPv6 is the fallback.
    for ip in texts:
        if ":" not in ip:
            return ip
    return texts[0] if texts else None


def _peer_name(peer: dict) -> str:
    host = _text(peer.get("HostName"))
    if host:
        return host
    dns = _text(peer.get("DNSName"))
    if dns:
        return dns.split(".")[0] or "?"
    return "?"


def parse_tailscale(text: str) -> Tuple[Optional[str], Optional[str], Optional[str], Tuple[VpnPeer, ...]]:
    """(backend_state, self_ip, tailnet_name, peers) from `tailscale status --json`."""
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return "unknown", None, None, ()
    if not isinstance(data, dict):
        return "unknown", None, None, ()

    backend = _text(data.get("BackendState")) or "unknown"

    self_ip = None
    me = data.get("Self")
    if isinstance(me, dict):
        ips = me.get("TailscaleIPs")
        if isinstance(ips, list) and ips and isinstance(ips[0], str):
            self_ip = ips[0]

    tailnet = None
    current = data.get("CurrentTailnet")
    if isinstance(current, dict):
        tailnet = _text(current.get("Name"))
    if tailnet is None:
        tailnet = _text(data.get("MagicDNSSuffix"))

    peers = []
    raw_peers = data.get("Peer")
    if isinstance(raw_peers, dict):
        for peer in raw_peers.values():
            if not isinstance(peer, dict):
                continue
            peers.append(VpnPeer(
                name=_peer_name(peer),
                ip=_peer_ip(peer.get("TailscaleIPs")),
                os=_text(peer.get("OS")) or "",
                online=peer.get("Online") is True,
                exit_node=peer.get("ExitNode") is True,
            ))
    peers.sort(key=lambda p: (not p.online, p.name.lower()))
    return backend, self_ip, tailnet, tuple(peers[:MAX_PEERS])


def _kind(name: str) -> str:
    if name.startswith(_WIREGUARD_PREFIXES):
        return "WireGuard"
    if name.startswith("tailscale"):
        return "Tailscale"
    if name.startswith(("tun", "tap")):
        return "OpenVPN/tun"
    return "VPN"


def tunnels(addrs: Optional[Mapping] = None, stats: Optional[Mapping] = None) -> Tuple[VpnTunnel, ...]:
    """VPN-like interfaces that exist on this machine, sorted by name."""
    try:
        if addrs is None:
            addrs = psutil.net_if_addrs()
        if stats is None:
            stats = psutil.net_if_stats()
    except (OSError, RuntimeError):
        return ()           # psutil can fail on odd systems; the page then shows no tunnels

    out = []
    for name in sorted(addrs):
        if not _TUNNEL_NAME.match(name):
            continue
        ip4 = None
        ip6 = None
        for addr in addrs[name]:
            if addr.family == socket.AF_INET and ip4 is None:
                ip4 = addr.address
            elif addr.family == socket.AF_INET6 and ip6 is None and "%" not in addr.address:
                ip6 = addr.address
        st = stats.get(name)
        out.append(VpnTunnel(
            name=name,
            kind=_kind(name),
            up=bool(getattr(st, "isup", False)),
            ip=ip4 or ip6,
        ))
    return tuple(out)


class VpnCollector(Collector):
    name = "vpn"
    interval = 15.0

    def __init__(self, runner: Optional[Callable] = None, which: Callable = shutil.which,
                 addrs_fn: Optional[Callable] = None, stats_fn: Optional[Callable] = None):
        super().__init__()
        self._run = runner or run_cmd
        self._which = which
        self._addrs = addrs_fn or psutil.net_if_addrs
        self._stats = stats_fn or psutil.net_if_stats

    def refresh(self) -> None:
        tailscale = None
        ts_ip = None
        tailnet = None
        peers = ()
        # Not installed: say nothing about Tailscale instead of guessing.
        if self._which("tailscale"):
            text = self._run(TAILSCALE, TAILSCALE_TIMEOUT)
            if text is None:
                tailscale = "unknown"       # installed but the command failed or hung
            else:
                tailscale, ts_ip, tailnet, peers = parse_tailscale(text)
        self.data = VpnStatus(
            tailscale=tailscale,
            tailscale_ip=ts_ip,
            tailnet=tailnet,
            peers=peers,
            tunnels=tunnels(self._addrs(), self._stats()),
        )
