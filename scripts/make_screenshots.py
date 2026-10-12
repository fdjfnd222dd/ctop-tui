#!/usr/bin/env python3
"""Regenerate the README screenshots from demo mode (no real machine data ever appears).

Run from the repo root:  .venv/bin/python scripts/make_screenshots.py
Needs `rsvg-convert` (Debian/Ubuntu: apt install librsvg2-bin) to turn Textual's SVG into PNG.
"""
import asyncio
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from ctop.app import Ctop  # noqa: E402

OUT = os.path.join(ROOT, "docs", "screenshots")
SIZE = (150, 46)


def save(app, name):
    svg = os.path.join(OUT, name + ".svg")
    with open(svg, "w") as f:
        f.write(app.export_screenshot())
    if shutil.which("rsvg-convert"):
        # keep runs of spaces: without this the renderer squeezes "Temp  49 C" into "Temp 49 C"
        with open(svg) as f:
            text = f.read().replace("<text ", '<text xml:space="preserve" ')
        with open(svg, "w") as f:
            f.write(text)
        subprocess.run(["rsvg-convert", "-w", "2200", svg, "-o", os.path.join(OUT, name + ".png")], check=True)
        os.remove(svg)                                   # keep only the PNG
    print("wrote", name)


async def main():
    os.makedirs(OUT, exist_ok=True)
    app = Ctop(demo=True)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause(3.5)
        app.s.cursor = "Firefox"
        app.redraw_list()
        await pilot.pause(0.5)
        save(app, "overview")

        await pilot.press("enter")                       # program details pop-up
        await pilot.pause(1.5)
        save(app, "details")
        await pilot.press("escape")

        app.s.cursor = "Slack"                           # the two-press stop prompt
        app.redraw_list()
        await pilot.press("x")
        await pilot.pause(0.5)
        save(app, "stop-prompt")
        await pilot.press("escape")

    # the security page has less to show, so a shorter window looks better
    app = Ctop(demo=True)
    async with app.run_test(size=(SIZE[0], 30)) as pilot:
        await pilot.pause(2.5)
        await pilot.press("2")
        await pilot.pause(2.0)
        save(app, "security")


asyncio.run(main())
