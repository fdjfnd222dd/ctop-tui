"""Wi-Fi link details (what you are attached to right now) and channel crowding.

LinkCollector reads `iw` and /sys; channel_report() is a pure function over the
nearby-network list, so it can be tested without any radio.
"""
from __future__ import annotations

import os
import re
from typing import Callable, Dict, Iterable, Optional

from ctop.collectors.base import Collector
from ctop.collectors.lan import is_random_mac
from ctop.collectors.wifi import channel_from_freq, run_cmd
from ctop.models import ChannelLoad, ChannelReport, LinkInfo

IW_TIMEOUT = 2.0
# Kernel names are at most 15 chars; checked before a name reaches argv or a path.
_IFACE_OK = re.compile(r"[A-Za-z0-9_.\-]{1,15}")
_MAC_OK = re.compile(r"[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}")
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")
_WIDTH_TOKEN = re.compile(r"(\d+)MHz")
_TXPOWER = re.compile(r"txpower\s+(-?\d+(?:\.\d+)?)\s*dBm")
_INFO_CHANNEL = re.compile(r"^\s*channel\s+(\d+)", re.M)
_INFO_WIDTH = re.compile(r"width:\s*(\d+)\s*MHz")

_BAND_ORDER = {"2.4 GHz": 0, "5 GHz": 1, "6 GHz": 2}
_NEIGHBOUR_SPAN = 4        # 2.4 GHz channels overlap up to 4 channel numbers away
_CROWDED = 6               # networks in the 2.4 GHz neighbourhood before we say "crowded"
_SHARED = 4                # networks on one 5 GHz channel before we say "shared"
_QUIET = 3                 # at or below this the channel is "quiet"
_2G_PICKS = (1, 6, 11)     # the three non-overlapping 2.4 GHz channels

_KEYS = ("connected", "bssid", "freq_mhz", "signal_dbm", "rx_mbps", "tx_mbps", "width_mhz", "standard")


def _band(freq_mhz: Optional[int]) -> str:
    # Same bands as the nmstate collector, kept local so the two stay independent.
    if freq_mhz is None:
        return "?"
    if 2400 <= freq_mhz <= 2500:
        return "2.4 GHz"
    if 4900 <= freq_mhz <= 5925:
        return "5 GHz"
    if 5926 <= freq_mhz <= 7125:
        return "6 GHz"
    return "?"


def _lead_number(text: str) -> Optional[float]:
    m = _NUMBER.match(text.strip())
    return float(m.group(0)) if m else None


def _rate_line(text: Optional[str]):
    """'866.7 MBit/s 80MHz VHT-MCS 9' -> (866.7, 80, tokens)."""
    if not text:
        return None, None, ()
    tokens = tuple(text.split())
    mbps = _lead_number(text)
    m = _WIDTH_TOKEN.search(text)
    return mbps, int(m.group(1)) if m else None, tokens


def _standard(tokens, band: str) -> Optional[str]:
    # Newest first: HE and VHT lines also mention MCS, so the plain "MCS" test goes last.
    if any(t.startswith("EHT") for t in tokens):
        return "Wi-Fi 7"
    if any(t.startswith("HE-") for t in tokens):
        return "Wi-Fi 6E" if band == "6 GHz" else "Wi-Fi 6"
    if any(t.startswith("VHT") for t in tokens):
        return "Wi-Fi 5"
    if "MCS" in tokens:
        return "Wi-Fi 4"
    return None


def parse_iw_link_full(text: str) -> dict:
    out: Dict[str, object] = {k: None for k in _KEYS}
    out["connected"] = False
    stripped = (text or "").strip()
    if not stripped.startswith("Connected to"):          # "Not connected." or no output
        return out
    out["connected"] = True

    head = stripped.splitlines()[0].split()
    if len(head) >= 3 and _MAC_OK.fullmatch(head[2]):
        out["bssid"] = head[2].upper()

    rx_text = tx_text = None
    for raw in stripped.splitlines()[1:]:
        line = raw.strip()
        if line.startswith("freq:"):
            f = _lead_number(line[len("freq:"):])
            out["freq_mhz"] = int(f) if f is not None else None
        elif line.startswith("signal:"):
            s = _lead_number(line[len("signal:"):])
            out["signal_dbm"] = int(s) if s is not None else None
        elif line.startswith("rx bitrate:"):
            rx_text = line[len("rx bitrate:"):]
        elif line.startswith("tx bitrate:"):
            tx_text = line[len("tx bitrate:"):]

    rx_mbps, rx_width, rx_tokens = _rate_line(rx_text)
    tx_mbps, tx_width, tx_tokens = _rate_line(tx_text)
    band = _band(out["freq_mhz"])
    out["rx_mbps"] = rx_mbps
    out["tx_mbps"] = tx_mbps
    out["width_mhz"] = rx_width if rx_width is not None else tx_width
    out["standard"] = _standard(rx_tokens, band) or _standard(tx_tokens, band)
    return out


def parse_iw_info(text: str) -> dict:
    out: Dict[str, object] = {"tx_power_dbm": None, "width_mhz": None, "channel": None}
    if not text:
        return out
    m = _TXPOWER.search(text)
    if m:
        out["tx_power_dbm"] = float(m.group(1))
    m = _INFO_CHANNEL.search(text)
    if m:
        out["channel"] = int(m.group(1))
    m = _INFO_WIDTH.search(text)
    if m:
        out["width_mhz"] = int(m.group(1))
    return out


