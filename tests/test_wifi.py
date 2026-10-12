"""Wi-Fi collector tests. Fake runner, temp sysfs tree, fake clock: never real iw/nmcli."""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest

from ctop.collectors.wifi import (
    WifiCollector,
    channel_from_freq,
    parse_iw_link,
    parse_nmcli_wifi,
    parse_proc_wireless,
    pct_from_dbm,
    quality_from_dbm,
    quality_from_pct,
    split_nmcli_line,
)

PROC_HEADER = (
    "Inter-| sta-|   Quality        |   Discarded packets ...\n"
    " face | tus | link level noise |  nwid  crypt   frag  retry   misc | beacon | 22\n"
)
PROC_SAMPLE = PROC_HEADER + " wlan0: 0000   69.  -41.  -256        0      0      0     27     12        0\n"
PROC_WEAK = PROC_HEADER + " wlan0: 0000   40.  -61.  -256        0      0      0     27     12        0\n"

IW_CONNECTED = (
    "Connected to 02:00:00:aa:bb:cc (on wlan0)\n"
    "\tSSID: ExampleNet\n"
    "\tfreq: 5180.0\n"
    "\tsignal: -42 dBm\n"
    "\trx bitrate: 390.0 MBit/s VHT-MCS 8 80MHz short GI VHT-NSS 1\n"
    "\ttx bitrate: 866.7 MBit/s VHT-MCS 9 80MHz short GI VHT-NSS 2\n"
)
IW_NOT_CONNECTED = "Not connected.\n"

NMCLI_SAMPLE = (
    "*:ExampleNet:36:5180 MHz:866 Mbit/s:78:WPA2 WPA3\n"
    " :Neighbour Net:44:5220 MHz:540 Mbit/s:62:WPA2\n"
    " :Open Cafe:1:2412 MHz:130 Mbit/s:55:\n"
    "*:Cafe\\: Free:6:2437 MHz:130 Mbit/s:60:WPA1 WPA2\n"
)


class FakeRunner:
    """Answers `iw` and `nmcli` from fixed text and records every argv."""

    def __init__(self, iw=None, nmcli=None):
        self.iw = iw
        self.nmcli = nmcli
        self.calls = []

    def __call__(self, argv, timeout):
        self.calls.append(list(argv))
        if argv[0] == "iw":
            return self.iw
        if argv[0] == "nmcli":
            return self.nmcli
        return None

    def count(self, tool):
        return sum(1 for c in self.calls if c[0] == tool)


class FakeClock:
    def __init__(self, now=0.0):
        self.now = now

    def __call__(self):
        return self.now


class WifiTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = self._tmp.name
        self.net_root = os.path.join(self.root, "net")
        self.proc_path = os.path.join(self.root, "wireless")
        os.makedirs(os.path.join(self.net_root, "wlan0", "wireless"))
        self.write_proc(PROC_SAMPLE)

    def write_proc(self, text):
        with open(self.proc_path, "w") as f:
            f.write(text)

    def collector(self, runner, clock=None, default="wlan0"):
        return WifiCollector(net_root=self.net_root, proc_wireless=self.proc_path, runner=runner,
                             clock=clock or FakeClock(), default_iface_fn=lambda: default)


