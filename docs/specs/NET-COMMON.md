# NET-COMMON - rules every Network-page task (T18-T32) follows

Read first: `docs/ARCHITECTURE.md`, `ctop/models.py` (the "network page" and "network tools" sections at the end),
`ctop/tools/__init__.py` (tool contract: `emit`, `cancelled`), and your own spec. Then build ONLY the files your spec names.

## Hard rules
1. **Only your files.** Never edit `ctop/models.py`, `ctop/app.py`, another task's file, or any doc. If the model is missing
   something you need, write a line `PROPOSED MODEL CHANGE: ...` at the end of your final answer instead and work around it.
2. **Python 3.9 syntax.** `from __future__ import annotations` at the top of every new module. No `match`, no `X | Y` types at
   runtime, no `list[int]` outside annotations. Standard library + `psutil`, `rich`, `textual` only. No new dependencies.
3. **No shell.** Every subprocess: argv list, `timeout=`, `stdin=subprocess.DEVNULL`, `env=dict(os.environ, LC_ALL="C")`.
   Use `ctop.collectors.wifi.run_cmd(argv, timeout) -> Optional[str]` (stdout on exit 0, else None) for read-only commands
   unless your spec says otherwise.
4. **Injectable.** Every command runner, socket factory, clock, file root or resolver is a parameter with a real default,
   so tests never run a real command, open a real socket, read real `/sys`, or sleep. A test suite must finish in < 2 s.
5. **Degrade, never crash.** Missing tool / permission / bad output => `None` fields or an `error` string in plain words.
   Collectors may raise (the base class records it); tools and renderers must not raise.
6. **Cheap.** Precompile regexes at module level. No busy loops. Use `select`/timeouts, never `time.sleep` in a polling loop
   shorter than 50 ms. Nothing scales worse than linearly with the number of networks / hops / ports.
7. **Privacy.** Tests and docstrings use documentation addresses only: IPv4 `192.0.2.x`, `198.51.100.x`, `203.0.113.x`,
   IPv6 `2001:db8::`, MACs `02:00:00:aa:bb:cc` / `AA:BB:CC:DD:EE:01`. Example SSIDs: `ExampleNet`, `Neighbour Net`, `Cafe: Free`.
   (Private ranges such as `10.0.0.1`, `100.64.0.1` are fine inside test code when the spec needs them.)
8. **Comments say why**, short and plain, matching the style of `ctop/collectors/ping.py`.
9. **Renderers** (`ctop/render/*`) are pure: `(models..., w, h) -> list[rich.text.Text]`, every line `no_wrap=True`, at most
   `w` cells (use `ctop.ui.fit`), at most `h` lines; `None` input => one short dim line. Colours from `ctop.ui`
   (`DIM`, `FG`, `TRACK`, `grad`) plus green `#56d364`, yellow `#f2cc60`, red `#ff7b72`, blue `#4ea1ff`, cyan `#39c5cf`.

## Done when (every task)
- `.venv/bin/python -m unittest tests.<your_test_module> -v` passes.
- `.venv/bin/python -m pyflakes <your files>` prints nothing.
- `.venv/bin/python -m unittest discover -s tests` still passes as a whole (do not break other tests).
- Your final answer: the files you created, the test count, and any PROPOSED MODEL CHANGE lines. Nothing else.
