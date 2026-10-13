"""DNS timing tool: wire format, resolver parsing, timing and verdicts. No network, no commands, no sleeps."""
from __future__ import annotations

import os
import socket
import struct
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ctop.tools import dnscheck as dc  # noqa: E402


class FakeClock:
    """Only moves when told to, so timing is exact and nothing sleeps."""

    def __init__(self, step: float = 0.0) -> None:
        self.now = 0.0
        self.step = step

    def __call__(self) -> float:
        self.now += self.step
        return self.now


class FakeSocket:
    def __init__(self, replies=(), timeout_after: bool = True) -> None:
        self.replies = list(replies)
        self.timeout_after = timeout_after
        self.sent = []
        self.closed = False

    def settimeout(self, value) -> None:
        self.timeout = value

    def sendto(self, data, addr) -> int:
        self.sent.append((data, addr))
        return len(data)

    def recvfrom(self, size):
        if self.replies:
            return self.replies.pop(0), ("192.0.2.1", 53)
        raise socket.timeout("timed out")

    def close(self) -> None:
        self.closed = True


def reply_for(qid: int, rcode: int = 0) -> bytes:
    return struct.pack(">HHHHHH", qid, 0x8180 | rcode, 1, 1, 0, 0) + b"\x00" * 10


class BuildQuery(unittest.TestCase):
    def test_exact_bytes(self):
        data = dc.build_query("example.com", 0x1234)
        expected = (bytes.fromhex("123401000001000000000000")
                    + b"\x07example\x03com\x00" + bytes.fromhex("00010001"))
        self.assertEqual(data, expected)


class ParseReply(unittest.TestCase):
    def test_matching_reply_gives_rcode(self):
        self.assertEqual(dc.parse_reply(reply_for(0x1234, 0), 0x1234), 0)
        self.assertEqual(dc.parse_reply(reply_for(0x1234, 3), 0x1234), 3)

    def test_wrong_id_is_none(self):
        self.assertIsNone(dc.parse_reply(reply_for(0x1234), 0x9999))

    def test_no_qr_bit_is_none(self):
        query_like = struct.pack(">HHHHHH", 0x1234, 0x0100, 1, 0, 0, 0)
        self.assertIsNone(dc.parse_reply(query_like, 0x1234))

    def test_short_data_is_none(self):
        self.assertIsNone(dc.parse_reply(b"\x12\x34\x81", 0x1234))


class ResolverParsing(unittest.TestCase):
    def test_read_resolv_skips_comments_and_options(self):
        text = "# generated\n; also a comment\noptions edns0\nnameserver 192.0.2.1\n\nnameserver 2001:db8::1\n"
        self.assertEqual(dc.read_resolv(text), ["192.0.2.1", "2001:db8::1"])

    def test_parse_resolvectl(self):
        text = ("Global: 192.0.2.1\n"
                "Link 2 (eth0): 192.0.2.10\n"
                "Link 3 (wlan0): 192.0.2.1 2001:db8::1\n")
        self.assertEqual(dc.parse_resolvectl(text),
                         ["192.0.2.1", "192.0.2.10", "192.0.2.1", "2001:db8::1"])

    def test_parse_nmcli_dns(self):
        text = "IP4.DNS[1]:192.0.2.1\nIP4.DNS[2]:198.51.100.7\nIP6.DNS[1]:2001:db8::53\nIP4.ADDRESS[1]:192.0.2.23/24\n"
        self.assertEqual(dc.parse_nmcli_dns(text), ["192.0.2.1", "198.51.100.7", "2001:db8::53"])