class TestPureHelpers(unittest.TestCase):
    def test_quality_from_dbm_boundaries(self):
        cases = {-50: "Excellent", -51: "Good", -60: "Good", -61: "Fair", -70: "Fair",
                 -71: "Weak", -80: "Weak", -81: "Poor", None: "Unknown"}
        for dbm, word in cases.items():
            self.assertEqual(quality_from_dbm(dbm), word, dbm)

    def test_quality_from_pct_boundaries(self):
        cases = {75: "Excellent", 74: "Good", 55: "Good", 54: "Fair", 40: "Fair",
                 39: "Weak", 25: "Weak", 24: "Poor", None: "Unknown"}
        for pct, word in cases.items():
            self.assertEqual(quality_from_pct(pct), word, pct)

    def test_pct_from_dbm(self):
        self.assertEqual(pct_from_dbm(-42), 100)
        self.assertEqual(pct_from_dbm(-75), 50)
        self.assertEqual(pct_from_dbm(-100), 0)
        self.assertEqual(pct_from_dbm(-120), 0)

    def test_channel_from_freq(self):
        cases = {2412: 1, 2437: 6, 2484: 14, 5180: 36, 5680: 136, 5955: 1, 1000: None, None: None}
        for freq, channel in cases.items():
            self.assertEqual(channel_from_freq(freq), channel, freq)

    def test_parse_proc_wireless(self):
        self.assertEqual(parse_proc_wireless(PROC_SAMPLE),
                         {"wlan0": {"link": 69.0, "dbm": -41.0}})
        self.assertEqual(parse_proc_wireless(PROC_HEADER), {})
        unsigned = PROC_HEADER + " wlan0: 0000   40.  216.  -256        0      0      0     27     12        0\n"
        self.assertEqual(parse_proc_wireless(unsigned)["wlan0"]["dbm"], -40)

    def test_parse_iw_link_connected(self):
        info = parse_iw_link(IW_CONNECTED)
        self.assertTrue(info["connected"])
        self.assertEqual(info["ssid"], "ExampleNet")
        self.assertEqual(info["freq_mhz"], 5180)
        self.assertEqual(info["signal_dbm"], -42)
        self.assertEqual(info["bitrate_mbps"], 866.7)
        self.assertNotIn("02:00:00:aa:bb:cc", repr(info))

    def test_parse_iw_link_not_connected(self):
        info = parse_iw_link("Not connected.\n")
        self.assertFalse(info["connected"])
        self.assertIsNone(info["ssid"])

    def test_parse_iw_link_ssid_with_spaces_and_rx_only(self):
        text = ("Connected to 02:00:00:aa:bb:cc (on wlan0)\n"
                "\tSSID: My Home Net\n"
                "\trx bitrate: 144.4 MBit/s\n")
        info = parse_iw_link(text)
        self.assertEqual(info["ssid"], "My Home Net")
        self.assertEqual(info["bitrate_mbps"], 144.4)

    def test_split_nmcli_line_escapes(self):
        fields = split_nmcli_line(r"*:Cafe\: Free:6:2437 MHz:130 Mbit/s:60:WPA1 WPA2")
        self.assertEqual(len(fields), 7)
        self.assertEqual(fields[1], "Cafe: Free")
        self.assertEqual(split_nmcli_line("a\\\\b:c"), ["a\\b", "c"])

    def test_parse_nmcli_in_use_row(self):
        row = parse_nmcli_wifi(NMCLI_SAMPLE)
        self.assertEqual(row["ssid"], "ExampleNet")
        self.assertEqual(row["security"], "WPA2 WPA3")
        self.assertEqual(row["channel"], 36)
        self.assertEqual(row["freq_mhz"], 5180)
        self.assertEqual(row["rate_mbps"], 866.0)
        self.assertEqual(row["signal_pct"], 78)
        self.assertIsNone(parse_nmcli_wifi(" :Neighbour Net:44:5220 MHz:540 Mbit/s:62:WPA2\n"))
        open_row = parse_nmcli_wifi("*:Open Cafe:1:2412 MHz:130 Mbit/s:55:\n")
        self.assertEqual(open_row["security"], "none")
        self.assertEqual(parse_nmcli_wifi("*:Cafe:6:2437 MHz:130 Mbit/s:60:WPA1 WPA2\n")["security"],
                         "WPA WPA2")


