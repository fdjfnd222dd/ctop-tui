"""Tests for ctop/collectors/thermal.py. Every fixture lives in a temp dir."""
from __future__ import annotations

import os
import tempfile
import unittest

from ctop.collectors.thermal import ThermalCollector, read_thermal
from ctop.models import Fan, Thermal


def _write(path, text):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(str(text))


def chip(root, n, name, **files):
    """Make <root>/hwmon<n>/ with its name file and each kwarg written as a file."""
    d = os.path.join(root, f"hwmon{n}")
    os.makedirs(d)
    _write(os.path.join(d, "name"), name)
    for fname, value in files.items():
        _write(os.path.join(d, fname), value)
    return d


def zone(root, n, ztype, temp):
    d = os.path.join(root, f"thermal_zone{n}")
    os.makedirs(d)
    _write(os.path.join(d, "type"), ztype)
    _write(os.path.join(d, "temp"), temp)
    return d


class ThermalTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        self.h = os.path.join(self.tmp, "hwmon")
        self.t = os.path.join(self.tmp, "thermal")
        os.makedirs(self.h)
        os.makedirs(self.t)

    def intel_tree(self):
        chip(self.h, 1, "acpitz", temp1_input="25000")
        chip(self.h, 4, "nvme",
             temp1_label="Composite", temp1_input="38850", temp1_max="80850", temp1_crit="81850",
             temp2_label="Sensor 1", temp2_input="38850", temp2_max="65261850")
        d7 = chip(self.h, 7, "dell_smm", temp1_input="42000", temp2_input="38000",
                  fan1_input="0", fan1_max="4900")
        os.makedirs(os.path.join(d7, "fan1_label"))      # unreadable label, as on dell_smm
        chip(self.h, 8, "iwlwifi_1", temp1_input="38000")
        chip(self.h, 9, "coretemp",
             temp1_label="Package id 0", temp1_input="44000",
             temp1_max="100000", temp1_crit="100000",
             temp2_label="Core 0", temp2_input="43000")

    def test_intel_laptop_full_tree(self):
        self.intel_tree()
        r = read_thermal(self.h, self.t)
        self.assertEqual(r.cpu_temp, 44.0)
        cpu = r.temps[0]
        self.assertEqual((cpu.label, cpu.kind, cpu.high, cpu.crit), ("CPU", "cpu", 100.0, 100.0))
        self.assertEqual({t.label for t in r.temps},
                         {"CPU", "Disk (nvme)", "Wi-Fi card", "System (ACPI)", "Board"})
        board = next(t for t in r.temps if t.label == "Board")
        self.assertEqual(board.celsius, 42.0)
        nvme = next(t for t in r.temps if t.label == "Disk (nvme)")
        self.assertEqual((nvme.high, nvme.crit), (80.85, 81.85))
        self.assertFalse(any("Sensor" in t.label for t in r.temps))
        self.assertEqual(r.fans, (Fan("Fan 1", 0, 4900),))
        self.assertEqual(r.hottest.label, "CPU")

    def test_kind_order(self):
        self.intel_tree()
        r = read_thermal(self.h, self.t)
        self.assertEqual([t.kind for t in r.temps], ["cpu", "disk", "wifi", "board", "board"])

    def test_amd_prefers_tdie(self):
        chip(self.h, 1, "k10temp", temp1_label="Tctl", temp1_input="62000",
             temp2_label="Tccd1", temp2_input="55000",
             temp3_label="Tdie", temp3_input="52000")
        self.assertEqual(read_thermal(self.h, self.t).cpu_temp, 52.0)

    def test_amd_tctl_only(self):
        chip(self.h, 1, "k10temp", temp1_label="Tctl", temp1_input="62000")
        self.assertEqual(read_thermal(self.h, self.t).cpu_temp, 62.0)

    def test_amd_gpu_edge(self):
        chip(self.h, 1, "amdgpu",
             temp1_label="edge", temp1_input="48000",
             temp2_label="junction", temp2_input="90000")
        r = read_thermal(self.h, self.t)
        self.assertEqual(len(r.temps), 1)
        self.assertEqual((r.temps[0].kind, r.temps[0].celsius), ("gpu", 48.0))

    def test_coretemp_without_package_uses_hottest_core(self):
        chip(self.h, 1, "coretemp",
             temp1_label="Core 0", temp1_input="50000",
             temp2_label="Core 1", temp2_input="55000")
        self.assertEqual(read_thermal(self.h, self.t).cpu_temp, 55.0)

        # A chip with no labels at all: temp1 is the CPU reading.
        h2 = os.path.join(self.tmp, "hwmon2")
        os.makedirs(h2)
        chip(h2, 1, "coretemp", temp1_input="50000")
        self.assertEqual(read_thermal(h2, self.t).cpu_temp, 50.0)

    def test_two_nvme_are_numbered(self):
        chip(self.h, 9, "nvme", temp1_label="Composite", temp1_input="40000")
        chip(self.h, 10, "nvme", temp1_label="Composite", temp1_input="41000")
        r = read_thermal(self.h, self.t)
        self.assertEqual([t.label for t in r.temps], ["Disk 1 (nvme)", "Disk 2 (nvme)"])

    def test_sanity_filters(self):
        chip(self.h, 1, "coretemp", temp1_label="Package id 0", temp1_input="255000")
        chip(self.h, 2, "nvme", temp1_label="Composite", temp1_input="-128000")
        chip(self.h, 3, "iwlwifi", temp1_input="0")
        r = read_thermal(self.h, self.t)
        self.assertEqual(r.temps, ())
        self.assertIsNone(r.cpu_temp)

    def test_fans_labels_and_zero(self):
        chip(self.h, 1, "it8728",
             fan1_input="1200", fan1_label="CPU Fan", fan1_max="3000",
             fan2_input="0",
             fan3_input="abc",
             fan4_input="-1")
        self.assertEqual(read_thermal(self.h, self.t).fans,
                         (Fan("CPU Fan", 1200, 3000), Fan("Fan 2", 0, None)))

    def test_thermal_zone_fallback(self):
        zone(self.t, 0, "acpitz", "25000")
        zone(self.t, 1, "x86_pkg_temp", "45000")
        r = read_thermal(self.h, self.t)
        self.assertEqual(r.cpu_temp, 45.0)
        self.assertEqual([t.label for t in r.temps], ["CPU"])

    def test_thermal_zone_ignored_when_hwmon_has_cpu(self):
        chip(self.h, 1, "coretemp", temp1_input="44000")
        zone(self.t, 0, "x86_pkg_temp", "99000")
        r = read_thermal(self.h, self.t)
        self.assertEqual(r.cpu_temp, 44.0)
        self.assertEqual(len([t for t in r.temps if t.kind == "cpu"]), 1)

    def test_empty_and_missing_roots(self):
        self.assertEqual(read_thermal(self.h, self.t), Thermal())
        missing = os.path.join(self.tmp, "does-not-exist")
        self.assertEqual(read_thermal(missing, missing), Thermal())

    def test_unknown_chip_temps_ignored_but_fans_kept(self):
        chip(self.h, 1, "weirdchip", temp1_input="40000", fan1_input="900")
        r = read_thermal(self.h, self.t)
        self.assertEqual(r.temps, ())
        self.assertEqual(r.fans, (Fan("Fan 1", 900, None),))

    def test_collector_publishes_new_object_each_refresh(self):
        d = chip(self.h, 1, "coretemp", temp1_label="Package id 0", temp1_input="44000")
        c = ThermalCollector(self.h, self.t)
        self.assertIsNone(c.data)
        c.run()
        first = c.data
        self.assertIsInstance(first, Thermal)
        self.assertIsNone(c.error)
        self.assertEqual((c.name, c.interval), ("thermal", 2.0))
        _write(os.path.join(d, "temp1_input"), "50000")
        c.run()
        self.assertEqual(c.data.cpu_temp, 50.0)
        self.assertIsNot(c.data, first)


if __name__ == "__main__":
    unittest.main()
