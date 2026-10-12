# T13 - Program details collector (Enter popup)

## 1 Purpose
`program_detail()` gathers a one-shot snapshot of one program group (all processes the RAM list grouped under one name) for the details popup: per-process RAM/threads/files/command line, plus that program's network sockets. It is called on demand (not on a timer) and must finish in under 100 ms for 30 processes.

## 2 Files
Create `ctop/collectors/details.py`, `tests/test_details.py`. READ: `docs/ARCHITECTURE.md`, `ctop/models.py` (`ProgramDetail`, `ProcessDetail`, `Listener`, `Connection`), and in `ctop/system.py` ONLY `_status_mem(pid)` (RssAnon+RssShmem from `/proc/PID/status`, bytes, raises `OSError`) and `Sampler.scan_procs` (to see how `members` are built: `(pid, p.create_time())`). Do not edit them. Touch nothing else.

## 3 Public API
```python
from __future__ import annotations
from typing import Callable, Iterable, List, Optional, Sequence, Tuple
from ctop.models import Connection, Listener, ProcessDetail, ProgramDetail

MAX_PROCESSES = 15
MAX_CONNECTIONS = 30
MAX_LISTENERS = 30
CMDLINE_LIMIT = 300

def program_detail(name: str,
                   members: Sequence[Tuple[int, float]],
                   listeners: Iterable[Listener] = (),
                   connections: Iterable[Connection] = (),
                   *,
                   proc_factory: Optional[Callable[[int], object]] = None,
                   mem_fn: Optional[Callable[[int], int]] = None,
                   fd_count_fn: Optional[Callable[[int], Optional[int]]] = None) -> ProgramDetail: ...

def count_open_files(pid: int) -> Optional[int]: ...      # len(os.listdir(f"/proc/{pid}/fd")); PermissionError -> None; other OSError -> None
```
`members` = `[(pid, start_time), ...]` exactly as `Sampler.groups[i][3]` holds them (`start_time` is `psutil.Process(pid).create_time()`). `listeners` / `connections` are the system-wide tuples from the ports collector (T08). The keyword-only hooks exist for tests: `proc_factory=None` -> `psutil.Process`; `mem_fn=None` -> `ctop.system._status_mem` (import it INSIDE the function, so importing `details.py` stays light); `fd_count_fn=None` -> `count_open_files`. Python 3.9 syntax only. Never raises (return a `ProgramDetail` with zero processes if nothing is alive).

## 4 Data sources
For each `(pid, start)` in `members`:
1. `p = proc_factory(pid)`; on `psutil.Error` (NoSuchProcess, AccessDenied...) skip the member entirely.
2. Inside ONE `with p.oneshot():` block read, in this order: `created = p.create_time()`; if `created != start` the pid was reused by a different program -> SKIP (exact float comparison, the same guard `ctop/safety.py` uses). Then `pname = p.name()`, `cmd = p.cmdline()`, `status = p.status()`, and each of `user = p.username()`, `threads = p.num_threads()` separately guarded: a `psutil.Error` (or `KeyError`) on one field -> that field is `None`, the process is still included.
3. `cmdline` = `" ".join(cmd)` cut with `[:300]` (no ellipsis; empty list - kernel threads, zombies - gives `""`). Strip control characters is NOT needed.
4. RAM: `mem_fn(pid)` (bytes). `OSError` -> `0`. Do NOT use PSS/`smaps_rollup`/`memory_full_info` (too slow); it is acceptable that this number differs slightly from the main list's PSS-based figure.
5. Open files: `fd_count_fn(pid)`. Never use `psutil.Process.open_files()` (slow, walks every fd). `/proc/PID/fd` of another user's process raises `PermissionError` -> `None`.
6. `ProcessDetail(pid=pid, name=pname, cmdline=cmdline, user=user, ram_bytes=ram, threads=threads, open_files=fds, started=created, status=status)`.

Totals are computed over ALL verified members, not just the 15 shown: `total_processes` = number of verified members (those that passed steps 1-2, even if a field was `None`), `total_ram_bytes` = sum of `ram_bytes`, `total_threads` = sum of non-None `threads`, `oldest_started` = minimum `started` (or `None` when empty).
`processes` = the verified members sorted by `ram_bytes` descending (tie: lower pid first), truncated to `MAX_PROCESSES`.
`connections` / `listeners`: keep items whose `.pid` is in the set of VERIFIED member pids (ignore items with `pid is None`); preserve input order; truncate to `MAX_CONNECTIONS` / `MAX_LISTENERS`. Result: `ProgramDetail(name=name, total_processes=..., processes=tuple(...), connections=tuple(...), listeners=tuple(...), oldest_started=..., total_ram_bytes=..., total_threads=...)`.

Illustrative fake for tests (a "process" object must support `oneshot()` as a context manager):
```python
class FakeProc:
    def __init__(self, pid, created=100.0, name="webdev", cmd=("webdev", "--port", "8080"), status="sleeping", user="alice", threads=4):
        ...
    def oneshot(self): return contextlib.nullcontext()    # 3.7+
    def create_time(self): return self.created
    def name(self): return self._name
    def cmdline(self): return list(self._cmd)
    def status(self): return self._status
    def username(self): return self._user     # tests may raise psutil.AccessDenied here
    def num_threads(self): return self._threads
```

