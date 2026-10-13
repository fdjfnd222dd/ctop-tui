"""Speed test tool: grading, rate maths and the whole run with fake connections and a fake clock.

No network, no real sleeping: the fake clock moves only when asked to, and the fake sleep moves it too.
"""
from __future__ import annotations

import os
import sys
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ctop.tools import speedtest as st  # noqa: E402


class FakeClock:
    """Moves a little on every read (like real time passing) and when sleep() is called."""

    def __init__(self, step: float = 0.05) -> None:
        self.now = 0.0
        self.step = step
        self._lock = threading.Lock()

    def __call__(self) -> float:
        with self._lock:
            value = self.now
            self.now += self.step
            return value

    def sleep(self, seconds: float) -> None:
        with self._lock:
            self.now += seconds


class Totals:
    """Bytes the fake server handed out and bytes the fake client pushed."""

    def __init__(self) -> None:
        self.down = 0
        self.up = 0
        self.opened = 0
        self.lock = threading.Lock()


class FakeResponse:
    def __init__(self, status: int, totals: Totals, chunks: int, on_read=None) -> None:
        self.status = status
        self._totals = totals
        self._left = chunks
        self._on_read = on_read

    def read(self, size: int) -> bytes:
        if self._on_read is not None:
            self._on_read()
        if self._left <= 0:
            return b""
        self._left -= 1
        with self._totals.lock:
            self._totals.down += st.CHUNK
        return b"\x00" * st.CHUNK


class FakeConn:
    """Stands in for http.client.HTTPSConnection: GET downloads, POST uploads."""

    def __init__(self, totals: Totals, status: int = 200, chunks: int = 10, on_read=None) -> None:
        self._totals = totals
        self._status = status
        self._chunks = chunks
        self._on_read = on_read
        self._method = None
        self.closed = False

    def request(self, method, path, headers=None):
        self._method = method

    def getresponse(self):
        return FakeResponse(self._status, self._totals, self._chunks, self._on_read)

    def putrequest(self, method, path):
        self._method = method

    def putheader(self, name, value):
        pass

    def endheaders(self):
        pass

    def send(self, data: bytes) -> None:
        with self._totals.lock:
            self._totals.up += len(data)

    def close(self) -> None:
        self.closed = True


def factory_for(totals: Totals, **kwargs):
    def make(host):
        with totals.lock:
            totals.opened += 1
        return FakeConn(totals, **kwargs)
    return make


class Grading(unittest.TestCase):
    def test_boundaries(self):
        self.assertEqual(st.grade(10, 30), "A")
        self.assertEqual(st.grade(10, 70), "B")
        self.assertEqual(st.grade(10, 130), "C")
        self.assertEqual(st.grade(10, 260), "D")
        self.assertEqual(st.grade(10, 300), "F")
        self.assertIsNone(st.grade(None, 5))
        self.assertIsNone(st.grade(5, None))

    def test_mbps(self):
        self.assertEqual(st.mbps(1_000_000, 1.0), 8.0)
        self.assertEqual(st.mbps(5, 0), 0.0)
        self.assertEqual(st.mbps(5, -1.0), 0.0)


class TcpRtt(unittest.TestCase):
    def test_handshake_time_and_close(self):
        closed = []

        class Sock:
            def close(self):
                closed.append(True)

        clock = FakeClock(step=0.01)           # 10 ms between the two reads
        value = st.tcp_rtt(connect=lambda addr, timeout: Sock(), clock=clock)
        self.assertIsInstance(value, float)
        self.assertAlmostEqual(value, 10.0, places=6)
        self.assertEqual(closed, [True])

    def test_connect_error_is_none(self):
        def refused(addr, timeout):
            raise OSError("refused")

        self.assertIsNone(st.tcp_rtt(connect=refused, clock=FakeClock()))


