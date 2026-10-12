#!/usr/bin/env python3
"""Tests for ctop's KILL SAFETY NET. Run:  ~/ctop/.venv/bin/python ~/ctop/test_safety.py

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

failures = []


def check(label, ok):
    print(("PASS " if ok else "FAIL ") + label)
    if not ok:
        failures.append(label)


def member(pid):
    return (pid, psutil.Process(pid).create_time())


# 1. name rules
for name in ("systemd", "systemd-udevd", "kwin_wayland", "Plasma (desktop)", "plasmashell", "Konsole",
             "pipewire", "wireplumber", "NetworkManager", "tailscaled", "dbus-broker", "Xwayland",
             "XWayland", "sddm", "polkitd", "Claude Code", "ctop.py", "tlp-pd", "earlyoom", "upowerd",
             "xdg-desktop-portal-kde", "org_kde_powerdevil", "kded6", "libvirtd", "sshd"):
    check(f"locked by name: {name}", ctop.is_protected(name))
for name in ("Firefox", "Chrome", "sleep", "kwrite", "baloo_file", "spotify", "code", "node-MainThread"):
    check(f"allowed by name: {name}", not ctop.is_protected(name))

# 2. system / other-user processes are refused (pid 1 is root's)
check("pid 1 (root) refused", ctop.kill_safety("whatever", [member(1)]) is not None)

# 3. the terminal/shell ctop runs in is refused, even under an innocent group name
parent = psutil.Process().parent()
if parent:
    check("own parent process refused",
          ctop.kill_safety("innocent-name", [member(parent.pid)]) is not None)
check("ctop itself refused", ctop.kill_safety("innocent-name", [member(os.getpid())]) is not None)

# 4. ONE locked member refuses the whole group
victim = subprocess.Popen(["sleep", "300"])
try:
    ok_member = member(victim.pid)
    check("plain own 'sleep' is allowed", ctop.kill_safety("sleep", [ok_member]) is None)
    check("group with a root process refused", ctop.kill_safety("sleep", [ok_member, member(1)]) is not None)

    # 5. pid reuse guard: wrong start time => nothing signalled
    stale = (victim.pid, ok_member[1] + 12345)
    check("stale start time signals nothing", ctop.signal_members([stale], signal.SIGTERM) == 0)
    check("victim still alive after stale signal", victim.poll() is None)

    # 6. real stop of an allowed program
    check("polite stop reaches 1 process", ctop.signal_members([ok_member], signal.SIGTERM) == 1)
    for _ in range(30):
        if victim.poll() is not None:
            break
        time.sleep(0.1)
    check("sleep died after SIGTERM", victim.poll() is not None)
    check("count_alive sees it gone", ctop.count_alive([ok_member]) == 0)
finally:
    if victim.poll() is None:
        victim.kill()

print("\n%d failure(s)" % len(failures))
sys.exit(1 if failures else 0)
