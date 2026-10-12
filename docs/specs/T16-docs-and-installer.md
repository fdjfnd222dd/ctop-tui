# T16 - README, install.sh, uninstall.sh, Debian porting guide, safety doc

## 1 Purpose
Make ctop publishable on GitHub and installable by a stranger on Arch, Debian 11/12/13 and Ubuntu 22.04/24.04
without root and without breaking their system Python. Five text files plus one small static test.

## 2 Files
Create: `README.md`, `install.sh`, `uninstall.sh`, `docs/PORTING-DEBIAN.md`, `docs/SAFETY.md`, `tests/test_docs.py` (static checks only).
READ first: `docs/ARCHITECTURE.md` (principles), `pyproject.toml` (name `ctop-tui`, command `ctop`, Python >= 3.9, deps textual + psutil),
`ctop/safety.py` (its KILL SAFETY NET comment block and `PROTECTED_PREFIXES` are the source for SAFETY.md), `docs/specs/PAGES.md` (key table, page names).
Do NOT edit `pyproject.toml`, `LICENSE` or any `.py` outside `tests/test_docs.py`. Use documentation addresses only; no real names, hostnames, home paths ("/home/..."), SSIDs.
Repository URL placeholder everywhere: `https://github.com/<owner>/ctop`.

## 3 Public API (the "interface" of these files)
- `./install.sh [--dry-run] [--help]` and `./uninstall.sh [--dry-run] [--help]`: bash, run as the normal user from any directory. Exit codes: `0` ok, `1` refused/failed, `2` prerequisite missing (message tells what to run).
- Environment override: `PYTHON=python3.11 ./install.sh` picks the interpreter (default `python3`).
- Result: `<repo>/.venv` (editable install) and the symlink `~/.local/bin/ctop -> <repo>/.venv/bin/ctop`. Nothing else on the machine changes.

## 4 Visual design (structure and exact content)
### README.md (in this order)
`# ctop` + one sentence (read-only btop-style dashboard: RAM by program, battery watts, network quality, security view) | `<!-- screenshot: add docs/screenshot.png -->` placeholder line + an image link `![ctop screenshot](docs/screenshot.png)` |
`## What you see` (bullets: CPU + temperature/fan, battery watts and health, memory ring by program, network speed + router/internet ping + Wi-Fi quality, Security page: listening ports, connections by program, devices on your network, "How exposed am I?", program details popup, alert banner) |
`## Keys` (the table below, copied exactly) | `## Install` with three sub-blocks `### Arch / EndeavourOS / Manjaro` (`sudo pacman -S python git` if missing), `### Debian 11/12/13` and `### Ubuntu 22.04/24.04` (`sudo apt install python3 python3-venv git`),
each followed by `git clone https://github.com/<owner>/ctop && cd ctop && ./install.sh` and `ctop`; say once: "install.sh never uses sudo; if something is missing it prints the one line for you to run yourself." |
`## Update` (`git pull && ./install.sh`) | `## Uninstall` (`./uninstall.sh`) | `## Privacy` | `## Safety` | `## Troubleshooting` | `## Requirements` | `## License` (MIT) | `## Reporting problems`.
Keys table (exact):
| Key | What it does |
|---|---|
| Tab, 1, 2 | switch page: 1 Overview, 2 Security |
| Up/Down or j/k | select a program |
| Enter | details of the selected program |
| x then x | stop the selected program (asks twice; system programs are locked) |
| s | cycle sort: RAM, CPU, name |
| / | search by name (type to filter, Enter keeps it, Esc clears) |
| space | pause / resume |
| + / - | refresh faster / slower (0.5 s, 1 s, 2 s, 5 s) |
| ? | help |
| q | quit |

