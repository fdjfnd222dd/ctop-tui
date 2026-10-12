# PAGES - maintainer's wiring guide (not for the Haiku implementers)

Audience: the maintainer editing `ctop/app.py`, `ctop/panels/*.py` after T03/T07/T11/T12/T14/T17 land.
Reference terminal: **120 x 35**. Smallest supported: 100 x 30 (below that panels just show fewer lines; `Panel.draw` already cuts to height).
Every renderer returns lines already `fit` to the width you pass, so pass `size.width` / `size.height` of the panel's `content_size` (border and padding already removed).

Recommended minimal plumbing: keep `Sampler` as the panel `state`, and let the tick copy the latest collector snapshots onto it
(`s.thermal, s.ping, s.wifi, s.sec, s.alerts, s.detail`; each is just a reference read of `collector.data`, no copying, no locks).
Panels never touch collectors.

## 1 Page 1 (Overview): where the new pieces plug in

### 1.1 Screen row budget (worst case, banner visible), 35 rows
```
row 1        #alerts   alert banner (T17), height 1, display:none when banner() is None
rows 2-14    #top      13 rows  = CPU panel (3fr) | Battery panel (2fr)
rows 15-34   #bottom   20 rows  = Memory panel (3fr) | Network panel (2fr)     (1fr: 35 - 1 - 13 - 1)
row 35       #status   status bar, height 1
```
With no banner the bottom row simply gets 21 rows. Compose order: `Static(id="alerts")`, `Horizontal#top`, `Horizontal#bottom`, `Static#status`.
CSS: `#alerts { height: 1; display: none; }`. In `tick()`: `b = banner(s.alerts, self.size.width)`; `alerts.display = b is not None`; `if b: alerts.update(b)`.
The banner is shared by both pages (put it OUTSIDE the page containers). It is the only widget allowed to appear on top of everything.

### 1.2 CPU panel (`ctop/panels/cpu.py`) - thermal_lines (T03)
Content size at 120 cols: width 68, height 11 (13 minus 2 border rows). Right block: `colw = 16`, `right_w = 34`. With 8 logical CPUs the block is:
```
C1  ■■■■■■■■  41%  C5  ...        4 rows (half = (n+1)//2)
(blank)                           1 row   separator
Temp       62 °C  warm            \
Fan        2400 rpm (46% of max)   > thermal_lines(...)  <= 4 rows
Hot spot   58 °C  Disk (nvme)     /
Speed      2.9 GHz                1 row
Load       0.4  0.5  0.6 (1/5/15) 1 row                      = 4+1+4+1+1 = 11 rows  -> exactly fits
```
Replace the old `if s.temp is not None: right.append(kv("Temp", ...))` with:
```python
half = (n + 1) // 2
room = h - half - 2                     # rows left after the core meters, Speed and Load
th = thermal_lines(getattr(s, "thermal", None), right_w)
if room >= 5:    right += [Text()] + th[:room - 1]
elif room >= 1:  right += th[:room]    # big CPUs (>= 12 threads): no blank, fewer lines
```
Keep the `Speed` and `Load` lines after it. `th` is ordered Temp, Fan(s), Hot spot, so slicing from the end drops the least important first.
`thermal` is `Thermal` or None (collector not ready). The old `s.temp` stays as fallback only if `s.thermal is None`.

### 1.3 Network panel (`ctop/panels/network.py`) - quality_lines (T07)
Content size at 120 cols: width 44, height 18 (20 minus 2). New row plan (this REPLACES the old `Wi-Fi` kv line and the blank line):
```
Download head          1
graph                  gr   (gr = clamp(1 + (h - 18)//2, 1, 5);  h=18 -> 1)
Upload head            1
graph                  gr
Since boot             1
------ separator       1
quality_lines(...)     <= 6   (Router 2, Internet 2, Wi-Fi 2)
Interface              1
Your IP                1
Gateway                1
DNS                    1
Interfaces + rows      remaining (heading + rows only if >= 2 rows remain)   -> 4 + 2gr + 6 + 4 = 16 + 2 = 18 rows at h=18
```
Code shape: `q = quality_lines(s.ping, s.wifi, w)[:max(0, min(6, h - 12))]`. Remove the old `net["ssid"]` line (SSID is now at the end of the Wi-Fi quality line when w >= 60). Keep the old `net` dict for iface/IP/gateway/DNS until T-data replaces it.
If `s.ping is None and s.wifi is None` quality_lines returns two dim lines; that is intended ("starting...").

### 1.4 Memory panel
Unchanged except sort and filter (see section 4): `groups` is re-sorted/filtered before drawing; `border_subtitle` shows `where/len(groups)` of the FILTERED list.

## 2 Page 2: the Security page ("2")

