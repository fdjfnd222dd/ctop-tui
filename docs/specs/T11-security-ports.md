# T11 - Security page A: listening ports and connections

## 1 Purpose
Two table renderers for the Security page in plain words for a non-expert: "which programs accept
connections, and from whom" and "which programs are talking to what". Pure functions, no I/O,
no name lookups (never resolve an IP to a hostname).

## 2 Files
Create: `ctop/render/security_ports.py`, `tests/test_render_security_ports.py`.
READ first: `docs/ARCHITECTURE.md`, `ctop/models.py` (`Listener`, `Connection`, `FirewallInfo`), `ctop/ui.py` (`fit`, `DIM`, `FG`, `PALETTE`).
Touch nothing else. Do not import from other `ctop/render/*` modules (they are written in parallel); keep helpers private (`_name`).

## 3 Public API
```python
from __future__ import annotations
from typing import Optional, Sequence
from rich.text import Text
from ctop.models import Connection, FirewallInfo, Listener

def listeners_lines(listeners: "Sequence[Listener]", firewall: Optional[FirewallInfo],
                    w: int, h: int) -> "list[Text]": ...
def connections_lines(conns: "Sequence[Connection]", w: int, h: int) -> "list[Text]": ...
```
Both return `len(result) <= h`. Python 3.9: no `X | Y`, no `match`.

## 4 Visual design
Shared cut rule `_assemble(head, rows, foot, h)` (write it once, use in both functions; `head` is one Text, `rows`/`foot` are lists):
`h <= 0` -> `[]`. If `1 + len(rows) + len(foot) <= h` -> `[head] + rows + foot`. Else drop `foot`; if `1 + len(rows) <= h` -> `[head] + rows`.
Else `keep = h - 2` (>= 0) -> `[head] + rows[:keep] + [more]`, `more = Text("... +%d more" % (len(rows) - keep), style=DIM)`. If `h == 1` -> `[more]` with `len(rows)`.
Every line goes through `ui.fit(t, w)` last. `w < 1` -> `[]`.

### listeners_lines
Dedupe first: key `(proto, port, process, exposure)` (the same program listening on IPv4 and IPv6 is ONE row).
Sort: exposure rank `network` 0, `specific` 1, `local` 2, then `port`, then `proto`. `process is None` -> program text `(other user)` in `DIM`.
Columns (left aligned, fixed widths, no extra gap): Port 6, Proto 7, Program `PROG_W`, Who can reach it = rest.
`PROG_W = 12 if w < 56 else 16 if w < 80 else 20`. Names longer than `PROG_W - 1` are cut to `PROG_W - 2` chars + `…`.
Header (style `bold DIM`), exact text: `Port  Proto  Program` padded to the column starts, then `Who can reach it`.
Port: `str(port)`, bold when exposure is `network`, else `FG`. Proto: `TCP` / `UDP`, `DIM`.
"Who can reach it" wording; full text when `w - 13 - PROG_W >= 24`, else the short text:
| exposure | full | short | colour |
|---|---|---|---|
| local | `only this computer` | `this computer` | `#56d364` |
| network | `everyone on your network` | `your network` | red `#ff7b72` if the firewall is NOT protecting (see below), else yellow `#f2cc60` |
| specific | `one network address` | `one address` | `#f2cc60` |
Firewall: `protected = firewall is not None and firewall.active is not False and firewall.default_incoming in ("deny", "reject")`.
`unprotected = firewall is not None and (firewall.active is False or firewall.default_incoming == "allow")`. Network rows are red when `unprotected`, else yellow.
When `protected` and the row is not `local`: if `w >= 110` append to that row, in `DIM`, `" (firewall blocks it unless a rule allows)"`;
else add ONE foot line (dim) `Firewall: blocks these unless a rule allows them.` (only if there is at least one non-local row).
Empty list -> return just `[Text("Nothing is listening for connections.", style=DIM)]` (cut by `h`, fitted).

Mock, width 90 (firewall on, default deny):
```
Port  Proto  Program             Who can reach it
8080  TCP    python3             everyone on your network
5353  UDP    avahi-daemon        everyone on your network
631   TCP    cupsd               only this computer
Firewall: blocks these unless a rule allows them.
```
Mock, width 40 (same data, h=4 so cut):
```
Port  Proto  Program     Who can reach it
8080  TCP    python3     your network
5353  UDP    avahi-daem… your network
... +1 more
```
### connections_lines
Keep only "live" connections: `status in {"ESTABLISHED", "SYN_SENT", "NONE", ""}` (UDP shows NONE; drop TIME_WAIT, CLOSE_WAIT and the rest - they are noise).
Group by program name (`process`, or `(other user)` when None). Per group: `n` = connections kept; tag:
`internet` if any kept connection has `remote_is_private == False`; else `this computer` if every remote ip starts with `127.` or equals `::1`; else `local network`.
Examples: up to 2 DISTINCT remotes, public ones first then private, formatted `ip:port`, IPv6 (contains `:`) as `[ip]:port`; joined by `", "`.
Sort groups: `internet` tags first, then count descending, then name. Tag colours: internet `#4ea1ff`, local network `#56d364`, this computer `DIM`.
Columns: `name_w = 16 if w < 60 else 20`; count right-aligned width 4; tag width 13 (`local network` is the longest).
`w >= 60`: `name.ljust(name_w) + " " + count.rjust(4) + " " + examples.ljust(ex_w) + " " + tag` with `ex_w = w - name_w - 1 - 4 - 1 - 1 - 13`; examples cut to `ex_w`; use only 1 example when `ex_w < 40`.
`w < 60`: no examples: `name.ljust(name_w) + " " + count.rjust(4) + " " + tag`.
Header (`bold DIM`) aligned to the same columns: `Program`, `Open`, `Talking to` (only when `w >= 60`), `Where`.
Empty -> `[Text("No open connections right now.", style=DIM)]`.