Privacy section must say, in plain words: ctop is read-only; sends nothing over the internet; the ONLY packets it sends are a few small pings to your router and to one public address (name it as in `collectors/ping.py`);
does not scan your network (devices come from the kernel's neighbour table); does no reverse-DNS lookups; no telemetry; no files written.
Safety section: 5-line summary of docs/SAFETY.md + link. Troubleshooting is a 2-column table, at least these rows: "`ensurepip is not available` -> `sudo apt install python3-venv`" | "`ctop: command not found` -> add `~/.local/bin` to PATH (give the exact line)" |
"boxes look broken / no colours -> use a UTF-8 terminal with 256 colours; the Linux text console is not supported" | "No battery found -> desktop/VM, normal" | "Temp not reported / no fan reported -> your hardware exposes no sensor, normal" |
"Wi-Fi line missing -> not on Wi-Fi, or install `network-manager`/`iw`" | "firewall: could not tell -> reading rules needs root; ctop never asks for it" | "(other user) in ports table -> that program belongs to another user".

### install.sh (bash; first line `#!/usr/bin/env bash`; second `set -euo pipefail`; every step has a `# why` comment)
1. `cd "$(dirname "$(readlink -f "$0")")"`. Refuse if `[ "$(id -u)" -eq 0 ]` ("Run as your normal user; a root-owned .venv would be a mess.").
2. Parse `--help` (usage text) and `--dry-run` (print each action prefixed `would: ` and change nothing).
3. Python check: `"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)'`; failure -> message "ctop needs Python 3.9 or newer (found X)" + the distro line below, exit 2.
4. venv check: create a throw-away venv in `mktemp -d` (`"$PY" -m venv "$tmp/v"`), remove it with a `trap ... EXIT`. Failure -> exit 2 with the hint line chosen from `/etc/os-release` (`ID`/`ID_LIKE`): debian|ubuntu -> `sudo apt install python3-venv`; arch -> `sudo pacman -S python`; anything else -> "install your distro's python venv package".
   The sudo lines live ONLY in variables like `HINT_APT='sudo apt install python3-venv'` and are printed with `printf`; no line of the script ever executes `sudo`.
5. If `.venv/bin/python` is missing or fails, (re)create `.venv`. Then `.venv/bin/python -m pip install --quiet --upgrade pip` and `.venv/bin/python -m pip install --quiet -e .` (idempotent: a second run just reinstalls nothing new).
6. `mkdir -p "$HOME/.local/bin"`; if `$HOME/.local/bin/ctop` exists and is a REGULAR FILE or a symlink that does not point into a `.venv/bin/ctop`, refuse (exit 1, tell the user to move it); otherwise `ln -sfn "$PWD/.venv/bin/ctop" "$HOME/.local/bin/ctop"`.
7. PATH check: `case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) warn ;; esac`; the warning prints (does not run) `echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc`.
8. Verify with `.venv/bin/python -c 'import ctop, textual, psutil'`; print `Installed. Run: ctop`. Plain ASCII output, no colour codes, no network access except what pip does.

### uninstall.sh
Remove `$HOME/.local/bin/ctop` only if it is a symlink whose target ends in `/.venv/bin/ctop` and lies inside this checkout; remove `<repo>/.venv` (guard: variable non-empty, basename is `.venv`, path is inside the script directory). Never touches the repo, `~/.local/bin` itself, config or any other file. Idempotent ("nothing to remove" is success). `--dry-run` supported. Ends with "ctop removed. You can delete this folder."

### docs/PORTING-DEBIAN.md
Title + a "Supported / tested" table (rows: Arch = tested by maintainer; Debian 11 py3.9, Debian 12 py3.11, Debian 13 py3.13, Ubuntu 22.04 py3.10, Ubuntu 24.04 py3.12 = "untested - please report"). Then one `##` section each, 3-8 lines, with the exact fix command where one exists:
python3-venv package (`ensurepip is not available`) | PEP 668 "externally-managed-environment" on Debian 12+/Ubuntu 23.04+ (why we use a venv; never `--break-system-packages`) | Textual and psutil wheels (Textual is pure Python; psutil has manylinux wheels for x86_64/aarch64; other CPUs need `sudo apt install build-essential python3-dev` first; do not use apt's python3-textual, it is old or missing; pip picks the newest Textual your Python supports) |
Battery files (`charge_*` vs `energy_*`, `power_now` vs current x voltage; desktops/VMs have none) | CPU temperature (Intel `coretemp` vs AMD `k10temp` with `Tctl`/`Tccd1` labels; ARM boards and VMs may have none; fans need a driver such as `dell_smm_hwmon`/`thinkpad_acpi`) |
Wi-Fi tools (`nmcli` from network-manager on desktops vs `iw` / `/proc/net/wireless` on minimal installs; ctop shows what it can get) | Firewall (Ubuntu ships ufw installed but off, Debian ships none; "no firewall detected" is information, not an error; rule lists need root, ctop never asks) |
GNOME/Ubuntu process names the safety net must know (the kernel cuts names to 15 characters, so `gnome-session-b`, `gnome-terminal-`): `gdm3`, `gnome-shell`, `gnome-session`, `gsd-*`, `mutter`, `Xwayland`, `ibus`, `at-spi`, `snapd`, `ptyxis`, `kgx`; Xfce `xfce4-`/`xfwm4`; Cinnamon `cinnamon`/`muffin`; MATE `mate-`/`marco` (T15 owns the list in `ctop/safety.py`) |
Terminal (UTF-8 locale, 256 colours, a font with Braille patterns such as DejaVu Sans Mono) | "How to report a result" (distro, version, `python3 --version`, which panels were empty).

### docs/SAFETY.md
Opening: ctop is read-only except ONE action, stopping a program, and only after two deliberate presses. Then `## The six rules` numbered 1-6 mirroring the comment block in `ctop/safety.py` one-for-one (same order, same meaning, plain words, each with a short "why");
`## What is locked by name` (list the CATEGORIES from `PROTECTED_PREFIXES`: init/login/session, desktop and compositor (KDE, GNOME, Xfce, Cinnamon, MATE), audio/network/power/firewall/VPN/input, terminals and multiplexers, ctop itself and Claude Code; say "prefix match, case-insensitive");
`## How a stop works` (first `x` arms for 5 s, second `x` must come at least 0.35 s later so a held key cannot kill, any other key cancels, polite stop first, "Checking in 3 s", a force-kill only after a polite stop was tried in the last 60 s and the program is still alive, process start time guards against pid reuse);
`## What ctop will never do` (no root, no other users' processes, no kill from a stale list while paused, no kill without the double press);
`## Report a program that should be locked` (open an issue titled `Protect: <program name>`, give the name exactly as ctop shows it, distro + desktop, and what breaks if it dies; no paths, usernames or hostnames; the fix is one lowercase prefix in `PROTECTED_PREFIXES` plus a line in the test).

## 5 Rules
- Plain language, no jargon without a one-line explanation; short sentences; no emoji; no badges that need the network; Markdown line length unconstrained, shell lines `<= 100` columns.
- Every command a user must run as root appears only as TEXT for them to run themselves.
- Shell: bash 4 features only, `shellcheck`-clean if available, quote every expansion, no `eval`, never pipe a download into a shell, no `pip install` outside `.venv`.
- Facts must match the repo: command name `ctop`, package `ctop-tui`, Python >= 3.9.
- No personal data (see section 2).

## 6 Test plan (`tests/test_docs.py`, stdlib only, reads files relative to the repo root)
- `test_scripts_have_strict_mode`: both scripts start with `#!/usr/bin/env bash` and contain `set -euo pipefail` in the first 5 lines.
- `test_bash_syntax`: `subprocess.run(["bash", "-n", path])` returns 0 for both (skip the test if `bash` is missing).
- `test_no_sudo_is_executed`: every line of both scripts that contains the word `sudo` matches `^\s*(#|HINT_[A-Z_]+=|printf|echo)`.
- `test_install_dry_run_changes_nothing`: run `bash install.sh --dry-run` with `env={"HOME": tmpdir, "PATH": os.environ["PATH"]}`; skip if exit code is 2; else exit code 0, output contains "would:", and neither `<repo>/.venv` newly appeared nor `tmpdir/.local/bin/ctop` exists.
- `test_uninstall_dry_run_exits_zero`: `bash uninstall.sh --dry-run` with a temp HOME returns 0.
- `test_readme_sections`: README contains, in order, the headings `## What you see`, `## Keys`, `## Install`, `## Privacy`, `## Safety`, `## Troubleshooting`, and the lines `./install.sh`, `sudo apt install python3 python3-venv git`, `sudo pacman -S python git`.
- `test_readme_key_table_matches`: each of `| Tab, 1, 2 |`, `| Enter |`, `| s |`, `| / |`, `| space |`, `| + / - |`, `| ? |`, `| q |` appears in README.
- `test_safety_doc_has_six_rules`: SAFETY.md has numbered items `1.` to `6.` under "The six rules" and the heading "Report a program that should be locked".
- `test_porting_doc_mentions`: PORTING-DEBIAN.md contains `python3-venv`, `externally-managed-environment`, `k10temp`, `nmcli`, `ufw`.
- `test_no_personal_data`: none of the five files contains `/home/`, `@gmail`, or an IPv4 address outside `192.0.2.`, `198.51.100.`, `203.0.113.`, `127.`, `0.0.0.0` (regex scan).

## 7 Done when
- [ ] `python -m unittest tests.test_docs -v` passes; `bash -n install.sh` and `bash -n uninstall.sh` are silent; both scripts are `chmod +x`.
- [ ] `./install.sh --dry-run` in a scratch HOME prints a plan and changes nothing (do not run the real install on a machine you care about while testing).
- [ ] Only the six listed files touched; no sudo executed anywhere; no new dependency.
