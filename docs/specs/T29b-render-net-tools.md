# T29b - Network page renderers B: Tools box (menu + result views, incl. the traceroute map)

## 1 Purpose
Pure drawing for the Tools box: the left-hand menu and the right-hand result view of whichever tool ran last. The traceroute view is the
showpiece: a clean vertical path from "you" to the target, one row per hop, coloured by where the hop is (home / provider / internet / target),
with a latency bar. Read `docs/specs/NETWORK-PAGE.md`, `docs/specs/NET-COMMON.md` (rule 9), `ctop/ui.py`.

## 2 Files
Create `ctop/render/net_tools.py`, `tests/test_render_net_tools.py`.

## 3 Public API
```python
MenuItem = Tuple[str, str, str]      # (key, label, shortcut) ; key "" = separator row (drawn as a dim rule, never the cursor)
KIND_COLOR = {"home": "#56d364", "isp": "#d9a441", "internet": "#4ea1ff", "target": "#39c5cf", "unknown": DIM}
KIND_WORD = {"home": "home", "isp": "provider", "internet": "internet", "target": "target", "unknown": ""}
FIX_HINT = {"reconnect": "Fix: Tools > Fix Wi-Fi (reconnect)", "dns": "Fix: Saved box > Enter on your network > DNS",
            "portal": "Fix: open a web browser and log in on the page it shows", "wifi-on": "Fix: press w to turn Wi-Fi on"}
def menu_lines(items: Sequence[MenuItem], cursor: int, focused: bool, running: Optional[str], w: int, h: int) -> List[Text]
def tool_view_lines(kind: Optional[str], result, w: int, h: int) -> List[Text]     # dispatcher; never raises (bad input -> dim line)
def idle_lines(w, h) -> List[Text]
def trace_lines(trace: Optional[TraceResult], w, h) -> List[Text]
def ports_lines(pc: Optional[PortCheckResult], w, h) -> List[Text]
def target_lines(trace, ports, w, h) -> List[Text]
def dns_lines(report: Optional[DnsReport], w, h) -> List[Text]
def speed_lines(sr: Optional[SpeedResult], w, h) -> List[Text]
def doctor_lines(dx: Optional[Diagnosis], w, h) -> List[Text]
def lan_lines(ls: Optional[LanScanResult], w, h) -> List[Text]
def message_lines(lines: Sequence[str], w, h) -> List[Text]         # kind "message": plain info text (e.g. network details)
```
Dispatcher kinds: None -> idle_lines; "target" -> result is `(trace, ports)` tuple; "trace"; "ports"; "dns"; "speed"; "doctor"; "lan"; "message" (tuple of str).

## 4 Layouts
**menu_lines** (w 22): one row per item: `label` left, shortcut right-aligned DIM (e.g. "Network doctor     d"). Separator -> DIM "─"*w.
Cursor row CURSOR style as in T29a (`on #1f3550` focused, `on #1a1f27` not). The `running` key gets a "◐ " prefix in cyan instead of two spaces
(all other rows start with two spaces). Scroll like T29a's window (implement locally; do not import T29a).

**idle_lines**: a short guide, DIM except the tool names:
"Pick a tool on the left, Enter runs it.", then one line each: "Doctor - finds where your connection breaks", "Trace & ports - the path to any address, and which ports answer",
"Speed test - download, upload and lag under load (~35 MB)", "DNS timing - is name lookup slowing you down?", "Find devices - everything on your home network",
then "Tools send traffic only when you start them. Esc stops a running tool."

**trace_lines** (the map). Header: `Path to 203.0.113.5 (example.org)` bold + right side status: "tracing… hop 4" cyan / "reached in 9 hops" green / "stopped at hop 12 - the rest does not answer" yellow / error red.
(Show "(name)" only when target != address.) Then:
```
 you
  │
  ●  1  192.0.2.1        home      ━━                3 ms
  ●  2  100.64.0.1       provider  ━━━━━            12 ms
  ○  3  no answer                                    * * *
  ●  4  198.51.100.9     internet  ━━━━━━━━         19 ms  33% lost
  ◆  9  203.0.113.5      target    ━━━━━━━━━━━      24 ms  ✓
```
Rows: glyph ("●" reply, "○" no answer DIM, "◆" target) in KIND_COLOR, ttl right-aligned 2, ip padded to the longest ip (max 39), KIND_WORD DIM padded 9,
bar of "━" scaled: length = round(avg / max_avg * bar_w), at least 1 when avg exists; bar_w = max(4, w - fixed columns - 18); bar colour grad(min(avg/150, 1));
ms right-aligned ("<1 ms" when < 1); loss > 0 -> " NN% lost" yellow. The "│" connector line between "you" and hop 1 only (keep it compact).
If there are more hops than fit: show the first hops and the last hop, with one DIM "  ⋮  N more hops" row in between. When not done, the rows that exist so far
are drawn and a DIM "  …" row follows. Error with no hops: just the header and the error in red.

