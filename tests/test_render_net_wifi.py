"""Tests for ctop/render/net_wifi.py - Wi-Fi list, saved & VPN, link details, events."""
from __future__ import annotations

import unittest

from rich.text import Text

from ctop.models import (ChannelLoad, ChannelReport, LinkInfo, NetEvent, NmState, SavedConnection,
                         VpnPeer, VpnStatus, VpnTunnel, WifiData, WifiNetwork)
from ctop.render.net_wifi import (CURSOR_ON, ago, events_lines, link_lines, saved_lines, signal_bars,
                                  window, wifi_list_lines)
from ctop.ui import TRACK

WIDTHS = (36, 44, 68, 100)
HEIGHTS = (3, 8, 13)
NOW = 1_700_000_000.0


def net(ssid="ExampleNet", in_use=False, saved=False, aps=1, security="WPA2", band="5 GHz",
        channel=136, signal=80):
    return WifiNetwork(ssid=ssid, bssid="02:00:00:AA:BB:CC", channel=channel, freq_mhz=5680,
                       band=band, rate_mbps=866, signal_pct=signal, security=security,
                       in_use=in_use, saved=saved, aps=aps)


def nm_with(networks=(), saved=(), **kw):
    base = dict(available=True, wifi_enabled=True, wifi_hw=True, wifi_device="wlan0",
                networks=tuple(networks), saved=tuple(saved), scanned_at=NOW)
    base.update(kw)
    return NmState(**base)


def conn(name, active=False, state="", kind="wifi", auto=True, priority=0, last=None):
    return SavedConnection(name=name, uuid="0" * 8, kind=kind, device="wlan0" if active else None,
                           active=active, state=state, autoconnect=auto, priority=priority,
                           last_used=last)


def link(connected=True, random_mac=True, **kw):
    base = dict(iface="wlan0", connected=connected, bssid="02:00:00:AA:BB:CC", freq_mhz=5680,
                channel=136, band="5 GHz", width_mhz=80, signal_dbm=-42, rx_mbps=867.0, tx_mbps=650.0,
                standard="Wi-Fi 5", tx_power_dbm=22.0, mac="02:00:00:AA:BB:CC", random_mac=random_mac,
                mtu=1500)
    base.update(kw)
    return LinkInfo(**base)


def wifi(quality="Excellent", ssid="ExampleNet", channel=136):
    return WifiData(iface="wlan0", ssid=ssid, signal_pct=80, signal_dbm=-42, bitrate_mbps=866.0,
                    freq_mhz=5680, channel=channel, security="WPA2", quality=quality)


def plain(lines):
    return [ln.plain.rstrip() for ln in lines]


def check_contract(tc, lines, w, h):
    tc.assertLessEqual(len(lines), h)
    for ln in lines:
        tc.assertIsInstance(ln, Text)
        tc.assertTrue(ln.no_wrap)
        tc.assertLessEqual(ln.cell_len, w)


class ContractTests(unittest.TestCase):
    """Every public renderer: no wrapping, never wider than w, never taller than h, no crash on None."""

    def test_all_functions_respect_size(self):
        samples = [
            ("wifi", lambda w, h: wifi_list_lines(nm_with([net(in_use=True, aps=2), net("Cafe: Free", security="")]),
                                                  0, True, w, h)),
            ("wifi-none", lambda w, h: wifi_list_lines(None, 0, False, w, h)),
            ("saved", lambda w, h: saved_lines(nm_with(saved=[conn("ExampleNet", True, "activated", priority=5)]),
                                               VpnStatus(tailscale="Running", tailscale_ip="100.64.0.1"),
                                               0, True, w, h, NOW)),
            ("saved-none", lambda w, h: saved_lines(None, None, 0, False, w, h, NOW)),
            ("link", lambda w, h: link_lines(link(), wifi(), ChannelReport(loads=(
                ChannelLoad("5 GHz", 136, 2, 80, True),), tip="Channel 6 is crowded."), 2, w, h)),
            ("link-none", lambda w, h: link_lines(None, None, None, None, w, h)),
            ("events", lambda w, h: events_lines(
                (NetEvent(NOW - 5, "warn", "Wi-Fi dropped for a moment"),) * 12, w, h, NOW)),
            ("events-none", lambda w, h: events_lines((), w, h, NOW)),
        ]
        for name, fn in samples:
            for w in WIDTHS:
                for h in HEIGHTS:
                    with self.subTest(fn=name, w=w, h=h):
                        check_contract(self, fn(w, h), w, h)


