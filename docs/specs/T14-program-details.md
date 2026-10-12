# T14 - Program details popup lines (Enter key)

## 1 Purpose
Fill the "program details" popup: what a program is (its processes), how much RAM and how many threads
it uses, how long it has been running, who it talks to and which ports it keeps open - in plain words.
Pure function over `ProgramDetail` (built elsewhere by the T13 collector). No I/O.

## 2 Files
Create: `ctop/render/details.py`, `tests/test_render_details.py`.
READ first: `docs/ARCHITECTURE.md`, `ctop/models.py` (`ProgramDetail`, `ProcessDetail`, `Connection`, `Listener`), `ctop/ui.py` (`fit`, `kv`, `fmt_gb`, `DIM`, `FG`).
Touch nothing else. Do not import other `ctop/render/*` modules; keep helpers private.

## 3 Public API
```python
from __future__ import annotations
from typing import List, Optional
from rich.text import Text
from ctop.models import ProgramDetail

def detail_lines(detail: ProgramDetail, w: int, h: int,
                 now: Optional[float] = None, locked: bool = False) -> "List[Text]": ...
```
`now` is the current epoch time (tests pass a fixed number; if None use `time.time()` - the only clock read allowed).
`locked=True` means ctop's safety net forbids stopping this program (the app passes `is_protected(name)`).
`len(result) <= h`. Python 3.9: no `X | Y`, no `match`.

## 4 Visual design
Blocks, top to bottom (`kv` always with `lw=12`; `fit` every line at the end):
1. HEAD (4 lines): `name` in `bold` + `"   N process"` / `"   N processes"` in `DIM` (N = `total_processes`) | `kv("RAM", fmt_gb(total_ram_bytes), "bold")` |
   `kv("Threads", str(total_threads))` | `kv("Running", age)` + dim `"  (oldest process)"`; `age = "unknown"` (DIM) when `oldest_started is None`.
   `age(seconds)`: `< 60` -> `"45s"`; `< 3600` -> `"12m"`; `< 86400` -> `"3h 12m"` (`f"{h}h {m:02d}m"`); else `"2d 3h"`. Negative -> `"0s"`.
2. PROCESSES: a header line then rows. Columns (single space between): PID left 7, RAM right 9 (`fmt_gb`), Threads right 7, Files right 5, Command = rest.
   Header (`bold DIM`) `PID`, `RAM`, `Threads`, `Files`, `Command`. If `w < 50` drop Threads and Files. `Command` width = `w - 32` (or `w - 18` when narrow);
   text is `cmdline`, or `name` when cmdline is empty; too long -> cut to `width - 1` chars + `…`. `threads`/`open_files` None -> `-`. Colours: pid `DIM`, RAM `FG`, numbers `DIM`, command `FG`.
   If rows are cut (or `total_processes > len(processes)`) end with `Text("... +N more", style=DIM)` where `N = total_processes - rows_shown`.
3. CONNECTIONS: `n = len(connections)`. `n == 0` -> `kv("Connections", "none open", DIM)`. Else `kv("Connections", f"{n} open: {pub} to the internet, {priv} on your local network")`
   (`pub` = count with `remote_is_private == False`); when `w < 70` use `f"{n} open ({pub} internet, {priv} local)"`. Then up to `ex` example lines: distinct `ip:port` (IPv6 as `[ip]:port`), public first;
   format `"  " + remote.ljust(26) + tag`, tag `internet` (`#4ea1ff`) or `local network` (`#56d364`). Hidden examples -> last line `"  ... +N more"` (`DIM`) replaces the final example.
4. LISTENING: no listeners -> `kv("Listening", "no open ports", DIM)`. Else `kv("Listening", "1 port")` / `"3 ports"` (distinct `(proto, port)`), then up to `ex` lines `"  TCP 8080   " + who`:
   `who`: exposure `local` -> `only this computer` (`#56d364`); `network` -> `everyone on your network` (`#f2cc60`); `specific` -> `one network address` (`#f2cc60`). Same "+N more" rule.
5. FOOT (always the LAST line, never cut): `Esc closes   x x stops this program` in `DIM`; if `locked`: `Esc closes   this program is locked and cannot be stopped` in `#8b5a5a`.
One blank line separates each pair of neighbouring blocks, including LISTENING|FOOT (4 blanks total, all dropped together when `blanks` is False).

Height fitting (brute force, deterministic): build candidates in this order and return the first with `len <= h`:
for `(ex, blanks)` in `[(3,True),(2,True),(1,True),(1,False),(0,False)]`: for `P` from `min(15, len(processes))` down to `min(2, len(processes))`: candidate = HEAD + PROCESSES(P rows) + CONNECTIONS(ex) + LISTENING(ex) + FOOT (+ blanks if `blanks`).
If none fits: `(HEAD + [FOOT])[: h - 1] + [FOOT]` for `h >= 2`, `[FOOT]` for `h == 1`, `[]` for `h <= 0`.

