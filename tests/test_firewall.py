"""Firewall status: fake root under a temp dir, fake runner, never touches systemctl or ufw."""
from __future__ import annotations

import os
import tempfile
import unittest

from ctop.collectors.firewall import (
    parse_default_input_policy,
    parse_firewalld_list_all,
    parse_is_active,
    parse_ufw_user_rules,
    read_firewall,
)
from ctop.models import FirewallInfo

UFW_RULES_EXCERPT = """\
### tuple ### allow tcp 22 0.0.0.0/0 any 0.0.0.0/0 in
### tuple ### allow tcp 8000,8001 0.0.0.0/0 any 192.0.2.0/24 in
### tuple ### allow udp 5353 0.0.0.0/0 any 0.0.0.0/0 in_wlan0 comment=4d444e53
### tuple ### deny any any 0.0.0.0/0 any 198.51.100.7 in
"""

FIREWALLD_LIST = """\
public (active)
  target: default
  interfaces: wlan0
  services: dhcpv6-client ssh
  ports: 8080/tcp 5000-5010/udp
"""

UNITS = ("ufw", "firewalld", "nftables")


def write(root, relpath, text):
    path = os.path.join(root, relpath)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


class FakeRunner:
    """Answers systemctl / firewall-cmd from canned text and records every call."""

    def __init__(self, systemctl="active\ninactive\ninactive\n", firewalld_list=None, state=None):
        self.systemctl = systemctl
        self.firewalld_list = firewalld_list
        self.state = state
        self.calls = []

    def __call__(self, argv, timeout):
        self.calls.append((list(argv), timeout))
        if argv[0] == "systemctl":
            return self.systemctl
        if argv[:2] == ["firewall-cmd", "--list-all"]:
            return self.firewalld_list
        if argv[:2] == ["firewall-cmd", "--state"]:
            return self.state
        return None


class FirewallTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_row1_ufw_on_with_rules(self):
        write(self.root, "etc/ufw/ufw.conf", "ENABLED=yes\n")
        write(self.root, "etc/default/ufw", 'DEFAULT_INPUT_POLICY="DROP"\n')
        write(self.root, "etc/ufw/user.rules", UFW_RULES_EXCERPT)
        info = read_firewall(self.root, FakeRunner("active\ninactive\ninactive\n"))
        self.assertEqual(info, FirewallInfo("ufw", True, "deny", True, (
            "allow tcp 22",
            "allow tcp 8000,8001 from 192.0.2.0/24",
            "allow udp 5353 on wlan0",
            "deny any any from 198.51.100.7",
        ), ""))

    def test_row1_rules_unreadable(self):
        write(self.root, "etc/ufw/ufw.conf", "ENABLED=yes\n")
        write(self.root, "etc/default/ufw", 'DEFAULT_INPUT_POLICY="DROP"\n')
        os.makedirs(os.path.join(self.root, "etc/ufw/user.rules"))
        info = read_firewall(self.root, FakeRunner("active\ninactive\ninactive\n"))
        self.assertTrue(info.active)
        self.assertFalse(info.rules_visible)
        self.assertEqual(info.rules, ())
        self.assertEqual(info.note, "Rule list needs root - run `sudo ufw status` in another terminal")

    def test_default_policies(self):
        self.assertEqual(parse_default_input_policy('DEFAULT_INPUT_POLICY="REJECT"\n'), "reject")
        self.assertEqual(parse_default_input_policy("DEFAULT_INPUT_POLICY=ACCEPT\n"), "allow")
        self.assertEqual(parse_default_input_policy("DEFAULT_INPUT_POLICY=DROP\n"), "deny")
        self.assertIsNone(parse_default_input_policy(""))
        # No /etc/default/ufw at all: ufw is still on, the policy is just unknown.
        write(self.root, "etc/ufw/ufw.conf", "ENABLED=yes\n")
        info = read_firewall(self.root, FakeRunner("active\ninactive\ninactive\n"))
        self.assertEqual(info.backend, "ufw")
        self.assertIsNone(info.default_incoming)

    def test_row2_enabled_but_service_failed(self):
        write(self.root, "etc/ufw/ufw.conf", "ENABLED=yes\n")
        info = read_firewall(self.root, FakeRunner("failed\ninactive\ninactive\n"))
        self.assertEqual(info.backend, "ufw")
        self.assertIsNone(info.active)
        self.assertIsNone(info.default_incoming)
        self.assertFalse(info.rules_visible)
        self.assertEqual(info.rules, ())
        self.assertEqual(info.note, "ufw is switched on in its settings, but its service is not running - "
                                    "run `sudo ufw status` to check.")

    def test_row3_firewalld(self):
        runner = FakeRunner("inactive\nactive\ninactive\n", firewalld_list=FIREWALLD_LIST)
        info = read_firewall(self.root, runner)
        self.assertEqual(info.backend, "firewalld")
        self.assertIs(info.active, True)
        self.assertEqual(info.default_incoming, "reject")
        self.assertEqual(info.rules, ("service dhcpv6-client", "service ssh",
                                      "port 8080/tcp", "port 5000-5010/udp"))
        self.assertTrue(info.rules_visible)
        self.assertEqual(info.note, "")

    def test_row3_firewalld_list_fails(self):
        info = read_firewall(self.root, FakeRunner("inactive\nactive\ninactive\n", firewalld_list=None))
        self.assertEqual(info.backend, "firewalld")
        self.assertIs(info.active, True)
        self.assertFalse(info.rules_visible)
        self.assertEqual(info.rules, ())
        self.assertEqual(info.note, "firewalld is running. Run `firewall-cmd --list-all` in another "
                                    "terminal to see its rules.")

    def test_ufw_on_wins_over_firewalld(self):
        write(self.root, "etc/ufw/ufw.conf", "ENABLED=yes\n")
        info = read_firewall(self.root, FakeRunner("active\nactive\ninactive\n", firewalld_list=FIREWALLD_LIST))
        self.assertEqual(info.backend, "ufw")

    def test_row4_ufw_disabled(self):
        write(self.root, "etc/ufw/ufw.conf", "ENABLED=no\n")
        info = read_firewall(self.root, FakeRunner("active\ninactive\ninactive\n"))
        self.assertEqual(info, FirewallInfo("ufw", False, None, False, (),
                                            "ufw is installed but switched off. Turn it on with `sudo ufw enable`."))

    def test_row5_conf_unreadable(self):
        os.makedirs(os.path.join(self.root, "etc/ufw/ufw.conf"))
        info = read_firewall(self.root, FakeRunner("active\ninactive\ninactive\n"))
        self.assertEqual(info.backend, "ufw")
        self.assertIsNone(info.active)
        self.assertEqual(info.note, "ufw is installed but its settings need root to read - run `sudo ufw status`.")

    def test_row6_nftables_only(self):
        info = read_firewall(self.root, FakeRunner("inactive\ninactive\nactive\n"))
        self.assertEqual(info.backend, "nftables")
        self.assertIsNone(info.active)
        self.assertIn("does not prove you are protected", info.note)

    def test_row7_nothing_detected_is_not_no_firewall(self):
        info = read_firewall(self.root, FakeRunner("inactive\ninactive\ninactive\n"))
        self.assertIsNone(info.backend)
        self.assertIsNone(info.active)
        self.assertIn("could still exist", info.note)

    def test_no_systemctl_binary(self):
        info = read_firewall(self.root, FakeRunner(systemctl=None, state=None))
        self.assertIsNone(info.backend)
        self.assertIsNone(info.active)
        self.assertIn("could still exist", info.note)

        write(self.root, "etc/ufw/ufw.conf", "ENABLED=yes\n")
        info = read_firewall(self.root, FakeRunner(systemctl=None, state=None))
        self.assertEqual(info.backend, "ufw")
        self.assertIs(info.active, True)

    def test_systemctl_short_output(self):
        self.assertEqual(parse_is_active("active\n", UNITS),
                         {"ufw": "active", "firewalld": "unknown", "nftables": "unknown"})
        write(self.root, "etc/ufw/ufw.conf", "ENABLED=yes\n")
        info = read_firewall(self.root, FakeRunner(systemctl="active\n"))
        self.assertEqual(info.backend, "ufw")
        self.assertIs(info.active, True)

    def test_tuple_parser_variants(self):
        text = "\n".join([
            "### tuple ### allow tcp 22 0.0.0.0/0 any 0.0.0.0/0 out",
            "### tuple ### allow tcp 443 0.0.0.0/0 any 0.0.0.0/0 out_eth0",
            "### tuple ### allow tcp 80 0.0.0.0/0 any 0.0.0.0/0 in_eth0",
            "### tuple ### allow tcp 443 ::/0 any 2001:db8::/32 in",
            "### tuple ### deny tcp 23 192.0.2.10 any 0.0.0.0/0 in",
            "### tuple ### allow",
            "# ### tuple ### commented out 1 2 3 4 5 6",
        ])
        self.assertEqual(parse_ufw_user_rules(text), (
            "allow tcp 22 (outgoing)",
            "allow tcp 443 (outgoing)",
            "allow tcp 80 on eth0",
            "allow tcp 443 from 2001:db8::/32",
            "deny tcp 23 to 192.0.2.10",
        ))

    def test_tuple_parser_caps_at_100(self):
        text = "\n".join(
            f"### tuple ### allow tcp {port} 0.0.0.0/0 any 0.0.0.0/0 in" for port in range(1000, 1150))
        rules = parse_ufw_user_rules(text)
        self.assertEqual(len(rules), 100)
        self.assertEqual(rules, tuple(f"allow tcp {port}" for port in range(1000, 1100)))

    def test_firewalld_zone_targets(self):
        self.assertEqual(parse_firewalld_list_all("  target: REJECT\n")[0], "reject")
        self.assertEqual(parse_firewalld_list_all("  target: DROP\n")[0], "deny")
        self.assertEqual(parse_firewalld_list_all("  target: ACCEPT\n")[0], "allow")
        self.assertIsNone(parse_firewalld_list_all("  interfaces: wlan0\n")[0])

    def test_runner_calls_have_timeouts(self):
        # No ufw files, so firewalld is reported and firewall-cmd --list-all is called too.
        runner = FakeRunner("inactive\nactive\ninactive\n", firewalld_list=FIREWALLD_LIST)
        read_firewall(self.root, runner)
        self.assertEqual(len(runner.calls), 2)
        self.assertTrue(runner.calls)
        for argv, timeout in runner.calls:
            self.assertLessEqual(timeout, 2)
            self.assertNotIn("sudo", argv)
            # Compare whole words: "nftables" is a legitimate systemctl unit name.
            self.assertNotIn(argv[0], ("nft", "iptables"))


if __name__ == "__main__":
    unittest.main()
