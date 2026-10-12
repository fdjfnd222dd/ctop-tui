"""Tests for ctop/collectors/ping.py. Every probe is an injected fake: no ping, no sockets."""
from __future__ import annotations

import socket
import unittest
from types import SimpleNamespace

from ctop.collectors.ping import (
    DEFAULT_TARGET, PingCollector, parse_ping_output, ping_once, summarize,
    tcp_connect_ms, valid_target,
)

GW = "192.0.2.1"
IPUTILS = "64 bytes from 192.0.2.1: icmp_seq=1 ttl=64 time=3.41 ms\n"
BUSYBOX = "64 bytes from 192.0.2.1: seq=0 ttl=64 time=0.321 ms"
LOST_BLOCK = ("--- 192.0.2.1 ping statistics ---\n"
              "1 packets transmitted, 0 received, 100% packet loss, time 0ms\n")
UNREACHABLE = "From 192.0.2.5 icmp_seq=1 Destination Host Unreachable\n"
TTL_EXCEEDED = "From 192.0.2.5 icmp_seq=1 Time to live exceeded\n"


def _proc(returncode=0, stdout="", stderr=""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def _tcp_spy(value):
    """Fake TCP fallback that returns `value` and records each target it was asked about."""
    calls = []

    def tcp(target):
        calls.append(target)
        return value
    return tcp, calls


def _recorder(reply):
    """Fake runner: records each target, answers with reply(target)."""
    targets = []

    def runner(target):
        targets.append(target)
        return reply(target)
    return runner, targets


def _fixed_gateway(gw):
    return lambda: ("wlan0", gw)


class ParseTests(unittest.TestCase):

    def test_parse_iputils_reply(self):
        self.assertEqual(parse_ping_output(IPUTILS), 3.41)

    def test_parse_busybox_reply(self):
        self.assertEqual(parse_ping_output(BUSYBOX), 0.321)

    def test_parse_lost_outputs(self):
        # "time 0ms" in the summary and "Time to live exceeded" are not round trips.
        for text in (LOST_BLOCK, UNREACHABLE, TTL_EXCEEDED, "", None):
            with self.subTest(text=text):
                self.assertIsNone(parse_ping_output(text))

    def test_valid_target(self):
        for good in ("1.1.1.1", "2001:db8::1", "example.net"):
            with self.subTest(target=good):
                self.assertTrue(valid_target(good))
        for bad in ("-f", "--help", "", "a b", "1.1.1.1;ls"):
            with self.subTest(target=bad):
                self.assertFalse(valid_target(bad))


class SummarizeTests(unittest.TestCase):

    def test_summarize_stats(self):
        r = summarize("Router", GW, (10.0, None, 14.0, 12.0))
        self.assertEqual(r.loss_pct, 25.0)
        self.assertEqual(r.avg_ms, 12.0)
        self.assertEqual(r.jitter_ms, 3.0)
        self.assertEqual(r.ms, 12.0)
        self.assertEqual(r.history, (10.0, None, 14.0, 12.0))

    def test_summarize_empty_and_all_lost(self):
        empty = summarize("Internet", "1.1.1.1", ())
        self.assertEqual(empty.loss_pct, 0.0)
        self.assertIsNone(empty.avg_ms)
        self.assertIsNone(empty.jitter_ms)
        self.assertIsNone(empty.ms)
        lost = summarize("Internet", "1.1.1.1", (None, None))
        self.assertEqual(lost.loss_pct, 100.0)
        self.assertIsNone(lost.avg_ms)


class PingOnceTests(unittest.TestCase):

    def test_ping_once_uses_tcp_when_binary_missing(self):
        tcp, calls = _tcp_spy(7.5)
        self.assertEqual(ping_once(GW, have_ping=False, tcp=tcp), 7.5)

        def missing(argv, **kwargs):
            raise FileNotFoundError("ping")
        self.assertEqual(ping_once(GW, have_ping=True, run=missing, tcp=tcp), 7.5)
        self.assertEqual(calls, [GW, GW])

    def test_ping_once_parses_run_output(self):
        tcp, calls = _tcp_spy(99.0)
        ok = ping_once(GW, have_ping=True, run=lambda argv, **kw: _proc(0, IPUTILS), tcp=tcp)
        self.assertEqual(ok, 3.41)
        lost = ping_once(GW, have_ping=True,
                         run=lambda argv, **kw: _proc(1, "100% packet loss"), tcp=tcp)
        self.assertIsNone(lost)
        self.assertEqual(calls, [])  # a plain loss must not fall back to TCP

    def test_ping_once_permission_error_falls_back(self):
        tcp, calls = _tcp_spy(7.5)
        run = lambda argv, **kw: _proc(2, "", "ping: socket: Operation not permitted")
        self.assertEqual(ping_once(GW, have_ping=True, run=run, tcp=tcp), 7.5)
        self.assertEqual(calls, [GW])

    def test_ping_once_argv(self):
        seen = {}

        def run(argv, **kwargs):
            seen["argv"] = argv
            seen["timeout"] = kwargs.get("timeout")
            return _proc(0, IPUTILS)
        tcp, _ = _tcp_spy(None)
        ping_once(GW, have_ping=True, run=run, tcp=tcp)
        self.assertEqual(seen["argv"], ["ping", "-n", "-c", "1", "-W", "1", GW])
        self.assertEqual(seen["timeout"], 2.5)

    def test_tcp_connect_refused_counts_as_reply(self):
        def refused(address, timeout):
            raise ConnectionRefusedError()

        def timed_out(address, timeout):
            raise socket.timeout("timed out")
        ms = tcp_connect_ms(GW, connect=refused)
        self.assertIsInstance(ms, float)
        self.assertGreaterEqual(ms, 0.0)
        self.assertIsNone(tcp_connect_ms(GW, connect=timed_out))


class PingCollectorTests(unittest.TestCase):

    def test_collector_pings_both_targets(self):
        runner, targets = _recorder(lambda t: 2.0 if t == GW else 20.0)
        c = PingCollector(gateway_fn=_fixed_gateway(GW), runner=runner, environ={})
        c.run()
        self.assertIsNone(c.error)
        self.assertEqual(c.data.router.target, GW)
        self.assertEqual(c.data.router.label, "Router")
        self.assertEqual(c.data.internet.target, "1.1.1.1")
        self.assertEqual(c.data.internet.label, "Internet")
        self.assertEqual(targets, [GW, "1.1.1.1"])

    def test_env_target_override(self):
        runner, _ = _recorder(lambda t: 1.0)
        c = PingCollector(gateway_fn=_fixed_gateway(GW), runner=runner,
                          environ={"CTOP_PING_TARGET": "198.51.100.7"})
        c.run()
        self.assertEqual(c.data.internet.target, "198.51.100.7")
        runner, _ = _recorder(lambda t: 1.0)
        c = PingCollector(gateway_fn=_fixed_gateway(GW), runner=runner,
                          environ={"CTOP_PING_TARGET": "-f"})
        c.run()
        self.assertEqual(c.data.internet.target, DEFAULT_TARGET)

    def test_no_ping_env(self):
        runner, targets = _recorder(lambda t: 2.0)
        c = PingCollector(gateway_fn=_fixed_gateway(GW), runner=runner,
                          environ={"CTOP_NO_PING": "1"})
        c.run()
        self.assertIsNone(c.data.internet)
        self.assertIsNotNone(c.data.router)
        self.assertNotIn(DEFAULT_TARGET, targets)
        self.assertEqual(targets, [GW])

    def test_no_gateway(self):
        runner, _ = _recorder(lambda t: 5.0)
        c = PingCollector(gateway_fn=lambda: (None, None), runner=runner, environ={})
        c.run()
        self.assertIsNone(c.data.router)
        self.assertIsNotNone(c.data.internet)

    def test_loss_is_recorded(self):
        runner, _ = _recorder(lambda t: None)
        c = PingCollector(gateway_fn=_fixed_gateway(GW), runner=runner, environ={})
        for _ in range(3):
            c.run()
        self.assertEqual(c.data.internet.loss_pct, 100.0)
        self.assertIsNone(c.data.internet.ms)
        self.assertEqual(len(c.data.internet.history), 3)

    def test_history_capped_at_120(self):
        runner, _ = _recorder(lambda t: 1.0)
        c = PingCollector(gateway_fn=_fixed_gateway(GW), runner=runner, environ={})
        for _ in range(130):
            c.run()
        self.assertEqual(len(c.data.internet.history), 120)

    def test_gateway_change_resets_router_history(self):
        current = {"gw": GW}
        runner, _ = _recorder(lambda t: 1.0)
        c = PingCollector(gateway_fn=lambda: ("wlan0", current["gw"]), runner=runner, environ={})
        for _ in range(5):
            c.refresh()
        current["gw"] = "198.51.100.1"
        c.refresh()
        self.assertEqual(len(c.data.router.history), 1)
        self.assertEqual(len(c.data.internet.history), 6)

    def test_refresh_publishes_new_object(self):
        runner, _ = _recorder(lambda t: 1.0)
        c = PingCollector(gateway_fn=_fixed_gateway(GW), runner=runner, environ={})
        c.refresh()
        first = c.data
        c.refresh()
        self.assertIsNot(first, c.data)


if __name__ == "__main__":
    unittest.main()
