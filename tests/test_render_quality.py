"""Tests for the network-quality lines (ping + Wi-Fi). Run from the repo root:
    python -m unittest tests.test_render_quality -v
Fixtures use documentation addresses and the example SSID only.
"""
import os
import sys
import unittest

from rich.cells import cell_len

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ctop.models import PingData, PingResult, WifiData  # noqa: E402
from ctop.render.quality import quality_lines, sparkline  # noqa: E402

R = PingResult("Router", "192.0.2.1", 2.0, 2.0, 0.4, 0.0, (2.0, 2.0, 3.0))
I = PingResult("Internet", "198.51.100.7", 18.0, 20.0, 3.0, 3.0, (18.0, None, 20.0, 19.0))
WF = WifiData("wlan0", "ExampleNet", 72, -58, 144.0, 5180, 36, "WPA2", "Good", (70, 72, 72))


def _const(_v):
    return "green"


def _find(lines, prefix):
    for t in lines:
        if t.plain.startswith(prefix):
            return t.plain.rstrip()
    raise AssertionError(f"no line starts with {prefix!r}: {[t.plain for t in lines]}")


class SparklineTests(unittest.TestCase):
    def test_sparkline_exact_width(self):
        t = sparkline([1, 2, 3], 10, 8, _const)
        self.assertEqual(cell_len(t.plain), 8)
        self.assertTrue(t.plain.startswith("     "))

    def test_sparkline_levels(self):
        t = sparkline([0, 50, 100], 100, 3, _const)
        self.assertEqual(t.plain, "▁▄█")

    def test_sparkline_lost_is_red_gap(self):
        t = sparkline([5.0, None, 5.0], 10, 3, _const)
        self.assertEqual(t.plain, "▄×▄")
        self.assertEqual(t.plain[1], "×")
        self.assertTrue(any(s.start <= 1 < s.end and "#ff7b72" in str(s.style) for s in t.spans))

    def test_sparkline_truncates_to_newest(self):
        values = list(range(100))
        t = sparkline(values, 100, 10, _const)
        self.assertEqual(len(t.plain), 10)
        self.assertEqual(t.plain, sparkline(values[-10:], 100, 10, _const).plain)


class QualityLineTests(unittest.TestCase):
    def test_internet_plain_words(self):
        lines = quality_lines(PingData(R, I), WF, 40)
        self.assertTrue(_find(lines, "Internet").startswith("Internet  18 ms, 3% of packets lost"))

    def test_router_no_loss(self):
        lines = quality_lines(PingData(R, I), WF, 40)
        self.assertTrue(_find(lines, "Router").startswith("Router    2 ms, no packets lost"))

    def test_no_reply_wording(self):
        bad = PingResult("Internet", "198.51.100.7", None, 20.0, 3.0, 12.0, (18.0, None))
        lines = quality_lines(PingData(R, bad), WF, 40)
        self.assertIn("no reply, 12% of packets lost", _find(lines, "Internet"))

    def test_waiting_for_first_reply(self):
        fresh = PingResult("Router", "192.0.2.1", None, None, None, 0.0, ())
        lines = quality_lines(PingData(fresh, I), None, 40)
        self.assertIn("waiting for first reply", _find(lines, "Router"))
        self.assertNotIn("packets lost", _find(lines, "Router"))

    def test_wifi_line(self):
        lines = quality_lines(None, WF, 40)
        self.assertEqual(_find(lines, "Wi-Fi     "), "Wi-Fi     Good, -58 dBm, link 144 Mbps")
        wide = quality_lines(None, WF, 90)
        self.assertTrue(_find(wide, "Wi-Fi     ").endswith("ExampleNet"))

    def test_wifi_unknown_quality(self):
        unk = WifiData("wlan0", None, None, None, None, None, None, None, "Unknown", ())
        lines = quality_lines(None, unk, 40)
        self.assertIn("signal unknown", _find(lines, "Wi-Fi     "))

    def test_none_sections(self):
        lines = [t.plain.rstrip() for t in quality_lines(None, None, 40)]
        self.assertEqual(lines, ["network checks are starting...", "Wi-Fi     not connected"])
        empty = quality_lines(PingData(None, None), None, 40)
        self.assertIn("no default gateway found", _find(empty, "Router"))
        self.assertIn("not measured yet", _find(empty, "Internet"))

    def test_full_is_six_lines_max(self):
        self.assertEqual(len(quality_lines(PingData(R, I), WF, 40)), 6)
        self.assertEqual(len(quality_lines(PingData(R, I), WF, 12)), 3)

    def test_width_guarantee(self):
        for w in (1, 12, 40, 90):
            for t in quality_lines(PingData(R, I), WF, w):
                self.assertEqual(cell_len(t.plain), w)
                self.assertTrue(t.no_wrap)

    def test_no_None_text(self):
        for t in quality_lines(PingData(None, None), None, 90):
            self.assertNotIn("None", t.plain)
        for t in quality_lines(PingData(R, None), WF, 90):
            self.assertNotIn("None", t.plain)


if __name__ == "__main__":
    unittest.main()
