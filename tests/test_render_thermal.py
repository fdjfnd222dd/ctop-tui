"""Tests for the CPU panel thermal lines (ctop/render/thermal.py)."""
from __future__ import annotations

import unittest

from rich.cells import cell_len

from ctop.models import Fan, TempReading, Thermal
from ctop.render.thermal import heat, thermal_lines

CPU = TempReading("CPU", 62.0, "cpu", high=90.0, crit=100.0)
SSD = TempReading("Disk (nvme)", 58.0, "disk")
F1 = Fan("Fan 1", 2400, 5200)


def L(lines):
    return [t.plain.rstrip() for t in lines]


class ThermalLinesTest(unittest.TestCase):
    def test_none_gives_one_dim_line(self):
        lines = thermal_lines(None, 40)
        self.assertEqual(len(lines), 1)
        self.assertTrue(L(lines)[0].startswith("temperature sensors not read yet"))

    def test_cpu_line_text(self):
        lines = L(thermal_lines(Thermal(temps=(CPU,), cpu_temp=62.0), 40))
        self.assertEqual(lines[0], "Temp       62 °C  warm")

    def test_word_very_hot(self):
        lines = L(thermal_lines(Thermal(temps=(CPU,), cpu_temp=95.0), 40))
        self.assertIn("very hot", lines[0])

    def test_missing_cpu_temp(self):
        lines = L(thermal_lines(Thermal(), 40))
        self.assertIn("Temp       not reported", lines)
        self.assertIn("Fan        no fan reported", lines)

    def test_fan_percent(self):
        lines = L(thermal_lines(Thermal(fans=(F1,), cpu_temp=50.0), 40))
        self.assertTrue(any("2400 rpm (46% of max)" in s for s in lines))

    def test_fan_without_max(self):
        lines = L(thermal_lines(Thermal(fans=(Fan("Fan 1", 3000),), cpu_temp=50.0), 40))
        fan_line = [s for s in lines if "3000 rpm" in s]
        self.assertEqual(len(fan_line), 1)
        self.assertNotIn("%", fan_line[0])

    def test_fan_stopped_hot_is_red(self):
        th = Thermal(fans=(Fan("Fan 1", 0, 5200),), cpu_temp=85.0)
        lines = thermal_lines(th, 40)
        fan_line = [t for t in lines if "0 rpm (stopped)" in t.plain][0]
        # kv() puts the DIM label span first, so check the value's span.
        self.assertTrue(any("#ff7b72" in str(s.style) for s in fan_line.spans))

    def test_two_fans_use_their_labels(self):
        fans = (Fan("Fan 1", 4800, 5000), Fan("Fan 2", 4750, 5000))
        lines = L(thermal_lines(Thermal(fans=fans, cpu_temp=91.0), 90))
        self.assertTrue(lines[1].startswith("Fan 1"))
        self.assertTrue(lines[2].startswith("Fan 2"))
        self.assertLessEqual(len(lines), 4)

    def test_hot_spot_shown_when_warm(self):
        lines = L(thermal_lines(Thermal(temps=(CPU, SSD), cpu_temp=62.0), 40))
        hot = [s for s in lines if s.startswith("Hot spot")]
        self.assertEqual(len(hot), 1)
        self.assertTrue(hot[0].startswith("Hot spot   58 °C"))
        self.assertTrue(hot[0].endswith("Disk (nvme)"))

    def test_hot_spot_hidden_when_cool(self):
        cool = TempReading("Disk (nvme)", 38.0, "disk")
        lines = L(thermal_lines(Thermal(temps=(CPU, cool), cpu_temp=62.0), 40))
        self.assertFalse(any(s.startswith("Hot spot") for s in lines))

    def test_hot_spot_ignores_cpu_kind(self):
        lines = L(thermal_lines(Thermal(temps=(CPU,), cpu_temp=62.0), 40))
        self.assertFalse(any(s.startswith("Hot spot") for s in lines))

    def test_width_guarantee(self):
        th = Thermal(temps=(CPU, SSD), fans=(F1,), cpu_temp=62.0)
        for w in (1, 12, 24, 40, 90):
            for t in thermal_lines(th, w):
                self.assertEqual(cell_len(t.plain), w)
                self.assertTrue(t.no_wrap)

    def test_never_more_than_four_lines(self):
        fans = (Fan("Fan 1", 3000, 5000), Fan("Fan 2", 3000, 5000), Fan("Fan 3", 3000, 5000))
        hot = (
            TempReading("Disk A", 70.0, "disk"),
            TempReading("Wi-Fi card", 66.0, "wifi"),
            TempReading("Board", 60.0, "board"),
        )
        lines = thermal_lines(Thermal(temps=(CPU,) + hot, fans=fans, cpu_temp=62.0), 90)
        self.assertLessEqual(len(lines), 4)

    def test_no_none_word_anywhere(self):
        cases = [
            None,
            Thermal(),
            Thermal(fans=(Fan("Fan 1", 0),), cpu_temp=None),
            Thermal(temps=(SSD,), cpu_temp=None),
        ]
        for th in cases:
            for line in L(thermal_lines(th, 60)):
                self.assertNotIn("None", line)


class HeatTest(unittest.TestCase):
    def test_heat_uses_sensor_limits(self):
        self.assertEqual(heat(70, TempReading("x", 70, "cpu", crit=80)), 0.75)
        self.assertEqual(heat(70), (70 - 40) / 55)
        self.assertEqual(heat(20), 0.0)
        self.assertEqual(heat(200), 1.0)


if __name__ == "__main__":
    unittest.main()
