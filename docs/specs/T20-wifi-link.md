# T20 - Wi-Fi link details + channel crowding

## 1 Purpose
Two things for the "Your Wi-Fi link" box: (a) `LinkCollector` publishes a `LinkInfo` about the link you are on right now
(access point, band, channel width, Wi-Fi generation, real rx/tx rates, transmit power, privacy MAC, MTU);
(b) `channel_report(networks, link)` is a pure function that turns the nearby-network list (T18's `NmState.networks`) into a
`ChannelReport` with one plain tip such as "Channel 6 is crowded (7 networks). Your router's 5 GHz band would be faster.".

## 2 Files
Create `ctop/collectors/wifilink.py`, `tests/test_wifilink.py`. Read `docs/specs/NET-COMMON.md`.
Reuse: `run_cmd`, `channel_from_freq` from `ctop.collectors.wifi`; `is_random_mac` from `ctop.collectors.lan`;
`band_of` - do NOT import from T18 (built in parallel); write a private `_band(freq)` with the same rule:
2400-2500 "2.4 GHz", 4900-5925 "5 GHz", 5926-7125 "6 GHz", else "?".

## 3 Public API
```python
def parse_iw_link_full(text: str) -> dict   # keys: connected bssid freq_mhz signal_dbm rx_mbps tx_mbps width_mhz standard
def parse_iw_info(text: str) -> dict        # keys: tx_power_dbm width_mhz channel (from `iw dev X info`)
def wifi_iface(net_root="/sys/class/net") -> Optional[str]   # first dir that has a `wireless` subdir or `phy80211` link; name must match ^[A-Za-z0-9_.-]{1,15}$
def channel_report(networks, link: Optional[LinkInfo]) -> ChannelReport

class LinkCollector(Collector):
    name = "wifilink"
    interval = 5.0
    def __init__(self, runner=None, net_root="/sys/class/net"): ...
    def refresh(self) -> None: ...   # self.data = LinkInfo or None (no Wi-Fi interface)
```

## 4 Sources
`iw dev <if> link` (timeout 2):
```
Connected to 02:00:00:aa:bb:cc (on wlan0)
	SSID: ExampleNet
	freq: 5680.0
	signal: -42 dBm
	rx bitrate: 866.7 MBit/s VHT-MCS 9 80MHz short GI VHT-NSS 2
	tx bitrate: 650.0 MBit/s VHT-MCS 7 80MHz short GI VHT-NSS 2
```
`Not connected.` -> connected False. freq may be `5680` or `5680.0` -> int. bssid upper case.
standard from the rx (else tx) bitrate line tokens: contains `EHT` -> "Wi-Fi 7"; `HE-` -> "Wi-Fi 6" (and band 6 GHz -> "Wi-Fi 6E");
`VHT` -> "Wi-Fi 5"; `MCS` alone (HT) -> "Wi-Fi 4"; else None. width from `(\d+)MHz` token on the bitrate line (default None).
`iw dev <if> info` (timeout 2): lines `channel 136 (5680 MHz), width: 80 MHz, center1: 5690 MHz` and `txpower 22.00 dBm`.
Width from info wins over the bitrate token. `/sys/class/net/<if>/address` -> mac (lower case), random_mac via `is_random_mac`.
`/sys/class/net/<if>/mtu` -> int. channel = `channel_from_freq(freq)` else info's channel. band = `_band(freq)`.
No Wi-Fi interface -> `data = None`. `iw` missing -> LinkInfo(iface, connected=False, mac/mtu from sysfs).

## 5 channel_report rules
Input `networks`: iterable of `WifiNetwork` (each has band, channel, signal_pct, aps, in_use). Each network counts `aps`
access points on its channel (approximation: all APs of a merged SSID share its channel). Ignore entries with channel None or band "?".
`loads`: one `ChannelLoad` per (band, channel), networks = summed aps, strongest_pct = max signal, mine = any in_use network on it
(or `link.channel` matches with the same band). Sorted by band order 2.4, 5, 6 then channel.
On 2.4 GHz, channels overlap: for the tip, the "neighbourhood load" of channel c = APs on channels within c-4..c+4.
Tip (first rule that applies, else ""):
1. no in-use network and no link -> "" .
2. mine on 2.4 GHz and the SAME SSID exists on 5/6 GHz in `networks`? (you cannot see that once merged, so instead:) if any network
   in the list with the same ssid as the in-use one has band 5 GHz -> "You are on 2.4 GHz; this network also has 5 GHz, which is usually faster.";
   else if mine on 2.4 GHz and neighbourhood load >= 6 -> "Channel N is crowded (K networks nearby). If your router lets you, pick channel X." where X is the
   one of 1/6/11 with the lowest neighbourhood load (and != N).
3. mine on 5 GHz and its channel has >= 4 networks -> "Channel N is shared by K networks; another 5 GHz channel may be faster."
4. mine and load small -> "Your channel is quiet - interference is not the problem."
Keep tips under 90 characters. Pure function, no I/O.

## 6 Tests (`tests/test_wifilink.py`)
* parse_iw_link_full: sample -> bssid "02:00:00:AA:BB:CC", freq 5680, signal -42, rx 866.7, tx 650.0, width 80, standard "Wi-Fi 5".
* HE line `rx bitrate: 1200.9 MBit/s 80MHz HE-MCS 11 HE-NSS 2 HE-GI 0.8` -> "Wi-Fi 6"; with freq 5955 the collector reports "Wi-Fi 6E".
* `Not connected.` -> connected False.
* parse_iw_info: txpower 22.0, width 80, channel 136.
* collector with fake runner + tmp net_root (wlan0/wireless/, address `02:00:00:aa:bb:cc`, mtu `1500`) -> full LinkInfo; random_mac True.
* collector without wireless dir -> data None; runner returning None -> connected False but mac/mtu filled.
* channel_report: crowded 2.4 GHz case produces the "crowded" tip naming 1, 6 or 11; dual-band SSID case produces the 5 GHz tip;
  quiet case; empty input -> ChannelReport((), ""). Loads sorted, mine flags right, counts use `aps`.
* iface name `x;rm` with a wireless dir is ignored.
