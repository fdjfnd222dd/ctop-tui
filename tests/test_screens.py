"""Tests for ctop/screens.py - the arrow-key menu, the text box and the Trace & ports dialog.

Each test runs the screen inside a tiny App through Textual's pilot and checks what the screen returns.
"""
from __future__ import annotations

import asyncio
import unittest
from unittest import mock

from textual.app import App
from textual.widgets import Input

from ctop.screens import InputScreen, MenuScreen, TargetScreen


async def _no_idle_wait(min_sleep: float = 0, max_sleep: float = 1) -> None:
    # Textual's pilot waits for CPU idle after every key press, which can take up to 1 s each
    # on a busy machine. Message ordering is still handled by pilot._wait_for_screen(),
    # so one turn of the event loop is enough for these tests.
    await asyncio.sleep(0)


_patches = []


def setUpModule():
    for target in ("textual.app.wait_for_idle", "textual.pilot.wait_for_idle"):
        patcher = mock.patch(target, _no_idle_wait)
        patcher.start()
        _patches.append(patcher)


def tearDownModule():
    while _patches:
        _patches.pop().stop()


class Host(App):
    """Pushes one screen on mount and remembers what it dismissed with."""

    def __init__(self, screen):
        super().__init__()
        self._screen_under_test = screen
        self.result = "unset"

    def on_mount(self) -> None:
        self.push_screen(self._screen_under_test, callback=self._done)

    def _done(self, value) -> None:
        self.result = value


class MenuScreenTests(unittest.IsolatedAsyncioTestCase):
    ITEMS = [(None, "Mode"), ("auto", "Automatic"), ("manual", "Manual"), (None, ""),
             ("dhcp", "DHCP only"), ("forget", "Forget this network")]

    async def _run(self, keys, **kw):
        screen = MenuScreen("Choose", self.ITEMS, subtitle="Pick one", danger=("forget",), **kw)
        app = Host(screen)
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause(0.01)
            for key in keys:
                await pilot.press(key)
                await pilot.pause(0.01)
            return app.result, screen

    async def test_down_down_enter_skips_headings(self):
        result, _ = await self._run(["down", "down", "enter"])
        self.assertEqual(result, "dhcp")

    async def test_escape_returns_none(self):
        result, _ = await self._run(["escape"])
        self.assertIsNone(result)

    async def test_number_key_picks_nth_selectable(self):
        result, _ = await self._run(["2"])
        self.assertEqual(result, "manual")

    async def test_current_value_is_preselected(self):
        result, _ = await self._run(["enter"], current="manual")
        self.assertEqual(result, "manual")

    async def test_up_at_top_stays_on_first(self):
        result, _ = await self._run(["up", "enter"])
        self.assertEqual(result, "auto")

    async def test_end_then_enter_picks_last(self):
        result, _ = await self._run(["end", "enter"])
        self.assertEqual(result, "forget")

    async def test_down_stops_at_the_end(self):
        result, _ = await self._run(["down"] * 10 + ["enter"])
        self.assertEqual(result, "forget")

    async def test_j_and_k_move(self):
        result, _ = await self._run(["j", "j", "k", "enter"])
        self.assertEqual(result, "manual")


class InputScreenTests(unittest.IsolatedAsyncioTestCase):
    async def test_validation_keeps_screen_open_and_shows_error(self):
        screen = InputScreen("Name", "Type a name", validate=lambda t: "Too short" if len(t) < 6 else None)
        app = Host(screen)
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause(0.01)
            await pilot.press(*"short")
            await pilot.press("enter")
            await pilot.pause(0.01)
            self.assertEqual(app.result, "unset")
            self.assertEqual(screen.error, "Too short")
            await pilot.press(*"er")
            await pilot.press("enter")
            await pilot.pause(0.01)
            self.assertEqual(app.result, "shorter")

    async def test_password_is_masked_and_ctrl_r_toggles(self):
        screen = InputScreen("Passphrase", "Type it", password=True)
        app = Host(screen)
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause(0.01)
            field = screen.query_one("#iinput", Input)
            self.assertTrue(field.password)
            await pilot.press("ctrl+r")
            await pilot.pause(0.01)
            self.assertFalse(field.password)
            await pilot.press("ctrl+r")
            await pilot.pause(0.01)
            self.assertTrue(field.password)

    async def test_escape_returns_none(self):
        app = Host(InputScreen("Name", "Type a name", initial="abc"))
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause(0.01)
            await pilot.press("escape")
            await pilot.pause(0.01)
            self.assertIsNone(app.result)

    async def test_initial_text_is_returned_on_enter(self):
        app = Host(InputScreen("Name", "Type a name", initial="ExampleNet"))
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause(0.01)
            await pilot.press("enter")
            await pilot.pause(0.01)
            self.assertEqual(app.result, "ExampleNet")