class ServersInUse(unittest.TestCase):
    def _resolv(self, text: str) -> str:
        import tempfile
        fd, path = tempfile.mkstemp()
        with os.fdopen(fd, "w") as fh:
            fh.write(text)
        self.addCleanup(os.remove, path)
        return path

    def test_stub_only_uses_resolvectl(self):
        path = self._resolv("nameserver 127.0.0.53\noptions edns0\n")
        calls = []

        def runner(argv, timeout):
            calls.append(argv)
            return "Global: 192.0.2.1\nLink 3 (wlan0): 192.0.2.1 2001:db8::1\n" if argv[0] == "resolvectl" else None

        self.assertEqual(dc.servers_in_use(path, runner), ["192.0.2.1", "2001:db8::1"])
        self.assertEqual(calls, [["resolvectl", "dns"]])

    def test_stub_with_resolvectl_failing_uses_nmcli(self):
        path = self._resolv("nameserver 127.0.0.53\n")

        def runner(argv, timeout):
            if argv[0] == "resolvectl":
                return None
            return "IP4.DNS[1]:198.51.100.7\nIP6.DNS[1]:fe80::1%wlan0\n"

        self.assertEqual(dc.servers_in_use(path, runner), ["198.51.100.7", "fe80::1"])

    def test_garbage_dropped_and_dupes_removed(self):
        path = self._resolv("nameserver 192.0.2.1\nnameserver not-an-ip\nnameserver 192.0.2.1\n"
                            "nameserver 999.1.1.1\nnameserver fe80::1%wlan0\n")
        self.assertEqual(dc.servers_in_use(path, lambda a, t: None), ["192.0.2.1", "fe80::1"])

    def test_local_dnsmasq_kept_without_runner(self):
        path = self._resolv("nameserver 127.0.0.1\n")

        def runner(argv, timeout):
            raise AssertionError("no command needed when resolv.conf has real entries")

        self.assertEqual(dc.servers_in_use(path, runner), ["127.0.0.1"])

    def test_missing_file_and_no_commands_is_empty(self):
        self.assertEqual(dc.servers_in_use("/nonexistent/ctop-test", lambda a, t: None), [])

    def test_max_four(self):
        text = "".join(f"nameserver 192.0.2.{i}\n" for i in range(1, 8))
        path = self._resolv(text)
        self.assertEqual(len(dc.servers_in_use(path, lambda a, t: None)), 4)


class LabelFor(unittest.TestCase):
    def test_labels(self):
        self.assertEqual(dc.label_for("192.0.2.1", "192.0.2.1"), "your router")
        self.assertEqual(dc.label_for("1.0.0.1", None), "Cloudflare")
        self.assertEqual(dc.label_for("127.0.0.1", None), "local cache")
        self.assertEqual(dc.label_for("::1", None), "local cache")
        self.assertEqual(dc.label_for("198.51.100.7", "192.0.2.1"), "configured")


class QueryOnce(unittest.TestCase):
    def test_matching_reply_gives_milliseconds(self):
        # The query id is random, so this fake answers with whatever id it was sent,
        # and the fake clock moves 20 ms during the send.
        clock = FakeClock(step=0.0)

        class EchoSocket(FakeSocket):
            def sendto(self, data, addr):
                clock.now += 0.02
                self.replies.append(reply_for(struct.unpack(">H", data[:2])[0]))
                return super().sendto(data, addr)

        sock = EchoSocket()
        ms = dc.query_once("192.0.2.1", "example.com", timeout=1.0,
                           sock_factory=lambda fam: sock, clock=clock)
        self.assertIsInstance(ms, float)
        self.assertAlmostEqual(ms, 20.0, places=6)
        self.assertEqual(sock.sent[0][1], ("192.0.2.1", 53))
        self.assertTrue(sock.closed)

    def test_timeout_is_none_and_socket_closed(self):
        sock = FakeSocket(replies=())
        clock = FakeClock(step=0.6)
        self.assertIsNone(dc.query_once("192.0.2.1", "example.com", timeout=1.0,
                                        sock_factory=lambda fam: sock, clock=clock))
        self.assertTrue(sock.closed)

    def test_wrong_id_replies_ignored_until_timeout(self):
        sock = FakeSocket(replies=[reply_for(0xBEEF)])
        clock = FakeClock(step=0.0)
        self.assertIsNone(dc.query_once("192.0.2.1", "example.com", timeout=1.0,
                                        sock_factory=lambda fam: sock, clock=clock))
        self.assertTrue(sock.closed)

    def test_os_error_is_none_and_socket_closed(self):
        class BrokenSocket(FakeSocket):
            def sendto(self, data, addr):
                raise OSError("network unreachable")

        sock = BrokenSocket()
        self.assertIsNone(dc.query_once("192.0.2.1", "example.com", sock_factory=lambda fam: sock))
        self.assertTrue(sock.closed)

    def test_bad_server_is_none_without_socket(self):
        def factory(fam):
            raise AssertionError("no socket for a bad address")

        self.assertIsNone(dc.query_once("not-an-address", "example.com", sock_factory=factory))


def fake_query_factory(table):
    """table: server -> ms or None for every name."""
    def query(server, name):
        return table[server]
    return query


