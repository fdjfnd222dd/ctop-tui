"""Tests for ctop/collectors/details.py. Fake processes only, except the smoke test on this process."""
import contextlib
import dataclasses
import os
import time
import unittest
from unittest import mock

import psutil

from ctop.collectors.details import CMDLINE_LIMIT, MAX_CONNECTIONS, count_open_files, program_detail
from ctop.models import Connection, Listener, ProgramDetail


class FakeProc:
    def __init__(self, pid, created=100.0, name="webdev", cmd=("webdev", "--port", "8080"),
                 status="sleeping", user="alice", threads=4):
        self.pid = pid
        self.created = created
        self._name = name
        self._cmd = tuple(cmd)
        self._status = status
        self._user = user
        self._threads = threads

    def oneshot(self):
        return contextlib.nullcontext()

    def create_time(self):
        return self.created

    def name(self):
        return self._name

    def cmdline(self):
        return list(self._cmd)

    def status(self):
        return self._status

    def username(self):
        return self._user

    def num_threads(self):
        return self._threads


class DeniedProc(FakeProc):
    def username(self):
        raise psutil.AccessDenied(self.pid)

    def num_threads(self):
        raise psutil.AccessDenied(self.pid)


def run(members, procs, listeners=(), connections=(), mem=None, fds=None):
    """program_detail with fake processes; no real /proc is read unless mem/fds say so."""
    def factory(pid):
        if pid not in procs:
            raise psutil.NoSuchProcess(pid)
        return procs[pid]
    return program_detail("webdev", members, listeners, connections, proc_factory=factory,
                          mem_fn=mem or (lambda pid: 0), fd_count_fn=fds or (lambda pid: None))


def conn(pid, port=51234):
    return Connection("tcp", port, "203.0.113.9", 443, "ESTABLISHED", pid, "webdev", False)


