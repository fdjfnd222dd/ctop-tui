"""Tests for ctop/tools/lanscan.py. Every route, address list, sender and neighbour table is a fake:
no packets, no sockets, no real sleeping."""
from __future__ import annotations

import threading
import unittest
from types import SimpleNamespace

from ctop.models import Device
from ctop.tools.lanscan import SETTLE, run_lanscan, scan_subnet

OWN_IP = "192.0.2.23"
GW = "192.0.2.1"
OWN_MAC = "AA:BB:CC:DD:EE:01"        # locally administered: a private Wi-Fi address


def _addr(address, netmask):
    return SimpleNamespace(family=2, address=address, netmask=netmask)


ADDRS = {"wlan0": [_addr(OWN_IP, "255.255.255.0")]}


def _route():
    return ("wlan0", GW)


def _neigh_factory(devices):
    def neigh(gateway):
        return tuple(devices)
    return neigh


DEVICES = (
    Device(ip=GW, mac="02:00:00:00:00:01", iface="wlan0", state="REACHABLE", is_gateway=True, random_mac=False),
    Device(ip="192.0.2.9", mac="AA:BB:CC:DD:EE:01", iface="wlan0", state="STALE", is_gateway=False,
           random_mac=False),
    Device(ip="192.0.2.200", mac="02:00:00:00:00:77", iface="wlan0", state="REACHABLE", is_gateway=False,
           random_mac=True),
    Device(ip="198.51.100.7", mac="AA:BB:CC:DD:EE:02", iface="wlan0", state="REACHABLE", is_gateway=False,
           random_mac=False),
)


class ScanSubnetTest(unittest.TestCase):
    def test_home_24(self):
        net, err = scan_subnet("192.168.1.23", "255.255.255.0")
        self.assertIsNone(err)
        self.assertEqual(str(net), "192.168.1.0/24")

    def test_wide_network_narrows_to_24(self):
        net, err = scan_subnet("10.1.2.3", "255.255.0.0")
        self.assertIsNone(err)
        self.assertEqual(str(net), "10.1.2.0/24")

    def test_documentation_range_accepted(self):
        net, err = scan_subnet("203.0.113.9", "255.255.255.0")
        self.assertIsNone(err)
        self.assertEqual(str(net), "203.0.113.0/24")

    def test_public_address_refused(self):
        net, err = scan_subnet("8.8.8.8", "255.255.255.0")
        self.assertIsNone(net)
        self.assertIn("Only private home networks", err)

    def test_small_subnet_keeps_its_size(self):
        net, err = scan_subnet("192.168.1.5", "255.255.255.252")
        self.assertIsNone(err)
        self.assertEqual(net.prefixlen, 30)
        self.assertEqual(len(list(net.hosts())), 2)

    def test_bad_input_gives_reasons(self):
        self.assertIn("Not an IPv4", scan_subnet("not-an-ip", "255.255.255.0")[1])
        self.assertIn("netmask", scan_subnet("192.168.1.5", "")[1])


class RunLanscanTest(unittest.TestCase):
    def _run(self, **overrides):
        sent = []
        sleeps = []
        kwargs = dict(
            route=_route,
            addrs=lambda: ADDRS,
            send=lambda ip: sent.append(ip),
            neigh=_neigh_factory(DEVICES),
            sleep=lambda s: sleeps.append(s),
            own_mac=lambda iface: OWN_MAC,
        )
        kwargs.update(overrides)
        return run_lanscan(**kwargs), sent, sleeps

    def test_full_sweep(self):
        updates = []
        result, sent, sleeps = self._run(on_update=updates.append)

        self.assertTrue(result.done)
        self.assertIsNone(result.error)
        self.assertEqual(result.subnet, "192.0.2.0/24")
        self.assertEqual(result.total, 254)
        self.assertEqual(result.probed, 254)
        self.assertEqual(len(sent), 253)
        self.assertNotIn(OWN_IP, sent)
        self.assertIn(GW, sent)
        self.assertIn(SETTLE, sleeps)

        ips = [d.ip for d in result.devices]
        self.assertEqual(ips, [GW, OWN_IP, "192.0.2.9", "192.0.2.200"])   # outside device dropped
        self.assertTrue(result.devices[0].is_gateway)
        self.assertTrue(result.devices[1].is_me)
        self.assertEqual(result.devices[1].mac, OWN_MAC)
        self.assertTrue(result.devices[1].random_mac)
        self.assertTrue(result.devices[3].random_mac)

        progress = [u.probed for u in updates if not u.done and u.probed]
        self.assertTrue(progress)
        self.assertEqual(progress, sorted(progress))
        self.assertEqual(updates[-1], result)

    def test_no_route(self):
        result, sent, _ = self._run(route=lambda: (None, None))
        self.assertTrue(result.done)
        self.assertEqual(result.error, "Not connected to a network")
        self.assertEqual(sent, [])

    def test_broken_route_lookup_is_an_error_not_a_crash(self):
        def boom():
            raise OSError("no /proc")
        result, sent, _ = self._run(route=boom)
        self.assertTrue(result.done)
        self.assertTrue(result.error)
        self.assertEqual(sent, [])

    def test_refused_subnet_never_sends(self):
        result, sent, _ = self._run(addrs=lambda: {"wlan0": [_addr("8.8.8.8", "255.255.255.0")]})
        self.assertTrue(result.done)
        self.assertIn("Only private home networks", result.error)
        self.assertEqual(sent, [])

    def test_cancel_stops_sending(self):
        cancel = threading.Event()
        sent = []

        def send(ip):
            sent.append(ip)
            if len(sent) == 20:
                cancel.set()

        result, _, _ = self._run(cancel=cancel, send=send)
        self.assertEqual(result.error, "cancelled")
        self.assertTrue(result.done)
        self.assertEqual(len(sent), 20)
        self.assertLess(len(sent), 253)

    def test_default_sleep_uses_cancel_event_without_real_sleep(self):
        # With a cancel event and no injected sleep, pauses go through cancel.wait, not time.sleep.
        waits = []

        class Flag(threading.Event):
            def wait(self, timeout=None):
                waits.append(timeout)
                return False

        result, sent, _ = self._run(cancel=Flag(), sleep=None, send=lambda ip: None)
        self.assertIn(SETTLE, waits)
        self.assertTrue(result.done)


if __name__ == "__main__":
    unittest.main()
