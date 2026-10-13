# T26 - Speed test tool (on demand, ~35 MB of data)

## 1 Purpose
`run_speed()` measures download and upload speed and "lag under load" (bufferbloat: how much your ping grows while the line is busy -
the thing that makes video calls stutter), using Cloudflare's public speed test endpoints over HTTPS with only the standard library.
It runs only when the user asks and tells the page how much data it used.

## 2 Files
Create `ctop/tools/speedtest.py`, `tests/test_speedtest.py`. Read `docs/specs/NET-COMMON.md`, `ctop/tools/__init__.py`.

## 3 Public API
```python
HOST = "speed.cloudflare.com"
DOWN_PATH = "/__down?bytes={n}"
UP_PATH = "/__up"
DOWN_STREAMS = 4                 # parallel connections; one TCP stream under-reads fast links
DOWN_BYTES = 25_000_000          # stop downloading after this many bytes in total ...
DOWN_SECONDS = 8.0               # ... or this many seconds, whichever first
UP_STREAMS = 2
UP_BYTES = 10_000_000
UP_SECONDS = 8.0
CHUNK = 65536
DATA_ESTIMATE_MB = 35            # shown to the user before the test starts
def grade(idle_ms: Optional[float], loaded_ms: Optional[float]) -> Optional[str]
    # increase = loaded - idle: <= 20 "A", <= 60 "B", <= 120 "C", <= 250 "D", else "F"; None if either is None
def mbps(nbytes: int, seconds: float) -> float                 # nbytes * 8 / seconds / 1e6; 0.0 when seconds <= 0
def tcp_rtt(host=HOST, port=443, timeout=2.0, connect=None, clock=time.perf_counter) -> Optional[float]   # TCP handshake ms, closes at once
def run_speed(on_update=None, cancel=None, conn_factory=None, rtt=None, clock=None, sleep=None) -> SpeedResult
```
`conn_factory(host) -> http.client.HTTPSConnection-like` (default `http.client.HTTPSConnection(host, timeout=10, context=ssl.create_default_context())`).
`rtt() -> Optional[float]` (default `tcp_rtt`). `clock` default `time.perf_counter`. `sleep(seconds)` default `cancel.wait` if cancel else `time.sleep`.

## 4 Stages (progress: latency 0-0.1, download 0.1-0.6, upload 0.6-1.0)
1. **latency** (idle): 5 calls of `rtt()` 100 ms apart; idle_ms = median of non-None; all None -> error "Could not reach the speed test server" -> stage "error", stop.
2. **download**: start DOWN_STREAMS threads; each opens `conn_factory(HOST)`, `request("GET", DOWN_PATH.format(n=DOWN_BYTES), headers={"User-Agent": "ctop"})`,
   `getresponse()`, then `read(CHUNK)` in a loop adding to a shared counter (use a `threading.Lock`), stopping when the total reaches DOWN_BYTES,
   DOWN_SECONDS passed, cancel is set, or read returns b"". Close the connection in `finally`.
   Meanwhile the main thread wakes every 0.25 s: appends the Mbps of the last interval to `samples` (keep last 40), updates progress, and every
   0.5 s calls `rtt()` - the loaded latency samples. Emit each wake.
   down_mbps = total bytes * 8 / (time from first byte to the end) / 1e6. Non-200 status or any exception in every stream with 0 bytes -> error.
3. **upload**: UP_STREAMS threads; each `putrequest("POST", UP_PATH)`, `putheader("Content-Length", str(UP_BYTES // UP_STREAMS))`,
   `putheader("Content-Type", "application/octet-stream")`, `endheaders()`, then `send(block)` of CHUNK zero bytes (one preallocated bytes
   object) until its share is sent, UP_SECONDS passed or cancel; then `getresponse()` (ignore errors after a stop). Same 0.25 s sampling. up_mbps likewise.
   If the upload stops early (time limit) the server sees a short body - fine, we only measure what we pushed.
4. loaded_ms = median of the rtt samples taken during download (None if none); grade; stage "done", progress 1.0.
bytes_used = downloaded + uploaded. cancel at any point -> stage "cancelled", keep the numbers so far. Never raise: any unexpected exception -> stage "error" with
`error="Speed test failed: <short reason>"`.
Must not hold more than one CHUNK per stream in memory (never accumulate the body).

## 5 Tests (`tests/test_speedtest.py`) - fake connections and a fake clock, NO network, run in well under 2 s
* grade boundaries: (10, 30) A, (10, 70) B, (10, 130) C, (10, 260) D, (10, 300) F, (None, 5) None.
* mbps(1_000_000, 1.0) == 8.0; mbps(5, 0) == 0.0.
* tcp_rtt with fake connect returning an object with close() -> float; raising OSError -> None.
* run_speed with: rtt fake returning 20.0 (idle) then 80.0; FakeConn whose getresponse() returns an object with status 200 and read() yielding
  CHUNK bytes N times then b""; send() counting bytes. Use a fake clock that advances 0.05 s per call and `sleep` that advances it - the test
  must not really sleep. Expect stage "done", down_mbps > 0, up_mbps > 0, idle_ms 20, loaded_ms 80, grade "B", bytes_used == downloaded + uploaded,
  progress 1.0, on_update called with stages latency, download, upload, done in that order.
* all rtt None -> stage "error", no connection made.
* cancel set during download (set it from inside FakeConn.read after a few chunks) -> stage "cancelled".
* conn_factory raising OSError -> stage "error" with a plain message.
* status 503 -> error.
