"""Tests for ctop/collectors/nmstate.py. Every nmcli call is a fake keyed on argv: no real commands, no sleeping."""
from __future__ import annotations

import unittest

from ctop.collectors import nmstate
from ctop.collectors.nmstate import (
    CONNS, DEVICES, GENERAL, WIFI_LIST, NmCollector, band_of, parse_connections,
    parse_devices, parse_general, parse_wifi_list, ssid_argv, unescape_value,
)

WIFI_SAMPLE = (
    "*:AA\\:BB\\:CC\\:DD\\:EE\\:01:ExampleNet:136:5680 MHz:1170 Mbit/s:77:WPA1 WPA2\n"
    " :AA\\:BB\\:CC\\:DD\\:EE\\:02:ExampleNet:1:2412 MHz:270 Mbit/s:59:WPA2\n"
    " :AA\\:BB\\:CC\\:DD\\:EE\\:03:Cafe\\: Free:6:2437 MHz:130 Mbit/s:60:\n"
    " :AA\\:BB\\:CC\\:DD\\:EE\\:04::11:2462 MHz:65 Mbit/s:30:WPA2\n"
)
GENERAL_OK = "running:connected:full:enabled:enabled:enabled\n"
DEVICES_OK = "wlan0:wifi:connected\np2p-dev-wlan0:wifi-p2p:disconnected\nlo:loopback:connected (externally)\n"
UUID_EX = "3f0c1e2a-1111-2222-3333-444455556666"
CONNS_OK = (
    "ExampleNet:" + UUID_EX + ":802-11-wireless:wlan0:yes:0:yes:activated:1791910451\n"
    "Cafe\\: Free:aaaaaaaa-0000-0000-0000-000000000002:802-11-wireless:--:yes:0:no::1700000000\n"
    "lo:bbbbbbbb-0000-0000-0000-000000000003:loopback:lo:yes:0:yes:activated:1\n"
)


class FakeRunner:
    """Answers nmcli argv from a dict; records every call. Unknown argv answers None (command failed)."""

    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def __call__(self, argv, timeout):
        self.calls.append(list(argv))
        assert timeout == nmstate.TIMEOUT
        val = self.answers.get(tuple(argv))
        return val(argv) if callable(val) else val

    def count(self, argv):
        return sum(1 for c in self.calls if c == list(argv))


def fake_clock(now=1000.0):
    return lambda: now


def ok_answers(wifi_list=WIFI_SAMPLE, conns=CONNS_OK, ssid_by_uuid=None):
    ssid_by_uuid = ssid_by_uuid or {UUID_EX: "ExampleNet\n", "aaaaaaaa-0000-0000-0000-000000000002": "Cafe\\: Free\n"}

    def ssid_lookup(argv):
        return ssid_by_uuid.get(argv[-1])

    answers = {
        tuple(GENERAL): GENERAL_OK,
        tuple(DEVICES): DEVICES_OK,
        tuple(CONNS): conns,
        tuple(WIFI_LIST): wifi_list,
    }
    return answers, ssid_lookup


class BandTests(unittest.TestCase):
    def test_bands(self):
        self.assertEqual(band_of(2412), "2.4 GHz")
        self.assertEqual(band_of(5180), "5 GHz")
        self.assertEqual(band_of(5955), "6 GHz")
        self.assertEqual(band_of(7115), "6 GHz")
        self.assertEqual(band_of(None), "?")


class ParseGeneralTests(unittest.TestCase):
    def test_sample(self):
        g = parse_general(GENERAL_OK)
        self.assertTrue(g["running"])
        self.assertTrue(g["wifi"])
        self.assertTrue(g["wwan"])
        self.assertTrue(g["wifi_hw"])
        self.assertEqual(g["state"], "connected")
        self.assertEqual(g["connectivity"], "full")

    def test_disabled_and_missing(self):
        g = parse_general("running:asleep:none:enabled:disabled:missing\n")
        self.assertFalse(g["wifi"])
        self.assertIsNone(g["wwan"])

    def test_not_running(self):
        self.assertFalse(parse_general("stopped:x:y:z:w:v\n")["running"])


