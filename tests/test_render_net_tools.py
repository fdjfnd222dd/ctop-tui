"""Tests for ctop/render/net_tools.py - the Tools box: menu, idle guide and every tool's result view."""
from __future__ import annotations

import unittest

from rich.text import Text

from ctop.models import (Diagnosis, DnsReport, DnsTiming, DoctorStep, FoundDevice, LanScanResult,
                         PortCheckResult, PortResult, SpeedResult, TraceHop, TraceResult)
from ctop.render.net_tools import (CURSOR_ON, FIX_HINT, dns_lines, doctor_lines, idle_lines, lan_lines,
                                   menu_lines, message_lines, ports_lines, speed_lines, target_lines,
                                   tool_view_lines, trace_lines)

WIDTHS = (30, 44, 56, 68, 100)
HEIGHTS = (3, 6, 16)


def plain(lines):
    return [ln.plain.rstrip() for ln in lines]


def styles_of(lines):
    """Every style string on the lines: the line's own style and each span's style."""
    out = []
    for ln in lines:
        if ln.style:
            out.append(str(ln.style))
        out.extend(str(s.style) for s in ln.spans)
    return out


def has_colour(lines, colour):
    return any(colour in st for st in styles_of(lines))


def text_of(lines):
    return "\n".join(plain(lines))


def check_contract(tc, lines, w, h):
    tc.assertLessEqual(len(lines), h)
    for ln in lines:
        tc.assertIsInstance(ln, Text)
        tc.assertTrue(ln.no_wrap)
        tc.assertLessEqual(ln.cell_len, w)


def sample_trace(reached=True, done=True):
    hops = (
        TraceHop(ttl=1, ip="192.0.2.1", rtts=(2.8, 3.1, 3.0), kind="home"),
        TraceHop(ttl=2, ip="100.64.0.1", rtts=(11.9, 12.4, 12.0), kind="isp"),
        TraceHop(ttl=3, ip=None, rtts=(None, None, None), kind="unknown"),
        TraceHop(ttl=4, ip="198.51.100.9", rtts=(18.0, None, 20.0), kind="internet"),
    )
    if reached:
        hops = hops + (TraceHop(ttl=9, ip="203.0.113.5", rtts=(23.0, 24.0, 25.0), kind="target", reached=True),)
    return TraceResult(target="example.org", address="203.0.113.5", hops=hops, done=done, reached=reached,
                       method="udp")


def long_trace(n=15):
    hops = tuple(TraceHop(ttl=i + 1, ip=f"198.51.100.{i + 1}", rtts=(10.0 + i,), kind="internet")
                 for i in range(n))
    return TraceResult(target="203.0.113.5", address="203.0.113.5", hops=hops, done=False, reached=False)


class MenuTests(unittest.TestCase):
    ITEMS = [("", "Network", ""), ("doctor", "Network doctor", "d"), ("trace", "Trace & ports", "t"),
             ("", "", ""), ("speed", "Speed test", "s"), ("lan", "Find devices", "f")]

    def test_separator_is_never_the_cursor(self):
        lines = menu_lines(self.ITEMS, 3, True, None, 22, 16)
        sep = plain(lines)[3]
        self.assertEqual(sep, "─" * 22)
        for ln in lines:
            self.assertNotIn(CURSOR_ON, [str(s.style) for s in ln.spans])

    def test_cursor_row_highlighted(self):
        lines = menu_lines(self.ITEMS, 2, True, None, 22, 16)
        self.assertIn(CURSOR_ON, [str(s.style) for s in lines[2].spans])

    def test_running_prefix(self):
        lines = menu_lines(self.ITEMS, 0, False, "trace", 22, 16)
        row = plain(lines)[2]
        self.assertTrue(row.startswith("◐ Trace"))
        self.assertTrue(plain(lines)[1].startswith("  Network doctor"))

    def test_shortcut_right_aligned_at_22(self):
        row = plain(menu_lines(self.ITEMS, 0, False, None, 22, 16))[1]
        self.assertEqual(len(row), 22)
        self.assertTrue(row.endswith(" d"))
        self.assertEqual(row.split()[-1], "d")

    def test_scrolls_to_cursor(self):
        many = [("k%d" % i, "Item %d" % i, "") for i in range(30)]
        lines = plain(menu_lines(many, 29, True, None, 22, 5))
        self.assertEqual(len(lines), 5)
        self.assertIn("Item 29", lines[-1])


