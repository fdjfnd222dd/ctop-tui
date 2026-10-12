"""Name list of the KILL SAFETY NET across desktops and distros.

Only name matching is tested here. No process is ever signalled and kill_safety()
is never called. Run:  python -m unittest tests.test_safety_names -v
"""
from __future__ import annotations

import unittest

from ctop.safety import is_protected, PROTECTED_PREFIXES

# The 50 prefixes that existed before T15. They must stay, in order, at the front of the tuple.
OLD_PREFIXES = (
    "systemd", "dbus", "sddm", "gdm", "lightdm", "login", "agetty", "pam", "sshd", "polkit",
    "kwin", "plasma", "kded", "kglobalaccel", "kactivity", "ksecret", "kscreen", "ksmserver",
    "kwalletd", "org_kde", "xdg-desktop-portal", "xwayland", "xorg", "ksplash", "startplasma",
    "pipewire", "wireplumber", "pulseaudio", "networkmanager", "nm-", "wpa_supplicant", "iwd",
    "tailscale", "libvirt", "ufw", "udev", "upower", "tlp", "earlyoom", "bluetooth", "fprint",
    "konsole", "kitty", "alacritty", "foot", "xterm", "gnome-terminal", "tmux",
    "ctop", "claude",
)

GNOME = [
    "gnome-shell", "gnome-session-binary", "gnome-keyring-daemon", "gnome-keyring-d",
    "gnome-settings-daemon", "gsd-power", "gsd-media-keys", "mutter-x11-frames", "goa-daemon",
    "goa-identity-se", "xdg-desktop-portal-gnome", "xdg-desktop-por", "xdg-document-portal",
    "xdg-permission-store",
]

KDE_EXTRAS = ["kdeinit5", "klauncher", "kcminit", "kaccess", "startkde", "kded6", "kwin_wayland",
              "Plasma (desktop)"]

XFCE = ["xfce4-session", "xfce4-panel", "xfce4-power-manager", "xfce4-power-man", "xfwm4",
        "xfdesktop", "xfsettingsd", "xfconfd"]

CINNAMON = ["cinnamon", "cinnamon-session", "cinnamon-launcher", "muffin", "nemo-desktop",
            "csd-power", "csd-media-keys"]

MATE = ["mate-session", "mate-panel", "mate-settings-daemon", "mate-power-manager", "mate-polkit",
        "mate-screensaver", "mate-notification-daemon", "marco"]

LXQT = ["lxqt-session", "lxqt-panel", "lxqt-policykit-agent", "lxqt-powermanagement",
        "lxqt-globalkeys-daemon", "lxqt-notificationd", "lxsession", "lxpanel"]

COSMIC_BUDGIE = ["cosmic-comp", "cosmic-session", "cosmic-panel", "cosmic-greeter",
                 "budgie-panel", "budgie-wm", "budgie-daemon"]

TILING_WMS = ["i3", "i3bar", "i3status", "sway", "swaybg", "swayidle", "swaylock", "Hyprland",
              "hyprpaper", "hypridle", "hyprlock", "wayfire", "waybar", "niri", "labwc", "bspwm",
              "sxhkd", "dwm", "awesome", "xmonad", "qtile", "openbox", "fluxbox", "icewm",
              "herbstluftwm", "mako"]

DISPLAY_MANAGERS = ["lightdm", "lightdm-gtk-greeter", "gdm3", "gdm-wayland-session",
                    "gdm-x-session", "sddm", "sddm-helper", "lxdm", "greetd", "plymouthd",
                    "xinit", "startx"]

TERMINALS = ["konsole", "kitty", "alacritty", "foot", "xterm", "gnome-terminal-server",
             "xfce4-terminal", "mate-terminal", "lxterminal", "qterminal", "terminator", "tilix",
             "wezterm-gui", "ghostty", "ptyxis", "kgx", "yakuake", "guake", "urxvt", "rxvt-unicode",
             "zellij", "tmux"]

DEBIAN_UBUNTU_SERVICES = ["cron", "cronie", "anacron", "rsyslogd", "syslog-ng", "apparmor",
                          "unattended-upgrade-shutdown", "unattended-upgr", "packagekitd", "snapd",
                          "snapd-desktop-integration", "avahi-daemon", "ModemManager",
                          "accounts-daemon", "colord", "cupsd", "cups-browsed", "udisksd",
                          "rtkit-daemon", "firewalld", "fail2ban-server", "dhclient", "dhcpcd",
                          "networkd-dispatcher", "chronyd", "ntpd", "irqbalance", "thermald",
                          "acpid", "smartd", "power-profiles-daemon", "power-profiles-"]

