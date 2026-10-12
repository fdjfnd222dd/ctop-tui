# T12 - Security page B: devices on your network, and "How exposed am I?"

## 1 Purpose
(a) A plain-words table of the devices this computer has recently talked to (read from the kernel's
neighbour table, passive - nothing is scanned). (b) The "How exposed am I?" box: a LOW/MEDIUM/HIGH
verdict from fixed rules, the reasons in full sentences, and the firewall state. Pure functions, no I/O.

## 2 Files
Create: `ctop/render/security_lan.py`, `tests/test_render_security_lan.py`.
READ first: `docs/ARCHITECTURE.md`, `ctop/models.py` (`Device`, `SecurityData`, `FirewallInfo`, `Listener`, `WifiData`), `ctop/ui.py` (`fit`, `kv`, `DIM`, `FG`).
Touch nothing else. Do not import other `ctop/render/*` modules; keep helpers private.

## 3 Public API
```python
from __future__ import annotations
from typing import List, Optional, Sequence, Tuple
from rich.text import Text
from ctop.models import Device, SecurityData, WifiData

def devices_lines(devices: "Sequence[Device]", w: int, h: int) -> "List[Text]": ...
def assess(sec: SecurityData, wifi: Optional[WifiData]) -> "Tuple[str, List[str]]": ...   # ("LOW"|"MEDIUM"|"HIGH", reasons)
def summary_lines(sec: SecurityData, wifi: Optional[WifiData], w: int, h: int) -> "List[Text]": ...
```
`len(result) <= h`. Python 3.9: no `X | Y`, no `match`.

