"""Speed test tool: download, upload and "lag under load" (bufferbloat) against Cloudflare's test endpoints.

Standard library only (http.client over TLS). Runs only when the user asks. It moves about
DATA_ESTIMATE_MB of data, so the page shows that number before the test starts.
Parallel streams are used because one TCP connection under-reads a fast line.
"""
from __future__ import annotations

import http.client
import socket
import ssl
import threading
import time
from collections import deque
from typing import Callable, List, Optional

from ctop.models import SpeedResult
from ctop.tools import cancelled, emit

HOST = "speed.cloudflare.com"
DOWN_PATH = "/__down?bytes={n}"
UP_PATH = "/__up"
DOWN_STREAMS = 4                 # one TCP stream under-reads fast links
DOWN_BYTES = 25_000_000          # stop downloading after this many bytes in total ...
DOWN_SECONDS = 8.0               # ... or this many seconds, whichever comes first
UP_STREAMS = 2
UP_BYTES = 10_000_000
UP_SECONDS = 8.0
CHUNK = 65536
DATA_ESTIMATE_MB = 35            # shown to the user before the test starts

_LATENCY_PINGS = 5
_LATENCY_GAP = 0.1               # seconds between idle pings
_WAKE = 0.25                     # how often the page gets a live reading
_LOADED_EVERY = 0.5              # how often a latency sample is taken while downloading
_SAMPLES = 40                    # live readings kept for the graph
_HTTP_TIMEOUT = 10
_BLOCK = bytes(CHUNK)            # one zero-filled block, reused for every upload send
_GRADES = ((20.0, "A"), (60.0, "B"), (120.0, "C"), (250.0, "D"))


def grade(idle_ms: Optional[float], loaded_ms: Optional[float]) -> Optional[str]:
    """Letter for how much the ping grows while the line is busy (the lag that makes calls stutter)."""
    if idle_ms is None or loaded_ms is None:
        return None
    increase = loaded_ms - idle_ms
    for limit, letter in _GRADES:
        if increase <= limit:
            return letter
    return "F"


def mbps(nbytes: int, seconds: float) -> float:
    if seconds <= 0:
        return 0.0
    return nbytes * 8 / seconds / 1e6


def tcp_rtt(host: str = HOST, port: int = 443, timeout: float = 2.0,
            connect: Optional[Callable] = None, clock: Callable[[], float] = time.perf_counter) -> Optional[float]:
    """Milliseconds for a TCP handshake to the server; the socket is closed at once, no data is sent."""
    if connect is None:
        connect = socket.create_connection
    start = clock()
    try:
        sock = connect((host, port), timeout)
    except OSError:
        return None
    try:
        sock.close()
    except OSError:
        pass
    return (clock() - start) * 1000.0


def _default_conn(host: str):
    return http.client.HTTPSConnection(host, timeout=_HTTP_TIMEOUT, context=ssl.create_default_context())


def _median(values: List[float]) -> Optional[float]:
    if not values:
        return None
    s = sorted(values)
    mid = len(s) // 2
    return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2.0


def _short(exc: BaseException) -> str:
    text = str(exc).strip() or type(exc).__name__
    return text[:80]


