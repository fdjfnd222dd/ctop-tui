"""Tests for the ports collector. Every socket row is an injected fake; nothing scans the machine."""
from __future__ import annotations

import socket
import unittest
from collections import namedtuple

import psutil

from ctop.collectors.ports import (
    MAX_CONNECTIONS, PortsCollector, ProcessNames, exposure_of, is_private_ip,
    read_connections, read_listeners)
from ctop.models import Connection, Listener

C = namedtuple("C", "fd family type laddr raddr status pid")
Addr = namedtuple("Addr", "ip port")
NAMES = {4242: "webdev", 4343: "avahi-daemon", 4545: "firefox"}.get


def tcp(ip, port, status="LISTEN", pid=None, raddr=()):
    family = socket.AF_INET6 if ":" in ip else socket.AF_INET
    return C(-1, family, socket.SOCK_STREAM, Addr(ip, port), raddr, status, pid)


def udp(ip, port, pid=None, raddr=()):
    family = socket.AF_INET6 if ":" in ip else socket.AF_INET
    return C(-1, family, socket.SOCK_DGRAM, Addr(ip, port), raddr, "NONE", pid)


def est(laddr_ip, lport, rip, rport, pid=None, status="ESTABLISHED"):
    return tcp(laddr_ip, lport, status=status, pid=pid, raddr=Addr(rip, rport))


class ExposureAndPrivacyTest(unittest.TestCase):

    def test_exposure_rules(self):
        for ip in ("127.0.0.1", "::1", "::ffff:127.0.0.1"):
            self.assertEqual(exposure_of(ip), "local", ip)
        for ip in ("0.0.0.0", "::"):
            self.assertEqual(exposure_of(ip), "network", ip)
        for ip in ("192.0.2.5", "2001:db8::5", "fe80::1%wlan0"):
            self.assertEqual(exposure_of(ip), "specific", ip)

    def test_is_private_ip(self):
        for ip in ("10.1.2.3", "172.16.0.1", "172.31.255.1", "192.168.1.5", "169.254.3.3",
                   "100.64.0.1", "100.127.255.1", "fd12::1", "fe80::1", "::1"):
            self.assertTrue(is_private_ip(ip), ip)
        for ip in ("172.32.0.1", "100.128.0.1", "203.0.113.9", "198.51.100.1",
                   "2001:db8::1", "garbage", ""):
            self.assertFalse(is_private_ip(ip), ip)


class ListenerTest(unittest.TestCase):

    def test_tcp_listener_dual_stack_pair_merges(self):
        rows = [tcp("0.0.0.0", 8080, pid=4242), tcp("::", 8080, pid=4242)]
        self.assertEqual(read_listeners(rows, NAMES),
                         (Listener("tcp", "0.0.0.0", 8080, 4242, "webdev", "network"),))

    def test_ipv6_only_listener_keeps_ipv6_address(self):
        rows = [tcp("::", 9000, pid=4242)]
        out = read_listeners(rows, NAMES)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].address, "::")

    def test_loopback_and_specific_not_merged_with_wildcard(self):
        rows = [tcp("127.0.0.1", 631, pid=4242), tcp("0.0.0.0", 631, pid=4242)]
        out = read_listeners(rows, NAMES)
        self.assertEqual(sorted(row.exposure for row in out), ["local", "network"])
        self.assertEqual(len(out), 2)

    def test_udp_listener_only_when_unconnected(self):
        rows = [
            udp("0.0.0.0", 5353, pid=4343),                              # listener
            udp("0.0.0.0", 40000, pid=4343, raddr=Addr("203.0.113.9", 53)),  # conversation
            udp("0.0.0.0", 0, pid=4343),                                 # port 0: skipped
        ]
        self.assertEqual(read_listeners(rows, NAMES),
                         (Listener("udp", "0.0.0.0", 5353, 4343, "avahi-daemon", "network"),))
        self.assertEqual(read_connections(rows, NAMES),
                         (Connection("udp", 40000, "203.0.113.9", 53, "NONE", 4343,
                                     "avahi-daemon", False),))

    def test_pid_none_gives_none_process(self):
        out = read_listeners([tcp("127.0.0.1", 631, pid=None)], NAMES)
        self.assertEqual(len(out), 1)
        self.assertIsNone(out[0].pid)
        self.assertIsNone(out[0].process)
        self.assertEqual(out[0].exposure, "local")

    def test_workers_sharing_a_socket_collapse(self):
        rows = [tcp("0.0.0.0", 80, pid=10), tcp("0.0.0.0", 80, pid=11)]
        out = read_listeners(rows, lambda pid: "webdev")
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].pid, 10)

    def test_listener_sort_by_port(self):
        rows = [tcp("0.0.0.0", 9000, pid=4242), tcp("0.0.0.0", 22, pid=4242),
                tcp("0.0.0.0", 631, pid=4242)]
        self.assertEqual([row.port for row in read_listeners(rows, NAMES)], [22, 631, 9000])


