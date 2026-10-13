"""Pop-up screens for the Network page: an arrow-key menu, a one-line text box, and the Trace & ports dialog.

They only collect input and hand it back through dismiss(value). They never touch the network or
the system: validation and presets come in as parameters, so this module imports nothing from ctop.netctl
or ctop.tools. Styled like HelpScreen / DetailScreen in ctop/app.py.
"""
from __future__ import annotations

from typing import Callable, Collection, List, Optional, Sequence, Tuple

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Static

DIM = "#7d8590"
FG = "#d0d4da"
RED = "#ff7b72"
CURSOR_STYLE = "bold on #1f3550"
SCREEN_BG = "#000000 60%"
MIN_MENU_W = 36
MAX_MENU_W = 70


def _window(n: int, cursor: int, rows: int) -> Tuple[int, int]:
    """(start, end) slice of n rows that keeps the cursor visible, near the middle when scrolling."""
    if n <= 0 or rows <= 0:
        return (0, 0)
    if n <= rows:
        return (0, n)
    cursor = min(max(cursor, 0), n - 1)
    start = min(max(cursor - rows // 2, 0), n - rows)
    return (start, start + rows)


class MenuScreen(ModalScreen):
    """Arrow-key menu. Returns the chosen item's value, or None on Esc."""

    BINDINGS = [
        Binding("up,k", "move(-1)", "Up", priority=True),
        Binding("down,j", "move(1)", "Down", priority=True),
        Binding("home", "jump(0)", "First", priority=True),
        Binding("end", "jump(-1)", "Last", priority=True),
        Binding("enter", "accept", "Choose", priority=True),
        Binding("escape", "cancel", "Cancel", priority=True),
    ]
    CSS = """
    MenuScreen { align: center middle; background: #000000 60%; }
    #mbox { background: #0c0e12; border: round #39c5cf; border-title-color: #39c5cf; padding: 0 1;
            height: auto; max-height: 90%; }
    #mbody { height: auto; }
    """

    def __init__(self, title: str, items: Sequence[Tuple[Optional[str], str]], subtitle: str = "",
                 danger: Collection[str] = (), current: Optional[str] = None):
        super().__init__()
        self._title = title
        self._items = [(v, label) for v, label in items]
        self._subtitle = subtitle
        self._danger = set(danger)
        self._current = current
        # Indexes of the rows that can be chosen; headings and rules are skipped by the cursor.
        self._selectable = [i for i, (v, _label) in enumerate(self._items) if v is not None]
        self.cursor = 0                       # index into self._items; always a selectable row
        if self._selectable:
            self.cursor = self._selectable[0]
            for i in self._selectable:
                if self._items[i][0] == current:
                    self.cursor = i
                    break

    def compose(self) -> ComposeResult:
        with Vertical(id="mbox"):
            yield Static(id="mbody")

    def on_mount(self) -> None:
        longest = max([len(label) for _v, label in self._items] + [len(self._title), len(self._subtitle)])
        self._box_w = max(MIN_MENU_W, min(longest + 10, MAX_MENU_W))
        box = self.query_one("#mbox", Vertical)
        box.styles.width = self._box_w
        box.border_title = self._title
        self._redraw()

    # ---- drawing ------------------------------------------------------------
    def _inner_width(self) -> int:
        return max(1, self._box_w - 4)        # border (2) + padding (2)

    def _redraw(self) -> None:
        inner = self._inner_width()
        lines: List[Text] = []
        if self._subtitle:
            lines.append(Text(self._subtitle, style=DIM, no_wrap=True))
        visible = max(3, self.app.size.height - 10)
        start, end = _window(len(self._items), self.cursor, visible)
        for i in range(start, end):
            value, label = self._items[i]
            if value is None:
                if label == "":
                    lines.append(Text("─" * inner, style=DIM, no_wrap=True))
                else:
                    lines.append(Text(label, style=f"bold {DIM}", no_wrap=True))
                continue
            on_cursor = i == self.cursor
            row = Text(no_wrap=True)
            row.append("▸ " if on_cursor else "  ", style="bold #39c5cf" if on_cursor else DIM)
            row.append("● " if value == self._current else "  ", style=FG)
            style = RED if value in self._danger else FG
            row.append(label, style=style)
            row.truncate(inner, overflow="ellipsis")
            row.pad_right(max(0, inner - row.cell_len))
            if on_cursor:
                row.stylize(CURSOR_STYLE, 0, len(row))
            lines.append(row)
        body = Text("\n").join(lines) if lines else Text("")
        self.query_one("#mbody", Static).update(body)

    # ---- actions ------------------------------------------------------------
    def action_move(self, delta: int) -> None:
        if not self._selectable:
            return
        pos = self._selectable.index(self.cursor) if self.cursor in self._selectable else 0
        pos = min(max(pos + delta, 0), len(self._selectable) - 1)   # stop at the ends, no wrap
        self.cursor = self._selectable[pos]
        self._redraw()

    def action_jump(self, which: int) -> None:
        if not self._selectable:
            return
        self.cursor = self._selectable[which]
        self._redraw()

    def action_accept(self) -> None:
        if self._selectable:
            self.dismiss(self._items[self.cursor][0])

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_key(self, event) -> None:
        # Number keys pick the Nth selectable row straight away (fast for experts).
        if event.character and event.character in "123456789":
            n = int(event.character) - 1
            if n < len(self._selectable):
                self.cursor = self._selectable[n]
                self.dismiss(self._items[self.cursor][0])
            event.stop()


class InputScreen(ModalScreen):
    """One line of text. Returns the text, or None on Esc."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", priority=True),
        Binding("ctrl+r", "toggle_password", "Show/hide", priority=True),
    ]
    CSS = """
    InputScreen { align: center middle; background: #000000 60%; }
    #ibox { width: 64; height: auto; background: #0c0e12; border: round #39c5cf; border-title-color: #39c5cf;
            padding: 0 1; }
    #iprompt { height: auto; color: #d0d4da; }
    #iinput { background: #131820; border: tall #262a31; }
    #iinput:focus { border: tall #39c5cf; }
    #ierr { height: 1; color: #ff7b72; }
    #ihint { height: auto; color: #7d8590; }
    """

    def __init__(self, title: str, prompt: str, password: bool = False, initial: str = "",
                 placeholder: str = "", validate: Optional[Callable[[str], Optional[str]]] = None):
        super().__init__()
        self._title = title
        self._prompt = prompt
        self._password = password
        self._initial = initial
        self._placeholder = placeholder
        self._validate = validate
        self.error = ""                       # the red line under the field, "" when none

    def compose(self) -> ComposeResult:
        with Vertical(id="ibox"):
            yield Static(self._prompt, id="iprompt")
            yield Input(value=self._initial, placeholder=self._placeholder, password=self._password,
                        id="iinput")
            yield Static("", id="ierr")
            hint = "Enter OK   Esc cancel"
            if self._password:
                hint += "\nCtrl+R shows/hides the password"
            yield Static(hint, id="ihint")

    def on_mount(self) -> None:
        self.query_one("#ibox", Vertical).border_title = self._title
        self.query_one("#iinput", Input).focus()

    @on(Input.Submitted, "#iinput")
    def _submitted(self, event: Input.Submitted) -> None:
        event.stop()
        text = event.value
        if self._validate is not None:
            reason = self._validate(text)
            if reason:
                self._show_error(reason)
                return
        self.dismiss(text)

    def _show_error(self, reason: str) -> None:
        self.error = reason
        self.query_one("#ierr", Static).update(Text(reason, style=RED, no_wrap=True))

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_toggle_password(self) -> None:
        if not self._password:
            return
        field = self.query_one("#iinput", Input)
        field.password = not field.password


class PresetRow(Static):
    """Focusable row that cycles the port presets with Left/Right."""

    can_focus = True
    BINDINGS = [
        Binding("left", "step(-1)", "Previous preset", show=False),
        Binding("right", "step(1)", "Next preset", show=False),
    ]

    def __init__(self, **kw):
        super().__init__("", **kw)

    def show(self, name: str, ports_text: str) -> None:
        t = Text(no_wrap=True)
        t.append("◂ ", style=DIM)
        t.append(name, style=f"bold {FG}")
        t.append(" ▸", style=DIM)
        if ports_text:
            t.append(f"   {ports_text}", style=DIM)
        self.update(t)

    def action_step(self, delta: int) -> None:
        screen = self.screen
        if isinstance(screen, TargetScreen):
            screen.cycle_preset(delta)


class TargetScreen(ModalScreen):
    """Address + ports for Trace & ports. Returns (host: str, ports: Tuple[int, ...]) or None."""

    BINDINGS = [
        Binding("up", "focus_step(-1)", "Previous field", priority=True),
        Binding("down", "focus_step(1)", "Next field", priority=True),
        Binding("enter", "submit", "Run", priority=True),
        Binding("escape", "cancel", "Cancel", priority=True),
    ]
    CSS = """
    TargetScreen { align: center middle; background: #000000 60%; }
    #tbox { width: 66; height: auto; background: #0c0e12; border: round #39c5cf; border-title-color: #39c5cf;
            padding: 0 1; }
    .trow { height: 3; }
    .tlabel { width: 18; height: 3; padding: 1 0 0 0; color: #d0d4da; }
    #tprow { height: 3; }
    #tpreset { height: 3; padding: 0 1; background: #131820; border: tall #262a31; color: #d0d4da; }
    #tpreset:focus { border: tall #39c5cf; }
    Input { background: #131820; border: tall #262a31; }
    Input:focus { border: tall #39c5cf; }
    #thint { height: auto; color: #7d8590; }
    #terr { height: 1; color: #ff7b72; }
    """

    def __init__(self, presets: Sequence[Tuple[str, Tuple[int, ...]]],
                 parse_ports: Callable[[str], Tuple[Tuple[int, ...], Optional[str]]],
                 host: str = "", ports_text: str = "",
                 validate_host: Optional[Callable[[str], Optional[str]]] = None):
        super().__init__()
        self._presets = list(presets)
        self._parse_ports = parse_ports
        self._host = host
        self._ports_text = ports_text
        self._validate_host = validate_host
        self._preset = 0
        self.error = ""                       # the red line at the bottom, "" when none

    def compose(self) -> ComposeResult:
        with Vertical(id="tbox"):
            with Horizontal(classes="trow"):
                yield Static("Address or name", classes="tlabel")
                yield Input(value=self._host, placeholder="example.org or 203.0.113.5", id="thost")
            with Horizontal(classes="trow"):
                yield Static("Ports", classes="tlabel")
                yield Input(value=self._ports_text, placeholder="22, 80, 443 (empty = trace only)", id="tports")
            with Horizontal(id="tprow"):
                yield Static("Preset", classes="tlabel")
                yield PresetRow(id="tpreset")
            yield Static("Enter runs the trace and checks these ports.  ↑/↓ move between fields.  Esc cancel",
                         id="thint")
            yield Static("", id="terr")

    def on_mount(self) -> None:
        self.query_one("#tbox", Vertical).border_title = "Trace & ports"
        self._show_preset()
        focus = "#tports" if self._host else "#thost"
        self.query_one(focus, Input).focus()

    # ---- presets ------------------------------------------------------------
    def _show_preset(self) -> None:
        name, ports = self._presets[self._preset] if self._presets else ("", ())
        self.query_one("#tpreset", PresetRow).show(name, ", ".join(str(p) for p in ports))

    def cycle_preset(self, delta: int) -> None:
        if not self._presets:
            return
        self._preset = (self._preset + delta) % len(self._presets)
        ports = self._presets[self._preset][1]
        self.query_one("#tports", Input).value = ", ".join(str(p) for p in ports)
        self._show_preset()

    # ---- focus --------------------------------------------------------------
    def _focus_order(self) -> list:
        return [self.query_one("#thost", Input), self.query_one("#tports", Input),
                self.query_one("#tpreset", PresetRow)]

    def action_focus_step(self, delta: int) -> None:
        order = self._focus_order()
        current = self.focused
        idx = order.index(current) if current in order else 0
        if current in order:
            idx = min(max(idx + delta, 0), len(order) - 1)   # no wrap, like the menu
        order[idx].focus()

    # ---- submit -------------------------------------------------------------
    def _show_error(self, reason: str) -> None:
        self.error = reason
        self.query_one("#terr", Static).update(Text(reason, style=RED, no_wrap=True))

    def action_submit(self) -> None:
        host = self.query_one("#thost", Input).value.strip()
        if not host:
            self._show_error("Type an address or name first.")
            return
        if self._validate_host is not None:
            reason = self._validate_host(host)
            if reason:
                self._show_error(reason)
                return
        ports_text = self.query_one("#tports", Input).value.strip()
        if not ports_text:
            self.dismiss((host, ()))              # trace only, no ports
            return
        ports, reason = self._parse_ports(ports_text)
        if reason:
            self._show_error(reason)
            return
        self.dismiss((host, tuple(ports)))

    def action_cancel(self) -> None:
        self.dismiss(None)
