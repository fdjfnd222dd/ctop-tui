"""On-demand network tools for the Network page (trace, port check, DNS timing, speed test, doctor, device finder).

Unlike collectors, a tool runs ONLY when the user asks for it, on one worker thread.
Every tool follows the same contract so the app can run them all the same way:

    def run_<tool>(..., on_update: Callable[[Result], None] = None,
                   cancel: Optional[threading.Event] = None, ...) -> Result

  * builds frozen result objects from ctop/models.py and calls on_update(result) with a NEW
    object every time something visible changed (at most ~10 times a second)
  * checks cancel.is_set() between steps and returns promptly (well under 1 s) when it is set
  * never raises: problems become the result's `error` field in plain words
  * every subprocess has timeout=, stdin=DEVNULL, argv list (no shell), env LC_ALL=C
  * anything it touches on the system (sockets, resolvers, commands) is injectable for tests
"""
from __future__ import annotations

import threading
from typing import Callable, Optional


def emit(on_update: Optional[Callable], result) -> None:
    """Send a progress snapshot; a broken callback must never stop the tool."""
    if on_update is not None:
        try:
            on_update(result)
        except Exception:
            pass


def cancelled(cancel: Optional[threading.Event]) -> bool:
    return cancel is not None and cancel.is_set()
