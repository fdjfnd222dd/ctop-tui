"""Tests for ctop/collectors/vpn.py. Tailscale output and interface tables are fakes: no real command, no psutil calls."""
from __future__ import annotations

import json
import socket
import unittest
from types import SimpleNamespace

from ctop.collectors.vpn import VpnCollector, parse_tailscale, tunnels
from ctop.models import VpnStatus

STATUS = {
    "BackendState": "Running",
    "Self": {"TailscaleIPs": ["100.64.0.1", "fd7a:115c:a1e0::1"], "HostName": "laptop"},
    "CurrentTailnet": {"Name": "example.ts.net"},
    "MagicDNSSuffix": "other.ts.net",
    "Peer": {
        "nodekey:a": {
            "HostName": "exit-box",
            "DNSName": "exit-box.example.ts.net.",
            "TailscaleIPs": ["fd7a:115c:a1e0::2", "100.64.0.2"],
            "OS": "linux",
            "Online": True,
            "ExitNode": True,
        },
        "nodekey:b": {
            "HostName": "phone",
            "DNSName": "phone.example.ts.net.",
            "TailscaleIPs": ["100.64.0.3"],
            "OS": "android",
            "Online": False,
            "ExitNode": False,
        },
        "nodekey:c": {
            "DNSName": "server-7.example.ts.net.",
            "TailscaleIPs": ["fd7a:115c:a1e0::9"],
            "OS": "",
            "Online": True,
            "ExitNode": False,
        },
    },
}


class ParseTailscaleTests(unittest.TestCase):
    def test_full_sample(self):
        backend, self_ip, tailnet, peers = parse_tailscale(json.dumps(STATUS))
        self.assertEqual(backend, "Running")
        self.assertEqual(self_ip, "100.64.0.1")
        self.assertEqual(tailnet, "example.ts.net")
        self.assertEqual([p.name for p in peers], ["exit-box", "server-7", "phone"])   # online first, then name
        exit_box, server, phone = peers
        self.assertEqual(exit_box.ip, "100.64.0.2")         # IPv4 preferred over the IPv6 address
        self.assertTrue(exit_box.online)
        self.assertTrue(exit_box.exit_node)
        self.assertEqual(exit_box.os, "linux")
        self.assertEqual(server.ip, "fd7a:115c:a1e0::9")    # only IPv6 available, so use it
        self.assertEqual(server.name, "server-7")           # no HostName: first label of DNSName
        self.assertFalse(phone.online)
        self.assertEqual(phone.ip, "100.64.0.3")

    def test_stopped_without_peers(self):
        backend, self_ip, tailnet, peers = parse_tailscale(json.dumps({"BackendState": "Stopped"}))
        self.assertEqual(backend, "Stopped")
        self.assertEqual(peers, ())
        self.assertIsNone(self_ip)

    def test_tailnet_falls_back_to_magic_dns(self):
        _, _, tailnet, _ = parse_tailscale(json.dumps({"MagicDNSSuffix": "home.ts.net"}))
        self.assertEqual(tailnet, "home.ts.net")

    def test_bad_json_is_unknown(self):
        self.assertEqual(parse_tailscale("not json {"), ("unknown", None, None, ()))

    def test_non_object_json_is_unknown(self):
        self.assertEqual(parse_tailscale("[1, 2]"), ("unknown", None, None, ()))

    def test_peer_without_any_name_is_question_mark(self):
        text = json.dumps({"BackendState": "Running", "Peer": {"k": {"TailscaleIPs": []}}})
        (peer,) = parse_tailscale(text)[3]
        self.assertEqual(peer.name, "?")
        self.assertIsNone(peer.ip)


class FakeCmd:
    def __init__(self, text):
        self.text = text
        self.calls = []

    def __call__(self, argv, timeout):
        self.calls.append((list(argv), timeout))
        return self.text


class CollectorTests(unittest.TestCase):
    def test_tailscale_not_installed_skips_command(self):
        runner = FakeCmd(json.dumps(STATUS))
        coll = VpnCollector(runner=runner, which=lambda name: None,
                            addrs_fn=lambda: {}, stats_fn=lambda: {})
        coll.refresh()
        self.assertEqual(runner.calls, [])
        self.assertIsNone(coll.data.tailscale)
        self.assertEqual(coll.data.peers, ())

    def test_runner_none_is_unknown(self):
        coll = VpnCollector(runner=lambda argv, t: None, which=lambda name: "/usr/bin/tailscale",
                            addrs_fn=lambda: {}, stats_fn=lambda: {})
        coll.refresh()
        self.assertEqual(coll.data.tailscale, "unknown")

    def test_full_refresh_uses_timeout_and_argv(self):
        runner = FakeCmd(json.dumps(STATUS))
        coll = VpnCollector(runner=runner, which=lambda name: "/usr/bin/tailscale",
                            addrs_fn=lambda: {}, stats_fn=lambda: {})
        coll.refresh()
        self.assertEqual(runner.calls, [(["tailscale", "status", "--json"], 3.0)])
        self.assertEqual(coll.data.tailscale, "Running")
        self.assertEqual(coll.data.tailscale_ip, "100.64.0.1")
        self.assertEqual(coll.data.tailnet, "example.ts.net")
        self.assertIsInstance(coll.data, VpnStatus)


class TunnelTests(unittest.TestCase):
    def test_kinds_up_and_ip(self):
        addrs = {
            "wg0": [SimpleNamespace(family=socket.AF_INET, address="10.8.0.2")],
            "tailscale0": [SimpleNamespace(family=socket.AF_INET6, address="fd7a::5%tailscale0")],
            "eth0": [SimpleNamespace(family=socket.AF_INET, address="192.0.2.10")],
            "tun0": [SimpleNamespace(family=socket.AF_INET6, address="2001:db8::7")],
        }
        stats = {
            "wg0": SimpleNamespace(isup=True),
            "tailscale0": SimpleNamespace(isup=False),
            "eth0": SimpleNamespace(isup=True),
            "tun0": SimpleNamespace(isup=True),
        }
        result = tunnels(addrs, stats)
        names = [t.name for t in result]
        self.assertEqual(names, ["tailscale0", "tun0", "wg0"])          # eth0 ignored, sorted by name
        by_name = {t.name: t for t in result}
        self.assertEqual(by_name["wg0"].kind, "WireGuard")
        self.assertTrue(by_name["wg0"].up)
        self.assertEqual(by_name["wg0"].ip, "10.8.0.2")
        self.assertEqual(by_name["tailscale0"].kind, "Tailscale")
        self.assertFalse(by_name["tailscale0"].up)
        self.assertIsNone(by_name["tailscale0"].ip)                     # the scoped IPv6 address is skipped
        self.assertEqual(by_name["tun0"].kind, "OpenVPN/tun")
        self.assertEqual(by_name["tun0"].ip, "2001:db8::7")

    def test_collector_reports_tunnels(self):
        addrs = {"wg1": [SimpleNamespace(family=socket.AF_INET, address="10.9.0.1")]}
        stats = {"wg1": SimpleNamespace(isup=True)}
        coll = VpnCollector(runner=lambda a, t: None, which=lambda name: None,
                            addrs_fn=lambda: addrs, stats_fn=lambda: stats)
        coll.refresh()
        self.assertEqual([t.name for t in coll.data.tunnels], ["wg1"])


if __name__ == "__main__":
    unittest.main()