class SignalAndTimeTests(unittest.TestCase):
    def _count_filled(self, t):
        # Unfilled cells are drawn in the track colour; filled ones use a gradient colour.
        self.assertEqual(t.plain, "▂▄▆█")
        styles = [""] * len(t.plain)
        for s in t.spans:
            for i in range(s.start, s.end):
                styles[i] = str(s.style)
        return sum(1 for st in styles if st and st != TRACK)

    def test_signal_thresholds(self):
        self.assertEqual(self._count_filled(signal_bars(100)), 4)
        self.assertEqual(self._count_filled(signal_bars(75)), 4)
        self.assertEqual(self._count_filled(signal_bars(74)), 3)
        self.assertEqual(self._count_filled(signal_bars(50)), 3)
        self.assertEqual(self._count_filled(signal_bars(49)), 2)
        self.assertEqual(self._count_filled(signal_bars(25)), 2)
        self.assertEqual(self._count_filled(signal_bars(24)), 1)
        self.assertEqual(self._count_filled(signal_bars(1)), 1)
        self.assertEqual(self._count_filled(signal_bars(0)), 0)
        self.assertEqual(self._count_filled(signal_bars(None)), 0)

    def test_ago_buckets(self):
        self.assertEqual(ago(None, NOW), "never")
        self.assertEqual(ago(NOW - 5, NOW), "now")
        self.assertEqual(ago(NOW - 59, NOW), "now")
        self.assertEqual(ago(NOW - 60, NOW), "1m")
        self.assertEqual(ago(NOW - 12 * 60, NOW), "12m")
        self.assertEqual(ago(NOW - 5 * 3600, NOW), "5h")
        self.assertEqual(ago(NOW - 3 * 86400, NOW), "3d")

    def test_window_keeps_cursor_visible(self):
        for cursor in (0, 15, 29):
            start, end = window(30, cursor, 10)
            self.assertLessEqual(start, cursor)
            self.assertLess(cursor, end)
            self.assertEqual(end - start, 10)
        self.assertEqual(window(30, 15, 10), (10, 20))
        self.assertEqual(window(30, 29, 10), (20, 30))
        self.assertEqual(window(5, 3, 10), (0, 5))
        self.assertEqual(window(0, 0, 10), (0, 0))


