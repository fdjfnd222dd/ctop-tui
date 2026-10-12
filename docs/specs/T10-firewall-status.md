# T10 - Firewall status without root

## 1 Purpose
`read_firewall()` tells the Security page, without sudo, which firewall tool is in charge, whether it is on, what it does with incoming traffic, and (when readable) its rules. It must be honest: "nothing detected" is NEVER reported as "no firewall".

## 2 Files
Create `ctop/collectors/firewall.py`, `tests/test_firewall.py`. READ: `docs/ARCHITECTURE.md`, `ctop/models.py` (`FirewallInfo`). Touch nothing else.

## 3 Public API
```python
from __future__ import annotations
from typing import Callable, List, Optional, Tuple
from ctop.models import FirewallInfo

Runner = Callable[[List[str], float], Optional[str]]     # (argv, timeout_seconds) -> stdout text, or None if the tool is missing / timed out

def run_cmd(argv: List[str], timeout: float) -> Optional[str]: ...    # subprocess.run(capture_output, text, timeout, stdin=DEVNULL, env LC_ALL=C); stdout even when exit code != 0; None on OSError/TimeoutExpired
def parse_ufw_conf(text: str) -> Optional[bool]: ...                  # ENABLED=yes -> True, no -> False, absent -> None
def parse_default_input_policy(text: str) -> Optional[str]: ...       # /etc/default/ufw: DROP->"deny", REJECT->"reject", ACCEPT->"allow", else None
def parse_ufw_user_rules(text: str) -> Tuple[str, ...]: ...           # "### tuple ###" lines -> readable rule strings (max 100)
def parse_firewalld_list_all(text: str) -> Tuple[Optional[str], Tuple[str, ...]]: ...   # (default_incoming, rules)
def parse_is_active(text: Optional[str], units: Tuple[str, ...]) -> dict: ...  # unit -> "active"/"inactive"/"failed"/"unknown"

def read_firewall(root: str = "/", runner: Optional[Runner] = None) -> FirewallInfo: ...
```
`runner=None` -> `run_cmd`. `root` is prefixed to `etc/ufw/ufw.conf`, `etc/default/ufw`, `etc/ufw/user.rules` (use `os.path.join(root, "etc/ufw/ufw.conf")`). Never raises: every file read and parse is wrapped. Python 3.9 syntax only.

## 4 Data sources
Files (world-readable on Arch, Debian and Ubuntu):
`etc/ufw/ufw.conf`: lines like `ENABLED=yes` (ignore `#` comments, strip quotes, case-insensitive value).
`etc/default/ufw`: `DEFAULT_INPUT_POLICY="DROP"` (value may be quoted; ignore other keys).
`etc/ufw/user.rules` (readable on Arch, usually `root:root 640` on Ubuntu/Debian -> `PermissionError` is normal). Anonymised excerpt, keep ONLY the `### tuple ###` lines (comments `comment=<hex>` are ignored, never decoded):
```
### tuple ### allow tcp 22 0.0.0.0/0 any 0.0.0.0/0 in
### tuple ### allow tcp 8000,8001 0.0.0.0/0 any 192.0.2.0/24 in
### tuple ### allow udp 5353 0.0.0.0/0 any 0.0.0.0/0 in_wlan0 comment=4d444e53
### tuple ### deny any any 0.0.0.0/0 any 198.51.100.7 in
```
Format: `### tuple ### <action> <proto> <dport> <dst> <sport> <src> [<direction>]`. Rule text = `f"{action} {proto} {dport}"`; append `" from " + src` if src is not `0.0.0.0/0`/`::/0`; append `" to " + dst` if dst is not `0.0.0.0/0`/`::/0`; direction `in_<iface>` -> append `" on <iface>"`; direction `out` or `out_*` -> append `" (outgoing)"`. Results for the excerpt: `allow tcp 22`, `allow tcp 8000,8001 from 192.0.2.0/24`, `allow udp 5353 on wlan0`, `deny any any from 198.51.100.7`. Only IPv4 `user.rules` is read (`user6.rules` mirrors it).
Commands (all through `runner`, one call each, max 3 calls total):
* `["systemctl", "is-active", "ufw", "firewalld", "nftables"]`, timeout 2 -> stdout has ONE line per unit in order (`active` / `inactive` / `failed` / `unknown`); the exit code is irrelevant. `None` or too few lines -> `"unknown"` for the missing units. (`ufw.service` reports `active` on Ubuntu even though it only runs once at boot - that is fine.)
* Only if firewalld is `active`: `["firewall-cmd", "--list-all"]`, timeout 2. If systemctl itself was unavailable (all `unknown`): try `["firewall-cmd", "--state"]` first and treat a line exactly equal to `running` as active. Anonymised `--list-all`:
```
public (active)
  target: default
  interfaces: wlan0
  services: dhcpv6-client ssh
  ports: 8080/tcp 5000-5010/udp
```
`parse_firewalld_list_all`: `target:` `default` or `REJECT` -> `"reject"`, `DROP` -> `"deny"`, `ACCEPT` -> `"allow"`, missing -> `None`; rules = `service <name>` for each word after `services:` and `port <p>` for each word after `ports:` (e.g. `service ssh`, `port 8080/tcp`).