class _Run:
    """One pass of the test. Worker threads move bytes; the main thread samples and reports."""

    def __init__(self, on_update, cancel, conn_factory, rtt, clock, sleep) -> None:
        self.on_update = on_update
        self.cancel = cancel
        self.conn_factory = conn_factory
        self.rtt = rtt
        self.clock = clock
        self.sleep = sleep
        self.lock = threading.Lock()
        # Shared with the worker threads; guarded by self.lock.
        self.moved = 0               # bytes moved in the current stage
        self.first_at: Optional[float] = None
        self.last_at: Optional[float] = None     # when the latest bytes moved (the stage may end a wake later)
        self.finished = 0            # streams that have stopped moving data
        self.stream_error: Optional[str] = None
        self.http_status: Optional[int] = None
        self.stop_at = 0.0
        self.limit = 0
        self.gen = 0                 # which stage the workers belong to; late workers of an old stage are ignored
        # Main-thread state.
        self.stage = "starting"
        self.progress = 0.0
        self.idle_ms: Optional[float] = None
        self.loaded: List[float] = []
        self.down_mbps: Optional[float] = None
        self.up_mbps: Optional[float] = None
        self.down_bytes = 0
        self.up_bytes = 0
        self.grade_word: Optional[str] = None
        self.error: Optional[str] = None
        self.samples: deque = deque(maxlen=_SAMPLES)

    # ---- reporting -----------------------------------------------------
    def result(self) -> SpeedResult:
        with self.lock:
            used = self.down_bytes + self.up_bytes + self.moved
        return SpeedResult(stage=self.stage, progress=self.progress, idle_ms=self.idle_ms,
                           loaded_ms=_median(self.loaded), down_mbps=self.down_mbps,
                           up_mbps=self.up_mbps, bytes_used=used, grade=self.grade_word,
                           samples=tuple(self.samples), error=self.error)

    def emit(self) -> None:
        emit(self.on_update, self.result())

    def _fail(self, message: str) -> bool:
        self.stage = "error"
        self.error = message
        self.emit()
        return False

    def _stop_now(self, gen: int) -> bool:
        # Reads are unlocked on purpose: an int read is atomic and a stale value costs one chunk.
        return (cancelled(self.cancel) or gen != self.gen or self.moved >= self.limit
                or self.clock() >= self.stop_at)

    # ---- stages --------------------------------------------------------
    def _latency(self) -> bool:
        self.stage = "latency"
        self.emit()
        values: List[Optional[float]] = []
        for i in range(_LATENCY_PINGS):
            if cancelled(self.cancel):
                self.stage = "cancelled"
                self.emit()
                return False
            values.append(self.rtt())
            self.progress = 0.1 * (i + 1) / _LATENCY_PINGS
            self.emit()
            if i < _LATENCY_PINGS - 1:
                self.sleep(_LATENCY_GAP)
        answered = [v for v in values if v is not None]
        if not answered:
            return self._fail("Could not reach the speed test server")
        self.idle_ms = _median(answered)
        return True

    def _phase(self, stage: str, streams: int, seconds: float, limit: int, body, span_start: float,
               span: float, loaded: bool) -> float:
        """Run the streams, sample speed about every 0.25 s, return the stage's Mbps (0.0 if no data)."""
        self.stage = stage
        self.samples.clear()
        start = self.clock()
        with self.lock:
            self.moved = 0
            self.first_at = None
            self.last_at = None
            self.finished = 0
            self.stream_error = None
            self.http_status = None
            self.stop_at = start + seconds
            self.limit = limit
            self.gen += 1
            gen = self.gen
        # A worker of the previous stage may still be inside a slow read; the generation number
        # keeps it from adding its bytes or its "finished" tick to this stage.
        threads = [threading.Thread(target=body, args=(gen,), daemon=True) for _ in range(streams)]
        for t in threads:
            t.start()
        last_t = start
        last_b = 0
        next_rtt = start
        while True:
            now = self.clock()
            with self.lock:
                moved = self.moved
                finished = self.finished
            if now > last_t:
                self.samples.append(mbps(moved - last_b, now - last_t))
            last_t, last_b = now, moved
            if loaded and now >= next_rtt and not cancelled(self.cancel):
                v = self.rtt()
                if v is not None:
                    self.loaded.append(v)
                next_rtt = now + _LOADED_EVERY
            frac = max(moved / limit, (now - start) / seconds)
            self.progress = span_start + span * min(1.0, frac)
            self.emit()
            if finished >= streams or moved >= limit or now >= self.stop_at or cancelled(self.cancel):
                break
            self.sleep(_WAKE)
        end = self.clock()
        with self.lock:
            moved = self.moved
            first = self.first_at
            last = self.last_at
            self.moved = 0
            self.gen += 1            # workers still running now are stale: they must stop and stay silent
        if stage == "download":
            self.down_bytes = moved
        else:
            self.up_bytes = moved
        if first is None:
            return 0.0
        # Measure to the last byte, not to the next wake-up: on a fast line the stage is over in
        # well under a second and the wake-up delay would otherwise read as lost speed.
        stop = last if last is not None and last > first else end
        return mbps(moved, stop - first)

    def _download_body(self, gen: int) -> None:
        conn = None
        try:
            conn = self.conn_factory(HOST)
            conn.request("GET", DOWN_PATH.format(n=DOWN_BYTES), headers={"User-Agent": "ctop"})
            resp = conn.getresponse()
            if resp.status != 200:
                with self.lock:
                    if gen == self.gen:
                        self.http_status = resp.status
            else:
                while not self._stop_now(gen):
                    data = resp.read(CHUNK)
                    if not data:
                        break
                    with self.lock:
                        if gen != self.gen:
                            break
                        if self.first_at is None:
                            self.first_at = self.clock()
                        self.moved += len(data)
                        self.last_at = self.clock()
        except Exception as exc:
            with self.lock:
                if self.stream_error is None and gen == self.gen:
                    self.stream_error = _short(exc)
        finally:
            with self.lock:
                if gen == self.gen:
                    self.finished += 1
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    def _upload_body(self, gen: int) -> None:
        conn = None
        share = UP_BYTES // UP_STREAMS
        try:
            conn = self.conn_factory(HOST)
            conn.putrequest("POST", UP_PATH)
            conn.putheader("Content-Length", str(share))
            conn.putheader("Content-Type", "application/octet-stream")
            conn.endheaders()
            sent = 0
            while sent < share and not self._stop_now(gen):
                n = min(CHUNK, share - sent)
                conn.send(_BLOCK if n == CHUNK else _BLOCK[:n])
                sent += n
                with self.lock:
                    if gen != self.gen:
                        break
                    if self.first_at is None:
                        self.first_at = self.clock()
                    self.moved += n
                    self.last_at = self.clock()
        except Exception as exc:
            with self.lock:
                if self.stream_error is None and gen == self.gen:
                    self.stream_error = _short(exc)
        finally:
            # Counted as finished before waiting for the reply, so the page never waits on the server.
            with self.lock:
                if gen == self.gen:
                    self.finished += 1
        if conn is not None:
            try:
                conn.getresponse()      # the server's answer; errors here are ignored
            except Exception:
                pass
            try:
                conn.close()
            except Exception:
                pass

    def _go(self) -> None:
        if not self._latency():
            return
        down = self._phase("download", DOWN_STREAMS, DOWN_SECONDS, DOWN_BYTES, self._download_body,
                           0.1, 0.5, loaded=True)
        if cancelled(self.cancel):
            self.down_mbps = down or None
            self.stage = "cancelled"
            self.emit()
            return
        if self.down_bytes == 0:
            if self.http_status is not None:
                self._fail(f"Speed test server answered with HTTP {self.http_status}")
            elif self.stream_error is not None:
                self._fail("Could not connect to the speed test server")
            else:
                self._fail("Download test got no data")
            return
        self.down_mbps = down
        up = self._phase("upload", UP_STREAMS, UP_SECONDS, UP_BYTES, self._upload_body, 0.6, 0.4,
                         loaded=False)
        if cancelled(self.cancel):
            self.up_mbps = up or None
            self.stage = "cancelled"
            self.emit()
            return
        if self.up_bytes == 0:
            if self.stream_error is not None:
                self._fail("Could not connect for the upload test")
            else:
                self._fail("Upload test sent no data")
            return
        self.up_mbps = up
        self.grade_word = grade(self.idle_ms, _median(self.loaded))
        self.stage = "done"
        self.progress = 1.0
        self.emit()

    def execute(self) -> SpeedResult:
        try:
            self._go()
        except Exception as exc:
            self.stage = "error"
            self.error = f"Speed test failed: {_short(exc)}"
            self.emit()
        return self.result()


def run_speed(on_update: Optional[Callable] = None, cancel: Optional[threading.Event] = None,
              conn_factory: Optional[Callable] = None, rtt: Optional[Callable] = None,
              clock: Optional[Callable] = None, sleep: Optional[Callable] = None) -> SpeedResult:
    """Run the whole test. Never raises; a problem becomes stage "error" with a plain message."""
    if conn_factory is None:
        conn_factory = _default_conn
    if rtt is None:
        rtt = tcp_rtt
    if clock is None:
        clock = time.perf_counter
    if sleep is None:
        sleep = cancel.wait if cancel is not None else time.sleep
    return _Run(on_update, cancel, conn_factory, rtt, clock, sleep).execute()
