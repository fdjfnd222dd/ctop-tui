"""KILL SAFETY NET - what ctop is allowed to stop, and how."""
import os

import psutil

# --------------------------------------------------------------------------
# KILL SAFETY NET
# Killing the wrong thing on this laptop could log the owner out, drop the
# network, or end the Claude session that is running right now. Every rule
# below makes a kill REFUSE; nothing here ever widens what can be killed.
#   1. Name list: desktop, login, audio, network, power, firewall, terminals,
#      shells' parents, Claude Code and ctop itself are locked.
#   2. Only processes owned by the current user (never root's, never other users').
#   3. Never anything in ctop's own ancestry (the terminal/shell it runs in).
#   4. If ANY process in a group is locked, the whole group is refused.
#   5. Checked again at kill time against live process info, and pid reuse is
#      guarded with the process start time, so a stale list can't hit a new process.
#   6. Polite stop (SIGTERM) first; force-kill only if it's still alive after.
# --------------------------------------------------------------------------
PROTECTED_PREFIXES = (
    # init, login, session
    "systemd", "dbus", "sddm", "gdm", "lightdm", "login", "agetty", "pam", "sshd", "polkit",
    # KDE desktop / compositor / display
    "kwin", "plasma", "kded", "kglobalaccel", "kactivity", "ksecret", "kscreen", "ksmserver",
    "kwalletd", "org_kde", "xdg-desktop-portal", "xwayland", "xorg", "ksplash", "startplasma",
    # audio, network, power, firewall, VM, VPN, input
    "pipewire", "wireplumber", "pulseaudio", "networkmanager", "nm-", "wpa_supplicant", "iwd",
    "tailscale", "libvirt", "ufw", "udev", "upower", "tlp", "earlyoom", "bluetooth", "fprint",
    # terminals + multiplexers (the one ctop runs in must never die)
    "konsole", "kitty", "alacritty", "foot", "xterm", "gnome-terminal", "tmux",
    # this tool, and the Claude Code session that may be driving the laptop
    "ctop", "claude",
    # GNOME shell, session, keyring, settings daemons, portals. Only the named session
    # programs: a bare "gnome-" / "mate-" / "lxqt-" / "xfce4-" / "kde" / "cosmic-" would
    # also lock ordinary apps such as gnome-calculator or xfce4-screenshooter.
    "gnome-shell", "gnome-session", "gnome-keyring", "gnome-settings", "gsd-", "mutter", "goa-",
    "xdg-desktop-por", "xdg-document", "xdg-permission",
    # KDE extras (kwin/plasma/kded/ksecret/... are already listed above)
    "kdeinit", "klauncher", "kcminit", "kaccess", "startkde",
    # XFCE
    "xfce4-session", "xfce4-panel", "xfce4-power", "xfwm4", "xfdesktop", "xfsettingsd", "xfconfd",
    # Cinnamon (every program starting with csd- belongs to the session)
    "cinnamon", "muffin", "nemo-desktop", "csd-",
    # MATE
    "mate-session", "mate-panel", "mate-settings", "mate-power", "mate-polkit", "mate-screens",
    "mate-notific", "marco",
    # LXQt / LXDE
    "lxqt-session", "lxqt-panel", "lxqt-policykit", "lxqt-powermana", "lxqt-globalkeys",
    "lxqt-notificat", "lxsession", "lxpanel",
    # COSMIC / Budgie (Budgie's apps are not named budgie-*, so every budgie- program is session)
    "cosmic-comp", "cosmic-session", "cosmic-panel", "cosmic-greeter", "budgie-",
    # i3 / sway / Hyprland / wayfire and other window managers, bars, notifiers.
    # Short names are safe: no common application starts with them. Never add 1-2 letters.
    "i3", "sway", "hypr", "wayfire", "waybar", "niri", "labwc", "bspwm", "sxhkd", "dwm", "awesome",
    "xmonad", "qtile", "openbox", "fluxbox", "icewm", "herbstluftwm", "mako",
    # other display managers, boot splash, X start-up
    "lxdm", "greetd", "plymouth", "xinit", "startx",
    # more terminals (the terminal ctop runs in must never die)
    "xfce4-terminal", "mate-terminal", "lxterminal", "qterminal", "terminator", "tilix", "wezterm",
    "ghostty", "ptyxis", "kgx", "yakuake", "guake", "urxvt", "rxvt", "zellij",
    # Debian/Ubuntu/Fedora system services. Root-owned ones are refused by the owner check,
    # but under sudo the name list is the only guard. Debian and Arch names both listed.
    "cron", "anacron", "rsyslog", "syslog-ng", "apparmor", "unattended-up", "packagekit", "snapd",
    "avahi", "modemmanager", "accounts-daemon", "colord", "cups", "udisks", "rtkit", "firewalld",
    "fail2ban", "dhclient", "dhcpcd", "networkd", "chrony", "ntpd", "irqbalance", "thermald", "acpid",
    "smartd", "power-profiles",
    # per-user session helpers: file system, input methods, accessibility, settings, VM guests
    "gvfs", "ibus", "fcitx", "at-spi", "dconf", "spice-vdagent", "vmtoolsd", "vboxclient",
    "vboxservice", "qemu-ga",
    # NOT locked on purpose (harmless to close or they restart): nautilus, dolphin, nemo,
    # caja, thunar, tracker-*, dunst, picom, orca (would hit orca-slicer), evolution.
)


def is_protected(name):
    n = (name or "").lower()
    return any(n.startswith(p) for p in PROTECTED_PREFIXES)


def kill_safety(group_name, members):
    """None if the program may be stopped, otherwise a plain-language reason.

    members is [(pid, start_time), ...] as recorded by the last process scan.
    """
    if is_protected(group_name):
        return "it is a protected system program"
    me = psutil.Process()
    shield = {me.pid} | {a.pid for a in me.parents()}     # ctop + terminal + shell + ...
    for pid, started in members:
        try:
            p = psutil.Process(pid)
            if p.create_time() != started:                # pid reused by a different program
                continue
            if pid in shield:
                return "it contains the terminal ctop is running in"
            if p.uids().real != os.getuid():
                return "it belongs to the system, not to you"
            if is_protected(p.name()):
                return f"it includes protected process '{p.name()}'"
        except psutil.Error:
            continue                                      # already gone
    return None


def signal_members(members, sig):
    """Send `sig` to each still-matching process. Returns how many were signalled."""
    sent = 0
    for pid, started in members:
        try:
            p = psutil.Process(pid)
            if p.create_time() != started:
                continue
            p.send_signal(sig)
            sent += 1
        except psutil.Error:
            pass
    return sent


def count_alive(members):
    n = 0
    for pid, started in members:
        try:
            p = psutil.Process(pid)
            if p.create_time() == started and p.status() != psutil.STATUS_ZOMBIE:
                n += 1
        except psutil.Error:
            pass
    return n
