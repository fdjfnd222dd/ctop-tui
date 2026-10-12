"""Tests for ctop/battery.py. Fake sysfs trees in temp dirs only; never /sys."""
from __future__ import annotations

import os
import tempfile
import unittest

from ctop.battery import read_battery


def make(root, name, **files):
    path = os.path.join(root, name)
    os.makedirs(path, exist_ok=True)
    for key, value in files.items():
        with open(os.path.join(path, key), "w") as f:
            f.write(f"{value}\n")
    return path


class BatteryReaderTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = tmp.name

    def test_charge_based_pack(self):
        make(self.root, "BAT0", type="Battery", status="Discharging", present=1,
             capacity=87, charge_full_design=3333000, charge_full=3333000,
             charge_now=2889000, current_now=1389000, voltage_now=12112000,
             voltage_min_design=11400000, cycle_count=0)
        b = read_battery(self.root)
        self.assertEqual(b.pct, 87)
        self.assertEqual(b.status, "Discharging")
        self.assertAlmostEqual(b.wh_now, 32.93, places=2)
        self.assertAlmostEqual(b.wh_full, 38.0, places=2)
        self.assertAlmostEqual(b.wh_design, 38.0, places=2)
        self.assertAlmostEqual(b.watts, 16.82, places=2)
        self.assertAlmostEqual(b.volts, 12.112, places=3)
        self.assertEqual(b.cycles, 0)

    def test_energy_based_pack_prefers_power_now(self):
        make(self.root, "BAT0", type="Battery", status="Charging", capacity=50,
             energy_full_design=50000000, energy_full=40000000, energy_now=20000000,
             power_now=9000000, current_now=1, voltage_now=11000000)
        b = read_battery(self.root)
        self.assertEqual(b.wh_design, 50.0)
        self.assertEqual(b.wh_full, 40.0)
        self.assertEqual(b.wh_now, 20.0)
        self.assertEqual(b.watts, 9.0)
        self.assertEqual(b.status, "Charging")

    def test_negative_current_is_magnitude(self):
        make(self.root, "BAT0", type="Battery", current_now=-2000000, voltage_now=10000000)
        self.assertEqual(read_battery(self.root).watts, 20.0)

    def test_no_power_supply_dir_returns_none(self):
        self.assertIsNone(read_battery(os.path.join(self.root, "missing")))

    def test_ac_only_returns_none(self):
        make(self.root, "AC", type="Mains", online=1)
        self.assertIsNone(read_battery(self.root))

    def test_wireless_peripheral_is_ignored(self):
        make(self.root, "hid-0018-battery-7", type="Battery", scope="Device",
             capacity=40, status="Discharging")
        self.assertIsNone(read_battery(self.root))
        make(self.root, "BAT0", type="Battery", status="Discharging", capacity=50,
             energy_full=40000000, energy_now=20000000)
        b = read_battery(self.root)
        self.assertEqual(b.pct, 50)
        self.assertEqual(b.wh_now, 20.0)

    def test_absent_bay_is_skipped(self):
        make(self.root, "BAT1", type="Battery", present=0, capacity=0)
        self.assertIsNone(read_battery(self.root))

    def test_two_packs_are_combined(self):
        make(self.root, "BAT0", type="Battery", status="Discharging", cycle_count=100,
             energy_now=10000000, energy_full=20000000, energy_full_design=25000000,
             power_now=4000000)
        make(self.root, "BAT1", type="Battery", status="Full", cycle_count=300,
             energy_now=20000000, energy_full=20000000, energy_full_design=25000000,
             power_now=1000000)
        b = read_battery(self.root)
        self.assertEqual(b.wh_now, 30.0)
        self.assertEqual(b.wh_full, 40.0)
        self.assertEqual(b.wh_design, 50.0)
        self.assertEqual(b.watts, 5.0)
        self.assertEqual(b.pct, 75)
        self.assertEqual(b.status, "Discharging")
        self.assertEqual(b.cycles, 300)

    def test_capacity_only_driver(self):
        make(self.root, "BAT0", type="Battery", capacity=64, status="Charging")
        b = read_battery(self.root)
        self.assertEqual(b.pct, 64)
        self.assertIsNone(b.watts)
        self.assertIsNone(b.volts)
        self.assertIsNone(b.wh_now)
        self.assertIsNone(b.wh_full)
        self.assertIsNone(b.wh_design)
        self.assertIsNone(b.cycles)

    def test_unknown_status_string(self):
        make(self.root, "BAT0", type="Battery", status="Weird", capacity=10)
        self.assertEqual(read_battery(self.root).status, "Unknown")
        with tempfile.TemporaryDirectory() as other:
            make(other, "BAT0", type="Battery", capacity=10)
            self.assertEqual(read_battery(other).status, "Unknown")

    def test_capacity_is_clamped(self):
        make(self.root, "BAT0", type="Battery", capacity=101)
        self.assertEqual(read_battery(self.root).pct, 100)

    def test_pct_computed_when_capacity_missing(self):
        make(self.root, "BAT0", type="Battery", charge_now=1500000,
             charge_full=3000000, voltage_min_design=11000000)
        self.assertEqual(read_battery(self.root).pct, 50)

    def test_garbage_values_do_not_crash(self):
        make(self.root, "BAT0", type="Battery", capacity="abc", charge_full="",
             current_now="x")
        b = read_battery(self.root)
        self.assertIsNotNone(b)
        self.assertIsNone(b.pct)

    def test_zero_full_energy_is_none(self):
        make(self.root, "BAT0", type="Battery", energy_full=0, energy_now=0, capacity=55)
        b = read_battery(self.root)
        self.assertIsNone(b.wh_full)
        self.assertEqual(b.pct, 55)

    def test_zero_cycles_is_kept(self):
        make(self.root, "BAT0", type="Battery", cycle_count=0)
        self.assertEqual(read_battery(self.root).cycles, 0)


if __name__ == "__main__":
    unittest.main()
