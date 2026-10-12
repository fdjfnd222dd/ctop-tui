"""The Textual app: layout, keys, and the stop-a-program flow."""
import signal
import threading
import time

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.screen import ModalScreen
from textual.widgets import Static

from ctop.panels.cpu import CpuPanel
from ctop.panels.memory import MemPanel
from ctop.panels.network import NetPanel
from ctop.panels.power import BatPanel
from ctop.safety import count_alive, kill_safety, signal_members
from ctop.system import Sampler
from ctop.ui import DIM, FG, Panel, fmt_gb

# --------------------------------------------------------------------------
# The app
# --------------------------------------------------------------------------
ARM_SECONDS = 5.0      # how long the first 'x' stays armed
MIN_GAP = 0.35         # second 'x' must come this much later (a held-down key must not kill)
SPEEDS = [0.5, 1.0, 2.0, 5.0]   # refresh intervals the + / - keys step through (seconds)
SORT_ORDER = ["ram", "cpu", "name"]
SORT_WORDS = {"ram": "RAM (biggest first)", "cpu": "CPU (busiest first; 100% = one full core)",
              "name": "name (A-Z)"}
HINT = "↑/↓ select   x x stop   s sort   / search   space pause   +/- speed   ? help   q quit"

HELP_TEXT = """\
 ctop - keys

   up / down  (or k / j)   choose a program in the list
   PgUp / PgDn, Home / End jump
   x  then  x              stop the chosen program (asks twice; system programs are locked)
   s                       sort the list: RAM -> CPU -> name
   /                       search by name  (Enter keeps it, Esc clears it)
   space                   pause / resume the whole display
   +  /  -                 refresh faster / slower (0.5 s, 1 s, 2 s, 5 s)
   ?                       this help         q   quit

 Numbers: RAM share uses PSS (shared memory is split fairly, so it adds up to your real RAM).
 CPU is % of ONE core, like top: 100% means one core fully busy.

 Press Esc, Enter or ? to close.
"""


class HelpScreen(ModalScreen):
    BINDINGS = [("escape,enter,question_mark,q", "app.pop_screen", "Close")]
    CSS = """
    HelpScreen { align: center middle; background: #000000 60%; }
    #help { width: 96; height: auto; padding: 1 2; background: #131820; border: round #4f8fd6; color: #d0d4da; }
    """

    def compose(self) -> ComposeResult:
        yield Static(HELP_TEXT, id="help")



