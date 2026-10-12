# T15 - Cross-desktop protected-program names

## 1 Purpose
`ctop/safety.py` refuses to kill a program whose name starts with any entry of `PROTECTED_PREFIXES`; today that list only knows KDE/Arch names. Extend it so that on GNOME, XFCE, Cinnamon, MATE, LXQt, tiling WMs, other display managers and Debian/Ubuntu system services the desktop, login, audio, network and package machinery is locked, while everyday applications stay killable.

## 2 Files
EDIT `ctop/safety.py` - ONLY the `PROTECTED_PREFIXES` tuple (append new entries inside it, before the closing `)`; keep every existing entry and comment exactly as is; do not touch `is_protected`, `kill_safety`, imports or the header comment). CREATE `tests/test_safety_names.py`. READ: `ctop/safety.py`, `tests/test_safety.py` (an old script-style test; do not edit or import it). Touch nothing else.

## 3 Public API
No new functions. `is_protected(name) -> bool` is unchanged: it lower-cases `name` and returns `any(n.startswith(p) for p in PROTECTED_PREFIXES)`. Therefore:
* every NEW prefix must be lower-case ASCII;
* **every NEW prefix must be at most 15 characters long.** Linux `comm` (what `psutil.Process.name()` returns, and what `kill_safety` checks per member) is cut to 15 characters, so `unattended-upgrade-shutdown` shows up as `unattended-upgr`; a 19-character prefix would never match it. (The pre-existing `xdg-desktop-portal` is 18 characters; it still works for group names, and `xdg-desktop-por` below covers the truncated form.)

## 4 The exact additions (copy these; group them with short `# comment` lines like the existing ones)
```python
    # GNOME (shell, session, keyring, settings daemons, portals; NOT gnome-* apps)
    "gnome-shell", "gnome-session", "gnome-keyring", "gnome-settings", "gsd-", "mutter", "goa-",
    "xdg-desktop-por", "xdg-document", "xdg-permission",
    # KDE extras (kwin/plasma/kded/ksecret/... are already listed)
    "kdeinit", "klauncher", "kcminit", "kaccess", "startkde",
    # XFCE
    "xfce4-session", "xfce4-panel", "xfce4-power", "xfwm4", "xfdesktop", "xfsettingsd", "xfconfd",
    # Cinnamon
    "cinnamon", "muffin", "nemo-desktop", "csd-",
    # MATE
    "mate-session", "mate-panel", "mate-settings", "mate-power", "mate-polkit", "mate-screens",
    "mate-notific", "marco",
    # LXQt / LXDE
    "lxqt-session", "lxqt-panel", "lxqt-policykit", "lxqt-powermana", "lxqt-globalkeys",
    "lxqt-notificat", "lxsession", "lxpanel",
    # COSMIC / Budgie
    "cosmic-comp", "cosmic-session", "cosmic-panel", "cosmic-greeter", "budgie-",
    # i3 / sway / Hyprland / wayfire and other window managers, bars, notifiers
    "i3", "sway", "hypr", "wayfire", "waybar", "niri", "labwc", "bspwm", "sxhkd", "dwm", "awesome",
    "xmonad", "qtile", "openbox", "fluxbox", "icewm", "herbstluftwm", "mako",
    # other display managers, boot splash, X start-up
    "lxdm", "greetd", "plymouth", "xinit", "startx",
    # more terminals (the terminal ctop runs in must never die)
    "xfce4-terminal", "mate-terminal", "lxterminal", "qterminal", "terminator", "tilix", "wezterm",
    "ghostty", "ptyxis", "kgx", "yakuake", "guake", "urxvt", "rxvt", "zellij",
    # Debian/Ubuntu/Fedora system services (root-owned ones are already refused by the owner check,
    # but if ctop is run with sudo the name list is the only guard)
    "cron", "anacron", "rsyslog", "syslog-ng", "apparmor", "unattended-up", "packagekit", "snapd",
    "avahi", "modemmanager", "accounts-daemon", "colord", "cups", "udisks", "rtkit", "firewalld",
    "fail2ban", "dhclient", "dhcpcd", "networkd", "chrony", "ntpd", "irqbalance", "thermald", "acpid",
    "smartd", "power-profiles",
    # per-user session helpers: file system, input methods, accessibility, settings, VM guests
    "gvfs", "ibus", "fcitx", "at-spi", "dconf", "spice-vdagent", "vmtoolsd", "vboxclient",
    "vboxservice", "qemu-ga",
```
(`pulseaudio`, `pipewire`, `wireplumber`, `networkmanager`, `nm-`, `polkit`, `systemd`, `dbus`, `gdm`, `sddm`, `lightdm`, `xorg`, `xwayland`, `udev`, `upower`, `tlp`, `bluetooth` already exist - do not repeat them.)