def wifi_iface(net_root: str = "/sys/class/net") -> Optional[str]:
    """First wireless interface under net_root (sorted, so the choice is stable), or None."""
    try:
        names = sorted(os.listdir(net_root))
    except OSError:
        return None
    for name in names:
        if not _IFACE_OK.fullmatch(name):
            continue
        base = os.path.join(net_root, name)
        if os.path.isdir(os.path.join(base, "wireless")) or os.path.exists(os.path.join(base, "phy80211")):
            return name
    return None


def _read_sys(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="ascii", errors="replace") as fh:
            return fh.read().strip()
    except OSError:
        return None


def channel_report(networks: Iterable, link: Optional[LinkInfo]) -> ChannelReport:
    """Per-channel load of the nearby networks, plus one plain tip about your own channel."""
    nets = [n for n in networks if n.channel is not None and n.band != "?"]
    mine_net = next((n for n in nets if n.in_use), None)

    def is_mine(band: str, ch: int) -> bool:
        if any(n.in_use and n.band == band and n.channel == ch for n in nets):
            return True
        return link is not None and link.channel == ch and link.band == band

    # Several APs of one SSID count toward every channel they use; merged SSIDs share one channel.
    agg: Dict[tuple, list] = {}
    for n in nets:
        entry = agg.setdefault((n.band, n.channel), [0, 0])
        entry[0] += n.aps
        entry[1] = max(entry[1], n.signal_pct or 0)
    loads = tuple(
        ChannelLoad(band=band, channel=ch, networks=count, strongest_pct=strongest,
                    mine=is_mine(band, ch))
        for (band, ch), (count, strongest) in sorted(
            agg.items(), key=lambda kv: (_BAND_ORDER.get(kv[0][0], 3), kv[0][1]))
    )

    if mine_net is not None:
        my_band, my_chan = mine_net.band, mine_net.channel
    elif link is not None:
        my_band, my_chan = link.band, link.channel
    else:
        return ChannelReport(loads, "")
    if my_chan is None or my_band == "?":
        return ChannelReport(loads, "")

    def on_channel(band: str, ch: int) -> int:
        return sum(n.aps for n in nets if n.band == band and n.channel == ch)

    def neighbourhood(ch: int) -> int:
        # 2.4 GHz channels overlap, so a neighbour 2 numbers away still slows you down.
        return sum(n.aps for n in nets
                   if n.band == "2.4 GHz" and abs(n.channel - ch) <= _NEIGHBOUR_SPAN)

    if my_band == "2.4 GHz":
        load = neighbourhood(my_chan)
        ssid = mine_net.ssid if mine_net is not None else ""
        if ssid and any(n.ssid == ssid and n.band == "5 GHz" for n in nets):
            tip = "You are on 2.4 GHz; this network also has 5 GHz, which is usually faster."
        elif load >= _CROWDED:
            best = min((c for c in _2G_PICKS if c != my_chan), key=lambda c: (neighbourhood(c), c))
            tip = ("Channel %d is crowded (%d networks nearby). If your router lets you, pick channel %d."
                   % (my_chan, load, best))
        else:
            tip = ""
    else:
        load = on_channel(my_band, my_chan)
        if my_band == "5 GHz" and load >= _SHARED:
            tip = "Channel %d is shared by %d networks; another 5 GHz channel may be faster." % (my_chan, load)
        else:
            tip = ""

    if not tip and load <= _QUIET:
        tip = "Your channel is quiet - interference is not the problem."
    return ChannelReport(loads, tip)


class LinkCollector(Collector):
    name = "wifilink"
    interval = 5.0

    def __init__(self, runner: Optional[Callable] = None, net_root: str = "/sys/class/net"):
        super().__init__()
        self._run = runner or run_cmd
        self._root = net_root

    def refresh(self) -> None:
        iface = wifi_iface(self._root)
        if iface is None:
            self.data = None
            return
        base = os.path.join(self._root, iface)
        mac = _read_sys(os.path.join(base, "address"))
        mac = mac.lower() if mac else None
        mtu_text = _read_sys(os.path.join(base, "mtu"))
        mtu = int(mtu_text) if mtu_text and mtu_text.isdigit() else None
        random_mac = is_random_mac(mac) if mac and _MAC_OK.fullmatch(mac) else None

        link_text = self._run(["iw", "dev", iface, "link"], IW_TIMEOUT)
        if link_text is None:
            # iw missing or hung: still show what /sys knows.
            self.data = LinkInfo(iface=iface, connected=False, mac=mac, random_mac=random_mac, mtu=mtu)
            return
        link = parse_iw_link_full(link_text)
        if not link["connected"]:
            self.data = LinkInfo(iface=iface, connected=False, mac=mac, random_mac=random_mac, mtu=mtu)
            return

        info_text = self._run(["iw", "dev", iface, "info"], IW_TIMEOUT)
        info = parse_iw_info(info_text or "")
        freq = link["freq_mhz"]
        # The channel number comes from the frequency (exact); `iw info` is the fallback.
        channel = channel_from_freq(freq) or info["channel"]
        # `iw info` states the width outright, so it beats the width guessed from the bit rate line.
        width = info["width_mhz"] if info["width_mhz"] is not None else link["width_mhz"]
        self.data = LinkInfo(
            iface=iface,
            connected=True,
            bssid=link["bssid"],
            freq_mhz=freq,
            channel=channel,
            band=_band(freq),
            width_mhz=width,
            signal_dbm=link["signal_dbm"],
            rx_mbps=link["rx_mbps"],
            tx_mbps=link["tx_mbps"],
            standard=link["standard"],
            tx_power_dbm=info["tx_power_dbm"],
            mac=mac,
            random_mac=random_mac,
            mtu=mtu,
        )