class FullRun(unittest.TestCase):
    def _run(self, **kwargs):
        clock = FakeClock(step=0.05)
        updates = []
        rtt_values = [20.0] * 5
        calls = {"n": 0}

        def rtt():
            calls["n"] += 1
            if rtt_values:
                return rtt_values.pop(0)
            return 80.0

        totals = Totals()
        conn_factory = kwargs.pop("conn_factory", None) or factory_for(totals, chunks=10)
        result = st.run_speed(on_update=updates.append, conn_factory=conn_factory, rtt=rtt,
                              clock=clock, sleep=clock.sleep, **kwargs)
        return result, updates, totals, calls

    def test_full_run_done(self):
        result, updates, totals, _ = self._run()
        self.assertEqual(result.stage, "done")
        self.assertGreater(result.down_mbps, 0)
        self.assertGreater(result.up_mbps, 0)
        self.assertEqual(result.idle_ms, 20.0)
        self.assertEqual(result.loaded_ms, 80.0)
        self.assertEqual(result.grade, "B")
        self.assertEqual(result.bytes_used, totals.down + totals.up)
        self.assertEqual(result.progress, 1.0)
        self.assertIsNone(result.error)
        self.assertLessEqual(len(result.samples), 40)
        stages = []
        for u in updates:
            if not stages or stages[-1] != u.stage:
                stages.append(u.stage)
        self.assertEqual(stages, ["latency", "download", "upload", "done"])

    def test_all_latency_probes_fail(self):
        totals = Totals()
        conn_factory = factory_for(totals)
        result = st.run_speed(conn_factory=conn_factory, rtt=lambda: None,
                              clock=FakeClock(), sleep=lambda s: None)
        self.assertEqual(result.stage, "error")
        self.assertEqual(result.error, "Could not reach the speed test server")
        self.assertEqual(totals.opened, 0)

    def test_cancel_during_download(self):
        cancel = threading.Event()
        totals = Totals()
        reads = {"n": 0}

        def on_read():
            reads["n"] += 1
            if reads["n"] == 3:
                cancel.set()

        conn_factory = factory_for(totals, chunks=1000, on_read=on_read)
        clock = FakeClock(step=0.05)
        rtt_values = [20.0] * 5
        result = st.run_speed(conn_factory=conn_factory,
                              rtt=lambda: rtt_values.pop(0) if rtt_values else 80.0,
                              clock=clock, sleep=clock.sleep, cancel=cancel)
        self.assertEqual(result.stage, "cancelled")
        self.assertIsNone(result.up_mbps)
        self.assertEqual(result.bytes_used, totals.down + totals.up)

    def test_conn_factory_error_is_plain_message(self):
        def broken(host):
            raise OSError("no route to host")

        clock = FakeClock()
        result = st.run_speed(conn_factory=broken, rtt=lambda: 20.0, clock=clock, sleep=clock.sleep)
        self.assertEqual(result.stage, "error")
        self.assertEqual(result.error, "Could not connect to the speed test server")

    def test_http_503_is_error(self):
        totals = Totals()
        clock = FakeClock()
        result = st.run_speed(conn_factory=factory_for(totals, status=503), rtt=lambda: 20.0,
                              clock=clock, sleep=clock.sleep)
        self.assertEqual(result.stage, "error")
        self.assertIn("503", result.error)
        self.assertEqual(totals.down, 0)

    def test_on_update_failure_does_not_stop_run(self):
        def bad(_result):
            raise ValueError("page gone")

        totals = Totals()
        clock = FakeClock()
        rtt_values = [20.0] * 5
        result = st.run_speed(on_update=bad, conn_factory=factory_for(totals, chunks=3),
                              rtt=lambda: rtt_values.pop(0) if rtt_values else 80.0,
                              clock=clock, sleep=clock.sleep)
        self.assertEqual(result.stage, "done")

class StaleWorkers(unittest.TestCase):
    def test_late_worker_of_an_old_stage_adds_nothing(self):
        # A download worker still inside a slow read when the upload stage starts must not
        # add bytes or a "finished" tick to the new stage.
        totals = Totals()
        clock = FakeClock()
        run = st._Run(None, None, factory_for(totals, chunks=3), lambda: 20.0, clock, clock.sleep)
        run.limit = st.DOWN_BYTES
        run.stop_at = 1e9
        run.gen = 2                    # the stage has moved on; this worker belongs to generation 1
        run._download_body(1)
        self.assertEqual(run.moved, 0)
        self.assertEqual(run.finished, 0)
        run._download_body(2)          # a current worker still counts normally
        self.assertEqual(run.moved, 3 * st.CHUNK)
        self.assertEqual(run.finished, 1)


if __name__ == "__main__":
    unittest.main()
