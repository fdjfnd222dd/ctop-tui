# ctop architecture

ctop is a read-only, full-screen terminal dashboard (Textual + psutil + rich).
Two layers that only meet through `ctop/models.py`:

```
  data layer (no UI imports)                       drawing layer (no system calls)
  ctop/system.py        Sampler: cpu/mem/net       ctop/ui.py        colours, meter, graph, ring, Panel
  ctop/battery.py       read_battery()             ctop/panels/*.py  the four dashboard panels
  ctop/collectors/*.py  background Collectors      ctop/render/*.py  pure functions: model -> list[Text]
  ctop/alerts.py        evaluate()                 ctop/app.py       keys, pages, wiring (maintainer-owned)
  ctop/safety.py        kill safety net
              \_____________ ctop/models.py (frozen dataclasses) _____________/
```

## Principles (every module must follow these)
1. **Read-only, with two fenced exceptions.** No writes to /sys, /proc, /etc. No root. No sudo. Programs are stopped only through `safety.py`; network settings are changed only through `netctl.py` (nmcli as the user, after a menu choice).
2. **Passive unless asked.** Background code sends nothing except the two ping targets in `collectors/ping.py`. The tools in `ctop/tools/` (trace, port check, DNS timing, speed test, doctor, device finder) run only when the user starts one, have hard limits (32 ports, 254 addresses, 30 hops) and stop on Esc. No lookups that leak (no reverse DNS, no "what is my public IP" services).
3. **Cheap.** ctop measures the battery it runs on. A collector refresh should cost well under 20 ms of CPU; every subprocess has a `timeout=`; nothing runs faster than its `interval`.
4. **Degrade, never crash.** Missing tool / file / permission => the field is `None` (see models.py) and the UI says so in plain words. No traceback may reach the screen.
5. **Portable.** Python >= 3.9 (Debian 11+ ships 3.9). Only `psutil`, `rich`, `textual` plus the standard library. Assume nothing about KDE, Arch, Dell, or one particular battery/Wi-Fi driver. Linux only.
6. **Testable without the machine.** Anything that reads a path takes the root as a parameter (`root="/sys/class/hwmon"`); anything that runs a command takes the command output (or a `runner` callable) as a parameter. Tests build fake trees under `tempfile.TemporaryDirectory()`.
7. **No personal data in the repo.** Fixtures and examples use documentation addresses only: IPv4 `192.0.2.x`, `198.51.100.x`, `203.0.113.x`; IPv6 `2001:db8::`; MACs `02:00:00:aa:bb:cc`. Never paste real IPs, MACs, SSIDs, hostnames or usernames captured from a developer machine.
8. **Comments say why.** Short, plain. A reader should learn why a number is computed that way.

## Collector contract
See `ctop/collectors/base.py`. A collector sets `name`, `interval`, implements `refresh()` and publishes a
new frozen model object to `self.data`. The app runs it on a worker thread; the UI only reads `.data`.

## Render contract
Every function in `ctop/render/` is pure: `(model(s), width[, height]) -> list[rich.text.Text]`.
* Each returned `Text` is `no_wrap=True` and at most `width` cells wide (use `ctop.ui.fit` / `runs` / `kv` / `meter` / `graph`).
* Never return more than `height` lines when a height is given; if the data does not fit, end with a dim "... +N more" line.
* `None` data => a short dim explanation line, e.g. "no fan reported".
* Plain units and plain words: GB, %, W, Mbps, ms, °C. Colours come from `ctop.ui` (`grad`, `DIM`, `FG`, `TRACK`); use green/yellow/red meaningfully.

## Tests
Standard library `unittest`, one file per task in `tests/`, runnable with
`python -m unittest discover -s tests -v` from the repo root. No pytest, no network, no root.

## Task map
| ID  | What | Files |
|-----|------|-------|
| T01 | Portable battery reader (charge_* **and** energy_*, current*voltage **or** power_now) | `ctop/battery.py`, `tests/test_battery.py` |
| T02 | Fans + temperatures collector (coretemp / k10temp / nvme / iwlwifi / dell_smm / thinkpad / thermal zones) | `ctop/collectors/thermal.py`, `tests/test_thermal.py` |
| T03 | Thermal lines for the CPU panel | `ctop/render/thermal.py`, `tests/test_render_thermal.py` |
| T04 | Alert rules (RAM, swap, battery, heat, fan, Wi-Fi, ping) | `ctop/alerts.py`, `tests/test_alerts.py` |
| T05 | Ping collector (router + internet latency, loss, jitter) | `ctop/collectors/ping.py`, `tests/test_ping.py` |
| T06 | Wi-Fi collector (signal, quality, bitrate, channel, security) | `ctop/collectors/wifi.py`, `tests/test_wifi.py` |
| T07 | Network-quality lines (ping + Wi-Fi) for the Network panel | `ctop/render/quality.py`, `tests/test_render_quality.py` |
| T08 | Listening ports + active connections | `ctop/collectors/ports.py`, `tests/test_ports.py` |
| T09 | LAN devices from the neighbour table | `ctop/collectors/lan.py`, `tests/test_lan.py` |
| T10 | Firewall status without root | `ctop/collectors/firewall.py`, `tests/test_firewall.py` |
| T11 | Security page renderers A: listening ports, connections | `ctop/render/security_ports.py`, `tests/test_render_security_ports.py` |
| T12 | Security page renderers B: devices, exposure summary | `ctop/render/security_lan.py`, `tests/test_render_security_lan.py` |
| T13 | Program details collector (for the Enter popup) | `ctop/collectors/details.py`, `tests/test_details.py` |
| T14 | Program details popup lines | `ctop/render/details.py`, `tests/test_render_details.py` |
| T15 | Cross-desktop protected-program names | `ctop/safety.py` (PROTECTED_PREFIXES only), `tests/test_safety_names.py` |
| T16 | README, install script, Debian porting guide, safety doc | `README.md`, `install.sh`, `docs/PORTING-DEBIAN.md`, `docs/SAFETY.md` |
| T17 | Alert banner line | `ctop/render/alerts.py`, `tests/test_render_alerts.py` |

Wiring (scheduler, pages, keys, popup screen, sort/search/pause) is done by the maintainer in `ctop/app.py` after the tasks land.
| T18 | NetworkManager state: radios, nearby Wi-Fi, saved profiles | `ctop/collectors/nmstate.py` |
| T19 | Network changes (the only module that changes settings) | `ctop/netctl.py` |
| T20 | Wi-Fi link details + channel crowding | `ctop/collectors/wifilink.py` |
| T21 | VPN / Tailscale status | `ctop/collectors/vpn.py` |
| T22 | Network event log | `ctop/netevents.py` |
| T23-T28 | Tools: trace, port check, DNS timing, speed test, doctor, device finder | `ctop/tools/*.py` |
| T29 | Network page renderers | `ctop/render/net_wifi.py`, `ctop/render/net_tools.py` |
| T30 | Arrow-key menus, input and target dialogs | `ctop/screens.py` |
| T31 | Demo data for the Network page | `ctop/demo_net.py` |

Page 3 wiring (focus, menus, tool threads, key bar) lives in `ctop/netpage.py`, mixed into the app; its boxes are in `ctop/panels/network_page.py`. Specs: `docs/specs/T18`-`T32` and `NETWORK-PAGE.md`.
