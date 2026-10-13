# T30 - Pop-up screens for the Network page (arrow-key menus, text input, target dialog)

## 1 Purpose
Three reusable Textual modal screens, styled like the existing pop-ups in `ctop/app.py` (`HelpScreen`, `DetailScreen`): dark box, round
border, title in the border. Everything is keyboard-driven: Up/Down to choose, Enter to accept, Esc to cancel. They return a value through
`self.dismiss(value)`; the app passes a callback to `push_screen`. They never touch the network or the system themselves.

## 2 Files
Create `ctop/screens.py`, `tests/test_screens.py`. Read `docs/specs/NET-COMMON.md`, `ctop/app.py` (HelpScreen/DetailScreen for style), Textual 8.x API
(installed: `.venv/lib/python3*/site-packages/textual`). Do NOT import `ctop.netctl` or `ctop.tools.*` (built in parallel): validation and presets are passed in.

## 3 Public API
```python
class MenuScreen(ModalScreen):
    """Arrow-key menu. Returns the chosen item's value, or None on Esc."""
    def __init__(self, title: str, items: Sequence[Tuple[Optional[str], str]], subtitle: str = "",
                 danger: Collection[str] = (), current: Optional[str] = None): ...
    # items: (value, label). value None = a non-selectable heading/separator row (label "" draws a dim rule).
    # danger: values drawn in red (e.g. "forget"). current: value marked with "●" (e.g. the DNS preset in use) - the cursor starts on it.

class InputScreen(ModalScreen):
    """One line of text. Returns the text, or None on Esc."""
    def __init__(self, title: str, prompt: str, password: bool = False, initial: str = "", placeholder: str = "",
                 validate: Optional[Callable[[str], Optional[str]]] = None): ...
    # validate(text) -> None if OK else a plain reason shown in red under the field; Enter does nothing else until it is OK.
    # password=True: Input(password=True) plus a line "Ctrl+R shows/hides the password".

class TargetScreen(ModalScreen):
    """Address + ports for Trace & ports. Returns (host: str, ports: Tuple[int, ...]) or None."""
    def __init__(self, presets: Sequence[Tuple[str, Tuple[int, ...]]], parse_ports: Callable[[str], Tuple[Tuple[int, ...], Optional[str]]],
                 host: str = "", ports_text: str = "", validate_host: Optional[Callable[[str], Optional[str]]] = None): ...
```

## 4 Behaviour
**MenuScreen**: one `Static` body redrawn as rich `Text` lines (do not use OptionList - we want the exact ctop look and no focus quirks).
Box width = min(max label length + 10, 70), at least 36; height = rows + 4 (title border, subtitle line, padding). Subtitle line DIM above the items.
Cursor row: `bold on #1f3550`, prefixed "▸ "; other rows "  ". Up/Down (and k/j) skip non-selectable rows and stop at the ends (no wrap); Home/End;
Enter -> `dismiss(value)`; Esc -> `dismiss(None)`. Number keys 1-9 jump to and accept the Nth selectable item (fast for experts).
Danger rows red `#ff7b72`. Current value: "● " marker after the cursor prefix, cursor starts there (else first selectable).
BINDINGS must use `priority=True` for up/down/enter/escape so the app's own bindings do not fire underneath.

**InputScreen**: Vertical box: prompt Static, Input, error Static (1 row, red), hint Static DIM "Enter OK   Esc cancel". On `Input.Submitted`:
run validate; error -> show it, keep the screen; OK -> dismiss(text). Ctrl+R toggles `input.password` when password=True. Esc -> None.
The Input has focus on mount.

**TargetScreen**: rows:
```
Address or name   [ 203.0.113.5          ]
Ports             [ 22, 80, 443          ]
Preset            ◂ Common ▸   (←/→ to change: fills the ports)
Enter runs the trace and checks these ports.  ↑/↓ move between fields.  Esc cancel
[error line, red]
```
Up/Down move focus between the two Inputs and the preset row (a focusable Static subclass `PresetRow` with `can_focus=True`).
On the preset row Left/Right cycle presets and write `", ".join(map(str, ports))` into the ports Input. Enter (from anywhere, including Input.Submitted):
host = stripped address; `validate_host(host)` (if given) or non-empty check -> error; ports text may be EMPTY = trace only -> ports `()`;
else `parse_ports(text)` -> error shown or OK -> `dismiss((host, ports))`. Initial focus: the address Input (or ports Input if host prefilled).
Tab is captured by the app for page switching; do not rely on Tab.

CSS: reuse the look `background: #000000 60%` for the screen, box `background: #0c0e12; border: round #39c5cf; border-title-color: #39c5cf; padding: 0 1;`,
Input styled `background: #131820; border: tall #262a31;` and `:focus` border `tall #39c5cf`.

## 5 Tests (`tests/test_screens.py`) using Textual's pilot (`async with app.run_test() as pilot`) in `unittest.IsolatedAsyncioTestCase`
Make a tiny `App` subclass in the test that pushes the screen in `on_mount` with a callback storing the result.
* MenuScreen: down, down, enter -> third selectable value (heading rows skipped); esc -> None; "2" -> second selectable value; current value preselected; up at top stays.
* InputScreen: type "short", enter with validate rejecting -> still open and error text shown; type more, enter -> value returned; password mode masks (input.password True); ctrl+r toggles.
* TargetScreen: type host, enter with empty ports -> (host, ()); down to ports, type "22, 443", enter -> (host, (22, 443)) using a fake parse_ports;
  down twice to preset row, right -> ports input text equals the second preset's ports; invalid ports -> error shown, not dismissed; empty host -> error.
Keep every test under 1 s; use `await pilot.pause()` after key presses.
