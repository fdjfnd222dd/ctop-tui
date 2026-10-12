"""Tests for ctop/render/security_lan.py: the device table and the exposure verdict."""
from __future__ import annotations

import unittest

from rich.cells import cell_len

from ctop.models import Device, FirewallInfo, Listener, SecurityData, WifiData
from ctop.render.security_lan import assess, devices_lines, summary_lines


def FW(active, default, backend="ufw", note="", rules_visible=False, rules=()):
    return FirewallInfo(backend, active, default, rules_visible, tuple(rules), note)


def SEC(listeners=(), fw=None):
    return SecurityData(listeners=tuple(listeners), firewall=fw or FW(True, "deny"))


def WIFI(sec):
    return WifiData("wlan0", "ExampleNet", 70, -60, 144.0, 5180, 36, sec, "Good")


NET = Listener("tcp", "0.0.0.0", 8080, 10, "python3", "network")
NET_V6 = Listener("tcp", "::", 8080, 10, "python3", "network")
NET_B = Listener("udp", "0.0.0.0", 5353, 12, "demo-server", "network")
LOC = Listener("tcp", "127.0.0.1", 631, 11, "cupsd", "local")

GW = Device("192.0.2.1", "02:00:00:aa:bb:cc", "wlan0", "REACHABLE", True, False)
D1 = Device("192.0.2.23", "02:00:00:aa:bb:cd", "wlan0", "REACHABLE", False, True)
D2 = Device("192.0.2.40", "02:00:00:aa:bb:ce", "wlan0", "STALE", False, False)
D3 = Device("192.0.2.9", "02:00:00:aa:bb:cf", "wlan0", "FAILED", False, False)


def _plain(lines):
    return [t.plain for t in lines]


def _ips(devices):
    # rows start after the note and header lines
    return [l[:16].strip() for l in _plain(devices_lines(devices, 40, 10))[2:]]


class DeviceTests(unittest.TestCase):
    def test_devices_roles_and_states(self):
        lines = _plain(devices_lines([GW, D1, D2], 90, 10))
        self.assertIn("router", lines[2])
        self.assertIn("active now", lines[2])
        self.assertIn("seen recently", lines[4])

    def test_devices_gateway_first_and_numeric_sort(self):
        self.assertEqual(_ips([D2, D1, GW]), ["192.0.2.1", "192.0.2.23", "192.0.2.40"])
        x9 = Device("192.0.2.9", "02:00:00:aa:bb:c1", "wlan0", "STALE", False, False)
        x23 = Device("192.0.2.23", "02:00:00:aa:bb:c2", "wlan0", "STALE", False, False)
        self.assertEqual(_ips([D2, x23, x9]), ["192.0.2.9", "192.0.2.23", "192.0.2.40"])

    def test_devices_failed_hidden(self):
        lines = _plain(devices_lines([GW, D3], 90, 10))
        self.assertFalse(any("192.0.2.9" in l for l in lines))

    def test_private_address_tag_only_when_wide(self):
        self.assertIn("private address", " ".join(_plain(devices_lines([D1], 90, 10))))
        self.assertNotIn("private address", " ".join(_plain(devices_lines([D1], 40, 10))))

    def test_devices_note_line(self):
        self.assertTrue(_plain(devices_lines([GW], 90, 10))[0].startswith(
            "Only devices this computer talked to recently."))
        self.assertTrue(_plain(devices_lines([GW], 40, 10))[0].startswith("Only recent contacts."))
        self.assertFalse(any(l.startswith("Only") for l in _plain(devices_lines([GW], 90, 3))))

    def test_devices_empty(self):
        lines = _plain(devices_lines([], 40, 10))
        self.assertTrue(any("No other devices seen yet." in l for l in lines))

    def test_devices_cut_more(self):
        devs = [Device("192.0.2.%d" % (10 + i), "02:00:00:aa:bb:cc", "wlan0", "STALE", False, False)
                for i in range(12)]
        lines = _plain(devices_lines(devs, 90, 6))
        self.assertEqual(len(lines), 6)
        self.assertTrue(lines[-1].startswith("... +"))

    def test_devices_mock_width_90(self):
        expected = """Only devices this computer talked to recently. Nothing is scanned.
IP address      MAC               Role    State
192.0.2.1       02:00:00:aa:bb:cc router  active now
192.0.2.23      02:00:00:aa:bb:cd device  active now    private address
192.0.2.40      02:00:00:aa:bb:ce device  seen recently""".split("\n")
        got = [l.rstrip() for l in _plain(devices_lines([GW, D1, D2], 90, 10))]
        self.assertEqual(got, expected)

    def test_devices_mock_width_40(self):
        expected = """Only recent contacts. Nothing scanned.
IP address      Role    State
192.0.2.1       router  active now
192.0.2.23      device  active now
192.0.2.40      device  seen recently""".split("\n")
        got = [l.rstrip() for l in _plain(devices_lines([GW, D1, D2], 40, 10))]
        self.assertEqual(got, expected)