class ConnectionTest(unittest.TestCase):

    def test_connections_basic_fields(self):
        rows = [est("192.0.2.5", 51234, "203.0.113.9", 443, pid=4545)]
        self.assertEqual(read_connections(rows, NAMES),
                         (Connection("tcp", 51234, "203.0.113.9", 443, "ESTABLISHED",
                                     4545, "firefox", False),))

    def test_connections_drop_loopback_pairs_and_listen(self):
        rows = [
            est("127.0.0.1", 40000, "127.0.0.1", 631, pid=4242),   # loopback pair: drop
            tcp("0.0.0.0", 22, pid=4242),                          # LISTEN: drop
            udp("0.0.0.0", 5353, pid=4343),                        # unconnected UDP: drop
            est("127.0.0.1", 40001, "192.0.2.5", 443, pid=4242),   # kept
        ]
        out = read_connections(rows, NAMES)
        self.assertEqual([row.remote_ip for row in out], ["192.0.2.5"])

    def test_connection_private_flag(self):
        rows = [est("192.0.2.5", 40000, "192.168.1.20", 80),
                est("192.0.2.5", 40001, "100.64.0.9", 80),
                est("192.0.2.5", 40002, "203.0.113.9", 80)]
        flags = {row.remote_ip: row.remote_is_private for row in read_connections(rows, NAMES)}
        self.assertEqual(flags, {"192.168.1.20": True, "100.64.0.9": True, "203.0.113.9": False})

    def test_connection_sort_and_cap(self):
        established = [est("192.0.2.10", 40000 + i, "192.0.2.5", 1000 + i, status="ESTABLISHED")
                       for i in range(100)]
        # TIME_WAIT rows come first in the input, so only the sort can put them last.
        time_wait = [est("192.0.2.10", 50000 + i, "192.0.2.5", 2000 + i, status="TIME_WAIT")
                     for i in range(150)]
        out = read_connections(time_wait + established, NAMES)
        self.assertEqual(len(out), MAX_CONNECTIONS)
        self.assertEqual(out[0].status, "ESTABLISHED")
        statuses = [row.status for row in out]
        self.assertEqual(statuses[:100], ["ESTABLISHED"] * 100)

    def test_other_socket_types_ignored(self):
        raw_listen = C(-1, socket.AF_INET, socket.SOCK_RAW, Addr("0.0.0.0", 9999), (), "LISTEN", 4242)
        raw_conn = C(-1, socket.AF_INET, socket.SOCK_RAW, Addr("192.0.2.5", 1), Addr("203.0.113.9", 2),
                     "ESTABLISHED", 4242)
        self.assertEqual(read_listeners([raw_listen], NAMES), ())
        self.assertEqual(read_connections([raw_conn], NAMES), ())


class ProcessNamesTest(unittest.TestCase):

    def test_process_names_cache(self):
        now = [0.0]
        calls = []

        def lookup(pid):
            calls.append(pid)
            return "webdev"

        names = ProcessNames(ttl=60, clock=lambda: now[0], lookup=lookup)
        self.assertEqual(names(7), "webdev")
        now[0] = 30.0
        self.assertEqual(names(7), "webdev")
        self.assertEqual(len(calls), 1)
        now[0] = 61.0
        self.assertEqual(names(7), "webdev")
        self.assertEqual(len(calls), 2)

    def test_process_names_none_pid_and_failed_lookup_cached(self):
        calls = []

        def lookup(pid):
            calls.append(pid)
            raise psutil.NoSuchProcess(pid)

        names = ProcessNames(ttl=60, clock=lambda: 0.0, lookup=lookup)
        self.assertIsNone(names(None))
        self.assertEqual(calls, [])
        self.assertIsNone(names(9))
        self.assertIsNone(names(9))
        self.assertEqual(calls, [9])


class CollectorTest(unittest.TestCase):

    def test_collector_scans_once_and_returns_tuple(self):
        # pid=None rows keep the default name lookup away from real processes.
        rows = [tcp("0.0.0.0", 8080), est("192.0.2.5", 40000, "203.0.113.9", 443)]
        calls = []

        def conns_fn():
            calls.append(1)
            return iter(rows)

        collector = PortsCollector(conns_fn=conns_fn)
        collector.run()
        self.assertIsInstance(collector.data, tuple)
        self.assertEqual(len(collector.data), 2)
        listeners, connections = collector.data
        self.assertIsInstance(listeners, tuple)
        self.assertIsInstance(connections, tuple)
        self.assertEqual(len(calls), 1)
        self.assertIsNone(collector.error)


class EmptyInputTest(unittest.TestCase):

    def test_empty_input(self):
        self.assertEqual(read_listeners([], NAMES), ())
        self.assertEqual(read_connections([], NAMES), ())


if __name__ == "__main__":
    unittest.main()