Mock, width 90:
```
Program              Open Talking to                                         Where
firefox                 2 203.0.113.5:443, 198.51.100.9:443                  internet
syncthing               1 192.0.2.20:22000                                   local network
```
Mock, width 40:
```
Program          Open Where
firefox             2 internet
syncthing           1 local network
```

## 5 Rules
- Width: every Text `cell_len(t.plain) == w` (via `fit`), `no_wrap=True`. Height: `len <= h`, ending with `... +N more` when rows were cut.
- `firewall` may be None; `process`/`pid` may be None; never print "None"; never crash on an empty sequence.
- No I/O, no DNS, no `socket` import, no `time`. Characters: ASCII plus `…`.

## 6 Test plan (`tests/test_render_security_ports.py`)
Fixtures: `A = Listener("tcp","0.0.0.0",8080,10,"python3","network")`, `A6 = Listener("tcp","::",8080,10,"python3","network")`,
`B = Listener("tcp","127.0.0.1",631,11,"cupsd","local")`, `C = Listener("udp","192.0.2.5",5353,None,None,"specific")`,
`FW = FirewallInfo("ufw", True, "deny", False)`, `FWOFF = FirewallInfo("ufw", False, "allow", False)`.
`K1 = Connection("tcp",50000,"203.0.113.5",443,"ESTABLISHED",20,"firefox",False)`, `K2 = Connection("tcp",50001,"198.51.100.9",443,"ESTABLISHED",20,"firefox",False)`,
`K3 = Connection("tcp",50002,"192.0.2.20",22000,"ESTABLISHED",21,"syncthing",True)`, `K4 = Connection("tcp",50003,"203.0.113.5",443,"TIME_WAIT",None,None,False)`.
- `test_listener_dedupes_v4_v6`: `[A, A6]` -> exactly one data row.
- `test_listener_sort_order`: `[B, A, C]` -> rows start with ports 8080, 5353, 631 in that order.
- `test_listener_plain_words_full`: w=90 -> row for A contains "everyone on your network", for B "only this computer", for C "one network address".
- `test_listener_short_words_narrow`: w=40 -> contains "your network" and no "everyone".
- `test_listener_colors`: B's "only this computer" span style `#56d364`; A with `FWOFF` is `#ff7b72`; A with `FW` is `#f2cc60`.
- `test_firewall_note_footer_when_narrow`: `FW`, w=90, h=10 -> last line plain starts with "Firewall: blocks these unless a rule allows them."; with `FWOFF` or None no such line.
- `test_firewall_suffix_inline_when_wide`: `FW`, w=120 -> A's row contains "(firewall blocks it unless a rule allows)" and there is no footer line.
- `test_unknown_owner`: C row contains "(other user)".
- `test_listeners_cut_with_more`: 10 distinct listeners, h=5 -> `len == 5`, last plain starts "... +", number = 10 - 3 = 7.
- `test_listeners_empty`: `[]` -> one line "Nothing is listening for connections."
- `test_connections_grouping`: `[K1, K2, K3, K4]`, w=90 -> 2 data rows; firefox row shows the count `2`, contains "203.0.113.5:443, 198.51.100.9:443" and "internet"; syncthing row contains "local network"; TIME_WAIT ignored (K4 not counted).
- `test_connections_ipv6_brackets`: remote "2001:db8::1" -> "[2001:db8::1]:443".
- `test_connections_narrow_has_no_examples`: w=40 -> no "203.0.113" in output.
- `test_connections_empty`: `[]` -> "No open connections right now."
- `test_width_and_height_guarantees`: for w in (1, 12, 40, 90), h in (0, 1, 2, 3, 20): all lines `cell_len == w`, `len <= h`, for both functions.

## 7 Done when
- [ ] `python -m unittest tests.test_render_security_ports -v` passes on Python 3.9+.
- [ ] `python -m pyflakes ctop/render/security_ports.py tests/test_render_security_ports.py` is silent.
- [ ] Only the two listed files created; no new dependency; no network/DNS call anywhere.
- [ ] Only documentation addresses in fixtures.