**ports_lines**:
```
Ports on 203.0.113.5            2 open · 1 closed · 1 no answer
● 443   HTTPS            open        12 ms
● 80    HTTP             open         9 ms
○ 22    SSH              closed      (refused)
◌ 3389  Remote Desktop   no answer   (firewall?)
```
open green "●", closed DIM "○", filtered yellow "◌" with word "no answer", error red "✗" "unreachable", pending cyan "…" "checking". Sorted: open first, then port number.
Last line DIM when it fits: "Open = a program on that device accepts connections there."

**target_lines**: if w >= 96: side by side with hcat (ctop.ui.hcat): trace gets w - 38, ports 36. Else stacked: trace gets h - min(len(ports rows)+1, h//2), ports the rest.
Either may be None (only the other is shown).

**dns_lines**: header "DNS timing" + status (running "testing…" / done). Rows: `● 192.0.2.1   your router   ━━━━━━  120 ms  in use` -
name column = label, bar like trace scaled to the slowest answered, ms or "no answer" red, "in use" tag cyan. Blank line, then the verdict (green if it says "fine",
yellow if it says "Switching", red if "not answering"; else FG).

**speed_lines**:
```
Speed test  ▸ downloading   ■■■■■■■■□□□□□□ 55%
↓ Download   245.3 Mbps
[graph of samples, 3 rows, ctop.ui.graph]
↑ Upload      38.1 Mbps
Ping  idle 14 ms → busy 52 ms   lag under load: B
Used 31 MB of data.  Server: speed.cloudflare.com
```
Stage words: starting/latency "measuring ping", download "downloading", upload "uploading", done "done" green, cancelled yellow, error red + error text.
Graph shows `samples` for the current stage (use the down colour #6fb3ff during download, #f08fd8 during upload); graph rows = 3 if h >= 9 else 1 else 0.
Grade colour: A/B green, C yellow, D/F red. Unknown values "-".

**doctor_lines**: header "Network doctor" + "checking…"/"done". One row per step: icon ✓ green ok, ! yellow warn, ✗ red fail, · DIM skip/pending, ◐ cyan running;
name padded 18 bold-ish FG; detail DIM (ok) / coloured (warn/fail). Then blank, verdict coloured by level, then FIX_HINT[suggestion] in cyan when present.

**lan_lines**: header "Devices on 192.0.2.0/24" + progress ("probing 120/253" with a small meter, or "found 7"). Rows: ip padded, mac or "-", tags:
"router" `#d9a441`, "this computer" cyan, "private MAC" DIM. Footer DIM "Sent one small packet to each address; nothing else was probed." when it fits.

**message_lines**: each string as FG line, first line bold.

## 5 Tests (`tests/test_render_net_tools.py`)
Width/height contract for every function at w in (30, 44, 56, 68, 100), h in (3, 6, 16) - no line wider than w, never more than h lines, no exception for None inputs.
* menu: separator not highlighted when cursor index points at a normal row; running prefix; shortcut right-aligned at w 22.
* trace: reached sample (4 hops incl. a no-answer hop and loss) -> "reached in", "✓", "provider", "* * *", "% lost"; not-done shows "tracing";
  15 hops in h=8 -> contains "more hops" and the last hop's ip; error result -> error text.
* ports: counts header, order open-first, words for each state.
* target: side-by-side at w 100 contains both headers on the first line; stacked at w 56.
* dns: verdict colouring by keyword (inspect styles), "in use" tag.
* speed: stage words, grade, MB used ("31 MB" from bytes_used 31_000_000 using MB = 1e6), graph rows present when h >= 9.
* doctor: icons per status, FIX_HINT shown for "dns".
* lan: progress while not done, tags.
* dispatcher: unknown kind -> one dim line; garbage result type -> dim line (no raise).
