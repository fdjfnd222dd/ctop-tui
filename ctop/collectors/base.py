"""Base class for every background data source (thermal, ping, wifi, security...)."""
from __future__ import annotations

import time


class Collector:
    """A data source refreshed off the UI thread.

    Subclass it, set `name` and `interval`, implement refresh().
    The app calls run() from a worker thread whenever `interval` seconds have
    passed and the previous run has finished.

    Rules for refresh():
      * assign a NEW immutable object to self.data (see ctop/models.py); never edit the old one
      * be cheap - ctop must not drain the battery it is measuring
      * never block longer than ~3 s (use timeouts on every subprocess)
      * be read-only: no writes, no root, no network traffic except what the collector is *for*
      * raising is allowed; run() records it in self.error and keeps going
    """

    name = "collector"
    interval = 5.0          # seconds between refreshes

    def __init__(self):
        self.data = None
        self.error = None
        self.busy = False
        self.last_run = 0.0

    def refresh(self):
        raise NotImplementedError

    def due(self, now=None):
        return not self.busy and (now or time.time()) - self.last_run >= self.interval

    def run(self):
        self.busy = True
        try:
            self.refresh()
            self.error = None
        except Exception as exc:                    # a broken sensor must never kill the dashboard
            self.error = repr(exc)
        finally:
            self.last_run = time.time()
            self.busy = False