SESSION_HELPERS = ["gvfsd", "gvfs-udisks2-vo", "gvfsd-fuse", "ibus-daemon", "ibus-x11",
                   "ibus-engine-simple", "fcitx5", "at-spi-bus-launcher", "at-spi2-registryd",
                   "dconf-service", "pulseaudio", "pipewire-pulse", "wireplumber", "spice-vdagent",
                   "spice-vdagentd", "vmtoolsd", "VBoxClient", "VBoxService", "qemu-ga"]

ARCH_LAPTOP_LOCKED = ["systemd", "systemd-udevd", "kwin_wayland", "Plasma (desktop)", "plasmashell",
                      "Konsole", "pipewire", "wireplumber", "NetworkManager", "tailscaled",
                      "dbus-broker", "Xwayland", "XWayland", "sddm", "polkitd", "Claude Code",
                      "ctop.py", "tlp-pd", "earlyoom", "upowerd", "xdg-desktop-portal-kde",
                      "org_kde_powerdevil", "kded6", "libvirtd", "sshd"]

# Everyday apps the owner may close. Over-broad prefixes would lock these.
EVERYDAY_APPS = ["firefox", "Firefox", "chrome", "google-chrome", "chromium", "spotify", "discord",
                 "code", "code-oss", "cursor", "slack", "steam", "vlc", "libreoffice", "soffice.bin",
                 "gimp", "thunderbird", "obs", "blender", "python", "python3", "node", "java", "npm",
                 "baloo_file", "kwrite", "kate", "dolphin", "nautilus", "nemo", "caja", "thunar",
                 "pcmanfm", "gnome-calculator", "gnome-text-editor", "gnome-software",
                 "gnome-control-center", "mate-calc", "lxqt-archiver", "xfce4-screenshooter",
                 "xfce4-taskmanager", "orca-slicer", "evolution", "telegram-desktop", "Web Content",
                 "Isolated Web Co", "sleep", "node-MainThread", "mpv", "okular", "evince", "krita",
                 "inkscape", "keepassxc", "dunst", "picom"]


class ProtectedNamesTest(unittest.TestCase):

    def _assert_locked(self, names):
        for n in names:
            with self.subTest(name=n):
                self.assertTrue(is_protected(n))

    def test_new_prefixes_are_lowercase_and_comm_safe(self):
        # Linux comm is cut to 15 characters, so a longer new prefix could never match a process.
        new = PROTECTED_PREFIXES[len(OLD_PREFIXES):]
        self.assertTrue(new)
        for p in new:
            with self.subTest(prefix=p):
                self.assertEqual(p, p.lower())
                self.assertLessEqual(len(p), 15)
                self.assertTrue(p)
                self.assertTrue(p.isascii())
                self.assertNotIn(p, OLD_PREFIXES)
        self.assertEqual(len(set(new)), len(new))

    def test_old_prefixes_still_present(self):
        for p in OLD_PREFIXES:
            with self.subTest(prefix=p):
                self.assertIn(p, PROTECTED_PREFIXES)
        self.assertEqual(tuple(PROTECTED_PREFIXES[:len(OLD_PREFIXES)]), OLD_PREFIXES)

    def test_gnome_locked(self):
        self._assert_locked(GNOME)

    def test_kde_extras_locked(self):
        self._assert_locked(KDE_EXTRAS)

    def test_xfce_locked(self):
        self._assert_locked(XFCE)

    def test_cinnamon_locked(self):
        self._assert_locked(CINNAMON)

    def test_mate_locked(self):
        self._assert_locked(MATE)

    def test_lxqt_locked(self):
        self._assert_locked(LXQT)

    def test_cosmic_budgie_locked(self):
        self._assert_locked(COSMIC_BUDGIE)

    def test_tiling_wms_locked(self):
        self._assert_locked(TILING_WMS)

    def test_display_managers_locked(self):
        self._assert_locked(DISPLAY_MANAGERS)

    def test_terminals_locked(self):
        self._assert_locked(TERMINALS)

    def test_debian_ubuntu_services_locked(self):
        self._assert_locked(DEBIAN_UBUNTU_SERVICES)

    def test_session_helpers_locked(self):
        self._assert_locked(SESSION_HELPERS)

    def test_arch_laptop_names_still_locked(self):
        self._assert_locked(ARCH_LAPTOP_LOCKED)

    def test_everyday_apps_stay_killable(self):
        for n in EVERYDAY_APPS:
            with self.subTest(name=n):
                self.assertFalse(is_protected(n))

    def test_empty_and_none(self):
        self.assertFalse(is_protected(""))
        self.assertFalse(is_protected(None))

    def test_truncated_comm_of_long_names(self):
        # comm is cut to 15 characters; these are the forms the kernel actually reports.
        self._assert_locked(["xdg-desktop-por", "unattended-upgr", "gnome-keyring-d",
                             "power-profiles-"])


if __name__ == "__main__":
    unittest.main()
