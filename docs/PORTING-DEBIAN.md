# Running ctop on Debian and Ubuntu

ctop is developed on Arch. This page lists what changes on Debian and Ubuntu, and the fix for each
problem. Every fix is a command for you to run yourself; ctop and `install.sh` never run them.

## Supported / tested

| Distro | Python | Status |
|---|---|---|
| Arch (EndeavourOS, Manjaro) | current | tested by maintainer |
| Debian 11 | 3.9 | untested - please report |
| Debian 12 | 3.11 | untested - please report |
| Debian 13 | 3.13 | untested - please report |
| Ubuntu 22.04 | 3.10 | untested - please report |
| Ubuntu 24.04 | 3.12 | untested - please report |

## python3-venv package (`ensurepip is not available`)

Debian and Ubuntu ship the venv module as a separate package. Without it, `install.sh` stops at
its probe step and prints the fix:

```
sudo apt install python3-venv
```

Then run `./install.sh` again. Nothing was changed by the failed run.

## PEP 668: "externally-managed-environment"

Debian 12 and later, and Ubuntu 23.04 and later, refuse `pip install` into the system Python with
the message `externally-managed-environment`. That is on purpose: a package installed into the
system Python can break the tools your system depends on.

ctop installs into its own `.venv` inside the checkout, so the system Python is never touched.
Do not work around the message with `--break-system-packages`.

## Textual and psutil wheels

- Textual is pure Python, so it installs on any CPU.
- psutil ships prebuilt wheels for x86_64 and aarch64. On another CPU pip builds psutil from source,
  which needs a compiler and the Python headers. Install them first:

```
sudo apt install build-essential python3-dev
```

- Do not use apt's `python3-textual` package. It is old or missing on some releases. pip picks the
  newest Textual your Python supports, and that is the one ctop wants.

## Battery readings

Linux exposes batteries in two styles: `charge_*` files with `current_now` times `voltage_now`, or
`energy_*` files with `power_now`. ctop reads both. Desktops and virtual machines have no battery,
so the battery panel says so and nothing is wrong.

## CPU temperature and fans

- Intel CPUs report temperature through the `coretemp` driver. AMD CPUs use `k10temp`, with labels
  such as `Tctl` or `Tccd1`. ctop reads both.
- ARM boards and most virtual machines expose no temperature sensor. That is normal.
- Fan speed needs a driver for your machine, such as `dell_smm_hwmon` or `thinkpad_acpi`. Without
  one, ctop shows "no fan reported".

## Wi-Fi tools

Desktop installs usually have `nmcli`, from the `network-manager` package. Minimal installs often
have only `iw`, or just `/proc/net/wireless`. ctop uses whichever it finds and shows what it can get.
To get the full Wi-Fi line, install one of them:

```
sudo apt install network-manager
```

## Firewall

- Ubuntu ships `ufw` installed but switched off.
- Debian ships no firewall tool at all.

"no firewall detected" is information, not an error. Reading the rule list needs root, and ctop
never asks for root, so it reports what it can see without it.

## Process names the safety net must know

The kernel cuts process names to 15 characters, so you will see names such as `gnome-session-b` and
`gnome-terminal-`. The safety net matches prefixes, case-insensitively, so these names are locked:

- GNOME and Ubuntu: `gdm3`, `gnome-shell`, `gnome-session`, `gsd-*`, `mutter`, `Xwayland`, `ibus`,
  `at-spi`, `snapd`, `ptyxis`, `kgx`.
- Xfce: `xfce4-` and `xfwm4`.
- Cinnamon: `cinnamon` and `muffin`.
- MATE: `mate-` and `marco`.

The list lives in `PROTECTED_PREFIXES` in `ctop/safety.py`. See [SAFETY.md](SAFETY.md) for why.

## Terminal

Use a UTF-8 locale (`echo $LANG` should end in `UTF-8`), a terminal with 256 colours, and a font that
has Braille patterns, such as DejaVu Sans Mono. The Linux text console (Ctrl+Alt+F2 and friends)
cannot draw ctop's graphs and is not supported.

## How to report a result

Open an issue and include:

- the distro and version (`cat /etc/os-release`, the `PRETTY_NAME` line);
- `python3 --version`;
- which panels were empty or wrong.

Mask IP addresses, Wi-Fi names, usernames and hostnames before you paste anything.

## Network page on Debian

- Every Debian desktop install has NetworkManager. nmcli 1.42 (Debian 12) and 1.52 (Debian 13) both work.
- A user logged in at the desktop may change Wi-Fi by default. Over SSH, or on a minimal install, polkit may refuse; the page then shows the reason and the tools still work.
- No NetworkManager at all (server, `ifupdown` only): the Wi-Fi and Saved boxes say so; trace, ports, DNS timing, speed test and the doctor still work.
- `iw` is optional (it adds the link details): `sudo apt install iw`.
- Tracing needs nothing extra. If the built-in way is unavailable it falls back to `tracepath` (package `iputils-tracepath`).
