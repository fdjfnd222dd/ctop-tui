"""Demo mode must never read the machine's programs or stop anything."""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ctop.app import Ctop  # noqa: E402
from ctop.demo import PROGRAMS  # noqa: E402


class DemoMode(unittest.TestCase):
    def test_runs_and_never_stops_anything(self):
        victim = subprocess.Popen(["sleep", "120"])
        try:
            async def scenario():
                app = Ctop(demo=True)
                async with app.run_test(size=(140, 40)) as pilot:
                    await pilot.pause(1.5)
                    names = {g.name for g in app.s.groups}
                    self.assertEqual(names, {p[0] for p in PROGRAMS})   # only made-up programs
                    self.assertEqual(app.s.groups[0].members, [])        # fake programs have no pids
                    app.s.cursor = "Slack"
                    await pilot.press("x")
                    await pilot.pause(0.5)
                    await pilot.press("x")
                    await pilot.pause(0.3)
                    await pilot.press("2")
                    await pilot.pause(0.5)
                    self.assertEqual(app.page, 2)
                    await pilot.press("q")
            asyncio.run(scenario())
            self.assertIsNone(victim.poll())          # nothing on the real machine was touched
        finally:
            victim.kill()


if __name__ == "__main__":
    unittest.main()
