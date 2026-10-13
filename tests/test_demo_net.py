"""Tests for ctop/demo_net.py: fake Network page data never touches the machine."""
from __future__ import annotations

import re
import socket
import threading
import unittest
from unittest import mock

from ctop import demo_net, netctl
from ctop.models import LinkInfo, NmState, VpnStatus

DOC_OR_FAKE = ("192.0.2.", "198.51.100.", "203.0.113.", "100.64.", "1.1.1.1", "9.9.9.9", "8.8.8.8")
IPV4 = re.compile(r"(?<![\d.])\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(?![\d.])")


class DemoNetTests(unittest.TestCase):
    def setUp(self):
        self._step = demo_net.STEP
        demo_net.STEP = 0
        self._no_sockets = mock.patch.object(socket, "socket", side_effect=AssertionError("demo opened a socket"))
        self._no_sockets.start()

    def tearDown(self):
        demo_net.STEP = self._step
        self._no_sockets.stop()

    def _addresses_ok(self, obj):
        for ip in IPV4.findall(repr(obj)):
            self.assertTrue(ip.startswith(DOC_OR_FAKE), ip)

    def test_collectors_publish_models(self):
        c = demo_net.make_demo_net_collectors()
        for name, cls in (("nmstate", NmState), ("wifilink", LinkInfo), ("vpn", VpnStatus)):
            c[name].run()
            self.assertIsNone(c[name].error)
            self.assertIsInstance(c[name].data, cls)
            self._addresses_ok(c[name].data)
        nm = c["nmstate"].data
        self.assertTrue(any(n.in_use for n in nm.networks))
        self.assertTrue(any(s.active for s in nm.saved))
        for s in nm.saved:
            self.assertTrue(netctl.valid_uuid(s.uuid), s.uuid)

    def test_demo_netctl_mirrors_netctl(self):
        ctl = demo_net.DemoNetctl()
        for name in ("connect_saved", "disconnect", "forget", "set_autoconnect", "set_priority", "set_dns",
                     "connect_new", "rescan", "wifi_radio", "airplane", "reconnect", "tailscale"):
            self.assertTrue(callable(getattr(netctl, name)), name)
            out = getattr(ctl, name)("x", "y")
            self.assertTrue(out.ok)
            self.assertIn("Demo", out.message)
        self.assertEqual(ctl.get_dns("x"), (("192.0.2.1",), False))
        self.assertIs(ctl.DNS_PRESETS, netctl.DNS_PRESETS)

    def test_tools_finish_and_update(self):
        cases = ((demo_net.demo_trace, ("example.org",)), (demo_net.demo_ports, ("example.org", (22, 80, 443, 3389))),
                 (demo_net.demo_dns, ()), (demo_net.demo_speed, ()), (demo_net.demo_doctor, ()),
                 (demo_net.demo_lanscan, ()))
        for fn, args in cases:
            seen = []
            r = fn(*args, on_update=seen.append)
            self.assertGreater(len(seen), 1, fn.__name__)
            done = getattr(r, "done", None)
            self.assertTrue(done if done is not None else r.stage == "done", fn.__name__)
            self._addresses_ok(r)
        trace = demo_net.demo_trace("example.org")
        self.assertTrue(trace.reached)
        self.assertEqual(trace.hops[-1].ip, "203.0.113.5")

    def test_cancel(self):
        ev = threading.Event()
        ev.set()
        self.assertEqual(demo_net.demo_trace("203.0.113.5", cancel=ev).error, "cancelled")
        self.assertEqual(demo_net.demo_speed(cancel=ev).stage, "cancelled")
        self.assertEqual(demo_net.demo_lanscan(cancel=ev).error, "cancelled")


if __name__ == "__main__":
    unittest.main()