def fake_parse_ports(text):
    """Accepts "22, 443"-style text; returns (ports, error) like the real parser."""
    try:
        ports = tuple(int(p) for p in text.replace(",", " ").split())
    except ValueError:
        return (), "Ports are numbers, like 22, 443."
    if any(p < 1 or p > 65535 for p in ports):
        return (), "Ports are 1 to 65535."
    return ports, None


PRESETS = [("Common", (22, 80, 443)), ("Web", (80, 443)), ("Remote", (22, 3389))]


class TargetScreenTests(unittest.IsolatedAsyncioTestCase):
    def _screen(self, **kw):
        return TargetScreen(PRESETS, fake_parse_ports, **kw)

    async def test_host_with_empty_ports_is_trace_only(self):
        app = Host(self._screen())
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause(0.01)
            await pilot.press(*"example.org")
            await pilot.press("enter")
            await pilot.pause(0.01)
            self.assertEqual(app.result, ("example.org", ()))

    async def test_down_then_ports_then_enter(self):
        app = Host(self._screen())
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause(0.01)
            await pilot.press(*"203.0.113.5")
            await pilot.press("down")
            await pilot.press(*"22, 443")
            await pilot.press("enter")
            await pilot.pause(0.01)
            self.assertEqual(app.result, ("203.0.113.5", (22, 443)))

    async def test_preset_row_right_fills_ports(self):
        screen = self._screen()
        app = Host(screen)
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause(0.01)
            await pilot.press(*"example.org")
            await pilot.press("down", "down")
            await pilot.pause(0.01)
            self.assertIs(app.focused, screen.query_one("#tpreset"))
            await pilot.press("right")
            await pilot.pause(0.01)
            self.assertEqual(screen.query_one("#tports", Input).value, "80, 443")
            await pilot.press("enter")
            await pilot.pause(0.01)
            self.assertEqual(app.result, ("example.org", (80, 443)))

    async def test_invalid_ports_show_error_and_stay_open(self):
        screen = self._screen()
        app = Host(screen)
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause(0.01)
            await pilot.press(*"example.org")
            await pilot.press("down")
            await pilot.press(*"abc")
            await pilot.press("enter")
            await pilot.pause(0.01)
            self.assertEqual(app.result, "unset")
            self.assertIn("numbers", screen.error)

    async def test_empty_host_is_an_error(self):
        screen = self._screen()
        app = Host(screen)
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause(0.01)
            await pilot.press("enter")
            await pilot.pause(0.01)
            self.assertEqual(app.result, "unset")
            self.assertIn("Type an address", screen.error)

    async def test_validate_host_rejects(self):
        screen = self._screen(validate_host=lambda h: "Not a valid name." if " " in h else None)
        app = Host(screen)
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause(0.01)
            await pilot.press(*"bad name")
            await pilot.press("enter")
            await pilot.pause(0.01)
            self.assertEqual(app.result, "unset")
            self.assertEqual(screen.error, "Not a valid name.")

    async def test_prefilled_host_focuses_ports(self):
        screen = self._screen(host="203.0.113.5", ports_text="443")
        app = Host(screen)
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause(0.01)
            self.assertIs(app.focused, screen.query_one("#tports", Input))
            await pilot.press("enter")
            await pilot.pause(0.01)
            self.assertEqual(app.result, ("203.0.113.5", (443,)))

    async def test_escape_cancels(self):
        app = Host(self._screen())
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause(0.01)
            await pilot.press("escape")
            await pilot.pause(0.01)
            self.assertIsNone(app.result)


if __name__ == "__main__":
    unittest.main()