## 4 Visual design
### devices_lines
Skip devices whose state is `FAILED` or `INCOMPLETE` (dead entries). Dedupe by `ip` (keep first).
Sort: `is_gateway` first, then `REACHABLE` before the rest, then address (`ipaddress.ip_address`, IPv4 before IPv6; on ValueError sort by string).
Role: `router` (bold `#39c5cf`) when `is_gateway`, else `device` (`FG`).
State in plain words: `REACHABLE` -> `active now` (`#56d364`); `STALE`, `DELAY`, `PROBE` -> `seen recently` (`DIM`); `PERMANENT` -> `fixed entry` (`DIM`); anything else -> `state.lower()` (`DIM`).
Tag `private address` (style `#bc8cff`) when `random_mac` (a phone/laptop hiding its real hardware ID - normal, not a threat).
Columns, fixed widths (cut longer values with `…`), no gap column:
- `w < 62`: IP 16, Role 8, State rest.   - `62 <= w < 84`: IP 16, MAC 18, Role 8, State 14.   - `w >= 84`: same plus tag (width 15) after State.
Lines, top to bottom: (1) note, only when `h >= 4`, style `DIM`: `w >= 70`: `Only devices this computer talked to recently. Nothing is scanned.` else `Only recent contacts. Nothing scanned.`;
(2) header, style `bold DIM`: `IP address`, `MAC`, `Role`, `State`; (3) one row per device; (4) `... +N more` when cut.
Cut rule: `top` = [note, header] (or [header] when `h < 4`); if `len(top) + len(rows) <= h` all; else `keep = h - len(top) - 1`; if `keep < 0` return `[more]` (with `N = len(rows)`) when `h == 1`;
otherwise `top + rows[:keep] + [more]`, `more = Text("... +%d more" % (len(rows) - keep), style=DIM)`. `h <= 0` -> `[]`.
Empty (after filtering) -> `top + [Text("No other devices seen yet.", style=DIM)]` cut to `h`.
Mock, width 40:
```
Only recent contacts. Nothing scanned.
IP address      Role    State
192.0.2.1       router  active now
192.0.2.23      device  active now
192.0.2.40      device  seen recently
```
Mock, width 90:
```
Only devices this computer talked to recently. Nothing is scanned.
IP address      MAC               Role    State
192.0.2.1       02:00:00:aa:bb:cc router  active now
192.0.2.23      02:00:00:aa:bb:cd device  active now    private address
192.0.2.40      02:00:00:aa:bb:ce device  seen recently
```
### assess(sec, wifi) - THE RULES (deterministic, no randomness, no I/O)
```
n   = number of DISTINCT (proto, port, process) among sec.listeners with exposure == "network"
fw  = sec.firewall;  ON = fw.active is True;  OFF = fw.active is False;  UNKNOWN = fw.active is None
deny = fw.default_incoming in ("deny", "reject")
level (0=LOW,1=MEDIUM,2=HIGH):
  n == 0                               -> 0
  n > 0 and (OFF or (ON and fw.default_incoming == "allow")) -> 2
  n > 0 otherwise (ON+deny, ON+unknown policy, UNKNOWN)      -> 1
weak Wi-Fi: wifi is not None and wifi.security is not None; s = wifi.security.strip().lower();
            weak = (s in ("none", "open")) or s.startswith("wep")   ->  if weak: level = min(level + 1, 2)
```
UNKNOWN counts as MEDIUM, not LOW, because "we could not check" is not "safe".
Reasons, in this order, EXACT sentences (`N` is `n`):
1. `n == 0`: `Nothing is listening for connections from other devices on your network.`  `n == 1`: `1 program accepts connections from other devices on your network.`  `n > 1`: `N programs accept connections from other devices on your network.`
2. Firewall sentence: `n>0 and OFF`: `The firewall is off, so nothing stops other devices from reaching them.` | `n>0, ON, deny`: `The firewall is on and blocks anything you have not allowed.` | `n>0, ON, allow`: `The firewall is on but lets connections in by default.` | `n>0, ON, policy None`: `The firewall is on, but its default rule is unknown.` | `n>0, UNKNOWN`: `Could not tell whether a firewall is on.` | `n==0, OFF`: `The firewall is off, but nothing is listening for outside connections right now.` | `n==0` otherwise: no sentence.
3. Wi-Fi: security none/open: `Your Wi-Fi network has no password, so people nearby can listen in.`  WEP: `Your Wi-Fi uses WEP, an old lock that is easy to break.`
### summary_lines
Body, then footer. Wrap every reason with `textwrap.wrap(reason, width=max(w - 2, 1))`; first piece prefixed `"• "`, the rest `"  "`. Reasons are `FG`.
1. Headline `kv("Exposure", LEVEL, bold+colour, lw=10)` + dim suffix: LOW `" - looks fine"` (`#56d364`), MEDIUM `" - worth a look"` (`#f2cc60`), HIGH `" - needs attention"` (`#ff7b72`).
2. Reason lines (above). 3. `kv("Firewall", ...)`: `"<backend>, on"` (green `#56d364`) / `"<backend>, off"` (red `#ff7b72`) / `"<backend>, state unknown"` (yellow); backend None -> `none detected` (`DIM`; then stop the firewall block here).
4. `kv("Incoming", ...)`: `deny`/`reject` -> `blocked unless a rule allows it`; `allow` -> `allowed by default` (yellow); None -> `unknown` (`DIM`). 5. `kv("Rules", f"{len(rules)} rules")` only if `rules_visible`.
6. `fw.note` (if non-empty), wrapped to `w`, `DIM`, no bullet. Footer (always last, `DIM`): `Not a security audit.`
Cut: `h <= 0` -> `[]`; `h == 1` -> headline only; `h == 2` -> `[headline, footer]`; if `len(body) + 1 <= h` -> `body + [footer]`; else `body[:h-2] + [more] + [footer]` (`more` = `... +N more`, N lines hidden). The footer is NEVER cut.
Mock, width 90, h 12 (1 listener, ufw on/deny, note "Rule list needs root."):
```
Exposure  MEDIUM - worth a look
• 1 program accepts connections from other devices on your network.
• The firewall is on and blocks anything you have not allowed.
Firewall  ufw, on
Incoming  blocked unless a rule allows it
Rule list needs root.
Not a security audit.
```
Mock, width 40, h 12 (2 listeners, firewall off, open Wi-Fi, note "Rule list needs root."):
```
Exposure  HIGH - needs attention
• 2 programs accept connections from
  other devices on your network.
• The firewall is off, so nothing stops
  other devices from reaching them.
• Your Wi-Fi network has no password, so
  people nearby can listen in.
Firewall  ufw, off
Incoming  allowed by default
Rule list needs root.
Not a security audit.
```
(that is 10 lines; with `h=8` it would show the first 6 body lines, `... +4 more`, and the footer.)