class AssessTests(unittest.TestCase):
    def test_assess_low(self):
        level, reasons = assess(SEC([LOC]), None)
        self.assertEqual(level, "LOW")
        self.assertEqual(reasons[0], "Nothing is listening for connections from other devices on your network.")

    def test_assess_medium_firewall_deny(self):
        level, reasons = assess(SEC([NET], FW(True, "deny")), None)
        self.assertEqual(level, "MEDIUM")
        self.assertEqual(reasons[0], "1 program accepts connections from other devices on your network.")
        self.assertEqual(reasons[1], "The firewall is on and blocks anything you have not allowed.")

    def test_assess_high_firewall_off(self):
        self.assertEqual(assess(SEC([NET], FW(False, "allow")), None)[0], "HIGH")

    def test_assess_high_default_allow(self):
        self.assertEqual(assess(SEC([NET], FW(True, "allow")), None)[0], "HIGH")

    def test_assess_unknown_is_medium(self):
        level, reasons = assess(SEC([NET], FW(None, None)), None)
        self.assertEqual(level, "MEDIUM")
        self.assertEqual(reasons[1], "Could not tell whether a firewall is on.")

    def test_wifi_open_raises_one_level(self):
        self.assertEqual(assess(SEC([LOC]), WIFI("none"))[0], "MEDIUM")
        self.assertEqual(assess(SEC([NET]), WIFI("WEP"))[0], "HIGH")
        self.assertEqual(assess(SEC([NET], FW(False, "deny")), WIFI("none"))[0], "HIGH")
        self.assertEqual(assess(SEC([NET]), WIFI("WPA2"))[0], "MEDIUM")
        self.assertEqual(assess(SEC([NET]), WIFI(None))[0], "MEDIUM")
        self.assertEqual(assess(SEC([LOC]), WIFI("WPA2"))[0], "LOW")

    def test_two_listeners_counted_once_per_program(self):
        level, reasons = assess(SEC([NET, NET_V6]), None)
        self.assertEqual(reasons[0], "1 program accepts connections from other devices on your network.")


class SummaryTests(unittest.TestCase):
    def test_summary_has_footer_always(self):
        sec = SEC([NET, NET_B], FW(False, "allow"))
        for h in (1, 2, 3, 5, 12):
            lines = _plain(summary_lines(sec, WIFI("none"), 40, h))
            self.assertLessEqual(len(lines), h)
            if h >= 2:
                self.assertEqual(lines[-1].rstrip(), "Not a security audit.")

    def test_summary_firewall_lines(self):
        lines = [l.rstrip() for l in _plain(summary_lines(SEC([NET]), None, 90, 12))]
        self.assertIn("Firewall  ufw, on", lines)
        self.assertIn("Incoming  blocked unless a rule allows it", lines)
        lines = [l.rstrip() for l in _plain(summary_lines(SEC([NET], FW(True, "deny", backend=None)), None, 90, 12))]
        self.assertTrue(any("none detected" in l for l in lines))
        self.assertFalse(any(l.startswith("Incoming") for l in lines))
        self.assertFalse(any("None" in l for l in lines))

    def test_summary_wraps_at_40(self):
        sec = SEC([NET, NET_B], FW(False, "allow", note="Rule list needs root."))
        lines = summary_lines(sec, WIFI("none"), 40, 12)
        self.assertTrue(all(cell_len(t.plain) == 40 for t in lines))
        plain = _plain(lines)
        i = next(k for k, l in enumerate(plain) if l.startswith("• 2 programs"))
        self.assertTrue(plain[i + 1].startswith("  other devices"))

    def test_summary_mock_width_40(self):
        sec = SEC([NET, NET_B], FW(False, "allow", note="Rule list needs root."))
        expected = """Exposure  HIGH - needs attention
• 2 programs accept connections from
  other devices on your network.
• The firewall is off, so nothing stops
  other devices from reaching them.
• Your Wi-Fi network has no password, so
  people nearby can listen in.
Firewall  ufw, off
Incoming  allowed by default
Rule list needs root.
Not a security audit.""".split("\n")
        got = [l.rstrip() for l in _plain(summary_lines(sec, WIFI("none"), 40, 12))]
        self.assertEqual(got, expected)

    def test_summary_mock_width_90(self):
        sec = SEC([NET], FW(True, "deny", note="Rule list needs root."))
        expected = """Exposure  MEDIUM - worth a look
• 1 program accepts connections from other devices on your network.
• The firewall is on and blocks anything you have not allowed.
Firewall  ufw, on
Incoming  blocked unless a rule allows it
Rule list needs root.
Not a security audit.""".split("\n")
        got = [l.rstrip() for l in _plain(summary_lines(sec, None, 90, 12))]
        self.assertEqual(got, expected)

    def test_width_guarantee(self):
        devs = [GW, D1, D2, D3]
        sec = SEC([NET, NET_B], FW(False, "allow", note="Rule list needs root."))
        for w in (1, 12, 40, 90):
            for lines in (devices_lines(devs, w, 10), summary_lines(sec, WIFI("WEP"), w, 12)):
                self.assertTrue(all(cell_len(t.plain) == w for t in lines), w)
                self.assertLessEqual(len(lines), 12)


if __name__ == "__main__":
    unittest.main()