### 2.1 Container
`Vertical#page2` next to `Vertical#page1` (page1 = `#top` + `#bottom`). Show one with `.display = True/False` (or `ContentSwitcher`). Banner and status stay outside.
Page 2 gets 33 rows (35 - banner - status): two rows of boxes, 17 + 16.
```
+- Listening ports --------------------------+ +- Connections -----------------------------+     row A: 17 rows
| Port  Proto  Program     Who can reach it  | | Program         Open  Talking to   Where  |     content (w,h) = (56, 15)
| 8080  TCP    python3     everyone on your..| | firefox           12  203.0.113.5:443 ... |     both 1fr (60 cols each)
| ...                                        | | ...                                       |
+--------------------------------------------+ +-------------------------------------------+
+- Devices on your network -------------------------------------+ +- How exposed am I? ---------------+   row B: 16 rows
| Only devices this computer talked to recently...              | | Exposure  MEDIUM - worth a look    |  content (w,h)
| IP address      MAC               Role    State               | | * 1 program accepts connections... |  devices: (68, 14)  [3fr = 72 cols]
| 192.0.2.1       02:00:00:aa:bb:cc router  active now          | | * The firewall is on and blocks... |  exposed: (44, 14)  [2fr = 48 cols]
| ...                                                           | | Firewall  ufw, on                  |
+---------------------------------------------------------------+ | Not a security audit.              |
                                                                  +------------------------------------+
```
CSS: `#secA { height: 17; } #secB { height: 1fr; }`, `#sec-ports, #sec-conn { width: 1fr } #sec-lan { width: 3fr } #sec-exposed { width: 2fr }`.
Border colours (reuse the existing four): ports `#c0627a`, connections `#4f8fd6`, devices `#5fa86b`, exposure `#d9a441`; the exposure box border colour may follow the verdict (green/yellow/red) - optional.

### 2.2 Who gets which (w, h) and which renderer
Four `Panel` subclasses in `ctop/panels/security.py` (maintainer writes them; each ~6 lines): `build(self, s, w, h)` gets the panel's own content size from `Panel.draw`.
| Panel | border title | call |
|---|---|---|
| ports | `Listening ports` | `listeners_lines(s.sec.listeners, s.sec.firewall, w, h)` |
| connections | `Connections` | `connections_lines(s.sec.connections, w, h)` |
| devices | `Devices on your network` | `devices_lines(s.sec.devices, w, h)` |
| exposed | `How exposed am I?` | `summary_lines(s.sec, s.wifi, w, h)` |
If `s.sec is None` (first refresh not done): `[Text("collecting... (takes a few seconds)", style=DIM)]`. Footer reminder in `border_subtitle` of the devices box: `passive: nothing is scanned`.
Tick redraws only the panels of the VISIBLE page (`self.query_one("#page2").query(Panel)`), not both.

## 3 Program-details modal (Enter, page 1)
`class DetailScreen(ModalScreen)`; `align: center middle`; inner box `width = min(screen_w - 8, 100)`, `height = min(screen_h - 6, 30)`, round border `#4ea1ff`, title = program name.
At 120x35: box 100 x 29, content (w, h) = (96, 26) after border+padding; last row of the box is a 1-row `Static#msg` for kill prompts, so call `detail_lines(detail, w, h - 1, now=time.time(), locked=is_protected(name))`.
```
+- firefox -----------------------------------------------------------+
| firefox   3 processes                                               |
| RAM         1.74 GB                                                 |
| ...                                                                 |
| PID           RAM Threads Files Command                             |
| ...                                                                 |
| Connections 9 open: 8 to the internet, 1 on your local network      |
| Listening   1 port                                                  |
|                                                                     |
| Esc closes   x x stops this program                                 |
| [#msg: kill prompt / result goes here]                              |
+---------------------------------------------------------------------+
```
Flow: Enter on page 1 with a selected program -> push `DetailScreen(name)` showing `Static("collecting...")`; a worker thread runs the T13 collector once (`<= 50 ms`), then `call_from_thread` updates the text; re-run every 2 s while open.
No program selected -> status message `Pick a program first with Up/Down, then press Enter.` (yellow `#f2cc60`), no modal. Keys inside the modal: `Esc` closes; `x` x `x` reuses the existing arm/confirm logic (prompt text goes to `#msg`, not the status bar); every other key ignored; `q` still quits the app.
After a successful stop the modal stays open and shows `closed`/`still running` in `#msg`; the program disappears from page 1 on the next scan.