class ParseDevicesTests(unittest.TestCase):
    def test_picks_wifi_not_p2p(self):
        self.assertEqual(parse_devices(DEVICES_OK), "wlan0")

    def test_none(self):
        self.assertIsNone(parse_devices("lo:loopback:connected\neth0:ethernet:connected\n"))


class ParseWifiListTests(unittest.TestCase):
    def setUp(self):
        self.nets = parse_wifi_list(WIFI_SAMPLE, frozenset({"Cafe: Free"}))

    def test_three_entries_in_use_first(self):
        self.assertEqual(len(self.nets), 3)
        first = self.nets[0]
        self.assertEqual(first.ssid, "ExampleNet")
        self.assertTrue(first.in_use)
        self.assertEqual(first.aps, 2)
        self.assertEqual(first.channel, 136)
        self.assertEqual(first.band, "5 GHz")
        self.assertEqual(first.security, "WPA WPA2")
        self.assertTrue(first.bssid.endswith(":01"))
        self.assertEqual(first.bssid, "AA:BB:CC:DD:EE:01")

    def test_open_and_hidden_and_saved(self):
        by_ssid = {n.ssid: n for n in self.nets}
        cafe = by_ssid["Cafe: Free"]
        self.assertEqual(cafe.security, "")
        self.assertTrue(cafe.saved)
        hidden = by_ssid[""]
        self.assertEqual(hidden.aps, 1)
        self.assertEqual(hidden.channel, 11)
        self.assertFalse(hidden.saved)

    def test_hidden_entry_is_never_marked_saved(self):
        text = " :AA\\:BB\\:CC\\:DD\\:EE\\:04::11:2462 MHz:65 Mbit/s:30:WPA2\n"
        nets = parse_wifi_list(text, frozenset({""}))
        self.assertEqual(len(nets), 1)
        self.assertFalse(nets[0].saved)

    def test_saved_flag_defaults_to_false(self):
        self.assertFalse(any(n.saved for n in parse_wifi_list(WIFI_SAMPLE)))

    def test_in_use_row_wins_over_stronger_ap(self):
        text = (
            " :AA\\:BB\\:CC\\:DD\\:EE\\:10:Home:36:5180 MHz:866 Mbit/s:90:WPA2\n"
            "*:AA\\:BB\\:CC\\:DD\\:EE\\:11:Home:40:5200 MHz:866 Mbit/s:40:WPA2\n"
        )
        (net,) = parse_wifi_list(text)
        self.assertTrue(net.in_use)
        self.assertEqual(net.bssid, "AA:BB:CC:DD:EE:11")
        self.assertEqual(net.signal_pct, 40)
        self.assertEqual(net.channel, 40)
        self.assertEqual(net.aps, 2)

    def test_garbage_lines_skipped(self):
        self.assertEqual(parse_wifi_list("nonsense\n\n:::\n"), ())

    def test_cap_at_sixty(self):
        lines = "".join(" :AA\\:BB\\:CC\\:DD\\:EE\\:%02X:Net%d:6:2437 MHz:10 Mbit/s:50:\n" % (i % 256, i)
                         for i in range(80))
        self.assertEqual(len(parse_wifi_list(lines)), 60)


