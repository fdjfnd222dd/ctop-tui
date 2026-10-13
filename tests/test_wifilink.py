"""Tests for ctop/collectors/wifilink.py. iw is a fake runner and /sys is a temp directory: no radio, no real commands."""
from __future__ import annotations

import os
import tempfile
import unittest

from ctop.collectors.wifilink import (
    LinkCollector, channel_report, parse_iw_info, parse_iw_link_full, wifi_iface,
)
from ctop.models import ChannelReport, LinkInfo, WifiNetwork

LINK_SAMPLE = (
    "Connected to 02:00:00:aa:bb:cc (on wlan0)\n"
    "\tSSID: ExampleNet\n"
    "\tfreq: 5680.0\n"
    "\tsignal: -42 dBm\n"
    "\trx bitrate: 866.7 MBit/s VHT-MCS 9 80MHz short GI VHT-NSS 2\n"
    "\ttx bitrate: 650.0 MBit/s VHT-MCS 7 80MHz short GI VHT-NSS 2\n"
)
HE_LINK = (
    "Connected to 02:00:00:aa:bb:cc (on wlan0)\n"
    "\tSSID: Lab\n"
    "\tfreq: {freq}\n"
    "\tsignal: -50 dBm\n"
    "\trx bitrate: 1200.9 MBit/s 80MHz HE-MCS 11 HE-NSS 2 HE-GI 0.8\n"
    "\ttx bitrate: 1200.9 MBit/s 80MHz HE-MCS 11 HE-NSS 2 HE-GI 0.8\n"
)
INFO_SAMPLE = (
    "Interface wlan0\n"
    "\tifindex 3\n"
    "\ttype managed\n"
    "\tchannel 136 (5680 MHz), width: 80 MHz, center1: 5690 MHz\n"
    "\ttxpower 22.00 dBm\n"
)
NOT_CONNECTED = "Not connected.\n"


def _net(ssid, band, channel, aps=1, signal=50, in_use=False, saved=False):
    freq = {"2.4 GHz": 2407 + 5 * channel, "5 GHz": 5000 + 5 * channel, "6 GHz": 5950 + 5 * channel}.get(band)
    return WifiNetwork(ssid=ssid, bssid="AA:BB:CC:DD:EE:01", channel=channel, freq_mhz=freq, band=band,
                       rate_mbps=100, signal_pct=signal, security="WPA2", in_use=in_use, saved=saved, aps=aps)


class ParseLinkTests(unittest.TestCase):
    def test_sample(self):
        p = parse_iw_link_full(LINK_SAMPLE)
        self.assertTrue(p["connected"])
        self.assertEqual(p["bssid"], "02:00:00:AA:BB:CC")
        self.assertEqual(p["freq_mhz"], 5680)
        self.assertEqual(p["signal_dbm"], -42)
        self.assertEqual(p["rx_mbps"], 866.7)
        self.assertEqual(p["tx_mbps"], 650.0)
        self.assertEqual(p["width_mhz"], 80)
        self.assertEqual(p["standard"], "Wi-Fi 5")

    def test_he_is_wifi_6(self):
        p = parse_iw_link_full(HE_LINK.format(freq="2412.0"))
        self.assertEqual(p["standard"], "Wi-Fi 6")
        self.assertEqual(p["rx_mbps"], 1200.9)
        self.assertEqual(p["width_mhz"], 80)

    def test_he_on_6ghz_is_wifi_6e(self):
        p = parse_iw_link_full(HE_LINK.format(freq="5955.0"))
        self.assertEqual(p["standard"], "Wi-Fi 6E")

    def test_ht_mcs_is_wifi_4(self):
        text = ("Connected to 02:00:00:aa:bb:cc (on wlan0)\n\tfreq: 2412\n"
                "\trx bitrate: 72.2 MBit/s MCS 7 short GI\n")
        self.assertEqual(parse_iw_link_full(text)["standard"], "Wi-Fi 4")

    def test_not_connected(self):
        p = parse_iw_link_full(NOT_CONNECTED)
        self.assertFalse(p["connected"])
        self.assertIsNone(p["bssid"])

    def test_empty_text(self):
        self.assertFalse(parse_iw_link_full("")["connected"])

    def test_info_sample(self):
        info = parse_iw_info(INFO_SAMPLE)
        self.assertEqual(info["tx_power_dbm"], 22.0)
        self.assertEqual(info["width_mhz"], 80)
        self.assertEqual(info["channel"], 136)

    def test_info_empty(self):
        self.assertEqual(parse_iw_info(""), {"tx_power_dbm": None, "width_mhz": None, "channel": None})


