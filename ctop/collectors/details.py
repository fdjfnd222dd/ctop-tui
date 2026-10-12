"""Program details for the Enter popup: one on-demand snapshot of one program group.

Only /proc and psutil are read. Nothing is cached, logged or sent anywhere;
command lines are shown on screen only.
"""
from __future__ import annotations

import os
import time
from typing import Callable, Iterable, List, Optional, Sequence, Tuple

import psutil

from ctop.models import Connection, Listener, ProcessDetail, ProgramDetail

MAX_PROCESSES = 15
MAX_CONNECTIONS = 30
MAX_LISTENERS = 30
CMDLINE_LIMIT = 300


def count_open_files(pid: int) -> Optional[int]:
    """Open file descriptors counted from /proc; psutil's per-file listing walks every fd and is too slow."""
    try:
        return len(os.listdir(f"/proc/{pid}/fd"))
    except OSError:                         # includes PermissionError for another user's process
        return None


def _read_member(pid: int, start: float, proc_factory: Callable[[int], object],
                 mem_fn: Callable[[int], int],
                 fd_count_fn: Callable[[int], Optional[int]]) -> Optional[ProcessDetail]:
    """One verified process, or None if it exited or its pid now belongs to another program."""
    try:
        p = proc_factory(pid)
        # One oneshot block batches the /proc reads instead of doing one read per field.
        with p.oneshot():
            created = p.create_time()
            if created != start:            # exact compare: same pid-reuse guard as safety.py
                return None
            pname = p.name()
            try:
                cmd = p.cmdline()
            except psutil.AccessDenied:     # another user's process: show no command line
                cmd = []
            status = p.status()
            # Optional fields: losing one must not hide the whole process.
            try:
                user = p.username()
            except (psutil.Error, KeyError):
                user = None
            try:
                threads = p.num_threads()
            except (psutil.Error, KeyError):
                threads = None
    except psutil.Error:                    # exited between the list scan and this click
        return None
    try:
        ram = mem_fn(pid)
    except OSError:
        ram = 0
    return ProcessDetail(pid=pid, name=pname, cmdline=" ".join(cmd)[:CMDLINE_LIMIT],
                         user=user, ram_bytes=ram, threads=threads,
                         open_files=fd_count_fn(pid), started=created, status=status)


def program_detail(name: str,
                   members: Sequence[Tuple[int, float]],
                   listeners: Iterable[Listener] = (),
                   connections: Iterable[Connection] = (),
                   *,
                   proc_factory: Optional[Callable[[int], object]] = None,
                   mem_fn: Optional[Callable[[int], int]] = None,
                   fd_count_fn: Optional[Callable[[int], Optional[int]]] = None) -> ProgramDetail:
    """Snapshot of one program group. Never raises; an empty group gives zero processes."""
    if proc_factory is None:
        proc_factory = psutil.Process
    if mem_fn is None:
        # Imported here so that loading this module does not pull in the UI stack.
        from ctop.system import _status_mem
        mem_fn = _status_mem
    if fd_count_fn is None:
        fd_count_fn = count_open_files

    verified: List[ProcessDetail] = []
    for pid, start in members:
        found = _read_member(pid, start, proc_factory, mem_fn, fd_count_fn)
        if found is not None:
            verified.append(found)

    # Totals cover every verified process; only the popup list is capped.
    shown = sorted(verified, key=lambda d: (-d.ram_bytes, d.pid))[:MAX_PROCESSES]
    pids = {d.pid for d in verified}        # a None pid never matches, so unowned sockets drop out
    owned_conns = [c for c in connections if c.pid in pids][:MAX_CONNECTIONS]
    owned_listeners = [l for l in listeners if l.pid in pids][:MAX_LISTENERS]
    return ProgramDetail(
        name=name,
        total_processes=len(verified),
        processes=tuple(shown),
        connections=tuple(owned_conns),
        listeners=tuple(owned_listeners),
        oldest_started=min((d.started for d in verified), default=None),
        total_ram_bytes=sum(d.ram_bytes for d in verified),
        total_threads=sum(d.threads for d in verified if d.threads is not None),
        captured_at=time.time(),
    )