class TestCollector(WifiTestCase):
    def test_no_wifi_interface_gives_none(self):
        shutil.rmtree(os.path.join(self.net_root, "wlan0"))
        os.makedirs(os.path.join(self.net_root, "eth0"))
        self.write_proc(PROC_HEADER)
        coll = self.collector(FakeRunner(iw=IW_CONNECTED, nmcli=NMCLI_SAMPLE))
        coll.run()
        self.assertIsNone(coll.data)
        self.assertIsNone(coll.error)

    def test_connected_full_merge(self):
        coll = self.collector(FakeRunner(iw=IW_CONNECTED, nmcli=NMCLI_SAMPLE))
        coll.refresh()
        d = coll.data
        self.assertEqual(d.iface, "wlan0")
        self.assertEqual(d.ssid, "ExampleNet")
        self.assertEqual(d.signal_dbm, -42)
        self.assertEqual(d.signal_pct, 100)
        self.assertEqual(d.quality, "Excellent")
        self.assertEqual(d.bitrate_mbps, 866.7)
        self.assertEqual(d.freq_mhz, 5180)
        self.assertEqual(d.channel, 36)
        self.assertEqual(d.security, "WPA2 WPA3")

    def test_proc_only_when_iw_and_nmcli_missing(self):
        self.write_proc(PROC_WEAK)
        coll = self.collector(FakeRunner(iw=None, nmcli=None))
        coll.refresh()
        d = coll.data
        self.assertEqual(d.signal_dbm, -61)
        self.assertEqual(d.quality, "Fair")
        self.assertIsNone(d.ssid)
        self.assertIsNone(d.security)
        self.assertIsNone(d.bitrate_mbps)

    def test_nmcli_is_cached_for_30_seconds(self):
        runner = FakeRunner(iw=IW_CONNECTED, nmcli=NMCLI_SAMPLE)
        clock = FakeClock()
        coll = self.collector(runner, clock=clock)
        for t in (0, 5, 10, 15, 20):
            clock.now = t
            coll.refresh()
        self.assertEqual(runner.count("nmcli"), 1)
        self.assertEqual(runner.count("iw"), 5)
        clock.now = 31
        coll.refresh()
        self.assertEqual(runner.count("nmcli"), 2)
        self.assertEqual(runner.count("iw"), 6)

    def test_nmcli_argv_never_rescans(self):
        runner = FakeRunner(iw=IW_CONNECTED, nmcli=NMCLI_SAMPLE)
        self.collector(runner).refresh()
        argv = next(c for c in runner.calls if c[0] == "nmcli")
        i = argv.index("--rescan")
        self.assertEqual(argv[i + 1], "no")
        self.assertIn("wlan0", argv)

    def test_disconnected_keeps_iface_and_unknown_quality(self):
        self.write_proc(PROC_HEADER)
        coll = self.collector(FakeRunner(iw=IW_NOT_CONNECTED, nmcli=None))
        coll.refresh()
        self.assertEqual(coll.data.iface, "wlan0")
        self.assertEqual(coll.data.quality, "Unknown")
        self.assertIsNone(coll.data.ssid)

    def test_history_capped_and_newest_last(self):
        runner = FakeRunner(iw=None, nmcli=None)
        coll = self.collector(runner)
        dbms = [-100 + i // 3 for i in range(130)]
        for dbm in dbms:
            runner.iw = "Connected to 02:00:00:aa:bb:cc (on wlan0)\n\tSSID: ExampleNet\n\tsignal: %d dBm\n" % dbm
            coll.refresh()
        self.assertEqual(len(coll.data.history), 120)
        self.assertEqual(coll.data.history[-1], coll.data.signal_pct)
        self.assertEqual(coll.data.history, tuple(pct_from_dbm(d) for d in dbms[-120:]))

    def test_invalid_iface_name_is_not_used(self):
        shutil.rmtree(os.path.join(self.net_root, "wlan0"))
        os.makedirs(os.path.join(self.net_root, "x;rm", "wireless"))
        self.write_proc(PROC_HEADER)
        runner = FakeRunner(iw=IW_CONNECTED, nmcli=NMCLI_SAMPLE)
        coll = self.collector(runner)
        coll.refresh()
        self.assertIsNone(coll.data)
        self.assertFalse(any("x;rm" in part for call in runner.calls for part in call))


if __name__ == "__main__":
    unittest.main()