## 5 Rules
- Width: every Text goes through `ui.fit(t, w)` last -> `cell_len(t.plain) == w`; `no_wrap=True`; `w < 1` -> `[]`. Height: `len <= h`.
- `wifi` may be None; `fw.note` may be empty; `listeners` may be empty; never print "None".
- No I/O, no clock, no `socket`. Documentation addresses only in tests.

## 6 Test plan (`tests/test_render_security_lan.py`)
Helpers: `def FW(active, default, backend="ufw", note="", rules_visible=False, rules=()): return FirewallInfo(backend, active, default, rules_visible, tuple(rules), note)`;
`def SEC(listeners=(), fw=None): return SecurityData(listeners=tuple(listeners), firewall=fw or FW(True,"deny"))`; `NET = Listener("tcp","0.0.0.0",8080,10,"python3","network")`;
`LOC = Listener("tcp","127.0.0.1",631,11,"cupsd","local")`; `def WIFI(sec): return WifiData("wlan0","ExampleNet",70,-60,144.0,5180,36,sec,"Good")`.
Devices: `GW = Device("192.0.2.1","02:00:00:aa:bb:cc","wlan0","REACHABLE",True,False)`, `D1 = Device("192.0.2.23","02:00:00:aa:bb:cd","wlan0","REACHABLE",False,True)`, `D2 = Device("192.0.2.40","02:00:00:aa:bb:ce","wlan0","STALE",False,False)`, `D3 = Device("192.0.2.9","02:00:00:aa:bb:cf","wlan0","FAILED",False,False)`.
- `test_devices_roles_and_states`: w=90 -> GW row has "router" and "active now"; D2 row "seen recently".
- `test_devices_gateway_first_and_numeric_sort`: `[D2, D1, GW]` -> order 192.0.2.1, 192.0.2.23, 192.0.2.40; also `192.0.2.9` sorts before `192.0.2.23`.
- `test_devices_failed_hidden`: D3 absent.
- `test_private_address_tag_only_when_wide`: w=90 D1 row contains "private address"; w=40 does not.
- `test_devices_note_line`: h=10 first line starts "Only devices this computer talked to recently." (w=90) / "Only recent contacts." (w=40); with h=3 no note line.
- `test_devices_empty`: `[]` -> contains "No other devices seen yet."
- `test_devices_cut_more`: 12 devices, h=6 -> `len == 6`, last line starts "... +".
- `test_assess_low`: `assess(SEC([LOC]), None)[0] == "LOW"`; reasons[0] == "Nothing is listening for connections from other devices on your network."
- `test_assess_medium_firewall_deny`: `SEC([NET], FW(True,"deny"))` -> "MEDIUM", reasons[0] == "1 program accepts connections from other devices on your network.", reasons[1] == "The firewall is on and blocks anything you have not allowed."
- `test_assess_high_firewall_off`: `FW(False,"allow")` -> "HIGH". `test_assess_high_default_allow`: `FW(True,"allow")` -> "HIGH". `test_assess_unknown_is_medium`: `FW(None,None)` -> "MEDIUM" and reasons[1] == "Could not tell whether a firewall is on."
- `test_wifi_open_raises_one_level`: LOW + `WIFI("none")` -> "MEDIUM"; MEDIUM + `WIFI("WEP")` -> "HIGH"; HIGH stays "HIGH"; `WIFI("WPA2")` and `WIFI(None)` change nothing.
- `test_two_listeners_counted_once_per_program`: NET and the same on "::" count as 1 program.
- `test_summary_has_footer_always`: for h in (1,2,3,5,12) last line (h>=2) is "Not a security audit."; `len <= h`.
- `test_summary_firewall_lines`: contains "Firewall  ufw, on" and "Incoming  blocked unless a rule allows it"; backend None -> "none detected" and no "Incoming".
- `test_summary_wraps_at_40`: all lines `cell_len == 40`; a long reason spans two lines (second starts with two spaces).
- `test_width_guarantee`: w in (1, 12, 40, 90) for both functions.

## 7 Done when
- [ ] `python -m unittest tests.test_render_security_lan -v` passes on Python 3.9+.
- [ ] `python -m pyflakes ctop/render/security_lan.py tests/test_render_security_lan.py` is silent.
- [ ] Only the two listed files created; no new dependency; stdlib `ipaddress` and `textwrap` are fine.