## 4 Full key map and what each key does to the status bar
Status bar (1 row): LEFT = hint or transient message (`say()`, reverts to the page hint after 6 s, kill prompts stay until resolved). RIGHT = state tags, always shown, right-aligned:
`1 Overview` (or `2 Security`), then `sort: RAM|CPU|name` (page 1 only), `every 1 s`, then optional `PAUSED` (bold `#000000 on #f2cc60`) and `filter: "text"` (`#39c5cf`). Left text is cropped to `width - len(right) - 2`.
Hint page 1: `Up/Down select   Enter details   x x stop   s sort   / search   space pause   ? help   q quit`. Hint page 2: `Tab or 1/2 switch page   space pause   ? help   q quit`.
| Key | Action | Status bar effect |
|---|---|---|
| Tab / 1 / 2 | Tab toggles page; 1 and 2 jump. Selection is kept. | right tag changes; hint swaps; left shows hint |
| Up Down j k, PgUp PgDn, Home End | move selection (page 1 only) | hint (disarms a pending kill) |
| Enter | open details modal | left: `Loading details...` until data arrives |
| x x | stop program (unchanged; first press arms 5 s, second at >= 0.35 s) | existing red prompt; **refused while PAUSED**: `Resume (space) first - the list may be out of date.` |
| s | cycle sort RAM -> CPU -> name -> RAM; selection follows the program name | left `Sorted by CPU` for 3 s; right tag updates |
| / | search mode: status bar row becomes an `Input` (`/ ` prompt); every printable key (even q j k x s space + - ?) goes into the text; filter is a case-insensitive substring of the program name; list updates per keystroke; cursor jumps to first match | left is the input; right keeps tags |
| Enter (in search) | keep the filter, leave search mode | right shows `filter: "fire"`; `Esc` later clears |
| Esc | priority order: close modal/help, else leave search mode and clear filter, else clear an active filter, else cancel an armed kill | message `Filter cleared` / hint |
| space | pause/resume: stops the 1 s tick (sampling + collector scheduling + banner re-eval); selection, sort, search, Enter still work on frozen data | right tag `PAUSED`; on resume `Resumed` and an immediate tick (reset the net-rate baseline so the first sample is not a huge spike) |
| + / - (also = / _) | `+` faster, `-` slower through `[0.5, 1, 2, 5]` s; recreate the `set_interval` timer | left `Refresh every 0.5 s` for 3 s; right tag `every 0.5 s`. CPU graph caption becomes `last {HIST*interval/60:g} minutes` (HIST=300) |
| ? | help overlay: a `ModalScreen` with the key table above; `Esc` or `?` closes | none |
| q / Ctrl+C | quit (also in modals; NOT while typing in search - Esc first) | - |
Implementation notes: use a `self.mode` in `{"normal","search"}`; `check_action` returns False for every binding except `escape` when `mode == "search"`. Sort CPU needs a per-program CPU %:
see `_model-change-requests.md` (Sampler change, not in models.py). Until it lands, `s` cycles RAM <-> name only.

## 5 Collectors: intervals and threads
| What | Where it runs | Interval | Notes |
|---|---|---|---|
| UI tick: `sample_fast()` (CPU, RAM, net counters, battery sysfs), banner/alert evaluation, redraw | main (Textual) thread | 1 s default (0.5/1/2/5 via `+`/`-`) | all reads are cheap psutil/sysfs; never blocks |
| `scan_procs()` (program groups) | worker thread | every 5 s (5 ticks at 1 s; keep it time-based: 5 s) | skipped while `procs_busy`; also once right after a kill |
| T02 thermal | worker thread | 2 s | dell_smm fan reads can stall for 100s of ms, hence off the main thread |
| T05 ping (router + internet, 1 packet each, `timeout` 1 s) | worker thread | 3 s (set by T05) | the only traffic ctop sends; history 120 samples = 6 min (the 60-cell sparkline shows the last 3 min); stops while paused |
| T06 wifi | worker thread | 5 s (set by T06; it runs `nmcli` itself only every 30 s) | history 120 samples = 10 min |
| T08 ports (listeners + connections; `data` is a 2-tuple) | worker thread | 5 s while page 2 is visible, else 30 s | `net_connections` is the costliest read (about 10-30 ms) |
| T09 lan (`ip neigh`) | worker thread | 10 s page 2 visible, else 60 s | passive; one subprocess with timeout |
| T10 firewall | worker thread | 30 s page 2 visible, else 120 s | root-free reads only |
| T13 details (`program_detail(name, members, listeners, connections)`) | worker thread | once on Enter, then 2 s while the modal is open | cost is proportional to the one program; `members` = `Sampler.groups[i][3]`, listeners/connections from the T08 tuple |
| T04 alerts `evaluate()` | main thread | every tick | pure function over the latest `.data` of everything, < 1 ms |
Scheduler (in `tick()`): `for c in collectors: if c.due() and not paused: threading.Thread(target=c.run, daemon=True).start()` - `Collector.due()` already refuses while `busy`.
Page-dependent slowdowns (the "else 30 s" rows) are done by assigning `c.interval` when the visible page changes; the collectors' own defaults (T02 2 s, T05 3 s, T06 5 s, T08 5 s, T09 10 s) are the page-2/foreground values.
Security snapshot: each tick, `s.sec = SecurityData(listeners, connections, lan.data or (), firewall.data or FirewallInfo(None, None, None, False), time.time())` built only when one of the three inputs changed (compare object identity); `None` until ports has run once.
Intervals are per collector; at most 4 workers run at once; nothing on the main thread may call a collector's `refresh()` directly.
Pause: no new workers start; running ones finish and publish. A collector error never shows a traceback: `c.error` is non-None -> its panel shows the renderer's own "not available" line.