class ParseConnectionsTests(unittest.TestCase):
    def test_wifi_row_full(self):
        conns = parse_connections(CONNS_OK, {UUID_EX: "ExampleNet"})
        wifi = [c for c in conns if c.name == "ExampleNet"][0]
        self.assertEqual(wifi.kind, "wifi")
        self.assertEqual(wifi.uuid, UUID_EX)
        self.assertEqual(wifi.device, "wlan0")
        self.assertTrue(wifi.active)
        self.assertTrue(wifi.autoconnect)
        self.assertEqual(wifi.state, "activated")
        self.assertEqual(wifi.last_used, 1791910451.0)
        self.assertEqual(wifi.ssid, "ExampleNet")

    def test_loopback_skipped(self):
        names = [c.name for c in parse_connections(CONNS_OK)]
        self.assertNotIn("lo", names)

    def test_dash_device_is_none_and_zero_timestamp_is_none(self):
        cafe = [c for c in parse_connections(CONNS_OK) if c.uuid.startswith("aaaa")][0]
        self.assertIsNone(cafe.device)
        self.assertFalse(cafe.active)
        zero = parse_connections("Net:cccccccc-0000:802-11-wireless:--:yes:0:no::0\n")[0]
        self.assertIsNone(zero.last_used)
        self.assertIsNone(zero.device)
        self.assertEqual(zero.state, "")

    def test_ordering_active_then_recent(self):
        text = (
            "Old:11111111-0000:802-3-ethernet:--:yes:0:no::100\n"
            "New:22222222-0000:802-3-ethernet:--:yes:0:no::900\n"
            "Live:33333333-0000:vpn:eth0:yes:0:yes:activated:1\n"
            "Never:44444444-0000:vpn:--:yes:0:no::0\n"
        )
        names = [c.name for c in parse_connections(text)]
        self.assertEqual(names, ["Live", "New", "Old", "Never"])

    def test_escaped_colon_in_name(self):
        conns = parse_connections("Cafe\\: Free:aaaaaaaa-0000:802-11-wireless:--:yes:0:no::5\n")
        self.assertEqual(conns[0].name, "Cafe: Free")
        self.assertEqual(conns[0].kind, "wifi")

    def test_kinds(self):
        text = (
            "v:11111111-0000:vpn:--:no:0:no::1\n"
            "w:22222222-0000:wireguard:--:no:0:no::1\n"
            "x:33333333-0000:gsm:--:no:0:no::1\n"
        )
        kinds = {c.name: c.kind for c in parse_connections(text)}
        self.assertEqual(kinds, {"v": "vpn", "w": "wireguard", "x": "other"})


class UnescapeTests(unittest.TestCase):
    def test_trailing_newline_and_escapes(self):
        self.assertEqual(unescape_value("Cafe\\: Free\n"), "Cafe: Free")
        self.assertEqual(unescape_value("a\\\\b\n"), "a\\b")


