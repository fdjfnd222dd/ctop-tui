# ctop

A read-only, btop-style terminal dashboard for Linux: RAM by program, battery watts, network quality and a security view.

<!-- screenshot: add docs/screenshot.png -->
![ctop screenshot](docs/screenshot.png)

## Quick install (Debian, Ubuntu, Linux Mint, Pop!_OS, Raspberry Pi OS)

```
sudo apt install -y python3 python3-venv git
git clone https://github.com/fdjfnd222dd/ctop-tui && cd ctop-tui
./install.sh && ctop
```

That is all. `install.sh` never uses sudo; the only sudo line is the first one. Quit ctop with `q`.

## What you see

- CPU: load per core, with temperature and fan speed where the hardware reports them.
- Battery: charge rate in watts and battery health.
- Memory: a RAM ring and a list of programs, sorted by RAM, CPU or name.
- Network: download and upload speed, ping to your router and to the internet, and Wi-Fi quality.
- Security page: listening ports, connections by program, devices on your network, and "How exposed am I?".
- Program details: press Enter on a program to see its processes, files and connections.
- Alert banner: one line at the top when RAM, swap, battery, heat, fan, Wi-Fi or ping needs a look.

## Keys

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

## Install

`install.sh` never uses sudo. If something is missing, it prints the one line for you to run yourself.

### Arch / EndeavourOS / Manjaro

Only if Python or git is missing:

```
sudo pacman -S python git
```

Then:

```
git clone https://github.com/fdjfnd222dd/ctop-tui && cd ctop-tui && ./install.sh
ctop
```

### Debian 11/12/13 and Ubuntu 22.04/24.04

Only if Python, the venv module or git is missing:

```
sudo apt install python3 python3-venv git
```

Then:

```
git clone https://github.com/fdjfnd222dd/ctop-tui && cd ctop-tui && ./install.sh
ctop
```

Run `./install.sh --dry-run` first if you want to see the plan before anything changes.
The script puts ctop in `<repo>/.venv` and links the `ctop` command into `~/.local/bin`. Nothing else on the machine changes.

## Update

```
git pull && ./install.sh
```

## Uninstall

```
./uninstall.sh
```

This removes the `ctop` link in `~/.local/bin` and the `.venv` folder inside the checkout. It does not delete the checkout itself; remove that folder by hand if you want it gone.

## Privacy

- ctop is read-only. It changes nothing on your system.
- It sends nothing over the internet. The only packets it sends are a few small pings: one to your router, and one to a public internet address (the default is `DEFAULT_TARGET` in `ctop/collectors/ping.py`). You can change that address with `CTOP_PING_TARGET`, or turn the internet ping off with `CTOP_NO_PING=1`.
- It does not scan your network. Devices come from the kernel's neighbour table, which is the list of devices your computer has talked to recently.
- It does no reverse-DNS lookups and does not ask "what is my public IP" services.
- No telemetry. No files are written.

## Safety

- ctop is read-only except for one action: stopping a program.
- A stop needs two deliberate presses of `x`, and the program must pass the safety list.
- Desktop, login, audio, network, power, firewall, terminal and ctop's own programs are locked, and so is Claude Code.
- ctop never uses root and never stops programs that belong to other users.
- A stop is polite first (SIGTERM). A force-kill happens only if the program is still alive afterwards.

Read [docs/SAFETY.md](docs/SAFETY.md) for the six rules and the full list of locked names.

## Troubleshooting

| Problem | Fix |
|---|---|
| `ensurepip is not available` | run `sudo apt install python3-venv` |
| `ctop: command not found` | add `~/.local/bin` to your PATH: run `echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc`, then open a new terminal |
| boxes look broken, or no colours | use a UTF-8 terminal with 256 colours; the Linux text console (Ctrl+Alt+F2 and friends) is not supported |
| No battery found | desktop or virtual machine: normal |
| Temp not reported, or no fan reported | your hardware exposes no sensor: normal |
| Wi-Fi line missing | you are not on Wi-Fi, or install `network-manager` (for `nmcli`) or `iw` |
| firewall: could not tell | reading firewall rules needs root; ctop never asks for it |
| (other user) in the ports table | that program belongs to another user |

## Requirements

- Linux. Python 3.9 or newer.
- git, and the `venv` module (on Debian and Ubuntu it is the `python3-venv` package).
- A UTF-8 terminal with 256 colours and a font with Braille characters, such as DejaVu Sans Mono.
- Installed by pip into `.venv`: `textual`, `psutil`, `rich`. Nothing else.
- Optional, for the Wi-Fi line: `network-manager` (`nmcli`) or `iw`.
- Developed on Arch. Other distros: see [docs/PORTING-DEBIAN.md](docs/PORTING-DEBIAN.md).

## License

MIT. See [LICENSE](LICENSE).

## Reporting problems

Open an issue at `https://github.com/fdjfnd222dd/ctop-tui/issues`. Include:

- your distro and version, and the output of `python3 --version`;
- which panels were empty or wrong;
- any error text ctop showed.

Do not paste IP addresses, Wi-Fi names, usernames or hostnames. Replace them with placeholders first.
