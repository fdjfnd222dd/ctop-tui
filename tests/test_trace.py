"""Tests for ctop/tools/trace.py. No real sockets, no real commands, no sleeping."""
from __future__ import annotations

import socket
import struct
import threading
import unittest

from ctop.tools import trace
from ctop.tools.trace import (
    BASE_PORT, classify, parse_recverr, parse_tracepath, resolve_target, run_trace,
)

TARGET = "203.0.113.5"
TP_SAMPLE = """\
 1?: [LOCALHOST]                      pmtu 1500
 1:  192.0.2.1                                             3.123ms
 2:  no reply
 3:  198.51.100.9                                         12.400ms asymm  4
 9:  203.0.113.5                                          24.100ms reached
     Resume: pmtu 1500 hops 9 back 9
"""


def _resolver(*entries):
    def resolve(name):
        return list(entries)
    return resolve


V4 = (socket.AF_INET, socket.SOCK_DGRAM, 17, "", (TARGET, 0))
V6 = (socket.AF_INET6, socket.SOCK_DGRAM, 17, "", ("2001:db8::5", 0, 0, 0))


class FakeClock:
    """Each call moves time forward by 0.2 s, so ROUND_WAIT passes without sleeping."""
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        self.t += 0.2
        return self.t


class FakeProber:
    """Answers each probe from `script` (ttl -> (ip, kind, ms)) on the next wait()."""
    def __init__(self, script, sends):
        self.script = script
        self.sends = sends
        self.queue = []
        self.closed = False

    def send(self, ttl, port):
        self.sends.append((ttl, port))
        if ttl in self.script:
            ip, kind, ms = self.script[ttl]
            self.queue.append((ttl, ip, kind, ms))
        return ttl

    def wait(self, timeout):
        out, self.queue = self.queue, []
        return out

    def close(self):
        self.closed = True


def _script_factory(script, sends):
    made = []

    def factory(family, addr):
        prober = FakeProber(script, sends)
        made.append(prober)
        return prober
    factory.made = made
    return factory


class ClassifyTest(unittest.TestCase):
    def test_home_isp_internet(self):
        self.assertEqual(classify("192.168.1.1"), "home")
        self.assertEqual(classify("10.0.0.1"), "home")
        self.assertEqual(classify("100.64.3.1"), "isp")
        self.assertEqual(classify("203.0.113.5"), "internet")
        self.assertEqual(classify("fe80::1"), "home")

    def test_target_and_unknown(self):
        self.assertEqual(classify("203.0.113.5", TARGET), "target")
        self.assertEqual(classify(None), "unknown")
        self.assertEqual(classify("not an address"), "unknown")


class ResolveTest(unittest.TestCase):
    def test_flag_like_input_is_refused(self):
        with self.assertRaisesRegex(ValueError, "Not a valid address or name"):
            resolve_target("-f")

    def test_unknown_name(self):
        def fail(name):
            raise socket.gaierror("no such host")
        with self.assertRaisesRegex(ValueError, "Could not find"):
            resolve_target("example.test", resolver=fail)

    def test_prefers_ipv4(self):
        self.assertEqual(resolve_target(TARGET, resolver=_resolver(V6, V4)), (TARGET, socket.AF_INET))
        self.assertEqual(resolve_target("example.test", resolver=_resolver(V6)),
                         ("2001:db8::5", socket.AF_INET6))


class ParseRecverrTest(unittest.TestCase):
    def _v4(self, origin, etype, code, ip):
        head = struct.pack("=IBBBBII", 113, origin, etype, code, 0, 0, 0)
        return head + struct.pack("=HH4s8x", socket.AF_INET, 0, socket.inet_aton(ip))

    def test_ipv4_time_exceeded_is_a_hop(self):
        data = self._v4(2, 11, 0, "198.51.100.9")
        self.assertEqual(parse_recverr(trace.SOL_IP, trace.IP_RECVERR, data, socket.AF_INET),
                         ("198.51.100.9", "hop"))

    def test_ipv4_port_unreachable_is_reached(self):
        data = self._v4(2, 3, 3, TARGET)
        self.assertEqual(parse_recverr(trace.SOL_IP, trace.IP_RECVERR, data, socket.AF_INET),
                         (TARGET, "reached"))

    def test_ipv4_other_unreachable(self):
        data = self._v4(2, 3, 1, "192.0.2.9")
        self.assertEqual(parse_recverr(trace.SOL_IP, trace.IP_RECVERR, data, socket.AF_INET),
                         ("192.0.2.9", "unreachable"))

    def test_ipv6_time_exceeded_is_a_hop(self):
        head = struct.pack("=IBBBBII", 113, 3, 3, 0, 0, 0, 0)
        addr = socket.inet_pton(socket.AF_INET6, "2001:db8::1")
        data = head + struct.pack("=HHI16sI", socket.AF_INET6, 0, 0, addr, 0)
        self.assertEqual(parse_recverr(trace.SOL_IPV6, trace.IPV6_RECVERR, data, socket.AF_INET6),
                         ("2001:db8::1", "hop"))

    def test_wrong_level_short_or_other_origin_is_none(self):
        data = self._v4(2, 11, 0, "198.51.100.9")
        self.assertIsNone(parse_recverr(trace.SOL_IPV6, trace.IP_RECVERR, data, socket.AF_INET))
        self.assertIsNone(parse_recverr(trace.SOL_IP, trace.IP_RECVERR, data[:10], socket.AF_INET))
        self.assertIsNone(parse_recverr(trace.SOL_IP, trace.IP_RECVERR, self._v4(1, 11, 0, "192.0.2.1"),
                                        socket.AF_INET))