class WifiListTests(unittest.TestCase):
    def setUp(self):
        self.nets = [net("ExampleNet", in_use=True, aps=2),
                     net("Neighbour Net", saved=True, band="2.4 GHz", channel=6, security="WPA2"),
                     net("Cafe: Free", band="2.4 GHz", channel=6, security=""),
                     net("", band="2.4 GHz", channel=11, security="WPA2", signal=20)]

    def test_rows_and_words(self):
        text = "\n".join(plain(wifi_list_lines(nm_with(self.nets), 0, True, 68, 13)))
        self.assertIn("✓ connected", text)
        self.assertIn("×2", text)
        self.assertIn("saved", text)
        self.assertIn("open ⚠", text)
        self.assertIn("(hidden network)", text)

    def test_802_1x_is_enterprise(self):
        nets = [net("Corp", security="802.1X")]
        self.assertIn("enterprise", "\n".join(plain(wifi_list_lines(nm_with(nets), 0, False, 68, 8))))

    def test_cursor_row_has_background_across_the_line(self):
        lines = wifi_list_lines(nm_with(self.nets), 1, True, 68, 13)
        # line 0 is the header, so the cursor on network 1 is line 2.
        row = lines[2]
        self.assertIn("Neighbour Net", row.plain)
        self.assertEqual(row.cell_len, 68)
        styles = [str(s.style) for s in row.spans]
        self.assertIn(CURSOR_ON, styles)
        self.assertTrue(any(s.start == 0 and s.end == len(row.plain) for s in row.spans
                            if str(s.style) == CURSOR_ON))

    def test_unfocused_cursor_uses_dim_background(self):
        lines = wifi_list_lines(nm_with(self.nets), 0, False, 68, 13)
        self.assertIn("on #1a1f27", [str(s.style) for s in lines[1].spans])

    def test_states(self):
        cases = {
            "looking for Wi-Fi networks": wifi_list_lines(None, 0, True, 68, 8),
            "Wi-Fi controls need NetworkManager": wifi_list_lines(
                NmState(available=False, reason="NetworkManager is not running."), 0, True, 68, 8),
            "No Wi-Fi hardware found": wifi_list_lines(nm_with(wifi_device=None), 0, True, 68, 8),
            "hardware switch": wifi_list_lines(nm_with(wifi_hw=False), 0, True, 68, 8),
            "Wi-Fi is off": wifi_list_lines(nm_with(wifi_enabled=False), 0, True, 68, 8),
            "No networks found yet": wifi_list_lines(nm_with([]), 0, True, 68, 8),
        }
        for needle, lines in cases.items():
            with self.subTest(state=needle):
                self.assertIn(needle, "\n".join(plain(lines)))

    def test_scrolls_with_cursor_visible(self):
        many = [net(f"Net {i}") for i in range(30)]
        lines = wifi_list_lines(nm_with(many), 29, True, 68, 6)
        self.assertIn("Net 29", "\n".join(plain(lines)))
        self.assertLessEqual(len(lines), 6)


class SavedTests(unittest.TestCase):
    def test_active_manual_priority_and_ago(self):
        saved = [conn("ExampleNet", True, "activated", priority=5, last=NOW),
                 conn("Neighbour Net", last=NOW - 2 * 86400),
                 conn("Office VPN", kind="vpn", auto=False, last=NOW - 5 * 3600)]
        lines = saved_lines(nm_with(saved=saved), None, 0, True, 44, 13, NOW)
        text = plain(lines)
        self.assertTrue(text[0].startswith("● ExampleNet p5"))
        self.assertIn("manual", text[2])
        self.assertIn("VPN", text[2])
        self.assertIn("5h", text[2])
        self.assertIn("2d", text[1])
        self.assertIn("now", text[0])

    def test_connecting_dot(self):
        saved = [conn("Cafe", True, "activating")]
        self.assertTrue(plain(saved_lines(nm_with(saved=saved), None, 0, True, 44, 8, NOW))[0].startswith("◐"))

    def test_vpn_section_running_and_stopped(self):
        vpn = VpnStatus(tailscale="Running", tailscale_ip="100.64.0.1",
                        peers=(VpnPeer("a", "100.64.0.2", "linux", True, False),
                               VpnPeer("b", "100.64.0.3", "linux", False, False),
                               VpnPeer("c", None, "linux", True, False)),
                        tunnels=(VpnTunnel("wg0", "WireGuard", True, "10.0.0.2"),))
        text = "\n".join(plain(saved_lines(nm_with(saved=[conn("ExampleNet", True, "activated")]),
                                           vpn, 0, True, 68, 13, NOW)))
        self.assertIn("100.64.0.1", text)
        self.assertIn("2/3 online", text)
        self.assertIn("wg0  WireGuard  up  10.0.0.2", text)
        stopped = "\n".join(plain(saved_lines(nm_with(saved=[conn("X")]),
                                              VpnStatus(tailscale="Stopped"), 0, True, 68, 13, NOW)))
        self.assertIn("off (Stopped)", stopped)

    def test_no_rule_without_vpn(self):
        text = plain(saved_lines(nm_with(saved=[conn("ExampleNet")]), None, 0, True, 44, 8, NOW))
        self.assertFalse(any(ln.startswith("─") for ln in text))

    def test_empty_and_unavailable(self):
        self.assertIn("No saved networks yet.", "\n".join(plain(saved_lines(nm_with(), None, 0, True, 44, 8, NOW))))
        self.assertIn("looking for Wi-Fi networks", "\n".join(plain(saved_lines(None, None, 0, True, 44, 8, NOW))))


