"""Tests for the program details popup lines (ctop/render/details.py). Documentation addresses only."""
import unittest
from dataclasses import replace

from rich.cells import cell_len

from ctop.models import Connection, Listener, ProcessDetail, ProgramDetail
from ctop.render.details import detail_lines

P1 = ProcessDetail(4101, "firefox", "/usr/lib/firefox/firefox -new-window", None, 1288490188, 84, 310, 1000.0, "running")
P2 = ProcessDetail(4188, "firefox", "", None, 440000000, None, None, 1000.0, "sleeping")
C1 = Connection("tcp", 50000, "203.0.113.5", 443, "ESTABLISHED", 4101, "firefox", False)
C2 = Connection("tcp", 50001, "192.0.2.20", 22000, "ESTABLISHED", 4101, "firefox", True)
LS = Listener("tcp", "127.0.0.1", 8080, 4101, "firefox", "local")
D = ProgramDetail("firefox", 3, (P1, P2), (C1, C2), (LS,), 1000.0, 1728490188, 142)
NOW = 1000.0 + 3 * 3600 + 12 * 60
FOOT = "Esc closes   x x stops this program"
LOCKED_FOOT = "Esc closes   this program is locked and cannot be stopped"


def plain(texts):
    return [t.plain.rstrip() for t in texts]


def find(lines, prefix):
    return next(line for line in lines if line.startswith(prefix))


class RenderDetailsTest(unittest.TestCase):
    def test_header(self):
        lines = plain(detail_lines(D, 90, 30, now=NOW))
        self.assertTrue(lines[0].startswith("firefox   3 processes"))
        self.assertEqual(find(lines, "RAM"), "RAM".ljust(12) + "1.61 GB")
        self.assertEqual(find(lines, "Threads"), "Threads     142")
        self.assertTrue(find(lines, "Running").startswith("Running     3h 12m"))

    def test_age_formats(self):
        for offset, expected in ((30, "30s"), (720, "12m"), (90000, "1d 1h")):
            lines = plain(detail_lines(D, 90, 30, now=1000.0 + offset))
            self.assertTrue(find(lines, "Running").startswith("Running".ljust(12) + expected))

    def test_unknown_start(self):
        lines = plain(detail_lines(replace(D, oldest_started=None), 90, 30, now=NOW))
        self.assertTrue(find(lines, "Running").startswith("Running     unknown"))

    def test_process_table_columns(self):
        hdr = find(plain(detail_lines(D, 90, 30, now=NOW)), "PID")
        positions = [hdr.index(k) for k in ("PID", "RAM", "Threads", "Files", "Command")]
        self.assertEqual(positions, sorted(positions))
        narrow = find(plain(detail_lines(D, 40, 30, now=NOW)), "PID")
        self.assertNotIn("Threads", narrow)

    def test_process_rows(self):
        lines = plain(detail_lines(D, 90, 30, now=NOW))
        row1 = find(lines, "4101")
        self.assertIn("84", row1)
        self.assertIn("310", row1)
        row2 = find(lines, "4188")
        self.assertEqual(row2.split()[-3:], ["-", "-", "firefox"])

    def test_command_trimmed_with_ellipsis(self):
        d = replace(D, processes=(replace(P1, cmdline="x" * 200),))
        texts = detail_lines(d, 60, 30, now=NOW)
        row = next(t.plain for t in texts if t.plain.startswith("4101"))
        self.assertTrue(row.rstrip().endswith("…"))
        self.assertEqual(cell_len(row), 60)

    def test_more_line(self):
        lines = plain(detail_lines(replace(D, total_processes=40), 90, 30, now=NOW))
        self.assertIn("... +38 more", lines)

    def test_connections_summary(self):
        lines = plain(detail_lines(D, 90, 30, now=NOW))
        self.assertIn("2 open: 1 to the internet, 1 on your local network", find(lines, "Connections"))
        self.assertTrue(any("203.0.113.5:443" in line for line in lines))
        self.assertTrue(any("192.0.2.20:22000" in line for line in lines))
        narrow = plain(detail_lines(D, 40, 30, now=NOW))
        self.assertIn("2 open (1 internet, 1 local)", find(narrow, "Connections"))

    def test_no_connections_or_ports(self):
        lines = plain(detail_lines(replace(D, connections=(), listeners=()), 90, 30, now=NOW))
        self.assertIn("none open", find(lines, "Connections"))
        self.assertIn("no open ports", find(lines, "Listening"))

    def test_listening_words(self):
        lines = plain(detail_lines(D, 90, 30, now=NOW))
        self.assertIn("1 port", find(lines, "Listening"))
        self.assertTrue(any("TCP 8080" in line for line in lines))
        self.assertTrue(any("only this computer" in line for line in lines))

    def test_footer_normal_and_locked(self):
        lines = plain(detail_lines(D, 90, 30, now=NOW))
        self.assertEqual(lines[-1], FOOT)
        locked = plain(detail_lines(D, 90, 30, now=NOW, locked=True))
        self.assertEqual(locked[-1], LOCKED_FOOT)

    def test_height_fit_and_footer_kept(self):
        for h in (1, 2, 5, 9, 12, 20, 30):
            lines = plain(detail_lines(D, 90, h, now=NOW))
            self.assertLessEqual(len(lines), h)
            self.assertEqual(lines[-1], FOOT)
        full = plain(detail_lines(D, 90, 30, now=NOW))
        for label in ("PID", "Connections", "Listening"):
            self.assertTrue(any(line.startswith(label) for line in full))
        mid = plain(detail_lines(D, 90, 12, now=NOW))
        self.assertTrue(any(line.startswith("Connections") for line in mid))

    def test_width_guarantee(self):
        for w in (1, 12, 40, 90):
            for t in detail_lines(D, w, 30, now=NOW):
                self.assertEqual(cell_len(t.plain), w)
                self.assertTrue(t.no_wrap)

    def test_no_None_text(self):
        for w, h in ((90, 30), (40, 20), (40, 5)):
            for line in plain(detail_lines(D, w, h, now=NOW)):
                self.assertNotIn("None", line)


if __name__ == "__main__":
    unittest.main()