class IfaceTests(unittest.TestCase):
    def test_wifi_iface_finds_wireless_dir(self):
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "eth0"))
            os.makedirs(os.path.join(root, "wlan0", "wireless"))
            self.assertEqual(wifi_iface(root), "wlan0")

    def test_no_wireless_returns_none(self):
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "eth0"))
            self.assertIsNone(wifi_iface(root))

    def test_missing_root_returns_none(self):
        self.assertIsNone(wifi_iface("/nonexistent-ctop-test-root"))

    def test_bad_name_ignored(self):
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "x;rm", "wireless"))
            self.assertIsNone(wifi_iface(root))


class FakeIw:
    def __init__(self, link=LINK_SAMPLE, info=INFO_SAMPLE):
        self.link = link
        self.info = info
        self.calls = []

    def __call__(self, argv, timeout):
        self.calls.append(list(argv))
        if argv[-1] == "link":
            return self.link
        if argv[-1] == "info":
            return self.info
        return None


def _make_root(root, mac="02:00:00:aa:bb:cc", mtu="1500"):
    base = os.path.join(root, "wlan0")
    os.makedirs(os.path.join(base, "wireless"))
    with open(os.path.join(base, "address"), "w") as fh:
        fh.write(mac + "\n")
    with open(os.path.join(base, "mtu"), "w") as fh:
        fh.write(mtu + "\n")


class CollectorTests(unittest.TestCase):
    def test_full_link_info(self):
        with tempfile.TemporaryDirectory() as root:
            _make_root(root)
            coll = LinkCollector(runner=FakeIw(), net_root=root)
            coll.refresh()
            link = coll.data
            self.assertIsInstance(link, LinkInfo)
            self.assertTrue(link.connected)
            self.assertEqual(link.iface, "wlan0")
            self.assertEqual(link.bssid, "02:00:00:AA:BB:CC")
            self.assertEqual(link.freq_mhz, 5680)
            self.assertEqual(link.channel, 136)
            self.assertEqual(link.band, "5 GHz")
            self.assertEqual(link.width_mhz, 80)
            self.assertEqual(link.signal_dbm, -42)
            self.assertEqual(link.rx_mbps, 866.7)
            self.assertEqual(link.tx_mbps, 650.0)
            self.assertEqual(link.standard, "Wi-Fi 5")
            self.assertEqual(link.tx_power_dbm, 22.0)
            self.assertEqual(link.mac, "02:00:00:aa:bb:cc")
            self.assertTrue(link.random_mac)
            self.assertEqual(link.mtu, 1500)

    def test_info_width_wins_over_bitrate_token(self):
        with tempfile.TemporaryDirectory() as root:
            _make_root(root)
            info = "\tchannel 136 (5680 MHz), width: 160 MHz, center1: 5650 MHz\n"
            coll = LinkCollector(runner=FakeIw(info=info), net_root=root)
            coll.refresh()
            self.assertEqual(coll.data.width_mhz, 160)

    def test_he_6ghz_collector_reports_6e(self):
        with tempfile.TemporaryDirectory() as root:
            _make_root(root)
            coll = LinkCollector(runner=FakeIw(link=HE_LINK.format(freq="5955.0")), net_root=root)
            coll.refresh()
            self.assertEqual(coll.data.standard, "Wi-Fi 6E")
            self.assertEqual(coll.data.band, "6 GHz")

    def test_no_wireless_dir_gives_none(self):
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "eth0"))
            runner = FakeIw()
            coll = LinkCollector(runner=runner, net_root=root)
            coll.refresh()
            self.assertIsNone(coll.data)
            self.assertEqual(runner.calls, [])

    def test_runner_returns_none_keeps_sysfs_fields(self):
        with tempfile.TemporaryDirectory() as root:
            _make_root(root)
            coll = LinkCollector(runner=lambda argv, t: None, net_root=root)
            coll.refresh()
            self.assertFalse(coll.data.connected)
            self.assertEqual(coll.data.mac, "02:00:00:aa:bb:cc")
            self.assertEqual(coll.data.mtu, 1500)
            self.assertTrue(coll.data.random_mac)

    def test_iw_commands_use_argv_with_interface(self):
        with tempfile.TemporaryDirectory() as root:
            _make_root(root)
            runner = FakeIw()
            LinkCollector(runner=runner, net_root=root).refresh()
            self.assertIn(["iw", "dev", "wlan0", "link"], runner.calls)