## 4b Decision table (check top to bottom; first match wins) - wording is exact
In the `note` column the note text is everything between the OUTER backticks; backticks inside it are literal characters of the note.
| # | Condition | backend | active | default_incoming | rules_visible / rules | note |
|---|---|---|---|---|---|---|
| 1 | `ufw.conf` says `ENABLED=yes` and ufw service is not `inactive`/`failed` | `"ufw"` | `True` | from `etc/default/ufw` (`None` if unreadable) | `True` + parsed rules if `user.rules` is readable; else `False`, `()` | `""` if rules read, else `Rule list needs root - run `sudo ufw status` in another terminal` |
| 2 | `ENABLED=yes` but service `inactive` or `failed` | `"ufw"` | `None` | `None` | `False`, `()` | `ufw is switched on in its settings, but its service is not running - run `sudo ufw status` to check.` |
| 3 | firewalld service `active` (or `--state` = running) | `"firewalld"` | `True` | from zone target | `True` + rules if `--list-all` worked, else `False`, `()` | `""` if listed, else `firewalld is running. Run `firewall-cmd --list-all` in another terminal to see its rules.` |
| 4 | `ufw.conf` says `ENABLED=no` | `"ufw"` | `False` | `None` | `False`, `()` | `ufw is installed but switched off. Turn it on with `sudo ufw enable`.` |
| 5 | `ufw.conf` exists but cannot be read / has no ENABLED line | `"ufw"` | `None` | `None` | `False`, `()` | `ufw is installed but its settings need root to read - run `sudo ufw status`.` |
| 6 | nftables service `active` | `"nftables"` | `None` | `None` | `False`, `()` | `nftables is loaded but its rules need root to read. Debian's default setup lets everything through, so this does not prove you are protected - run `sudo nft list ruleset` to check.` |
| 7 | nothing above | `None` | `None` | `None` | `False`, `()` | `No firewall tool (ufw or firewalld) was found. That is normal on a fresh Debian install. A firewall written directly with nftables or iptables could still exist - it cannot be seen without root. Run `sudo nft list ruleset` to check.` |
`active` is `False` ONLY in row 4 (an explicit "off" in ufw's own settings). Row 7 must never produce `active=False`. If ufw is on (row 1) AND firewalld is active, report ufw.

## 5 Edge cases & distro differences
* Ubuntu desktop ships ufw installed but DISABLED (row 4); Debian ships NO ufw (row 7, or row 6 if the `nftables` package is installed and enabled - its default `/etc/nftables.conf` accepts everything, hence the cautious wording); Fedora-family uses firewalld (row 3); Arch ships nothing by default.
* `iptables`/legacy rules cannot be detected unprivileged - the row 7 note says so. Do not try `iptables -L` or `nft list` (they need root and would print errors).
* No systemd (Devuan, Alpine, containers): `systemctl` missing -> all `unknown`; file-based ufw rows 1/4/5 still work.
* `ufw.conf` present but `etc/default/ufw` missing -> row 1 with `default_incoming=None`.
* Malformed `### tuple ###` lines are skipped silently; more than 100 rules are cut at 100.

## 6 Performance & safety limits
At most two small file reads, one `systemctl` call and (rarely) one `firewall-cmd` call, each with `timeout=2`; about 10-20 ms total. Intended to be called at most every 10 s by the caller. Strictly read-only: never `ufw enable`, `ufw status`, `nft`, `iptables`, `systemctl start/stop`, never `sudo`. Rule comments (hex-encoded user text) are never decoded or shown.

## 7 Test plan (`tests/test_firewall.py`) - fake root under `tempfile.TemporaryDirectory()`, fake runner
Helper `write(root, relpath, text)` creates dirs/files; `FakeRunner(systemctl="active\ninactive\ninactive\n", firewalld_list=None, state=None)` returns the canned text by argv[0]/argv[1] and records calls. Simulate an unreadable file by making the path a directory.
* `test_row1_ufw_on_with_rules`: `ufw.conf` `ENABLED=yes`, `default/ufw` `DEFAULT_INPUT_POLICY="DROP"`, `user.rules` = the excerpt; systemctl `active/inactive/inactive` -> `FirewallInfo("ufw", True, "deny", True, (the 4 strings above), "")`.
* `test_row1_rules_unreadable`: same but `user.rules` is a directory -> `rules_visible False`, `rules == ()`, note exactly `Rule list needs root - run `sudo ufw status` in another terminal`, `active True`.
* `test_default_policies`: `"REJECT"` -> `"reject"`, `ACCEPT` -> `"allow"`, quotes optional, missing file -> `None`.
* `test_row2_enabled_but_service_failed`: systemctl `failed/inactive/inactive` -> `active is None` and the exact row 2 note.
* `test_row3_firewalld`: no ufw files; systemctl `inactive/active/inactive`; `--list-all` sample -> `backend "firewalld"`, `active True`, `default_incoming "reject"`, `rules == ("service dhcpv6-client","service ssh","port 8080/tcp","port 5000-5010/udp")`, `rules_visible True`.
* `test_row3_firewalld_list_fails`: `--list-all` returns `None` -> `rules_visible False` and the exact row 3 note.
* `test_ufw_on_wins_over_firewalld`: ufw enabled and firewalld active -> backend `"ufw"`.
* `test_row4_ufw_disabled`: `ENABLED=no` (the Ubuntu default) -> `FirewallInfo("ufw", False, None, False, (), <row 4 note>)`.
* `test_row5_conf_unreadable`: `ufw.conf` is a directory -> `active is None`, row 5 note.
* `test_row6_nftables_only`: no ufw files, systemctl `inactive/inactive/active` -> backend `"nftables"`, `active is None`, note contains `does not prove you are protected`.
* `test_row7_nothing_detected_is_not_no_firewall`: empty root, systemctl `inactive` x3 -> `backend is None`, `active is None` (NOT False), note contains `could still exist`.
* `test_no_systemctl_binary`: runner returns `None` for everything, empty root -> row 7; with ufw enabled in files -> row 1 (`active True`).
* `test_systemctl_short_output`: stdout `active\n` only -> ufw `active`, others `unknown`, no crash.
* `test_tuple_parser_variants`: lines with `out`, `in_eth0`, ipv6 `::/0`, a malformed `### tuple ### allow` and 150 valid lines -> exactly the expected strings, malformed skipped, length capped at 100.
* `test_runner_calls_have_timeouts`: every recorded call has timeout `<= 2` and never contains `sudo`, `nft`, `iptables`.

## 8 Done when
- [ ] `python -m unittest tests.test_firewall -v` passes; no subprocess run, no read outside the temp root.
- [ ] `python -m pyflakes ctop/collectors/firewall.py tests/test_firewall.py` clean.
- [ ] No new dependencies; Python 3.9 syntax.
- [ ] Only the two listed files created; note strings match the table character for character (ASCII hyphen, backticks included).
- [ ] `active=False` appears in the code only in row 4.
