# T32 - Documentation for the Network page

## 1 Purpose
Update the user docs for the new Network page (page 3). ctop's promise changes from "read-only" to: **read-only, except two things you
confirm: stopping a program, and network changes (Wi-Fi, saved networks, DNS, VPN). Network tools send traffic only when you start them.**

## 2 Files (edit only these)
`README.md`, `docs/SAFETY.md`, `docs/PORTING-DEBIAN.md`, `docs/ARCHITECTURE.md`. Read first: `docs/specs/NETWORK-PAGE.md` (keys), all specs T18-T31
(features), `tests/test_docs.py` (these tests MUST keep passing - section order, key table rows, "## The six rules", porting words, no personal data:
only IPv4 from 192.0.2.x / 198.51.100.x / 203.0.113.x / 127.x / 0.0.0.0 may appear in docs - so write public resolvers as "Cloudflare", "Quad9", "Google" in words, never their IPs).

## 3 What to write
* **README.md**: tagline + intro mention the Network page. Features table: new row **Network page** (Wi-Fi picker with arrow keys, saved networks,
  DNS switcher, VPN/Tailscale, link details and channel crowding, event log) and row **Network tools** (doctor, trace & ports, speed test, DNS timing, find devices).
  Keys table: change `Tab, 1, 2` row to `Tab, 1, 2, 3` (keep the test's expected rows valid - read the test and keep every row it asserts, adding new rows),
  add a short "Network page keys" table: ←/→ box, ↑/↓ choose, Enter menu/run, r rescan, t trace & ports, d doctor, w Wi-Fi on/off, Esc stop tool.
  Privacy section: rewrite to state exactly what traffic each tool sends and that nothing runs on its own (speed test ~35 MB to Cloudflare; trace sends UDP probes to the
  address you type; port check opens TCP connections to the ports you pick, max 32, nothing is sent over them; DNS timing sends a few lookups to your DNS and three public
  resolvers; doctor pings your router and does one NetworkManager connectivity check; find devices sends one empty UDP packet to each address of your own
  private subnet, max 254). Safety section: add the network changes bullet list (confirm menus, never a password in a command line, a wrong password leaves no
  half-made profile, airplane mode keeps cables working, no root). Troubleshooting rows: "Network page says NetworkManager is not running" and
  "NetworkManager refused (polkit)".
* **docs/SAFETY.md**: new section `## Network changes` after the existing rules (do not renumber "The six rules"): what can change, the confirms, polkit, password handling,
  what is never done (no hostname changes, no `networking off`, no firewall edits, no root).
* **docs/PORTING-DEBIAN.md**: section "Network page on Debian": NetworkManager is on every Debian desktop install; nmcli 1.42 (Debian 12) and 1.52 (Debian 13) both work;
  desktop users in the active session may change Wi-Fi by default; over SSH or on minimal installs polkit may say no -> the page shows the reason; `iw` optional
  (`sudo apt install iw` shown as a command for the reader); Tailscale optional; tracing needs nothing extra (falls back to `tracepath` from `iputils-tracepath`).
* **docs/ARCHITECTURE.md**: principle 1 and 2 rewritten to the new promise (changes only through `ctop/netctl.py` and `ctop/safety.py`; traffic only from
  `collectors/ping.py` and user-started `ctop/tools/*`). Add `ctop/netctl.py`, `ctop/tools/`, `ctop/screens.py`, `ctop/netevents.py`, `ctop/demo_net.py` to the diagram/list,
  and task map rows T18-T32.

## 4 Done when
`.venv/bin/python -m unittest tests.test_docs -v` passes, and the full suite still passes. Plain words, short sentences, same tone as the existing docs.