class CollectorTests(unittest.TestCase):
    def test_networkmanager_missing(self):
        runner = FakeRunner({})
        coll = NmCollector(runner=runner, clock=fake_clock(5.0))
        coll.refresh()
        st = coll.data
        self.assertFalse(st.available)
        self.assertIn("network-manager", st.reason)
        self.assertEqual(st.scanned_at, 5.0)
        self.assertEqual(len(runner.calls), 1)

    def test_networkmanager_not_running(self):
        runner = FakeRunner({tuple(GENERAL): "stopped:x:y:z:w:v\n"})
        coll = NmCollector(runner=runner, clock=fake_clock())
        coll.refresh()
        self.assertFalse(coll.data.available)
        self.assertEqual(coll.data.reason, "NetworkManager is not running")

    def test_full_path(self):
        answers, lookup = ok_answers()
        coll = self._collector(answers, lookup, clock=fake_clock(42.0))
        coll.refresh()
        st = coll.data
        self.assertTrue(st.available)
        self.assertEqual(st.wifi_device, "wlan0")
        self.assertEqual(st.scanned_at, 42.0)
        self.assertTrue(st.wifi_enabled)
        ex = [n for n in st.networks if n.ssid == "ExampleNet"][0]
        self.assertTrue(ex.in_use)
        self.assertEqual(ex.aps, 2)
        self.assertTrue(ex.saved)
        wifi_saved = [c for c in st.saved if c.kind == "wifi"]
        self.assertIn("Cafe: Free", {c.ssid for c in wifi_saved})
        self.assertIn("ExampleNet", {c.ssid for c in wifi_saved})
        self.assertTrue(any(n.saved for n in st.networks if n.ssid == "Cafe: Free"))

    def _collector(self, answers, lookup, clock=fake_clock()):
        runner = FakeRunner(answers)
        calls = []   # every command, including the -g SSID lookups

        def dispatch(argv, timeout):
            calls.append(list(argv))
            if argv[:2] == ["nmcli", "-g"]:
                return lookup(argv)
            return runner(argv, timeout)

        coll = NmCollector(runner=dispatch, clock=clock)
        coll.calls = calls
        return coll

    def test_ssid_cache_not_looked_up_twice(self):
        answers, lookup = ok_answers()
        coll = self._collector(answers, lookup)
        coll.refresh()
        lookups = [c for c in coll.calls if c[:2] == ["nmcli", "-g"]]
        coll.refresh()
        lookups2 = [c for c in coll.calls if c[:2] == ["nmcli", "-g"]]
        self.assertEqual(len(lookups), 2)
        self.assertEqual(len(lookups2), 2)

    def test_at_most_eight_lookups_per_refresh(self):
        conns = "".join("Net%d:%08d-0000-0000-0000-000000000000:802-11-wireless:--:yes:0:no::1\n" % (i, i)
                        for i in range(10))
        answers = {tuple(GENERAL): GENERAL_OK, tuple(DEVICES): DEVICES_OK, tuple(CONNS): conns,
                   tuple(WIFI_LIST): ""}
        coll = self._collector(answers, lambda argv: "Ssid%s\n" % argv[-1][:8])
        coll.refresh()
        self.assertEqual(sum(1 for c in coll.calls if c[:2] == ["nmcli", "-g"]), 8)
        coll.refresh()
        self.assertEqual(sum(1 for c in coll.calls if c[:2] == ["nmcli", "-g"]), 10)

    def test_failed_lookup_caches_profile_name(self):
        answers, _ = ok_answers()
        coll = self._collector(answers, lambda argv: None)
        coll.refresh()
        self.assertEqual(coll._ssid_cache[UUID_EX], "ExampleNet")
        coll.refresh()
        self.assertEqual(sum(1 for c in coll.calls if c[:2] == ["nmcli", "-g"]), 2)

    def test_wifi_disabled_skips_wifi_list(self):
        answers, lookup = ok_answers()
        answers[tuple(GENERAL)] = "running:connected:full:enabled:disabled:missing\n"
        coll = self._collector(answers, lookup)
        coll.refresh()
        self.assertFalse(coll.data.wifi_enabled)
        self.assertEqual(coll.data.networks, ())
        self.assertFalse(any(c == WIFI_LIST for c in coll.calls))

    def test_no_wifi_device_skips_wifi_list(self):
        answers, lookup = ok_answers()
        answers[tuple(DEVICES)] = "eth0:ethernet:connected\n"
        coll = self._collector(answers, lookup)
        coll.refresh()
        self.assertIsNone(coll.data.wifi_device)
        self.assertFalse(any(c == WIFI_LIST for c in coll.calls))

    def test_bad_uuid_never_reaches_argv(self):
        conns = "Evil:x;rm:802-11-wireless:--:yes:0:no::1\n"
        answers = {tuple(GENERAL): GENERAL_OK, tuple(DEVICES): DEVICES_OK, tuple(CONNS): conns,
                   tuple(WIFI_LIST): ""}
        coll = self._collector(answers, lambda argv: "x")
        coll.refresh()
        self.assertFalse(any("x;rm" in " ".join(c) for c in coll.calls))
        self.assertIsNone(coll.data.saved[0].ssid)

    def test_wifi_list_always_no_rescan(self):
        answers, lookup = ok_answers()
        coll = self._collector(answers, lookup)
        coll.refresh()
        coll.refresh()
        lists = [c for c in coll.calls if c == WIFI_LIST]
        self.assertEqual(len(lists), 2)
        for argv in lists:
            i = argv.index("--rescan")
            self.assertEqual(argv[i + 1], "no")

    def test_ssid_argv_shape(self):
        self.assertEqual(ssid_argv("abc12345"),
                         ["nmcli", "-g", "802-11-wireless.ssid", "connection", "show", "uuid", "abc12345"])


if __name__ == "__main__":
    unittest.main()