**Deliberate decisions about breadth** (put a one-line comment in the tuple where noted):
* `gnome-` / `mate-` / `lxqt-` / `xfce4-` / `kde` / `cosmic-` are NOT added as bare prefixes: they would lock gnome-calculator, gnome-text-editor, mate-calc, lxqt-archiver, xfce4-screenshooter, cosmic-files - ordinary applications the user may want to close. Only the named session/daemon programs are listed.
* `budgie-` and `csd-`/`gsd-`/`goa-` ARE bare prefixes because every program starting that way is part of the desktop session (Budgie's apps are not named `budgie-*`).
* Short prefixes `i3`, `dwm`, `mako`, `niri`, `hypr`, `sway` are accepted: no common application starts with them. Never add 1-2 letter prefixes such as `st`, `ly`, `x` or `k` (`st` would lock `steam`).
* NOT protected on purpose: nautilus, dolphin, nemo, caja, thunar, pcmanfm, tracker-*, gnome-software, baloo_file (indexer), dunst, picom, orca (`orca` would lock the Orca-slicer app), evolution, kdeconnectd, firefox/chrome/etc. Killing these is harmless; they restart or just close.
* Existing broad entries (`pam`, `foot`, `iwd`, `login`) are out of scope; do not change them.

## 5 Edge cases & distro differences
* Group names come from `prog_name()` (exe basename or friendly name, e.g. "Plasma (desktop)", "Xwayland"); member names are 15-character `comm` values. Prefixes must work for both - hence the 15-character rule and tests with truncated forms.
* Case is irrelevant (`ModemManager`, `VBoxClient`, `Hyprland` are lower-cased before matching).
* Snap/Flatpak wrappers show the app name, not `snapd`; `snapd-desktop-integration` (a user helper) is intentionally locked by `snapd`.
* Debian vs Arch name differences are covered by listing both (`cron`/`cronie` via `cron`, `rsyslog`/`syslog-ng`, `gdm3` via existing `gdm`, `ntpd`/`chrony`).
* Arch laptop behaviour must not regress: every name in `tests/test_safety.py` "locked by name" stays locked, every "allowed" name stays allowed (checked in the new tests).

## 6 Performance & safety limits
`is_protected` is called once per process per kill request; ~250 `startswith` checks are microseconds. This change can only make kills REFUSE more often - it must never widen what can be killed. No other behaviour changes; no imports added.

## 7 Test plan (`tests/test_safety_names.py`, `unittest`, `from ctop.safety import is_protected, PROTECTED_PREFIXES`; no process is ever signalled, `kill_safety` is not called)
Define module-level constants and loop with `with self.subTest(name=n):`.
* `test_new_prefixes_are_lowercase_and_comm_safe`: take the NEW prefixes as `PROTECTED_PREFIXES[len(OLD_PREFIXES):]` where `OLD_PREFIXES` is a copy of the 50 pre-existing entries pasted in the test file; assert each new one is `== p.lower()`, `<= 15` chars, non-empty, ASCII, unique, and not already in OLD.
* `test_old_prefixes_still_present`: every entry of `OLD_PREFIXES` is still in `PROTECTED_PREFIXES` and is the leading slice (order preserved).
* `test_gnome_locked`: `gnome-shell gnome-session-binary gnome-keyring-daemon gnome-keyring-d gnome-settings-daemon gsd-power gsd-media-keys mutter-x11-frames goa-daemon goa-identity-se xdg-desktop-portal-gnome xdg-desktop-por xdg-document-portal xdg-permission-store`
* `test_kde_extras_locked`: `kdeinit5 klauncher kcminit kaccess startkde kded6 kwin_wayland "Plasma (desktop)"`
* `test_xfce_locked`: `xfce4-session xfce4-panel xfce4-power-manager xfce4-power-man xfwm4 xfdesktop xfsettingsd xfconfd`
* `test_cinnamon_locked`: `cinnamon cinnamon-session cinnamon-launcher muffin nemo-desktop csd-power csd-media-keys`
* `test_mate_locked`: `mate-session mate-panel mate-settings-daemon mate-power-manager mate-polkit mate-screensaver mate-notification-daemon marco`
* `test_lxqt_locked`: `lxqt-session lxqt-panel lxqt-policykit-agent lxqt-powermanagement lxqt-globalkeys-daemon lxqt-notificationd lxsession lxpanel`
* `test_cosmic_budgie_locked`: `cosmic-comp cosmic-session cosmic-panel cosmic-greeter budgie-panel budgie-wm budgie-daemon`
* `test_tiling_wms_locked`: `i3 i3bar i3status sway swaybg swayidle swaylock Hyprland hyprpaper hypridle hyprlock wayfire waybar niri labwc bspwm sxhkd dwm awesome xmonad qtile openbox fluxbox icewm herbstluftwm mako`
* `test_display_managers_locked`: `lightdm lightdm-gtk-greeter gdm3 gdm-wayland-session gdm-x-session sddm sddm-helper lxdm greetd plymouthd xinit startx`
* `test_terminals_locked`: `konsole kitty alacritty foot xterm gnome-terminal-server xfce4-terminal mate-terminal lxterminal qterminal terminator tilix wezterm-gui ghostty ptyxis kgx yakuake guake urxvt rxvt-unicode zellij tmux`
* `test_debian_ubuntu_services_locked`: `cron cronie anacron rsyslogd syslog-ng apparmor unattended-upgrade-shutdown unattended-upgr packagekitd snapd snapd-desktop-integration avahi-daemon ModemManager accounts-daemon colord cupsd cups-browsed udisksd rtkit-daemon firewalld fail2ban-server dhclient dhcpcd networkd-dispatcher chronyd ntpd irqbalance thermald acpid smartd power-profiles-daemon power-profiles-`
* `test_session_helpers_locked`: `gvfsd gvfs-udisks2-vo gvfsd-fuse ibus-daemon ibus-x11 ibus-engine-simple fcitx5 at-spi-bus-launcher at-spi2-registryd dconf-service pulseaudio pipewire-pulse wireplumber spice-vdagent spice-vdagentd vmtoolsd VBoxClient VBoxService qemu-ga`
* `test_arch_laptop_names_still_locked`: the 25 names from `tests/test_safety.py` ("systemd", "systemd-udevd", "kwin_wayland", "Plasma (desktop)", "plasmashell", "Konsole", "pipewire", "wireplumber", "NetworkManager", "tailscaled", "dbus-broker", "Xwayland", "XWayland", "sddm", "polkitd", "Claude Code", "ctop.py", "tlp-pd", "earlyoom", "upowerd", "xdg-desktop-portal-kde", "org_kde_powerdevil", "kded6", "libvirtd", "sshd").
* `test_everyday_apps_stay_killable` (this is the over-breadth guard): `firefox Firefox chrome google-chrome chromium spotify discord code code-oss cursor slack steam vlc libreoffice soffice.bin gimp thunderbird obs blender python python3 node java npm baloo_file kwrite kate dolphin nautilus nemo caja thunar pcmanfm gnome-calculator gnome-text-editor gnome-software gnome-control-center mate-calc lxqt-archiver xfce4-screenshooter xfce4-taskmanager orca-slicer evolution telegram-desktop "Web Content" "Isolated Web Co" sleep node-MainThread mpv okular evince krita inkscape keepassxc dunst picom` -> all `assertFalse(is_protected(n))`.
* `test_empty_and_none`: `is_protected("")`, `is_protected(None)` -> False.
* `test_truncated_comm_of_long_names`: `"xdg-desktop-por"`, `"unattended-upgr"`, `"gnome-keyring-d"`, `"power-profiles-"` -> True (guards the 15-character rule).

## 8 Done when
- [ ] `python -m unittest tests.test_safety_names -v` passes (and does NOT import or run `tests/test_safety.py`).
- [ ] `python -m pyflakes ctop/safety.py tests/test_safety_names.py` clean.
- [ ] `git diff`-style check: the only changed lines in `ctop/safety.py` are additions inside `PROTECTED_PREFIXES`.
- [ ] No new dependencies; every new prefix is lower-case and <= 15 characters.
- [ ] Only `ctop/safety.py` (tuple only) and `tests/test_safety_names.py` touched.