class ContractTests(unittest.TestCase):
    """Every renderer: no line wider than w, never more than h lines, no exception for None."""

    def test_all_functions_respect_size(self):
        trace = sample_trace()
        ports = PortCheckResult(target="203.0.113.5", address="203.0.113.5", done=True, results=(
            PortResult(443, "HTTPS", "open", 12.0), PortResult(22, "SSH", "closed")))
        dns = DnsReport(timings=(DnsTiming("192.0.2.1", "your router", True, 120.0, 3, 3),
                                 DnsTiming("9.9.9.9", "Quad9", False, None, 0, 3, error="timeout")),
                        done=True, verdict="Switching to a faster server would help.")
        speed = SpeedResult(stage="download", progress=0.5, idle_ms=14.0, loaded_ms=52.0,
                            down_mbps=245.3, grade="B", samples=(10.0, 40.0, 245.3), bytes_used=31_000_000)
        doctor = Diagnosis(steps=(DoctorStep("link", "Wi-Fi link", "ok", "5 GHz"),
                                  DoctorStep("dns", "DNS", "warn", "slow")),
                           done=True, level="warn", verdict="Name lookup is slow.", suggestion="dns")
        lan = LanScanResult(subnet="192.0.2.0/24", devices=(FoundDevice("192.0.2.1", "02:00:00:AA:BB:CC",
                                                                       False, True, False, 2.0),),
                            probed=120, total=253, done=False)
        cases = [
            ("menu", lambda w, h: menu_lines([("doctor", "Network doctor", "d"), ("", "", "")], 0, True, "doctor", w, h)),
            ("idle", lambda w, h: idle_lines(w, h)),
            ("trace", lambda w, h: trace_lines(trace, w, h)),
            ("trace-none", lambda w, h: trace_lines(None, w, h)),
            ("trace-long", lambda w, h: trace_lines(long_trace(), w, h)),
            ("ports", lambda w, h: ports_lines(ports, w, h)),
            ("target", lambda w, h: target_lines(trace, ports, w, h)),
            ("target-trace-only", lambda w, h: target_lines(trace, None, w, h)),
            ("dns", lambda w, h: dns_lines(dns, w, h)),
            ("speed", lambda w, h: speed_lines(speed, w, h)),
            ("speed-none", lambda w, h: speed_lines(None, w, h)),
            ("doctor", lambda w, h: doctor_lines(doctor, w, h)),
            ("lan", lambda w, h: lan_lines(lan, w, h)),
            ("message", lambda w, h: message_lines(["Network details", "Name: ExampleNet"], w, h)),
            ("dispatch-none", lambda w, h: tool_view_lines(None, None, w, h)),
        ]
        for name, fn in cases:
            for w in WIDTHS:
                for h in HEIGHTS:
                    with self.subTest(view=name, w=w, h=h):
                        check_contract(self, fn(w, h), w, h)

    def test_none_inputs_do_not_raise(self):
        for fn in (lambda: trace_lines(None, 44, 8), lambda: ports_lines(None, 44, 8),
                   lambda: target_lines(None, None, 44, 8), lambda: dns_lines(None, 44, 8),
                   lambda: speed_lines(None, 44, 8), lambda: doctor_lines(None, 44, 8),
                   lambda: lan_lines(None, 44, 8), lambda: idle_lines(44, 8)):
            self.assertTrue(fn())


class TraceTests(unittest.TestCase):
    def test_reached_sample(self):
        text = text_of(trace_lines(sample_trace(), 100, 16))
        self.assertIn("reached in 9 hops", text)
        self.assertIn("✓", text)
        self.assertIn("provider", text)
        self.assertIn("* * *", text)
        self.assertIn("% lost", text)
        self.assertIn("Path to 203.0.113.5 (example.org)", text)

    def test_not_done_shows_tracing(self):
        text = text_of(trace_lines(sample_trace(reached=False, done=False), 100, 16))
        self.assertIn("tracing", text)
        self.assertIn("…", text)

    def test_stopped_without_reply(self):
        t = sample_trace(reached=False, done=True)
        self.assertIn("stopped at hop 4", text_of(trace_lines(t, 100, 16)))

    def test_many_hops_collapse_but_keep_last(self):
        lines = trace_lines(long_trace(15), 80, 8)
        text = text_of(lines)
        self.assertIn("more hops", text)
        self.assertIn("198.51.100.15", text)
        self.assertLessEqual(len(lines), 8)

    def test_error_text_shown(self):
        t = TraceResult(target="nowhere.invalid", done=True, error="could not find that name")
        text = text_of(trace_lines(t, 68, 8))
        self.assertIn("could not find that name", text)
        self.assertIn("Path to nowhere.invalid", text)

    def test_error_is_red(self):
        t = TraceResult(target="nowhere.invalid", done=True, error="could not find that name")
        lines = trace_lines(t, 68, 8)
        self.assertTrue(has_colour(lines, "#ff7b72"))