class TestProgramDetail(unittest.TestCase):
    def test_basic_totals(self):
        procs = {10: FakeProc(10, created=100.0),
                 11: FakeProc(11, created=110.0, name="webdev-helper", threads=2)}
        d = run([(10, 100.0), (11, 110.0)], procs,
                mem={10: 3_000_000, 11: 1_000_000}.get)
        self.assertEqual(d.total_processes, 2)
        self.assertEqual(d.total_ram_bytes, 4_000_000)
        self.assertEqual(d.total_threads, 6)
        self.assertEqual(d.oldest_started, 100.0)
        self.assertEqual(d.processes[0].pid, 10)           # biggest RAM first
        self.assertEqual(d.processes[1].name, "webdev-helper")

    def test_process_fields(self):
        d = run([(10, 100.0)], {10: FakeProc(10)}, mem=lambda pid: 1, fds=lambda pid: 7)
        p = d.processes[0]
        self.assertEqual(p.cmdline, "webdev --port 8080")
        self.assertEqual(p.user, "alice")
        self.assertEqual(p.status, "sleeping")
        self.assertEqual(p.started, 100.0)
        self.assertEqual(p.open_files, 7)

    def test_pid_reuse_is_skipped(self):
        d = run([(10, 99.5)], {10: FakeProc(10, created=100.0)})
        self.assertEqual(d.total_processes, 0)
        self.assertEqual(d.processes, ())

    def test_missing_process_is_skipped(self):
        d = run([(999, 1.0), (10, 100.0)], {10: FakeProc(10)})
        self.assertEqual(d.total_processes, 1)
        self.assertEqual(d.processes[0].pid, 10)

    def test_cmdline_truncated_to_300(self):
        d = run([(10, 100.0)], {10: FakeProc(10, cmd=("x" * 500,))})
        self.assertEqual(len(d.processes[0].cmdline), CMDLINE_LIMIT)
        d = run([(10, 100.0)], {10: FakeProc(10, cmd=())})
        self.assertEqual(d.processes[0].cmdline, "")

    def test_cmdline_access_denied_gives_empty(self):
        class NoCmdProc(FakeProc):
            def cmdline(self):
                raise psutil.AccessDenied(self.pid)
        d = run([(10, 100.0)], {10: NoCmdProc(10)})
        self.assertEqual(d.total_processes, 1)
        self.assertEqual(d.processes[0].cmdline, "")

    def test_access_denied_fields_become_none(self):
        d = run([(10, 100.0)], {10: DeniedProc(10, threads=4)})
        p = d.processes[0]
        self.assertIsNone(p.user)
        self.assertIsNone(p.threads)
        self.assertEqual(d.total_processes, 1)
        self.assertEqual(d.total_threads, 0)

    def test_mem_oserror_gives_zero(self):
        def mem(pid):
            raise OSError("gone")
        d = run([(10, 100.0)], {10: FakeProc(10)}, mem=mem)
        self.assertEqual(d.processes[0].ram_bytes, 0)

    def test_open_files_none_on_permission_error(self):
        with mock.patch("os.listdir", side_effect=PermissionError):
            self.assertIsNone(count_open_files(1))
        with mock.patch("os.listdir", side_effect=FileNotFoundError):
            self.assertIsNone(count_open_files(10 ** 9))
        n = count_open_files(os.getpid())
        self.assertIsInstance(n, int)
        self.assertGreaterEqual(n, 3)

    def test_top_15_by_ram(self):
        procs = {pid: FakeProc(pid) for pid in range(1, 21)}
        members = [(pid, 100.0) for pid in range(1, 21)]
        d = run(members, procs, mem=lambda pid: pid * 1000)
        self.assertEqual(len(d.processes), 15)
        rams = [p.ram_bytes for p in d.processes]
        self.assertEqual(rams, sorted(rams, reverse=True))
        self.assertEqual(d.total_processes, 20)
        self.assertEqual(d.total_ram_bytes, sum(range(1, 21)) * 1000)

    def test_ram_tie_breaks_by_pid(self):
        procs = {12: FakeProc(12), 11: FakeProc(11)}
        d = run([(12, 100.0), (11, 100.0)], procs, mem=lambda pid: 500)
        self.assertEqual(d.processes[0].pid, 11)

    def test_connections_filtered_to_members(self):
        conns = [conn(10, 40001), conn(99, 40002), conn(None, 40003), conn(11, 40004)]
        d = run([(10, 100.0), (11, 100.0)], {10: FakeProc(10), 11: FakeProc(11)}, connections=conns)
        self.assertEqual([c.local_port for c in d.connections], [40001, 40004])

    def test_connections_capped_at_30(self):
        conns = [conn(10, 40000 + i) for i in range(50)]
        d = run([(10, 100.0)], {10: FakeProc(10)}, connections=conns)
        self.assertEqual(len(d.connections), MAX_CONNECTIONS)
        self.assertEqual(d.connections[0].local_port, 40000)

    def test_listeners_capped_at_30(self):
        lst = [Listener("tcp", "0.0.0.0", 8000 + i, 10, "webdev", "network") for i in range(50)]
        d = run([(10, 100.0)], {10: FakeProc(10)}, listeners=lst)
        self.assertEqual(len(d.listeners), 30)

    def test_listeners_filtered(self):
        lst = [Listener("tcp", "0.0.0.0", 8080, 10, "webdev", "network"),
               Listener("tcp", "127.0.0.1", 9000, 99, "other", "local")]
        d = run([(10, 100.0)], {10: FakeProc(10)}, listeners=lst)
        self.assertEqual(len(d.listeners), 1)
        self.assertEqual(d.listeners[0].port, 8080)

    def test_connection_of_skipped_member_not_included(self):
        d = run([(10, 99.5), (11, 100.0)], {10: FakeProc(10), 11: FakeProc(11)},
                connections=[conn(10)])
        self.assertEqual(d.connections, ())

    def test_real_process_smoke_and_speed(self):
        members = [(os.getpid(), psutil.Process().create_time())] * 30
        start = time.perf_counter()
        d = program_detail("self", members)
        elapsed = time.perf_counter() - start
        self.assertEqual(d.total_processes, 30)
        self.assertTrue(d.processes[0].name)
        self.assertGreater(d.processes[0].ram_bytes, 0)
        self.assertLess(elapsed, 1.0)           # generous for slow CI; the target is 0.1 s

    def test_empty_members(self):
        d = program_detail("x", [])
        self.assertIsNotNone(d.captured_at)     # set on every call, so compare the rest
        self.assertEqual(dataclasses.replace(d, captured_at=None),
                         ProgramDetail(name="x", total_processes=0))


if __name__ == "__main__":
    unittest.main()
