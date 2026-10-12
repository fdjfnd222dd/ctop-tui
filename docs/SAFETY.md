# ctop safety

ctop is read-only except for ONE action: stopping a program. Even that needs two deliberate presses
of `x`, and it refuses to touch anything on the locked list.

## The six rules

These mirror the KILL SAFETY NET comment in `ctop/safety.py`, in the same order.

1. **Locked names.** Desktop, login, audio, network, power, firewall, VPN, input, terminal and
   multiplexer programs, plus ctop itself and Claude Code, can never be stopped.
   Why: stopping one of them could log you out, drop the network, or end the session that is
   driving the machine.

2. **Only your own programs.** ctop stops only processes owned by the user who started it. Never
   root's, never another user's.
   Why: system services and other people's programs are not yours to stop.

3. **Never ctop's own ancestry.** The terminal, the shell and everything above ctop in the process
   tree are off limits.
   Why: stopping your terminal ends ctop and whatever you were doing in it.

4. **All or nothing.** If ANY process in a program's group is locked, the whole group is refused.
   Why: a program stopped halfway can leave a broken half still running.

5. **Checked again at kill time.** Right before a stop, ctop re-reads live process information. It
   also compares each process's start time, so a process ID reused by a new program is skipped.
   Why: the list on screen may be seconds old, and process IDs get reused.

6. **Polite first.** ctop sends SIGTERM first and gives the program time to save and exit. It
   force-kills only if the program is still alive afterwards.
   Why: a polite stop lets programs save their work.

## What is locked by name

Names are matched as a prefix, case-insensitively. For example, anything starting with `ufw` is
locked. The categories are:

- **Init, login and session:** systemd, D-Bus, display managers (sddm, gdm, lightdm), login,
  getty, PAM, sshd, polkit.
- **Desktop and compositor:** KDE (kwin, plasma and related), GNOME (gnome-shell, gnome-session,
  mutter and related), Xfce, Cinnamon, MATE, and the X and Wayland helpers.
- **Audio, network, power, firewall, VPN and input:** PipeWire, WirePlumber, PulseAudio,
  NetworkManager, wpa_supplicant, iwd, Tailscale, libvirt, ufw, udev, upower, TLP, earlyoom,
  Bluetooth, fingerprint readers.
- **Terminals and multiplexers:** Konsole, kitty, Alacritty, foot, xterm, gnome-terminal, tmux and
  others.
- **ctop itself and Claude Code:** any name starting with `ctop` or `claude`.

## How a stop works

1. Select a program and press `x`. The first `x` arms the stop for 5 seconds. The status bar asks
   for a second `x`.
2. The second `x` must come at least 0.35 seconds after the first. A held key repeats much faster
   than that, so holding a key down cannot stop a program.
3. Any other key cancels the stop.
4. ctop asks the program to stop politely (SIGTERM), then shows "Checking in 3 s" while it waits.
5. A force-kill (SIGKILL) happens only when a polite stop was tried in the last 60 seconds and the
   program is still alive.
6. Before every signal, ctop checks the process start time, so a process ID reused by a different
   program is never signalled.

## What ctop will never do

- Run as root, use sudo, or ask for a password.
- Stop other users' programs or system services.
- Stop anything from a stale list while the display is paused. Resume first with space.
- Stop anything without the double press.

## Report a program that should be locked

If ctop let you stop something that should be protected, or you think a program should be on the
list:

1. Open an issue titled `Protect: <program name>`.
2. Give the program name exactly as ctop shows it.
3. Give your distro and desktop.
4. Say what breaks if the program dies.

Do not include paths, usernames or hostnames.

The fix is one lowercase prefix in `PROTECTED_PREFIXES` in `ctop/safety.py`, plus a line in the
safety test.