class ParseTracepathTest(unittest.TestCase):
    def test_sample(self):
        rows = parse_tracepath(TP_SAMPLE)
        self.assertEqual(rows, [
            (1, "192.0.2.1", 3.123, False),
            (2, None, None, False),
            (3, "198.51.100.9", 12.4, False),
            (9, TARGET, 24.1, True),
        ])


class RunTraceTest(unittest.TestCase):
    SCRIPT = {1: ("192.0.2.1", "hop", 2.0),
              3: ("198.51.100.9", "hop", 5.0),
              4: (TARGET, "reached", 9.0)}

    def test_builds_hops_and_reaches_target(self):
        sends = []
        factory = _script_factory(self.SCRIPT, sends)
        updates = []
        result = run_trace(TARGET, on_update=updates.append, resolver=_resolver(V4),
                           prober_factory=factory, clock=FakeClock())

        self.assertTrue(result.done)
        self.assertTrue(result.reached)
        self.assertEqual(result.method, "udp")
        self.assertIsNone(result.error)
        self.assertEqual([h.ttl for h in result.hops], [1, 2, 3, 4])

        silent = result.hops[1]
        self.assertIsNone(silent.ip)
        self.assertEqual(silent.rtts, (None, None, None))

        dest = result.hops[3]
        self.assertTrue(dest.reached)
        self.assertEqual(dest.kind, "target")
        self.assertEqual(len(dest.rtts), trace.PROBES)

        self.assertEqual(result.hops[0].kind, "internet")     # 192.0.2.x is a documentation address
        self.assertEqual(result.hops[2].kind, "internet")

        # The destination is found at TTL 4, so no round probes beyond it.
        self.assertEqual([t for t, _ in sends], [1, 2, 3, 4] * trace.PROBES)
        self.assertEqual([p for _, p in sends[:4]], [BASE_PORT + t for t in (1, 2, 3, 4)])
        self.assertEqual(len(factory.made), trace.PROBES)
        self.assertTrue(all(p.closed for p in factory.made))
        self.assertEqual(sends[0][1], BASE_PORT + 1)

        self.assertGreaterEqual(len(updates), 2)
        self.assertEqual(updates[-1], result)
        self.assertFalse(updates[0].done)

    def test_unreachable_ends_the_trace(self):
        script = {1: ("192.0.2.1", "hop", 2.0),
                  2: ("192.0.2.9", "unreachable", 4.0),
                  3: ("192.0.2.7", "hop", 6.0)}
        result = run_trace(TARGET, resolver=_resolver(V4), prober_factory=_script_factory(script, []),
                           clock=FakeClock(), max_hops=6)
        self.assertEqual([h.ttl for h in result.hops], [1, 2])
        self.assertFalse(result.reached)
        self.assertTrue(result.done)

    def test_never_more_than_max_hops(self):
        script = {t: ("192.0.2.%d" % t, "hop", 1.0) for t in range(1, 8)}
        result = run_trace(TARGET, max_hops=3, resolver=_resolver(V4),
                           prober_factory=_script_factory(script, []), clock=FakeClock())
        self.assertEqual(len(result.hops), 3)
        self.assertFalse(result.reached)

    def test_factory_failure_falls_back_to_tracepath(self):
        argvs = []

        def runner(argv, timeout):
            argvs.append((argv, timeout))
            return TP_SAMPLE

        def no_recverr(family, addr):
            raise OSError("RECVERR not allowed")

        result = run_trace(TARGET, resolver=_resolver(V4), prober_factory=no_recverr,
                           tracepath_runner=runner, clock=FakeClock())
        self.assertEqual(result.method, "tracepath")
        self.assertTrue(result.reached)
        self.assertTrue(result.done)
        self.assertEqual(result.hops[-1].ttl, 9)
        self.assertEqual(result.hops[-1].kind, "target")
        self.assertEqual([h.ttl for h in result.hops], [1, 2, 3, 9])
        self.assertEqual(argvs[0][0][:2], ["tracepath", "-n"])
        self.assertEqual(argvs[0][0][-1], TARGET)

    def test_tracepath_missing_reports_plain_error(self):
        def no_recverr(family, addr):
            raise OSError("no")
        result = run_trace(TARGET, resolver=_resolver(V4), prober_factory=no_recverr,
                           tracepath_runner=lambda argv, timeout: None, clock=FakeClock())
        self.assertTrue(result.done)
        self.assertIn("tracepath", result.error)

    def test_cancel_before_start(self):
        cancel = threading.Event()
        cancel.set()
        made = []

        def factory(family, addr):
            made.append(addr)
            return FakeProber({}, [])

        result = run_trace(TARGET, cancel=cancel, resolver=_resolver(V4), prober_factory=factory,
                           clock=FakeClock())
        self.assertEqual(result.error, "cancelled")
        self.assertTrue(result.done)
        self.assertEqual(made, [])

    def test_bad_target_is_an_error_result(self):
        result = run_trace("-f", resolver=_resolver(V4))
        self.assertTrue(result.done)
        self.assertIn("Not a valid address", result.error)


if __name__ == "__main__":
    unittest.main()