class PortsTests(unittest.TestCase):
    def test_counts_order_and_words(self):
        pc = PortCheckResult(target="203.0.113.5", address="203.0.113.5", done=True, results=(
            PortResult(3389, "Remote Desktop", "filtered"), PortResult(22, "SSH", "closed"),
            PortResult(80, "HTTP", "open", 9.0), PortResult(443, "HTTPS", "open", 12.0)))
        lines = plain(ports_lines(pc, 68, 13))
        self.assertIn("2 open", lines[0])
        self.assertIn("1 closed", lines[0])
        self.assertIn("1 no answer", lines[0])
        self.assertTrue(lines[1].startswith("●") and "80" in lines[1])
        self.assertTrue(lines[2].startswith("●") and "443" in lines[2])
        self.assertIn("(refused)", lines[3])
        self.assertIn("no answer", lines[4])
        self.assertIn("(firewall?)", lines[4])

    def test_error_and_pending_words(self):
        pc = PortCheckResult(target="x", address="203.0.113.5", results=(
            PortResult(443, "HTTPS", "error"), PortResult(80, "HTTP", "pending")))
        text = text_of(ports_lines(pc, 68, 13))
        self.assertIn("unreachable", text)
        self.assertIn("checking", text)

    def test_footer_when_it_fits(self):
        pc = PortCheckResult(target="x", address="203.0.113.5", done=True,
                             results=(PortResult(443, "HTTPS", "open", 12.0),))
        self.assertIn("accepts connections", text_of(ports_lines(pc, 68, 6)))


class TargetTests(unittest.TestCase):
    def setUp(self):
        self.trace = sample_trace()
        self.ports = PortCheckResult(target="203.0.113.5", address="203.0.113.5", done=True,
                                     results=(PortResult(443, "HTTPS", "open", 12.0),))

    def test_side_by_side_at_wide(self):
        first = plain(target_lines(self.trace, self.ports, 100, 16))[0]
        self.assertIn("Path to", first)
        self.assertIn("Ports on", first)

    def test_stacked_at_narrow(self):
        text = text_of(target_lines(self.trace, self.ports, 56, 16))
        self.assertIn("Path to", text)
        self.assertIn("Ports on", text)

    def test_one_side_none(self):
        self.assertIn("Ports on", text_of(target_lines(None, self.ports, 56, 16)))
        self.assertIn("Path to", text_of(target_lines(self.trace, None, 56, 16)))


class DnsTests(unittest.TestCase):
    def _colours(self, verdict):
        rep = DnsReport(timings=(DnsTiming("192.0.2.1", "your router", True, 20.0, 3, 3),), done=True,
                        verdict=verdict)
        return dns_lines(rep, 68, 16)

    def test_in_use_tag(self):
        rep = DnsReport(timings=(DnsTiming("192.0.2.1", "your router", True, 120.0, 3, 3),), done=True,
                        verdict="Your DNS is fine.")
        self.assertIn("in use", text_of(dns_lines(rep, 68, 16)))

    def test_verdict_colouring(self):
        self.assertTrue(has_colour(self._colours("Your DNS is fine."), "#56d364"))
        self.assertTrue(has_colour(self._colours("Switching to Cloudflare would help."), "#f2cc60"))
        self.assertTrue(has_colour(self._colours("The router is not answering."), "#ff7b72"))

    def test_no_answer_in_red(self):
        rep = DnsReport(timings=(DnsTiming("192.0.2.1", "your router", False, None, 0, 3, error="timeout"),),
                        done=False)
        self.assertIn("no answer", text_of(dns_lines(rep, 68, 16)))
        self.assertIn("testing", text_of(dns_lines(rep, 68, 16)))


