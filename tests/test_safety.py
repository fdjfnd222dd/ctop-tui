#!/usr/bin/env python3
"""Tests for ctop's KILL SAFETY NET. Run:  python -m unittest tests.test_safety -v

These only ever signal a throw-away `sleep` process the test starts itself.
Nothing here touches any real program.
"""
import os
import signal
import subprocess
import sys
import time

import psutil

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ctop import safety as ctop

import unittest


def member(pid):
    return (pid, psutil.Process(pid).create_time())


class NameRules(unittest.TestCase):
    LOCKED = ("systemd", "systemd-udevd", "kwin_wayland", "Plasma (desktop)", "plasmashell", "Konsole",
              "pipewire", "wireplumber", "NetworkManager", "tailscaled", "dbus-broker", "Xwayland",
              "XWayland", "sddm", "polkitd", "Claude Code", "ctop.py", "tlp-pd", "earlyoom", "upowerd",
              "xdg-desktop-portal-kde", "org_kde_powerdevil", "kded6", "libvirtd", "sshd")
    ALLOWED = ("Firefox", "Chrome", "sleep", "kwrite", "baloo_file", "spotify", "code", "node-MainThread")

    def test_locked_names(self):
        for name in self.LOCKED:
            self.assertTrue(ctop.is_protected(name), name)

    def test_allowed_names(self):
        for name in self.ALLOWED:
            self.assertFalse(ctop.is_protected(name), name)


class LiveProcessRules(unittest.TestCase):
    def test_root_process_refused(self):
        self.assertIsNotNone(ctop.kill_safety("whatever", [member(1)]))

    def test_own_parent_and_self_refused(self):
        parent = psutil.Process().parent()
        if parent:
            self.assertIsNotNone(ctop.kill_safety("innocent-name", [member(parent.pid)]))
        self.assertIsNotNone(ctop.kill_safety("innocent-name", [member(os.getpid())]))

    def test_stop_flow_on_throwaway_sleep(self):
        victim = subprocess.Popen(["sleep", "300"])
        try:
            ok_member = member(victim.pid)
            self.assertIsNone(ctop.kill_safety("sleep", [ok_member]))
            # one locked member refuses the whole group
            self.assertIsNotNone(ctop.kill_safety("sleep", [ok_member, member(1)]))
            # pid-reuse guard: a wrong start time signals nothing
            stale = (victim.pid, ok_member[1] + 12345)
            self.assertEqual(ctop.signal_members([stale], signal.SIGTERM), 0)
            self.assertIsNone(victim.poll())
            # a real polite stop
            self.assertEqual(ctop.signal_members([ok_member], signal.SIGTERM), 1)
            for _ in range(30):
                if victim.poll() is not None:
                    break
                time.sleep(0.1)
            self.assertIsNotNone(victim.poll())
            self.assertEqual(ctop.count_alive([ok_member]), 0)
        finally:
            if victim.poll() is None:
                victim.kill()


if __name__ == "__main__":
    unittest.main()
