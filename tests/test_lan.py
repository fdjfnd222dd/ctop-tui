"""Tests for ctop/collectors/lan.py.

Command output and file text are injected. `ip` is never run and the real
/proc/net/arp is never read.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from typing import Dict, List, Optional, Tuple

from ctop.collectors.lan import (LanCollector, is_random_mac, parse_neigh_text,
                                 parse_proc_arp, read_devices)
from ctop.models import Device

GW = "192.0.2.1"
GW_MAC = "00:00:5e:00:53:01"
M = "02:00:00:aa:bb:cc"
NO_ARP = "/nonexistent/ctop-test/arp"      # never created, so the file fallback finds nothing

JSON = ('[{"dst":"192.0.2.1","dev":"wlan0","lladdr":"00:00:5e:00:53:01","state":["REACHABLE"]},'
        '{"dst":"192.0.2.23","dev":"wlan0","lladdr":"02:00:00:aa:bb:cc","state":["STALE"]},'
        '{"dst":"192.0.2.77","dev":"wlan0","state":["FAILED"]},'
        '{"dst":"fe80::1","dev":"wlan0","lladdr":"00:00:5e:00:53:01","router":null,"state":["STALE"]}]')

TEXT = ("192.0.2.1 dev wlan0 lladdr 00:00:5e:00:53:01 REACHABLE\n"
        "192.0.2.9 dev wlan0  FAILED\n"
        "192.0.2.10 dev wlan0 INCOMPLETE\n"
        "fe80::1 dev wlan0 lladdr 00:00:5e:00:53:01 router STALE\n")

ARP_HEADER = ("IP address       HW type     Flags       HW address            Mask     Device\n")
ARP_GW = "192.0.2.1        0x1         0x2         00:00:5e:00:53:01     *        wlan0\n"
ARP = ARP_HEADER + ARP_GW


def _entry(dst, dev, mac=None, state=None):
    e = {"dst": dst, "dev": dev}
    if mac is not None:
        e["lladdr"] = mac
    if state is not None:
        e["state"] = [state]
    return e


def _json(*entries):
    return json.dumps(list(entries))


class FakeRunner:
    """Stands in for run_cmd: returns canned stdout keyed by argv and records each call."""

    def __init__(self, outputs: Optional[Dict[Tuple[str, ...], str]] = None) -> None:
        self.outputs = outputs or {}
        self.calls: List[Tuple[List[str], float]] = []

    def __call__(self, argv: List[str], timeout: float) -> Optional[str]:
        self.calls.append((list(argv), timeout))
        return self.outputs.get(tuple(argv))


class ParserTests(unittest.TestCase):
    def test_random_mac_bit(self):
        self.assertTrue(is_random_mac("02:00:00:aa:bb:cc"))
        self.assertFalse(is_random_mac("00:00:5e:00:53:01"))
        self.assertTrue(is_random_mac("da:a1:19:00:00:01"))
        self.assertFalse(is_random_mac("d8:a1:19:00:00:01"))
        self.assertFalse(is_random_mac("junk"))

    def test_text_parser(self):
        self.assertEqual(parse_neigh_text(TEXT), [
            (GW, "wlan0", GW_MAC, "REACHABLE"),
            ("fe80::1", "wlan0", GW_MAC, "STALE"),
        ])

    def test_proc_arp_parser(self):
        rows = parse_proc_arp(ARP + "192.0.2.5        0x1         0x0         00:00:00:00:00:00     *        wlan0\n")
        self.assertEqual(rows, [(GW, "wlan0", GW_MAC, "STALE")])
        rows = parse_proc_arp(ARP + "192.0.2.7        0x1         0x6         02:00:00:aa:bb:cc     *        wlan0\n")
        self.assertEqual(rows[-1], ("192.0.2.7", "wlan0", M, "PERMANENT"))


class ReadDevicesTests(unittest.TestCase):
    def test_json_basic(self):
        fake = FakeRunner()
        devs = read_devices(JSON, GW, runner=fake, arp_path=NO_ARP)
        self.assertEqual(devs, (
            Device("192.0.2.1", GW_MAC, "wlan0", "REACHABLE", True, False),
            Device("192.0.2.23", M, "wlan0", "STALE", False, True),
        ))
        self.assertEqual(fake.calls, [])     # the JSON was enough; no command ran

    def test_skips_failed_incomplete_and_macless(self):
        js = _json(_entry("192.0.2.77", "wlan0", M, "FAILED"),
                   _entry("192.0.2.78", "wlan0", "02:00:00:aa:bb:cd", "INCOMPLETE"),
                   _entry("192.0.2.79", "wlan0", None, "REACHABLE"))
        self.assertEqual(read_devices(js, GW, runner=FakeRunner(), arp_path=NO_ARP), ())

    def test_gateway_first_then_numeric_ip_order(self):
        js = _json(_entry("192.0.2.100", "wlan0", "02:00:00:aa:bb:01", "STALE"),
                   _entry("192.0.2.9", "wlan0", "02:00:00:aa:bb:02", "STALE"),
                   _entry("192.0.2.1", "wlan0", "02:00:00:aa:bb:03", "STALE"),
                   _entry("192.0.2.50", "wlan0", "02:00:00:aa:bb:04", "STALE"))
        devs = read_devices(js, "192.0.2.50", runner=FakeRunner(), arp_path=NO_ARP)
        self.assertEqual([d.ip for d in devs], ["192.0.2.50", "192.0.2.1", "192.0.2.9", "192.0.2.100"])

    def test_ipv6_only_device_is_kept(self):
        js = _json(_entry("2001:db8::5", "wlan0", M, "REACHABLE"),
                   _entry("192.0.2.9", "wlan0", "02:00:00:aa:bb:dd", "STALE"))
        devs = read_devices(js, None, runner=FakeRunner(), arp_path=NO_ARP)
        self.assertEqual([d.ip for d in devs], ["192.0.2.9", "2001:db8::5"])

    def test_same_mac_collapses_prefers_ipv4(self):
        js = _json(_entry("fe80::2", "wlan0", M, "STALE"),
                   _entry("2001:db8::2", "wlan0", M, "REACHABLE"),
                   _entry("192.0.2.2", "wlan0", M, "DELAY"))
        devs = read_devices(js, None, runner=FakeRunner(), arp_path=NO_ARP)
        self.assertEqual(devs, (Device("192.0.2.2", M, "wlan0", "DELAY", False, True),))

    def test_same_mac_on_two_interfaces_is_two_devices(self):
        js = _json(_entry("192.0.2.2", "wlan0", M, "STALE"),
                   _entry("192.0.2.3", "eth0", M, "STALE"))
        devs = read_devices(js, None, runner=FakeRunner(), arp_path=NO_ARP)
        self.assertEqual(len(devs), 2)

    def test_state_is_list_or_string(self):
        js = ('[{"dst":"192.0.2.5","dev":"wlan0","lladdr":"02:00:00:aa:bb:01","state":"STALE"},'
              '{"dst":"192.0.2.6","dev":"wlan0","lladdr":"02:00:00:aa:bb:02","state":["STALE"]},'
              '{"dst":"192.0.2.7","dev":"wlan0","lladdr":"02:00:00:aa:bb:03","state":["stale"]}]')
        devs = read_devices(js, None, runner=FakeRunner(), arp_path=NO_ARP)
        self.assertEqual([d.state for d in devs], ["STALE", "STALE", "STALE"])

    def test_invalid_json_falls_back_to_text(self):
        fake = FakeRunner({("ip", "neigh", "show"): TEXT})
        devs = read_devices("not json", GW, runner=fake, arp_path=NO_ARP)
        self.assertEqual(devs, (Device(GW, GW_MAC, "wlan0", "REACHABLE", True, False),))
        self.assertEqual(fake.calls, [(["ip", "neigh", "show"], 2)])

    def test_fallback_chain_with_runner(self):
        fake = FakeRunner()          # both ip commands "fail"
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "arp")
            with open(path, "w") as f:
                f.write(ARP)
            devs = read_devices(None, None, runner=fake, arp_path=path)
        self.assertEqual(len(devs), 1)
        self.assertEqual(devs[0].state, "STALE")
        self.assertEqual(devs[0].ip, GW)

    def test_nothing_available(self):
        self.assertEqual(read_devices(None, None, runner=FakeRunner(), arp_path=NO_ARP), ())

    def test_multicast_and_loopback_ignored(self):
        js = _json(_entry("224.0.0.251", "wlan0", "01:00:5e:00:00:fb", "REACHABLE"),
                   _entry("127.0.0.1", "lo", M, "REACHABLE"),
                   _entry("192.0.2.44", "wlan0", "ff:ff:ff:ff:ff:ff", "REACHABLE"),
                   _entry("192.0.2.50", "lo", "02:00:00:aa:bb:02", "REACHABLE"))
        self.assertEqual(read_devices(js, None, runner=FakeRunner(), arp_path=NO_ARP), ())

    def test_gateway_none(self):
        devs = read_devices(JSON, None, runner=FakeRunner(), arp_path=NO_ARP)
        self.assertFalse(any(d.is_gateway for d in devs))

    def test_runner_argv(self):
        fake = FakeRunner()
        read_devices(None, None, runner=fake, arp_path=NO_ARP)
        self.assertEqual(fake.calls[0], (["ip", "-j", "neigh", "show"], 2))


class CollectorTests(unittest.TestCase):
    def test_collector_uses_gateway_fn(self):
        fake = FakeRunner({("ip", "-j", "neigh", "show"): JSON})
        c = LanCollector(gateway_fn=lambda: ("wlan0", GW), runner=fake)
        c.run()
        self.assertTrue(c.data[0].is_gateway)
        self.assertIsNone(c.error)
        self.assertEqual(c.name, "lan")
        self.assertEqual(c.interval, 10.0)


if __name__ == "__main__":
    unittest.main()
