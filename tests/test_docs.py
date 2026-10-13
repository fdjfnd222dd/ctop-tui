"""Static checks for the README, the two installer scripts and the docs.

Standard library only. No network, no root. The installer is only ever run with --dry-run,
against a scratch HOME, so these tests never change the machine.
"""
import os
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = ("install.sh", "uninstall.sh")
TEXT_FILES = ("README.md", "install.sh", "uninstall.sh", "docs/PORTING-DEBIAN.md", "docs/SAFETY.md")


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def scratch_env(home):
    return {"HOME": home, "PATH": os.environ["PATH"]}


class TestScripts(unittest.TestCase):
    def test_scripts_have_strict_mode(self):
        for name in SCRIPTS:
            lines = read(name).splitlines()
            self.assertEqual(lines[0], "#!/usr/bin/env bash", name)
            self.assertTrue(any("set -euo pipefail" in line for line in lines[:5]), name)

    @unittest.skipUnless(shutil.which("bash"), "bash is not installed")
    def test_bash_syntax(self):
        for name in SCRIPTS:
            r = subprocess.run(["bash", "-n", os.path.join(ROOT, name)],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, f"{name}: {r.stderr}")

    def test_no_sudo_is_executed(self):
        # sudo may appear only in comments, in HINT_ variables (text for the owner to run),
        # or inside printf/echo output. Nothing may execute it.
        allowed = re.compile(r"^\s*(#|HINT_[A-Z_]+=|printf|echo)")
        for name in SCRIPTS:
            for number, line in enumerate(read(name).splitlines(), 1):
                if "sudo" in line:
                    self.assertTrue(allowed.match(line), f"{name}:{number}: {line!r}")

    @unittest.skipUnless(shutil.which("bash"), "bash is not installed")
    def test_install_dry_run_changes_nothing(self):
        repo_venv = os.path.join(ROOT, ".venv")
        existed = os.path.exists(repo_venv)
        with tempfile.TemporaryDirectory() as home:
            r = subprocess.run(["bash", os.path.join(ROOT, "install.sh"), "--dry-run"],
                               env=scratch_env(home), cwd=home,
                               capture_output=True, text=True, timeout=120)
            if r.returncode == 2:
                self.skipTest("prerequisite missing: " + r.stdout + r.stderr)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("would:", r.stdout)
            if not existed:
                self.assertFalse(os.path.exists(repo_venv), "dry run created .venv")
            self.assertFalse(os.path.exists(os.path.join(home, ".local", "bin", "ctop")))

    @unittest.skipUnless(shutil.which("bash"), "bash is not installed")
    def test_uninstall_dry_run_exits_zero(self):
        with tempfile.TemporaryDirectory() as home:
            r = subprocess.run(["bash", os.path.join(ROOT, "uninstall.sh"), "--dry-run"],
                               env=scratch_env(home), cwd=home,
                               capture_output=True, text=True, timeout=120)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


class TestReadme(unittest.TestCase):
    def test_readme_sections(self):
        text = read("README.md")
        position = -1
        for heading in ("## Features", "## Install", "## Keys", "## Why I built it",
                        "## Built with", "## Safety", "## Privacy",
                        "## Troubleshooting"):
            index = text.find(heading)
            self.assertGreater(index, position, f"{heading} missing or out of order")
            position = index
        for line in ("./install.sh", "sudo apt install -y python3 python3-venv git",
                     "sudo pacman -S --needed python git", "ctop --demo"):
            self.assertIn(line, text)

    def test_readme_key_table_matches(self):
        text = read("README.md")
        for row in ("| `Tab`, `1`, `2`, `3` |", "| `Enter` |", "| `s` |", "| `/` |",
                    "| `space` |", "| `+` `-` |", "| `?` |", "| `q` |"):
            self.assertIn(row, text)


class TestSafetyAndPorting(unittest.TestCase):
    def test_safety_doc_has_six_rules(self):
        text = read("docs/SAFETY.md")
        self.assertIn("## The six rules", text)
        section = text.split("## The six rules", 1)[1].split("\n## ", 1)[0]
        for number in range(1, 7):
            self.assertRegex(section, r"(?m)^%d\. " % number)
        self.assertIn("## Report a program that should be locked", text)

    def test_porting_doc_mentions(self):
        text = read("docs/PORTING-DEBIAN.md")
        for word in ("python3-venv", "externally-managed-environment", "k10temp",
                     "nmcli", "ufw"):
            self.assertIn(word, text)


class TestNoPersonalData(unittest.TestCase):
    def test_no_personal_data(self):
        ipv4 = re.compile(r"(?<![\d.])\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(?![\d.])")
        allowed = ("192.0.2.", "198.51.100.", "203.0.113.", "127.", "0.0.0.0")
        for name in TEXT_FILES:
            text = read(name)
            self.assertNotIn("/home/", text, name)
            self.assertNotIn("@gmail", text, name)
            for match in ipv4.finditer(text):
                address = match.group(0)
                self.assertTrue(address.startswith(allowed),
                                f"{name}: real-looking IPv4 address {address}")


if __name__ == "__main__":
    unittest.main()