class SpeedTests(unittest.TestCase):
    def test_download_stage_and_mb(self):
        sr = SpeedResult(stage="download", progress=0.55, idle_ms=14.0, loaded_ms=52.0, down_mbps=245.3,
                         grade="B", samples=(10.0, 20.0, 245.3), bytes_used=31_000_000)
        text = text_of(speed_lines(sr, 68, 16))
        self.assertIn("downloading", text)
        self.assertIn("245.3 Mbps", text)
        self.assertIn("31 MB", text)
        self.assertIn("B", text)
        self.assertIn("speed.cloudflare.com", text)

    def test_graph_rows_only_when_tall(self):
        sr = SpeedResult(stage="download", samples=(10.0, 20.0, 30.0))
        tall = speed_lines(sr, 68, 16)
        short = speed_lines(sr, 68, 4)
        self.assertGreater(len(tall), len(short))
        self.assertTrue(any("█" in ln.plain or "▁" in ln.plain for ln in tall))

    def test_stage_words_and_grades(self):
        self.assertIn("done", text_of(speed_lines(SpeedResult(stage="done", grade="A"), 68, 16)))
        self.assertIn("cancelled", text_of(speed_lines(SpeedResult(stage="cancelled"), 68, 16)))
        err = text_of(speed_lines(SpeedResult(stage="error", error="no route"), 68, 16))
        self.assertIn("no route", err)
        self.assertIn("uploading", text_of(speed_lines(SpeedResult(stage="upload"), 68, 16)))
        self.assertIn("measuring ping", text_of(speed_lines(SpeedResult(stage="latency"), 68, 16)))
        self.assertIn("lag under load: -", text_of(speed_lines(SpeedResult(stage="done"), 68, 16)))


class DoctorTests(unittest.TestCase):
    def test_icons_and_fix_hint(self):
        dx = Diagnosis(steps=(DoctorStep("link", "Wi-Fi link", "ok", "5 GHz"),
                              DoctorStep("router", "Router", "warn", "slow"),
                              DoctorStep("dns", "DNS", "fail", "no answer"),
                              DoctorStep("internet", "Internet", "running"),
                              DoctorStep("portal", "Login page", "skip")),
                       done=True, level="fail", verdict="Name lookup is failing.", suggestion="dns")
        lines = plain(doctor_lines(dx, 68, 16))
        self.assertTrue(lines[1].strip().startswith("✓"))
        self.assertTrue(lines[2].strip().startswith("!"))
        self.assertTrue(lines[3].strip().startswith("✗"))
        self.assertTrue(lines[4].strip().startswith("◐") or "◐" in lines[4])
        self.assertIn(FIX_HINT["dns"], text_of(doctor_lines(dx, 68, 16)))

    def test_checking_status(self):
        self.assertIn("checking", text_of(doctor_lines(Diagnosis(done=False), 68, 16)))


class LanTests(unittest.TestCase):
    def test_progress_while_scanning(self):
        ls = LanScanResult(subnet="192.0.2.0/24", probed=120, total=253, done=False)
        self.assertIn("probing 120/253", text_of(lan_lines(ls, 68, 16)))

    def test_found_and_tags(self):
        ls = LanScanResult(subnet="192.0.2.0/24", done=True, devices=(
            FoundDevice("192.0.2.1", "02:00:00:AA:BB:CC", False, True, False, 1.0),
            FoundDevice("192.0.2.20", "02:00:00:AA:BB:01", True, False, True, None),
            FoundDevice("192.0.2.30", None, False, False, False, None)))
        text = text_of(lan_lines(ls, 100, 16))
        self.assertIn("found 3", text)
        self.assertIn("router", text)
        self.assertIn("this computer", text)
        self.assertIn("private MAC", text)
        self.assertIn("Sent one small packet", text)


class DispatcherTests(unittest.TestCase):
    def test_unknown_kind_is_one_dim_line(self):
        lines = tool_view_lines("bogus", None, 44, 8)
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0].plain.rstrip(), "Nothing to show.")

    def test_garbage_result_is_dim_line(self):
        for kind in ("trace", "ports", "dns", "speed", "doctor", "lan", "target"):
            with self.subTest(kind=kind):
                lines = tool_view_lines(kind, object(), 44, 8)
                self.assertEqual(len(lines), 1)

    def test_dispatch_routes_to_views(self):
        self.assertIn("reached in 9 hops", text_of(tool_view_lines("trace", sample_trace(), 100, 16)))
        pc = PortCheckResult(target="x", done=True, results=(PortResult(443, "HTTPS", "open", 1.0),))
        self.assertIn("Ports on", text_of(tool_view_lines("ports", pc, 68, 8)))
        self.assertIn("Ports on", text_of(tool_view_lines("target", (None, pc), 68, 8)))
        self.assertIn("Network details", text_of(tool_view_lines("message", ("Network details", "x"), 68, 8)))

    def test_idle_when_no_kind(self):
        self.assertIn("Pick a tool", text_of(tool_view_lines(None, None, 68, 8)))

    def test_message_first_line_bold(self):
        lines = message_lines(["Title", "body"], 44, 8)
        self.assertIn("bold", str(lines[0].style))
        self.assertNotIn("bold", str(lines[1].style))


if __name__ == "__main__":
    unittest.main()
