"""Tests for the Security page port and connection tables (ctop/render/security_ports.py)."""
from __future__ import annotations

import unittest
from typing import List

from rich.cells import cell_len
from rich.text import Text

from ctop.models import Connection, FirewallInfo, Listener
from ctop.render.security_ports import connections_lines, listeners_lines

A = Listener("tcp", "0.0.0.0", 8080, 10, "python3", "network")
A6 = Listener("tcp", "::", 8080, 10, "python3", "network")
B = Listener("tcp", "127.0.0.1", 631, 11, "cupsd", "local")
C = Listener("udp", "192.0.2.5", 5353, None, None, "specific")
FW = FirewallInfo("ufw", True, "deny", False)
FWOFF = FirewallInfo("ufw", False, "allow", False)

K1 = Connection("tcp", 50000, "203.0.113.5", 443, "ESTABLISHED", 20, "firefox", False)
K2 = Connection("tcp", 50001, "198.51.100.9", 443, "ESTABLISHED", 20, "firefox", False)
K3 = Connection("tcp", 50002, "192.0.2.20", 22000, "ESTABLISHED", 21, "syncthing", True)
K4 = Connection("tcp", 50003, "203.0.113.5", 443, "TIME_WAIT", None, None, False)


def _plain(lines: List[Text]) -> List[str]:
    return [t.plain.rstrip() for t in lines]


def _styles_at(t: Text, phrase: str) -> List[str]:
    """Styles of every span that covers `phrase` inside the line."""
    start = t.plain.find(phrase)
    if start < 0:
        return []
    end = start + len(phrase)
    return [s.style for s in t.spans if s.start <= start and s.end >= end]


class ListenerTests(unittest.TestCase):

    def test_listener_dedupes_v4_v6(self):
        lines = _plain(listeners_lines([A, A6], FW, 90, 10))
        rows = [line for line in lines[1:] if not line.startswith("Firewall:")]
        self.assertEqual(len(rows), 1)

    def test_listener_sort_order(self):
        lines = listeners_lines([B, A, C], FW, 90, 10)
        self.assertTrue(lines[1].plain.startswith("8080"))
        self.assertTrue(lines[2].plain.startswith("5353"))
        self.assertTrue(lines[3].plain.startswith("631"))

    def test_listener_plain_words_full(self):
        lines = _plain(listeners_lines([A, B, C], FW, 90, 10))
        row_a = next(line for line in lines if line.startswith("8080"))
        row_b = next(line for line in lines if line.startswith("631"))
        row_c = next(line for line in lines if line.startswith("5353"))
        self.assertIn("everyone on your network", row_a)
        self.assertIn("only this computer", row_b)
        self.assertIn("one network address", row_c)

    def test_listener_short_words_narrow(self):
        text = "\n".join(_plain(listeners_lines([A, B, C], FW, 40, 10)))
        self.assertIn("your network", text)
        self.assertNotIn("everyone", text)

    def test_listener_colors(self):
        row_b = next(t for t in listeners_lines([B], FW, 90, 10) if t.plain.startswith("631"))
        self.assertIn("#56d364", _styles_at(row_b, "only this computer"))
        row_a_off = next(t for t in listeners_lines([A], FWOFF, 90, 10) if t.plain.startswith("8080"))
        self.assertIn("#ff7b72", _styles_at(row_a_off, "everyone on your network"))
        row_a_on = next(t for t in listeners_lines([A], FW, 90, 10) if t.plain.startswith("8080"))
        self.assertIn("#f2cc60", _styles_at(row_a_on, "everyone on your network"))

    def test_firewall_note_footer_when_narrow(self):
        lines = _plain(listeners_lines([A, B, C], FW, 90, 10))
        self.assertTrue(lines[-1].startswith("Firewall: blocks these unless a rule allows them."))
        for firewall in (FWOFF, None):
            text = "\n".join(_plain(listeners_lines([A, B, C], firewall, 90, 10)))
            self.assertNotIn("Firewall:", text)

    def test_firewall_suffix_inline_when_wide(self):
        lines = _plain(listeners_lines([A], FW, 120, 10))
        self.assertIn("(firewall blocks it unless a rule allows)", lines[1])
        self.assertFalse(any(line.startswith("Firewall:") for line in lines))

    def test_unknown_owner(self):
        row_c = next(t for t in listeners_lines([C], FW, 90, 10) if t.plain.startswith("5353"))
        self.assertIn("(other user)", row_c.plain)

    def test_listeners_cut_with_more(self):
        many = [Listener("tcp", "127.0.0.1", port, None, None, "local") for port in range(1000, 1010)]
        lines = listeners_lines(many, None, 90, 5)
        self.assertEqual(len(lines), 5)
        self.assertTrue(lines[-1].plain.startswith("... +"))
        self.assertEqual(lines[-1].plain.rstrip(), "... +%d more" % (10 - 3))

    def test_listeners_empty(self):
        self.assertEqual(_plain(listeners_lines([], None, 90, 10)),
                         ["Nothing is listening for connections."])


class ConnectionTests(unittest.TestCase):

    def test_connections_grouping(self):
        lines = _plain(connections_lines([K1, K2, K3, K4], 90, 10))
        self.assertEqual(len(lines), 3)  # header + two programs
        firefox = lines[1]
        self.assertTrue(firefox.startswith("firefox"))
        self.assertIn("   2 ", firefox)  # TIME_WAIT (K4) is not counted
        self.assertIn("203.0.113.5:443, 198.51.100.9:443", firefox)
        self.assertIn("internet", firefox)
        self.assertIn("local network", lines[2])

    def test_connections_ipv6_brackets(self):
        conn = Connection("tcp", 50004, "2001:db8::1", 443, "ESTABLISHED", 20, "firefox", False)
        text = "\n".join(_plain(connections_lines([conn], 90, 10)))
        self.assertIn("[2001:db8::1]:443", text)

    def test_connections_narrow_has_no_examples(self):
        text = "\n".join(_plain(connections_lines([K1, K2, K3], 40, 10)))
        self.assertNotIn("203.0.113", text)

    def test_connections_empty(self):
        self.assertEqual(_plain(connections_lines([], 90, 10)), ["No open connections right now."])


class GuaranteeTests(unittest.TestCase):

    def test_width_and_height_guarantees(self):
        fixtures = [
            ([A, A6, B, C], FW),
            ([A, B, C], FWOFF),
            ([], None),
        ]
        conn_fixtures = [
            [K1, K2, K3, K4],
            [],
        ]
        for w in (1, 12, 40, 90):
            for h in (0, 1, 2, 3, 20):
                for listeners, firewall in fixtures:
                    lines = listeners_lines(listeners, firewall, w, h)
                    self.assertLessEqual(len(lines), h)
                    for t in lines:
                        self.assertEqual(cell_len(t.plain), w)
                for conns in conn_fixtures:
                    lines = connections_lines(conns, w, h)
                    self.assertLessEqual(len(lines), h)
                    for t in lines:
                        self.assertEqual(cell_len(t.plain), w)


if __name__ == "__main__":
    unittest.main()
