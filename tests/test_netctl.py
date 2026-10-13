"""Tests for ctop/netctl.py. Every command is a fake `run`: nothing real runs, nothing sleeps."""
from __future__ import annotations

import subprocess
import unittest
from types import SimpleNamespace

from ctop import netctl
from ctop.netctl import (
    airplane, connect_new, connect_saved, disconnect, forget, get_dns, parse_dns_list,
    rescan, reconnect, set_autoconnect, set_dns, set_priority, tailscale, valid_iface,
    valid_password, valid_ssid, valid_uuid, wifi_radio,
)

U = "3f0c1e2a-1111-2222-3333-444455556666"
U2 = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
PW = "hunter22pw"
WRONG_PW_ERR = "Error: Connection activation failed: Secrets were required, but not provided"


def _proc(returncode=0, stdout="", stderr=""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


class Fake:
    """Records every call. `replies` is a list of procs (or exceptions) returned in order."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append((list(argv), kwargs))
        if self.replies:
            reply = self.replies.pop(0)
        else:
            reply = _proc()
        if isinstance(reply, BaseException):
            raise reply
        return reply

    @property
    def argvs(self):
        return [c[0] for c in self.calls]


class ValidatorTests(unittest.TestCase):
    def test_uuid(self):
        self.assertTrue(valid_uuid(U))
        self.assertFalse(valid_uuid("-f"))
        self.assertFalse(valid_uuid("x;rm"))
        self.assertFalse(valid_uuid("3f0c1e2a-1111-2222-3333-44445555666"))  # 35 chars
        self.assertFalse(valid_uuid(None))

    def test_ssid(self):
        self.assertTrue(valid_ssid("Cafe: Free"))
        self.assertTrue(valid_ssid("ExampleNet"))
        self.assertTrue(valid_ssid("x" * 32))
        self.assertFalse(valid_ssid("x" * 33))
        self.assertFalse(valid_ssid(""))
        self.assertFalse(valid_ssid("bad\nname"))
        self.assertFalse(valid_ssid("bad\0name"))
        self.assertFalse(valid_ssid("é" * 17))  # 34 UTF-8 bytes

    def test_iface(self):
        self.assertTrue(valid_iface("wlan0"))
        self.assertFalse(valid_iface("-x"))
        self.assertFalse(valid_iface("a" * 16))
        self.assertFalse(valid_iface("wl an"))

    def test_password(self):
        self.assertIsNotNone(valid_password("1234567", "WPA2"))
        self.assertIsNone(valid_password("12345678", "WPA2"))
        self.assertIsNone(valid_password("a" * 64, "WPA2"))  # 64 chars, all hex
        self.assertIsNone(valid_password("ab" * 32, "WPA3"))
        self.assertIsNotNone(valid_password("g" * 64, "WPA2"))  # 64 chars, not hex, too long
        self.assertIsNotNone(valid_password("pässwörd1", "WPA2"))
        self.assertIsNone(valid_password("", ""))
        self.assertIsNone(valid_password("whatever", ""))

    def test_parse_dns_list(self):
        self.assertEqual(parse_dns_list("1.1.1.1, 9.9.9.9"), ("1.1.1.1", "9.9.9.9"))
        self.assertEqual(parse_dns_list("1.1.1.1 9.9.9.9"), ("1.1.1.1", "9.9.9.9"))
        self.assertEqual(parse_dns_list("2001:db8::1 1.1.1.1"), ("2001:db8::1", "1.1.1.1"))
        self.assertIsNone(parse_dns_list("1.1.1.1 2.2.2.2 3.3.3.3 4.4.4.4"))
        self.assertIsNone(parse_dns_list("nope"))
        self.assertEqual(parse_dns_list(""), ())
        self.assertEqual(parse_dns_list("   "), ())


class SavedProfileTests(unittest.TestCase):
    def test_connect_saved_argv_and_timeout(self):
        fake = Fake(_proc(0))
        out = connect_saved(U, "ExampleNet", run=fake)
        self.assertTrue(out.ok)
        self.assertEqual(out.message, "Connected to ExampleNet.")
        self.assertEqual(fake.argvs[0], ["nmcli", "--wait", "30", "connection", "up", "uuid", U])
        self.assertEqual(fake.calls[0][1]["timeout"], 40)
        self.assertIs(fake.calls[0][1]["stdin"], subprocess.DEVNULL)
        self.assertEqual(fake.calls[0][1]["env"]["LC_ALL"], "C")

    def test_bad_uuid_runs_nothing(self):
        fake = Fake()
        out = connect_saved("-f", run=fake)
        self.assertFalse(out.ok)
        self.assertEqual(fake.calls, [])

    def test_disconnect_forget_autoconnect_argv(self):
        fake = Fake(_proc(0), _proc(0), _proc(0), _proc(0))
        disconnect(U, run=fake)
        forget(U, run=fake)
        set_autoconnect(U, False, run=fake)
        set_autoconnect(U, True, run=fake)
        self.assertEqual(fake.argvs[0], ["nmcli", "connection", "down", "uuid", U])
        self.assertEqual(fake.argvs[1], ["nmcli", "connection", "delete", "uuid", U])
        self.assertEqual(fake.argvs[2][-2:], ["connection.autoconnect", "no"])
        self.assertEqual(fake.argvs[3][-2:], ["connection.autoconnect", "yes"])

    def test_priority_clamped(self):
        fake = Fake(_proc(0), _proc(0))
        set_priority(U, 5000, run=fake)
        set_priority(U, -5000, run=fake)
        self.assertEqual(fake.argvs[0][-2:], ["connection.autoconnect-priority", "999"])
        self.assertEqual(fake.argvs[1][-2:], ["connection.autoconnect-priority", "-999"])

    def test_priority_not_a_number(self):
        fake = Fake()
        self.assertFalse(set_priority(U, "high", run=fake).ok)
        self.assertEqual(fake.calls, [])

    def test_get_dns_both_shapes(self):
        fake = Fake(_proc(0, stdout="1.1.1.1,1.0.0.1\nyes\n"))
        self.assertEqual(get_dns(U, run=fake), (("1.1.1.1", "1.0.0.1"), True))
        self.assertEqual(fake.argvs[0][:3], ["nmcli", "-g", "ipv4.dns,ipv4.ignore-auto-dns"])
        self.assertEqual(get_dns(U, run=Fake(_proc(0, stdout="\nno\n"))), ((), False))

    def test_get_dns_failure_is_none(self):
        self.assertIsNone(get_dns(U, run=Fake(_proc(10, stderr="Error: no such profile"))))
        self.assertIsNone(get_dns(U, run=Fake(subprocess.TimeoutExpired("nmcli", 10))))
        self.assertIsNone(get_dns("bad", run=Fake()))


class DnsTests(unittest.TestCase):
    def test_auto_argv(self):
        fake = Fake(_proc(0), _proc(0))
        out = set_dns(U, (), "ExampleNet", run=fake)
        self.assertTrue(out.ok)
        self.assertEqual(out.message, "DNS for ExampleNet: automatic.")
        self.assertEqual(fake.argvs[0], [
            "nmcli", "connection", "modify", "uuid", U,
            "ipv4.dns", "", "ipv4.ignore-auto-dns", "no",
            "ipv6.dns", "", "ipv6.ignore-auto-dns", "no"])
        self.assertEqual(fake.argvs[1], ["nmcli", "--wait", "30", "connection", "up", "uuid", U])

    def test_v4_only_leaves_ipv6_alone(self):
        fake = Fake(_proc(0), _proc(0))
        out = set_dns(U, ("1.1.1.1", "1.0.0.1"), "ExampleNet", run=fake)
        self.assertEqual(out.message, "DNS for ExampleNet: 1.1.1.1, 1.0.0.1.")
        argv = fake.argvs[0]
        self.assertIn("ipv4.dns", argv)
        self.assertIn("1.1.1.1,1.0.0.1", argv)
        self.assertNotIn("ipv6.dns", argv)
        self.assertNotIn("ipv6.ignore-auto-dns", argv)

    def test_mixed_families(self):
        fake = Fake(_proc(0), _proc(0))
        set_dns(U, ("9.9.9.9", "2620:fe::fe"), "", run=fake)
        argv = fake.argvs[0]
        self.assertEqual(argv[argv.index("ipv4.dns") + 1], "9.9.9.9")
        self.assertEqual(argv[argv.index("ipv6.dns") + 1], "2620:fe::fe")
        self.assertEqual(argv[argv.index("ipv6.ignore-auto-dns") + 1], "yes")

    def test_bad_server_runs_nothing(self):
        fake = Fake()
        self.assertFalse(set_dns(U, ("nope",), run=fake).ok)
        self.assertFalse(set_dns(U, ("1.1.1.1", "2.2.2.2", "3.3.3.3", "4.4.4.4"), run=fake).ok)
        self.assertFalse(set_dns(U, (1,), run=fake).ok)
        self.assertEqual(fake.calls, [])


class NewNetworkTests(unittest.TestCase):
    def test_open_network(self):
        fake = Fake(_proc(0))
        out = connect_new("Cafe: Free", "ignored", "", "wlan0", run=fake)
        self.assertTrue(out.ok)
        argv = fake.argvs[0]
        self.assertEqual(argv, ["nmcli", "--wait", "30", "device", "wifi", "connect",
                                "Cafe: Free", "ifname", "wlan0"])
        self.assertNotIn("ignored", argv)
        self.assertEqual(len(fake.calls), 1)

    def test_secured_success_keeps_password_out_of_argv(self):
        new_uuid = "3f0c1e2a-1111-2222-3333-444455556666"
        fake = Fake(_proc(0, stdout="Connection 'ExampleNet' (%s) successfully added.\n" % new_uuid),
                    _proc(0))
        out = connect_new("ExampleNet", PW, "WPA2", "wlan0", run=fake)
        self.assertTrue(out.ok)
        self.assertEqual(len(fake.calls), 2)
        add = fake.argvs[0]
        self.assertIn("wifi-sec.key-mgmt", add)
        self.assertIn("wpa-psk", add)
        up = fake.argvs[1]
        self.assertIn("passwd-file", up)
        self.assertIn("/dev/stdin", up)
        self.assertEqual(fake.calls[1][1]["input"], "802-11-wireless-security.psk:hunter22pw\n")
        self.assertNotIn("stdin", fake.calls[1][1])
        for argv, _kw in fake.calls:
            for element in argv:
                self.assertNotIn(PW, element)
        self.assertNotIn(PW, " ".join(" ".join(a) for a in fake.argvs))

    def test_wpa3_only_uses_sae(self):
        fake = Fake(_proc(0, stdout="Connection 'X' (%s) successfully added." % U), _proc(0))
        connect_new("ExampleNet", PW, "WPA3", "wlan0", run=fake)
        self.assertIn("sae", fake.argvs[0])
        self.assertNotIn("wpa-psk", fake.argvs[0])

    def test_wpa2_wpa3_uses_wpa_psk(self):
        fake = Fake(_proc(0, stdout="Connection 'X' (%s) successfully added." % U), _proc(0))
        connect_new("ExampleNet", PW, "WPA2 WPA3", "wlan0", run=fake)
        self.assertIn("wpa-psk", fake.argvs[0])
        self.assertNotIn("sae", fake.argvs[0])

    def test_wrong_password_rolls_back(self):
        fake = Fake(_proc(0, stdout="Connection 'X' (%s) successfully added." % U),
                    _proc(4, stderr=WRONG_PW_ERR),
                    _proc(0))
        out = connect_new("ExampleNet", PW, "WPA2", "wlan0", run=fake)
        self.assertFalse(out.ok)
        self.assertIn("password", out.message)
        self.assertEqual(fake.argvs[2], ["nmcli", "connection", "delete", "uuid", U])

    def test_enterprise_runs_nothing(self):
        fake = Fake()
        out = connect_new("Corp", PW, "WPA2 802.1X", "wlan0", run=fake)
        self.assertFalse(out.ok)
        self.assertIn("802.1X", out.message)
        self.assertEqual(fake.calls, [])

    def test_bad_inputs_run_nothing(self):
        fake = Fake()
        self.assertFalse(connect_new("x" * 33, "", "", "wlan0", run=fake).ok)
        self.assertFalse(connect_new("ExampleNet", "", "", "-x", run=fake).ok)
        self.assertFalse(connect_new("ExampleNet", "short", "WPA2", "wlan0", run=fake).ok)
        self.assertEqual(fake.calls, [])


class ErrorMappingTests(unittest.TestCase):
    def test_not_authorized(self):
        out = connect_saved(U, run=Fake(_proc(8, stderr="Error: not authorized")))
        self.assertFalse(out.ok)
        self.assertIn("polkit", out.message)

    def test_missing_nmcli(self):
        out = connect_saved(U, run=Fake(FileNotFoundError("nmcli")))
        self.assertEqual(out.message, "nmcli is not installed (package: network-manager)")

    def test_timeout_raised(self):
        out = connect_saved(U, run=Fake(subprocess.TimeoutExpired("nmcli", 40)))
        self.assertEqual(out.message, "Timed out - the network did not answer.")

    def test_no_network_and_generic(self):
        out = connect_new_open_failing("No network with SSID 'ExampleNet' found.")
        self.assertIn("out of range", out.message)
        out = disconnect(U, run=Fake(_proc(10, stderr="Error: something odd\nmore")))
        self.assertEqual(out.message, "Failed: something odd")

    def test_message_is_cut(self):
        out = disconnect(U, run=Fake(_proc(10, stderr="Error: " + "z" * 300)))
        self.assertEqual(len(out.message), len("Failed: ") + 100)


def connect_new_open_failing(stderr):
    return connect_new("ExampleNet", "", "", "wlan0", run=Fake(_proc(10, stderr=stderr)))


class RadioAndRecoveryTests(unittest.TestCase):
    def test_wifi_radio_and_airplane_argv(self):
        fake = Fake(_proc(0), _proc(0), _proc(0), _proc(0))
        wifi_radio(True, run=fake)
        wifi_radio(False, run=fake)
        airplane(True, run=fake)
        airplane(False, run=fake)
        self.assertEqual(fake.argvs, [
            ["nmcli", "radio", "wifi", "on"],
            ["nmcli", "radio", "wifi", "off"],
            ["nmcli", "radio", "all", "off"],
            ["nmcli", "radio", "all", "on"],
        ])

    def test_no_argv_touches_networking_or_hostname(self):
        fake = Fake(*[_proc(0)] * 30)
        for fn in (lambda: airplane(True, run=fake), lambda: wifi_radio(False, run=fake),
                   lambda: reconnect(U, run=fake), lambda: rescan(run=fake)):
            fn()
        for argv in fake.argvs:
            self.assertNotIn("networking", argv)
            self.assertNotIn("hostname", argv)

    def test_reconnect_tries_up_even_if_down_fails(self):
        fake = Fake(_proc(10, stderr="Error: not active"), _proc(0))
        out = reconnect(U, "ExampleNet", run=fake)
        self.assertTrue(out.ok)
        self.assertEqual(out.message, "Reconnected to ExampleNet.")
        self.assertEqual(fake.argvs[0], ["nmcli", "connection", "down", "uuid", U])
        self.assertEqual(fake.argvs[1], ["nmcli", "--wait", "30", "connection", "up", "uuid", U])

    def test_rescan_argv(self):
        fake = Fake(_proc(0), _proc(0))
        rescan(run=fake)
        rescan("wlan0", run=fake)
        self.assertEqual(fake.argvs[0], ["nmcli", "device", "wifi", "rescan"])
        self.assertEqual(fake.argvs[1], ["nmcli", "device", "wifi", "rescan", "ifname", "wlan0"])
        self.assertFalse(rescan("-x", run=Fake()).ok)

    def test_tailscale_missing_and_present(self):
        fake = Fake(_proc(0))
        self.assertEqual(tailscale(True, run=fake, which=lambda name: None),
                         netctl.Outcome(False, "Tailscale is not installed"))
        self.assertEqual(fake.calls, [])
        out = tailscale(False, run=fake, which=lambda name: "/usr/bin/tailscale")
        self.assertTrue(out.ok)
        self.assertEqual(fake.argvs[0], ["tailscale", "down"])


class AllowListTests(unittest.TestCase):
    def test_every_public_call_uses_only_nmcli_or_tailscale(self):
        fake = Fake(*[_proc(0, stdout="1.1.1.1\nno\n")] * 40)
        calls = [
            lambda: connect_saved(U, "n", run=fake),
            lambda: disconnect(U, "n", run=fake),
            lambda: forget(U, "n", run=fake),
            lambda: set_autoconnect(U, True, "n", run=fake),
            lambda: set_priority(U, 1, "n", run=fake),
            lambda: get_dns(U, run=fake),
            lambda: set_dns(U, ("1.1.1.1",), "n", run=fake),
            lambda: connect_new("ExampleNet", "", "", "wlan0", run=fake),
            lambda: connect_new("ExampleNet", PW, "WPA2", "wlan0", run=fake),
            lambda: rescan("wlan0", run=fake),
            lambda: wifi_radio(True, run=fake),
            lambda: airplane(False, run=fake),
            lambda: reconnect(U, "n", run=fake),
            lambda: tailscale(True, run=fake, which=lambda name: "/usr/bin/tailscale"),
        ]
        for call in calls:
            call()
        self.assertTrue(fake.argvs)
        for argv in fake.argvs:
            self.assertIn(argv[0], ("nmcli", "tailscale"))
            self.assertNotIn("sudo", argv)
            self.assertNotIn("pkexec", argv)
            self.assertNotIn("networking", argv)


class HardeningTests(unittest.TestCase):
    def test_lone_surrogate_ssid_is_rejected_not_raised(self):
        self.assertFalse(valid_ssid("bad\udc80name"))
        fake = Fake()
        self.assertFalse(connect_new("bad\udc80name", "", "", "wlan0", run=fake).ok)
        self.assertEqual(fake.calls, [])

    def test_infinite_priority_is_rejected_not_raised(self):
        fake = Fake()
        self.assertFalse(set_priority(U, float("inf"), run=fake).ok)
        self.assertEqual(fake.calls, [])

    def test_open_ssid_starting_with_dash_runs_nothing(self):
        fake = Fake()
        out = connect_new("-x", "", "", "wlan0", run=fake)
        self.assertFalse(out.ok)
        self.assertEqual(fake.calls, [])

    def test_owe_is_treated_as_open_and_never_sends_a_password(self):
        fake = Fake(_proc(0))
        out = connect_new("ExampleNet", "ignored-secret", "OWE", "wlan0", run=fake)
        self.assertTrue(out.ok)
        self.assertEqual(fake.argvs, [["nmcli", "--wait", "30", "device", "wifi", "connect",
                                       "ExampleNet", "ifname", "wlan0"]])
        self.assertNotIn("input", fake.calls[0][1])

    def test_secured_up_step_is_exercised_in_allow_list(self):
        added = "Connection 'ExampleNet' (%s) successfully added.\n" % U
        fake = Fake(_proc(0, stdout=added), _proc(0))
        self.assertTrue(connect_new("ExampleNet", PW, "WPA2", "wlan0", run=fake).ok)
        self.assertEqual(len(fake.calls), 2)
        for argv in fake.argvs:
            self.assertEqual(argv[0], "nmcli")
            self.assertTrue(all(PW not in a for a in argv))


if __name__ == "__main__":
    unittest.main()