## 5 Edge cases & distro differences
* Process exited between the list scan and the click -> `psutil.NoSuchProcess` -> skipped; if all exited, `total_processes == 0`, `processes == ()`, `oldest_started is None`.
* pid reuse -> `create_time` differs -> skipped (never show a stranger's command line under the wrong program).
* Another user's or root's process (rare because the list is mostly the user's own): `cmdline()` may raise `AccessDenied` -> treat as `""`; `open_files` -> `None`; `username()` works.
* Zombie: `cmdline()` is empty, `status == "zombie"`; keep it.
* Same on Arch, Debian and Ubuntu: only `/proc` and psutil are used. `psutil.Process.username()` falls back to the numeric uid text if the uid has no passwd entry (that is fine).
* Command lines can contain secrets (tokens in arguments). They are shown only on screen inside the popup; never log, print or store them.

## 6 Performance & safety limits
Budget: under 100 ms for 30 processes (measured ~1 ms per process with `oneshot()`: one `/proc/PID/stat+status+cmdline` read set). One `oneshot()` block per process, no `children()`, no `open_files()`, no `connections()` (the socket list is passed in), no `memory_full_info()`. Read-only, no signals sent, no subprocess. Pure function of its arguments plus `/proc`; no module-level caches.

## 7 Test plan (`tests/test_details.py`) - inject `proc_factory` / `mem_fn` / `fd_count_fn` for logic tests
`procs = {10: FakeProc(10, created=100.0), 11: FakeProc(11, created=110.0, name="webdev-helper"), ...}`; `factory = lambda pid: procs[pid]` (raise `psutil.NoSuchProcess(pid)` when missing).
* `test_basic_totals`: members `[(10,100.0),(11,110.0)]`, `mem_fn={10:3_000_000, 11:1_000_000}.get`, `threads` 4 and 2 -> `total_processes==2`, `total_ram_bytes==4_000_000`, `total_threads==6`, `oldest_started==100.0`, `processes[0].pid==10` (biggest RAM first), `name` preserved.
* `test_process_fields`: first process -> `cmdline=="webdev --port 8080"`, `user=="alice"`, `status=="sleeping"`, `started==100.0`, `open_files==7` when `fd_count_fn=lambda pid: 7`.
* `test_pid_reuse_is_skipped`: members `[(10, 99.5)]` while the fake says `created==100.0` -> `total_processes==0`, `processes==()`.
* `test_missing_process_is_skipped`: member pid 999 not in `procs` (factory raises `NoSuchProcess`) -> skipped; the other member still reported.
* `test_cmdline_truncated_to_300`: `cmd=("x"*500,)` -> `len(cmdline)==300`; `cmd=()` -> `""`.
* `test_access_denied_fields_become_none`: fake whose `username()` raises `psutil.AccessDenied` and `num_threads()` raises it too -> `user is None`, `threads is None`, still listed; `total_threads` ignores it.
* `test_mem_oserror_gives_zero`: `mem_fn` raising `OSError` -> `ram_bytes==0`.
* `test_open_files_none_on_permission_error`: `count_open_files` against a monkeypatched `os.listdir` raising `PermissionError` -> `None`; missing dir -> `None`; against the real `os.getpid()` -> an `int >= 3`.
* `test_top_15_by_ram`: 20 fake members with distinct RAM -> `len(processes)==15`, descending RAM, `total_processes==20`, `total_ram_bytes` = sum of all 20.
* `test_ram_tie_breaks_by_pid`: equal RAM, pids 12 and 11 -> 11 first.
* `test_connections_filtered_to_members`: connections with pids 10, 11, 99 and `None` -> only pids 10 and 11 kept, input order preserved; build them as `Connection("tcp",51234,"203.0.113.9",443,"ESTABLISHED",pid,"webdev",False)`.
* `test_connections_capped_at_30`: 50 connections for pid 10 -> `len==30` (the first 30). Listeners likewise capped at 30.
* `test_listeners_filtered`: `Listener("tcp","0.0.0.0",8080,10,"webdev","network")` kept, one with pid 99 dropped.
* `test_connection_of_skipped_member_not_included`: connection for pid 10 whose member was skipped for pid reuse -> not included.
* `test_real_process_smoke_and_speed`: members = `[(os.getpid(), psutil.Process().create_time())] * 30` with NO hooks -> `total_processes==30`, `processes[0].name` non-empty, `time.perf_counter()` elapsed `< 1.0` s (generous for slow CI; the target is 0.1 s), `ram_bytes > 0`.
* `test_empty_members`: `program_detail("x", [])` -> `ProgramDetail(name="x", total_processes=0)` equal to the defaults.

## 8 Done when
- [ ] `python -m unittest tests.test_details -v` passes (the smoke test only inspects the test process itself).
- [ ] `python -m pyflakes ctop/collectors/details.py tests/test_details.py` clean.
- [ ] No new dependencies (psutil + stdlib); Python 3.9 syntax; `ctop.system` imported only inside the function.
- [ ] Only the two listed files created; `system.py` and `models.py` untouched.
- [ ] No `open_files()`, `memory_full_info()` or `children()` calls anywhere in the module.