def _report_nets():
    """Me on channel 1, with seven other 2.4 GHz networks on channels 2-5 that overlap it."""
    nets = [_net("Mine", "2.4 GHz", 1, in_use=True, signal=70)]
    for i, ch in enumerate((2, 3, 4, 5, 2, 3, 4)):
        nets.append(_net("Other%d" % i, "2.4 GHz", ch, signal=40 + i))
    return nets


class ChannelReportTests(unittest.TestCase):
    def test_empty_input(self):
        self.assertEqual(channel_report([], None), ChannelReport((), ""))

    def test_crowded_2g_names_a_free_channel(self):
        nets = _report_nets()
        link = LinkInfo(iface="wlan0", connected=True, band="2.4 GHz", channel=1)
        rep = channel_report(nets, link)
        self.assertEqual(rep.tip, "Channel 1 is crowded (8 networks nearby). "
                                  "If your router lets you, pick channel 11.")
        self.assertLess(len(rep.tip), 90)

    def test_dual_band_ssid_gives_5ghz_tip(self):
        nets = [_net("Home", "2.4 GHz", 6, in_use=True), _net("Home", "5 GHz", 36)]
        rep = channel_report(nets, None)
        self.assertIn("5 GHz", rep.tip)
        self.assertIn("usually faster", rep.tip)

    def test_quiet_channel(self):
        nets = [_net("Mine", "5 GHz", 36, in_use=True)]
        self.assertEqual(channel_report(nets, None).tip, "Your channel is quiet - interference is not the problem.")

    def test_shared_5ghz_channel(self):
        nets = [_net("Mine", "5 GHz", 36, in_use=True)] + [_net("N%d" % i, "5 GHz", 36) for i in range(4)]
        self.assertIn("shared by 5 networks", channel_report(nets, None).tip)

    def test_no_in_use_and_no_link_gives_no_tip(self):
        nets = [_net("Other", "5 GHz", 36)]
        self.assertEqual(channel_report(nets, None).tip, "")

    def test_loads_sorted_mine_flags_and_counts_use_aps(self):
        nets = [
            _net("Mine", "5 GHz", 36, aps=2, signal=60, in_use=True),
            _net("A", "2.4 GHz", 11, aps=3, signal=30),
            _net("B", "2.4 GHz", 1, aps=1, signal=80),
            _net("C", "6 GHz", 5, aps=1, signal=20),
        ]
        rep = channel_report(nets, None)
        keys = [(c.band, c.channel) for c in rep.loads]
        self.assertEqual(keys, [("2.4 GHz", 1), ("2.4 GHz", 11), ("5 GHz", 36), ("6 GHz", 5)])
        by_key = {(c.band, c.channel): c for c in rep.loads}
        self.assertEqual(by_key[("5 GHz", 36)].networks, 2)
        self.assertTrue(by_key[("5 GHz", 36)].mine)
        self.assertFalse(by_key[("2.4 GHz", 1)].mine)
        self.assertEqual(by_key[("2.4 GHz", 1)].strongest_pct, 80)

    def test_unknown_band_and_missing_channel_ignored(self):
        nets = [_net("Odd", "?", 3), WifiNetwork(ssid="NoCh", bssid="AA:BB:CC:DD:EE:02", channel=None,
                                                 freq_mhz=None, band="5 GHz", rate_mbps=None, signal_pct=5,
                                                 security="", in_use=False, saved=False)]
        self.assertEqual(channel_report(nets, None), ChannelReport((), ""))


if __name__ == "__main__":
    unittest.main()