class RunDns(unittest.TestCase):
    def test_rows_order_median_and_verdict(self):
        updates = []
        table = {"192.0.2.1": 120.0, "1.1.1.1": 14.0, "9.9.9.9": 20.0, "8.8.8.8": 30.0}
        report = dc.run_dns(gateway="192.0.2.1", on_update=updates.append,
                            servers=["192.0.2.1"], query=fake_query_factory(table))
        self.assertTrue(report.done)
        servers = [r.server for r in report.timings]
        self.assertEqual(servers, ["192.0.2.1", "1.1.1.1", "9.9.9.9", "8.8.8.8"])
        first = report.timings[0]
        self.assertTrue(first.in_use)
        self.assertEqual(first.label, "your router")
        self.assertEqual(first.ms, 120.0)
        self.assertEqual(first.answered, 3)
        self.assertEqual(first.tries, 3)
        self.assertIsNone(first.error)
        self.assertEqual(report.timings[1].label, "Cloudflare")
        self.assertIn("Switching DNS", report.verdict)
        self.assertIn("120 ms", report.verdict)
        self.assertIn("Cloudflare takes 14 ms", report.verdict)
        self.assertTrue(updates)
        self.assertFalse(updates[0].done)
        self.assertIsNone(updates[0].timings[0].ms)
        self.assertEqual(updates[0].timings[0].tries, 0)

    def test_fine_dns_verdict(self):
        table = {"192.0.2.1": 15.0, "1.1.1.1": 14.0, "9.9.9.9": 20.0, "8.8.8.8": 30.0}
        report = dc.run_dns(servers=["192.0.2.1"], query=fake_query_factory(table))
        self.assertEqual(report.verdict, "Your DNS is fine (15 ms).")

    def test_every_in_use_failing_gives_not_answering(self):
        table = {"192.0.2.1": None, "1.1.1.1": 14.0, "9.9.9.9": 20.0, "8.8.8.8": 30.0}
        report = dc.run_dns(servers=["192.0.2.1"], query=fake_query_factory(table))
        self.assertIn("not answering", report.verdict)
        self.assertEqual(report.timings[0].error, "no answer")
        self.assertEqual(report.timings[0].tries, 3)

    def test_no_in_use_servers(self):
        table = {"1.1.1.1": 14.0, "9.9.9.9": 20.0, "8.8.8.8": 30.0}
        report = dc.run_dns(servers=[], query=fake_query_factory(table))
        self.assertEqual(report.verdict, "Could not find which DNS server this computer uses.")

    def test_public_blocked_still_reports_in_use(self):
        table = {"192.0.2.1": 25.0, "1.1.1.1": None, "9.9.9.9": None, "8.8.8.8": None}
        report = dc.run_dns(servers=["192.0.2.1"], query=fake_query_factory(table))
        self.assertIn("public servers did not answer", report.verdict)

    def test_cancel_returns_promptly(self):
        cancel = threading.Event()
        cancel.set()
        started = time.perf_counter()

        def slow_query(server, name):
            raise AssertionError("cancelled before any query should run")

        report = dc.run_dns(servers=["192.0.2.1"], cancel=cancel, query=slow_query)
        self.assertLess(time.perf_counter() - started, 0.5)
        self.assertTrue(report.done)
        self.assertEqual(report.verdict, "Cancelled.")

    def test_cancel_is_also_sent_to_the_page(self):
        cancel = threading.Event()
        cancel.set()
        updates = []
        report = dc.run_dns(servers=["192.0.2.1"], cancel=cancel, on_update=updates.append,
                            query=lambda server, name: 10.0)
        self.assertEqual(updates[-1], report)
        self.assertEqual(updates[-1].verdict, "Cancelled.")

    def test_query_exception_does_not_escape(self):
        def boom(server, name):
            raise RuntimeError("bad resolver state")

        report = dc.run_dns(servers=["192.0.2.1"], query=boom)
        self.assertTrue(report.done)
        self.assertTrue(report.verdict)

    def test_broken_callback_does_not_stop_run(self):
        def bad(_report):
            raise ValueError("ui gone")

        table = {"192.0.2.1": 15.0, "1.1.1.1": 14.0, "9.9.9.9": 20.0, "8.8.8.8": 30.0}
        report = dc.run_dns(servers=["192.0.2.1"], on_update=bad, query=fake_query_factory(table))
        self.assertTrue(report.done)


if __name__ == "__main__":
    unittest.main()
