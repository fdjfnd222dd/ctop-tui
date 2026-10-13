# T29a - Network page renderers A: Wi-Fi list, saved & VPN, link details, events

## 1 Purpose
Pure drawing functions for four boxes of the Network page. Read `docs/specs/NETWORK-PAGE.md` (sizes, focus, cursor colours) and
`docs/specs/NET-COMMON.md` (rule 9). Look at `ctop/render/security_ports.py` and `ctop/render/quality.py` for the house style.

## 2 Files
Create `ctop/render/net_wifi.py`, `tests/test_render_net_wifi.py`. Import helpers from `ctop.ui` (`fit`, `kv`, `runs`, `grad`, `DIM`, `FG`, `TRACK`).

## 3 Public API
```python
CURSOR_ON = "on #1f3550"      # focused box cursor row background (append to the row's styles via Text.stylize over the whole line)
CURSOR_OFF = "on #1a1f27"
def signal_bars(pct: Optional[int]) -> Text            # 4 cells "▂▄▆█"; filled = 4 if >=75, 3 if >=50, 2 if >=25, 1 if >0, 0 if 0/None;
                                                       # filled cells coloured grad(1 - pct/100) (strong = green), unfilled TRACK
def ago(ts: Optional[float], now: float) -> str        # None -> "never"; <60 s "now"; <3600 "12m"; <86400 "5h"; else "3d"
def window(n: int, cursor: int, rows: int) -> Tuple[int, int]   # (start, end) slice keeping cursor visible, cursor near the middle when scrolling
def wifi_list_lines(nm: Optional[NmState], cursor: int, focused: bool, w: int, h: int) -> List[Text]
def saved_lines(nm: Optional[NmState], vpn: Optional[VpnStatus], cursor: int, focused: bool, w: int, h: int, now: float) -> List[Text]
def link_lines(link: Optional[LinkInfo], wifi: Optional[WifiData], report: Optional[ChannelReport], aps: Optional[int], w: int, h: int) -> List[Text]
def events_lines(events: Sequence[NetEvent], w: int, h: int, now: float) -> List[Text]
```

## 4 Layouts
**wifi_list_lines** (w 68 typical; at w < 60 drop the rate column... there is no rate column; drop "Security" then "Band" as w shrinks):
```
Signal  Network                        Status        Band     Ch  Security
▂▄▆█   ExampleNet                  ×2  ✓ connected   5 GHz   136  WPA WPA2
▂▄▆█   Neighbour Net                   saved         2.4 GHz   6  WPA2
▂▄__   Cafe: Free                                    2.4 GHz   6  open ⚠
▂___   (hidden network)                              2.4 GHz  11  WPA2
```
Header DIM. SSID column flexes (w - fixed columns), cut with "…". `×N` (DIM) when aps > 1. "✓ connected" green bold, "saved" blue `#4ea1ff`.
Open networks: "open ⚠" yellow. `802.1X` shows "enterprise". Row = cursor -> whole line padded to w and stylized CURSOR_ON/OFF.
The header is not part of the cursor index; cursor indexes `nm.networks`. Use `window()` for scrolling with h-1 rows for items.
States (single dim lines, may be 2 lines): nm None -> "looking for Wi-Fi networks…"; not available -> nm.reason + "Wi-Fi controls need NetworkManager.";
wifi_device None -> "No Wi-Fi hardware found."; wifi_hw False -> "Wi-Fi is blocked by a hardware switch or airplane key.";
wifi_enabled False -> "Wi-Fi is off.  Press w to turn it on." (yellow); no networks -> "No networks found yet - press r to scan.".

**saved_lines** (w 44): items = `nm.saved` (cursor indexes them). Row:
```
● ExampleNet            Wi-Fi   auto   now
○ Neighbour Net         Wi-Fi   auto   2d
○ Office VPN            VPN     manual 5h
```
"●" green for active (state "activating" -> "◐" yellow), "○" DIM otherwise; kind label Wi-Fi/Cable/VPN/WireGuard/Other; "auto" DIM or "manual" yellow;
priority != 0 appends " p5"; last column `ago(last_used, now)` ("now" for active). Name column flexes.
Below the list (always visible, reserve these lines first from h): a DIM rule line "─" * w then VPN lines:
- vpn None -> nothing extra. tailscale None -> skip line. tailscale "Running" -> "Tailscale  ● on   100.64.0.1   3 of 7 devices online" (green dot);
  other states -> "Tailscale  ○ off (Stopped)" / "needs login". One line per up tunnel not named tailscale*: "wg0  WireGuard  up  10.0.0.2". At most 3 VPN lines.
  No VPN lines at all -> no rule line.
Empty saved -> "No saved networks yet." nm None / unavailable -> same messages as the Wi-Fi box (short form).

**link_lines** (w 44, h 8; drop lines from the END when h is smaller):
```
ExampleNet · 5 GHz · ch 136 · 80 MHz · Wi-Fi 5
Signal   -42 dBm  Excellent
Link     ↓ 867 Mbps  ↑ 650 Mbps
Router   02:00:00:AA:BB:CC  (1 of 2 access points)
Your MAC private (random) ✓          | or "hardware address" DIM
Power    22 dBm transmit   MTU 1500
Nearby   2.4: 1·2  6·7  11·1   5: 36·1  136·1◆
Tip: Channel 6 is crowded (10 networks nearby)...
```
First line from wifi.ssid (bold) + link fields joined with " · " (skip unknown parts). Signal quality word coloured green/yellow/red by wifi.quality.
"Nearby" line: per band in order, `channel·count`, the channel you are on followed by "◆" and coloured cyan; counts >= 5 yellow, >= 8 red. Cut with fit.
Tip line DIM italic, only if report.tip. link None -> "No Wi-Fi link."; link.connected False -> "Wi-Fi is not connected." plus the MAC line.

**events_lines**: newest FIRST (events tuple is newest last). Row: `HH:MM:SS  ! text` - time DIM (`time.strftime("%H:%M:%S", time.localtime(ts))`,
older than 20 h -> `%a %H:%M`), icon: info "·" `#4ea1ff`, warn "!" yellow bold, crit "✗" red bold; text FG. Empty -> DIM "Nothing yet - drops, roaming and lag spikes show up here.".
More events than h -> show the newest h-1 and a DIM "… N older" line.

## 5 Tests (`tests/test_render_net_wifi.py`)
For every function: each line `no_wrap`, `cell_len <= w`, `len <= h` for w in (36, 44, 68, 100) and h in (3, 8, 13).
* signal_bars thresholds (count the non-TRACK cells via spans or plain text); ago buckets; window keeps cursor in range for n=30, rows=10, cursor 0/15/29 and n<rows.
* wifi list: connected row contains "✓ connected", ×2 shown, open shows "open", hidden shows "(hidden network)", every state message, cursor row has
  the background style applied across the line (check `line.spans` includes a style with bgcolor).
* saved: active dot, manual, priority suffix, ago, VPN section lines (Running with peers count, Stopped), no rule line without VPN.
* link: full sample contains "Wi-Fi 5", "136", "private"; crowded counts coloured; disconnected message; h=3 keeps the first 3 lines.
* events: newest first, icons, "… N older" line, empty message.
Build models directly from `ctop/models.py` with documentation addresses.
