"""Tests for the one-line alert banner (ctop/render/alerts.py). No machine state needed."""
from __future__ import annotations

import unittest

from rich.cells import cell_len

from ctop.models import Alert
from ctop.render.alerts import CRIT_STYLE, INFO_STYLE, WARN_STYLE, banner
from ctop.ui import DIM

CRIT = Alert("crit", "battery-low", "Battery is at 9% and not charging - plug in the charger soon")
WARN = Alert("warn", "ram-high", "RAM is 91% full - close a big program")
INFO = Alert("info", "wifi-weak", "Wi-Fi signal is weak")


class BannerTests(unittest.TestCase):

    def test_none_when_empty(self):
        self.assertIsNone(banner([], 90))
        self.assertIsNone(banner([CRIT], 0))

    def test_single_alert_text(self):
        self.assertEqual(banner([WARN], 90).plain.rstrip(), " ! RAM is 91% full - close a big program")

    def test_most_severe_wins(self):
        plain = banner([INFO, WARN, CRIT], 120).plain
        self.assertTrue(plain.startswith(" ! Battery is at 9%"))
        self.assertTrue(plain.rstrip().endswith("(+2 more)"))

    def test_first_of_equal_severity(self):
        other = Alert("warn", "disk-full", "Disk is almost full")
        self.assertTrue(banner([WARN, other], 90).plain.startswith(" ! RAM is 91%"))

    def test_styles(self):
        self.assertEqual(banner([CRIT], 90).style, CRIT_STYLE)
        self.assertEqual(banner([WARN], 90).style, WARN_STYLE)
        self.assertEqual(banner([INFO], 90).style, INFO_STYLE)
        self.assertEqual(banner([Alert("fatal", "x", "Odd level")], 90).style, INFO_STYLE)

    def test_info_uses_i_marker(self):
        self.assertTrue(banner([INFO], 90).plain.startswith(" i "))

    def test_info_style_is_dim(self):
        self.assertEqual(INFO_STYLE, DIM)

    def test_exact_width_and_no_wrap(self):
        for w in (1, 5, 10, 40, 90, 200):
            t = banner([CRIT, WARN], w)
            self.assertEqual(cell_len(t.plain), w)
            self.assertTrue(t.no_wrap)

    def test_shortened_keeps_suffix(self):
        plain = banner([CRIT, WARN], 40).plain.rstrip()
        self.assertTrue(plain.endswith("(+1 more)"))
        self.assertIn("…", plain)

    def test_very_narrow_drops_suffix(self):
        t = banner([CRIT, WARN], 8)
        self.assertEqual(cell_len(t.plain), 8)
        self.assertNotIn("…", t.plain)
        self.assertTrue(t.plain.startswith(" ! "))

    def test_whitespace_and_newlines_collapsed(self):
        self.assertIn("a b", banner([Alert("warn", "k", "a\n  b")], 90).plain)

    def test_empty_text_uses_key(self):
        self.assertIn("ram-high", banner([Alert("warn", "ram-high", "")], 90).plain)

    def test_no_None_text(self):
        for alerts in ([CRIT], [WARN, INFO], [Alert("info", "k", "")]):
            self.assertNotIn("None", banner(alerts, 90).plain)


if __name__ == "__main__":
    unittest.main()