class Ctop(App):
    TITLE = "ctop"
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("ctrl+c", "quit", "Quit", show=False),
        Binding("up,k", "move(-1)", "Up", show=False),
        Binding("down,j", "move(1)", "Down", show=False),
        Binding("pageup", "move(-10)", "Page up", show=False),
        Binding("pagedown", "move(10)", "Page down", show=False),
        Binding("home", "jump(0)", "Top", show=False),
        Binding("end", "jump(-1)", "Bottom", show=False),
        Binding("x", "kill", "Stop program"),
        Binding("escape", "cancel", "Cancel", show=False),
        Binding("s", "sort", "Sort", show=False),
        Binding("slash", "search", "Search", show=False),
        Binding("space", "pause", "Pause", show=False),
        Binding("plus,equals_sign", "speed(1)", "Faster", show=False),
        Binding("minus", "speed(-1)", "Slower", show=False),
        Binding("question_mark", "help", "Help", show=False),
    ]
    CSS = """
    Screen { background: #0c0e12; }
    Panel { height: 100%; padding: 0 1; background: #0c0e12; color: #d0d4da; overflow: hidden;
            border-title-style: bold; border-subtitle-color: #7d8590; }
    #top { height: 13; }
    #bottom { height: 1fr; }
    #status { height: 1; padding: 0 1; background: #131820; }
    #cpu { width: 3fr; border: round #5fa86b; border-title-color: #5fa86b; }
    #bat { width: 2fr; border: round #d9a441; border-title-color: #d9a441; }
    #mem { width: 3fr; border: round #4f8fd6; border-title-color: #4f8fd6; }
    #net { width: 2fr; border: round #c0627a; border-title-color: #c0627a; }
    """

    def compose(self) -> ComposeResult:
        with Horizontal(id="top"):
            yield CpuPanel("CPU", id="cpu")
            yield BatPanel("Battery", id="bat")
        with Horizontal(id="bottom"):
            yield MemPanel("Memory by program", id="mem")
            yield NetPanel("Network", id="net")
        yield Static(id="status")

    def on_mount(self):
        self.s = Sampler()
        self.speed_i = 1
        self._last_procs = self._last_net = 0.0
        self.say(HINT, DIM)
        self.tick()
        self._timer = self.set_interval(SPEEDS[self.speed_i], self.tick)

    def check_action(self, action, parameters):
        # while typing a search, letters must go to the search box, not trigger shortcuts
        if getattr(self, "s", None) and self.s.search_mode:
            return False
        return True

    def tick(self):
        s, now = self.s, time.time()
        if s.paused:
            return
        s.interval = SPEEDS[self.speed_i]
        s.sample_fast()
        # slow scans run on their own clock (seconds), whatever the refresh speed is
        if now - self._last_procs >= 5 and not s.procs_busy:
            self._last_procs = now
            threading.Thread(target=s.scan_procs, daemon=True).start()
        if now - self._last_net >= 15 and not s.net_busy:
            self._last_net = now
            threading.Thread(target=s.scan_net, daemon=True).start()
        for panel in self.query(Panel):
            panel.draw(s)

    # ---- sort / search / pause / speed / help
    def action_sort(self):
        s = self.s
        s.sort_key = SORT_ORDER[(SORT_ORDER.index(s.sort_key) + 1) % len(SORT_ORDER)]
        self.say(f"Sorted by {SORT_WORDS[s.sort_key]}", "#7aa2d6")
        self.redraw_list()

    def action_search(self):
        self.disarm(quiet=True)
        self.s.search_mode = True
        self.show_search()

    def show_search(self):
        s = self.s
        n = len(s.visible_groups())
        self.say(f"Search: {s.filter_text}_    ({n} match{'es' if n != 1 else ''})    Enter keeps it   Esc clears it",
                 "bold #7aa2d6")
        names = [g.name for g in s.visible_groups()]
        if s.cursor not in names:
            s.cursor = names[0] if names else None
        self.redraw_list()

    def action_pause(self):
        s = self.s
        s.paused = not s.paused
        self.say("PAUSED - the display is frozen. Press space to resume." if s.paused else HINT,
                 "bold #f2cc60" if s.paused else DIM)

    def action_speed(self, delta: int):
        i = max(0, min(len(SPEEDS) - 1, self.speed_i - delta))     # + = faster = shorter interval
        if i == self.speed_i:
            self.say(f"Already at the {'fastest' if delta > 0 else 'slowest'} speed ({SPEEDS[i]:g} s).", DIM)
            return
        self.speed_i = i
        self._timer.stop()
        self._timer = self.set_interval(SPEEDS[i], self.tick)
        self.say(f"Refreshing every {SPEEDS[i]:g} s", "#7aa2d6")

    def action_help(self):
        self.push_screen(HelpScreen())

    # ---- status line
    def say(self, text, style=FG):
        self.query_one("#status", Static).update(Text(text, style=style, no_wrap=True))

    def redraw_list(self):
        self.query_one("#mem", Panel).draw(self.s)

    # ---- selecting
    def disarm(self, quiet=False):
        if self.s.armed:
            self.s.armed = None
            if not quiet:
                self.say(HINT, DIM)
            self.redraw_list()

    def action_move(self, delta: int):
        self.disarm()
        names = [g.name for g in self.s.visible_groups()]
        if not names:
            return
        i = names.index(self.s.cursor) if self.s.cursor in names else -1
        i = 0 if i < 0 else max(0, min(len(names) - 1, i + delta))
        self.s.cursor = names[i]
        self.redraw_list()

    def action_jump(self, where: int):
        self.disarm()
        names = [g.name for g in self.s.visible_groups()]
        if names:
            self.s.cursor = names[where]
            self.redraw_list()

    def action_cancel(self):
        self.disarm()

    def on_key(self, event):
        s = self.s
        if s.search_mode:                                    # the search box owns the keyboard
            event.stop()
            event.prevent_default()
            k = event.key
            if k == "escape":
                s.filter_text, s.search_mode = "", False
                self.say(HINT, DIM)
                self.redraw_list()
                return
            if k == "enter":
                s.search_mode = False
                self.say(f"Showing programs matching '{s.filter_text}'. Press / to edit, / then Esc to clear."
                         if s.filter_text else HINT, "#7aa2d6" if s.filter_text else DIM)
                self.redraw_list()
                return
            if k == "backspace":
                s.filter_text = s.filter_text[:-1]
            elif event.is_printable and event.character:
                s.filter_text += event.character
            self.show_search()
            return
        # "any other key cancels" - x itself is handled by action_kill
        if event.key != "x":
            self.disarm()

    # ---- stopping a program (two presses, with the safety net)
    def action_kill(self):
        s, now = self.s, time.time()
        name = s.cursor
        group = next((g for g in s.groups if g.name == name), None)
        if name is None or group is None:
            self.say("Pick a program first with ↑/↓ (or j/k), then press x twice.", "#f2cc60")
            s.cursor = None
            return
        reason = kill_safety(name, group.members)
        if reason:
            s.armed = None
            self.say(f"Refused: {name} - {reason}. ctop will not stop it.", "bold #ff7b72")
            self.redraw_list()
            return
        force = now - s.term_sent.get(name, 0) < 60          # polite stop already tried
        if s.armed == name and now - s.armed_at >= MIN_GAP:
            self.do_kill(name, group, force)
            return
        if s.armed == name:                                  # key still held down: ignore
            return
        s.armed, s.armed_at = name, now
        what = "FORCE-KILL (cannot be saved)" if force else "Stop"
        self.say(f"{what} {name}?  {group.count} process{'es' if group.count != 1 else ''}, "
                 f"{fmt_gb(group.bytes)} of RAM. Unsaved work in it is lost.  "
                 f"Press x again to confirm - any other key cancels.", "bold #ffffff on #6b1f1f")
        self.redraw_list()
        self.set_timer(ARM_SECONDS, self.arm_timeout)

    def arm_timeout(self):
        if self.s.armed and time.time() - self.s.armed_at >= ARM_SECONDS - 0.1:
            self.disarm()

    def do_kill(self, name, group, force):
        s = self.s
        members = group.members
        sent = signal_members(members, signal.SIGKILL if force else signal.SIGTERM)
        s.armed = None
        s.term_sent[name] = time.time()
        self.say(f"{'Force-killed' if force else 'Asked'} {name} to close ({sent} processes). Checking in 3 s...",
                 "#f2cc60")
        self.redraw_list()
        self.set_timer(3.0, lambda: self.check_kill(name, members))

    def check_kill(self, name, members):
        s = self.s
        left = count_alive(members)
        if left == 0:
            s.term_sent.pop(name, None)
            self.say(f"{name} is closed.", "bold #56d364")
        else:
            self.say(f"{name} is still running ({left} processes). "
                     f"Press x twice again to FORCE-kill it.", "#ff9e64")
        if not s.procs_busy:                                 # refresh the list right away
            threading.Thread(target=s.scan_procs, daemon=True).start()


def main():
    Ctop().run()