Mock, width 90, h 30:
```
firefox   3 processes
RAM         1.74 GB
Threads     142
Running     3h 12m  (oldest process)

PID           RAM Threads Files Command
4101      1.20 GB      84   310 /usr/lib/firefox/firefox -new-window https://example.org/…
4188      0.41 GB      31    96 /usr/lib/firefox/firefox -contentproc -isForBrowser
4230      0.13 GB      27    58 /usr/lib/firefox/firefox -contentproc -parentBuildID

Connections 9 open: 8 to the internet, 1 on your local network
  203.0.113.5:443            internet
  198.51.100.9:443           internet
  192.0.2.20:22000           local network

Listening   1 port
  TCP 8080   only this computer

Esc closes   x x stops this program
```
Mock, width 40, h 20 (no Threads/Files columns, short connection sentence):
```
firefox   3 processes
RAM         1.74 GB
Threads     142
Running     3h 12m  (oldest process)

PID           RAM Command
4101      1.20 GB /usr/lib/firefox/fire…
4188      0.41 GB /usr/lib/firefox/fire…
4230      0.13 GB /usr/lib/firefox/fire…

Connections 9 open (8 internet, 1 local)
  203.0.113.5:443            internet
  198.51.100.9:443           internet
  192.0.2.20:22000           local network

Listening   1 port
  TCP 8080   only this computer

Esc closes   x x stops this program
```
(Lines wider than `w` are cut by `fit`; at 40 the example lines lose the end of the tag - that is fine.)

## 5 Rules
- Width: last step `fit(t, w)` on every line -> `cell_len(t.plain) == w`; `no_wrap=True`; `w < 1` -> `[]`.
- Height: `len <= h`; the footer is always the last line when `h >= 1`.
- None handling: `started`, `threads`, `open_files`, `user`, `oldest_started` may be None; empty tuples everywhere are fine; never print "None".
- Never show `user`, never show environment; the command line is already truncated to 300 chars by the collector.
- No I/O except the optional `time.time()`. No `psutil` import.

## 6 Test plan (`tests/test_render_details.py`)
Fixture (documentation addresses only):
`P1 = ProcessDetail(4101,"firefox","/usr/lib/firefox/firefox -new-window",None,1288490188,84,310,1000.0,"running")`,
`P2 = ProcessDetail(4188,"firefox","",None,440000000,None,None,1000.0,"sleeping")`,
`C1 = Connection("tcp",50000,"203.0.113.5",443,"ESTABLISHED",4101,"firefox",False)`, `C2 = Connection("tcp",50001,"192.0.2.20",22000,"ESTABLISHED",4101,"firefox",True)`,
`LS = Listener("tcp","127.0.0.1",8080,4101,"firefox","local")`,
`D = ProgramDetail("firefox",3,(P1,P2),(C1,C2),(LS,),1000.0,1728490188,142)`. `NOW = 1000.0 + 3*3600 + 12*60`.
- `test_header`: `detail_lines(D, 90, 30, now=NOW)` first line starts "firefox   3 processes"; contains a line starting "RAM" with "1.61 GB" (`1728490188 / 1024**3`), "Threads     142", and "Running     3h 12m".
- `test_age_formats`: `now` offsets 30 s -> "30s", 720 s -> "12m", 90000 s -> "1d 1h".
- `test_unknown_start`: `oldest_started=None` -> "Running     unknown".
- `test_process_table_columns`: header line contains "PID", "RAM", "Threads", "Files", "Command" in that order at w=90; at w=40 contains no "Threads".
- `test_process_rows`: P1 row contains "84" and "310"; P2 row shows `-` twice and its command is "firefox" (the name, since cmdline is empty).
- `test_command_trimmed_with_ellipsis`: a 200-char cmdline at w=60 -> row ends with "…" (rstrip) and `cell_len == 60`.
- `test_more_line`: `total_processes=40`, 2 rows -> a line "... +38 more".
- `test_connections_summary`: contains "2 open: 1 to the internet, 1 on your local network" at w=90, "2 open (1 internet, 1 local)" at w=40; example lines contain "203.0.113.5:443" and "192.0.2.20:22000".
- `test_no_connections_or_ports`: empty tuples -> "none open" and "no open ports".
- `test_listening_words`: "1 port" and "TCP 8080" and "only this computer".
- `test_footer_normal_and_locked`: last line "Esc closes   x x stops this program"; with `locked=True` "Esc closes   this program is locked and cannot be stopped".
- `test_height_fit_and_footer_kept`: for h in (1, 2, 5, 9, 12, 20, 30): `len <= h`, last line is the footer; at h=30 all sections present; at h=12 "Connections" still present.
- `test_width_guarantee`: w in (1, 12, 40, 90) -> `cell_len == w`, `no_wrap`.
- `test_no_None_text`: no line contains "None".

## 7 Done when
- [ ] `python -m unittest tests.test_render_details -v` passes on Python 3.9+.
- [ ] `python -m pyflakes ctop/render/details.py tests/test_render_details.py` is silent.
- [ ] Only the two listed files created; no new dependency.