class LinkTests(unittest.TestCase):
    def test_full_sample(self):
        report = ChannelReport(loads=(ChannelLoad("2.4 GHz", 1, 2, 60, False),
                                      ChannelLoad("2.4 GHz", 6, 10, 80, False),
                                      ChannelLoad("5 GHz", 136, 1, 40, True)),
                               tip="Channel 6 is crowded (10 networks nearby).")
        lines = link_lines(link(), wifi(), report, 2, 44, 8)
        text = "\n".join(plain(lines))
        self.assertIn("Wi-Fi 5", text)
        self.assertIn("136", text)
        self.assertIn("private", text)
        self.assertIn("Excellent", text)
        self.assertIn("Tip:", text)

    def test_crowded_counts_are_coloured(self):
        report = ChannelReport(loads=(ChannelLoad("2.4 GHz", 6, 10, 80, False),))
        lines = link_lines(link(), wifi(), report, None, 44, 8)
        nearby = [ln for ln in lines if ln.plain.startswith("Nearby")][0]
        styles = {str(s.style) for s in nearby.spans}
        self.assertIn("#ff7b72", styles)

    def test_hardware_address_when_not_random(self):
        text = "\n".join(plain(link_lines(link(random_mac=False), wifi(), None, None, 44, 8)))
        self.assertIn("hardware address", text)

    def test_disconnected(self):
        text = plain(link_lines(link(connected=False), None, None, None, 44, 8))
        self.assertEqual(text[0], "Wi-Fi is not connected.")
        self.assertTrue(text[1].startswith("Your MAC"))

    def test_none(self):
        self.assertEqual(plain(link_lines(None, None, None, None, 44, 8)), ["No Wi-Fi link."])

    def test_height_three_keeps_first_three_lines(self):
        full = plain(link_lines(link(), wifi(), None, None, 44, 8))
        short = plain(link_lines(link(), wifi(), None, None, 44, 3))
        self.assertEqual(short, full[:3])


class EventTests(unittest.TestCase):
    def test_newest_first_with_icons(self):
        events = (NetEvent(NOW - 100, "info", "Connected to ExampleNet"),
                  NetEvent(NOW - 10, "crit", "Lost the link"),
                  NetEvent(NOW - 5, "warn", "Lag spike"))
        lines = events_lines(events, 68, 8, NOW)
        text = plain(lines)
        self.assertIn("Lag spike", text[0])
        self.assertIn("Lost the link", text[1])
        self.assertIn("Connected", text[2])
        self.assertIn("!", text[0])
        self.assertIn("✗", text[1])
        self.assertIn("·", text[2])

    def test_more_than_height_shows_older_line(self):
        events = tuple(NetEvent(NOW - 100 + i, "info", f"event {i}") for i in range(12))  # oldest first
        text = plain(events_lines(events, 68, 4, NOW))
        self.assertEqual(len(text), 4)
        self.assertIn("event 11", text[0])
        self.assertIn("… 9 older", text[3])

    def test_old_events_show_day(self):
        text = plain(events_lines((NetEvent(NOW - 30 * 3600, "info", "old"),), 68, 3, NOW))[0]
        self.assertNotRegex(text, r"^\d\d:\d\d:\d\d")

    def test_empty_message(self):
        self.assertIn("Nothing yet", plain(events_lines((), 68, 3, NOW))[0])


if __name__ == "__main__":
    unittest.main()
