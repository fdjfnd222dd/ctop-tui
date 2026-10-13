"""Tests for ctop/tools/portcheck.py. Every probe and resolver is an injected fake: no sockets, no sleeping."""
from __future__ import annotations

import errno
import socket
import threading
import unittest

from ctop.tools.portcheck import (
    MAX_PORTS, parse_ports, run_ports, service_name, tcp_probe,
)

TARGET = "192.0.2.10"
V4 = (socket.AF_INET, socket.SOCK_DGRAM, 17, "", (TARGET, 0))


def _resolver(*entries):
    return lambda name: list(entries)


def _failing_resolver(name):
    raise socket.gaierror("no such host")


class ParsePortsTest(unittest.TestCase):
    def test_mixed_separators_and_range(self):
        self.assertEqual(parse_ports("22, 80 443 8000-8003"),
                         ((22, 80, 443, 8000, 8001, 8002, 8003), None))

    def test_duplicates_removed_and_sorted(self):
        self.assertEqual(parse_ports("443 22 443,22"), ((22, 443), None))

    def test_errors_have_plain_reasons(self):
        cases = {
            "0": "Ports must be between 1 and 65535",
            "65536": "Ports must be between 1 and 65535",
            "abc": "Not a port number",
            "9-3": "Range start is above its end",
            "": "Type at least one port",
            "   ": "Type at least one port",
            "1-65535": "At most 32 ports at a time",
            ",".join(str(p) for p in range(1, 34)): "At most 32 ports at a time",
        }
        for text, reason in cases.items():
            ports, error = parse_ports(text)
            self.assertEqual(ports, (), text)
            self.assertIn(reason, error, text)

    def test_exactly_the_cap_is_allowed(self):
        ports, error = parse_ports("1-%d" % MAX_PORTS)
        self.assertIsNone(error)
        self.assertEqual(len(ports), MAX_PORTS)


class ServiceNameTest(unittest.TestCase):
    def test_known_and_unknown(self):
        self.assertEqual(service_name(443), "HTTPS")
        self.assertEqual(service_name(12345), "")


class RunPortsTest(unittest.TestCase):
    STATES = {22: ("closed", 3.0), 80: ("open", 4.5), 443: ("open", 5.0), 3389: ("filtered", None)}

    def _probe(self, calls=None):
        def probe(address, port, timeout, family):
            if calls is not None:
                calls.append((address, port, timeout, family))
            return self.STATES[port]
        return probe

    def test_states_order_and_done(self):
        updates = []
        result = run_ports(TARGET, (3389, 22, 80, 443), on_update=updates.append,
                           resolver=_resolver(V4), probe=self._probe())
        self.assertTrue(result.done)
        self.assertIsNone(result.error)
        self.assertEqual(result.address, TARGET)
        self.assertEqual([r.port for r in result.results], [22, 80, 443, 3389])
        states = {r.port: r.state for r in result.results}
        self.assertEqual(states, {22: "closed", 80: "open", 443: "open", 3389: "filtered"})
        self.assertEqual(result.results[0].service, "SSH")
        self.assertEqual(result.results[1].ms, 4.5)

        # The first update shows every port as pending before any probe finished.
        self.assertTrue(all(r.state == "pending" for r in updates[0].results))
        self.assertFalse(updates[0].done)
        self.assertEqual(updates[-1], result)

    def test_too_many_ports_never_probes(self):
        calls = []
        result = run_ports(TARGET, tuple(range(1, 34)), resolver=_resolver(V4), probe=self._probe(calls))
        self.assertTrue(result.done)
        self.assertIn("At most 32", result.error)
        self.assertEqual(calls, [])

    def test_resolver_failure_is_an_error(self):
        calls = []
        result = run_ports("example.test", (80,), resolver=_failing_resolver, probe=self._probe(calls))
        self.assertTrue(result.done)
        self.assertIn("Could not find", result.error)
        self.assertEqual(calls, [])

    def test_probe_gets_resolved_address_and_timeout(self):
        calls = []
        run_ports(TARGET, (80,), timeout=2.5, resolver=_resolver(V4), probe=self._probe(calls))
        self.assertEqual(calls, [(TARGET, 80, 2.5, socket.AF_INET)])

    def test_cancel_before_start(self):
        cancel = threading.Event()
        cancel.set()
        calls = []
        result = run_ports(TARGET, (80,), cancel=cancel, resolver=_resolver(V4), probe=self._probe(calls))
        self.assertEqual(result.error, "cancelled")
        self.assertTrue(result.done)
        self.assertEqual(calls, [])

    def test_a_crashing_probe_marks_only_its_port(self):
        def probe(address, port, timeout, family):
            if port == 22:
                raise RuntimeError("boom")
            return ("open", 1.0)
        result = run_ports(TARGET, (22, 80), resolver=_resolver(V4), probe=probe)
        states = {r.port: r.state for r in result.results}
        self.assertEqual(states, {22: "error", 80: "open"})


class FakeSocket:
    """Records what happened to it. connect_ex returns `code` or raises `exc`."""
    def __init__(self, code=0, exc=None):
        self.code = code
        self.exc = exc
        self.closed = False
        self.sent = []
        self.timeout = None

    def settimeout(self, value):
        self.timeout = value

    def connect_ex(self, addr):
        if self.exc is not None:
            raise self.exc
        return self.code

    def send(self, data):
        self.sent.append(data)

    sendall = send

    def close(self):
        self.closed = True


class TcpProbeTest(unittest.TestCase):
    def _run(self, **kwargs):
        made = []

        def factory(family, kind):
            sock = FakeSocket(**kwargs)
            made.append(sock)
            return sock
        state = tcp_probe(TARGET, 80, timeout=1.0, clock=iter([1.0, 1.25]).__next__,
                          sock_factory=factory)
        return state, made[0]

    def test_open(self):
        (state, ms), sock = self._run(code=0)
        self.assertEqual(state, "open")
        self.assertAlmostEqual(ms, 250.0)
        self.assertTrue(sock.closed)
        self.assertEqual(sock.sent, [])

    def test_refused_is_closed(self):
        (state, ms), sock = self._run(code=errno.ECONNREFUSED)
        self.assertEqual(state, "closed")
        self.assertTrue(sock.closed)

    def test_timeout_is_filtered(self):
        state, sock = self._run(exc=socket.timeout("timed out"))
        self.assertEqual(state[0], "filtered")
        self.assertTrue(sock.closed)
        self.assertEqual(sock.timeout, 1.0)

    def test_unreachable_is_error(self):
        (state, ms), sock = self._run(code=errno.EHOSTUNREACH)
        self.assertEqual(state, "error")
        self.assertIsNone(ms)
        self.assertTrue(sock.closed)
        self.assertEqual(sock.sent, [])

    def test_no_socket_available_is_error(self):
        def factory(family, kind):
            raise OSError(errno.EMFILE, "too many open files")
        self.assertEqual(tcp_probe(TARGET, 80, sock_factory=factory), ("error", None))


if __name__ == "__main__":
    unittest.main()
