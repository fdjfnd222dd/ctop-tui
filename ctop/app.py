"""The Textual app: layout, keys, and the stop-a-program flow."""
import signal
import threading
import time

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
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
HINT = "↑/↓ or j/k select a program    x x stop it (asks twice; system programs are locked)    q quit"


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
        self.ticks = 0
        self.say(HINT, DIM)
        self.tick()
        self.set_interval(1.0, self.tick)

    def tick(self):
        s = self.s
        s.sample_fast()
        if self.ticks % 5 == 0 and not s.procs_busy:
            threading.Thread(target=s.scan_procs, daemon=True).start()
        if self.ticks % 15 == 0 and not s.net_busy:
            threading.Thread(target=s.scan_net, daemon=True).start()
        self.ticks += 1
        for panel in self.query(Panel):
            panel.draw(s)

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
        names = [g[0] for g in self.s.groups]
        if not names:
            return
        i = names.index(self.s.cursor) if self.s.cursor in names else -1
        i = 0 if i < 0 else max(0, min(len(names) - 1, i + delta))
        self.s.cursor = names[i]
        self.redraw_list()

    def action_jump(self, where: int):
        self.disarm()
        names = [g[0] for g in self.s.groups]
        if names:
            self.s.cursor = names[where]
            self.redraw_list()

    def action_cancel(self):
        self.disarm()

    def on_key(self, event):
        # "any other key cancels" - x itself is handled by action_kill
        if event.key != "x":
            self.disarm()

    # ---- stopping a program (two presses, with the safety net)
    def action_kill(self):
        s, now = self.s, time.time()
        name = s.cursor
        group = next((g for g in s.groups if g[0] == name), None)
        if name is None or group is None:
            self.say("Pick a program first with ↑/↓ (or j/k), then press x twice.", "#f2cc60")
            s.cursor = None
            return
        reason = kill_safety(name, group[3])
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
        self.say(f"{what} {name}?  {group[2]} process{'es' if group[2] != 1 else ''}, "
                 f"{fmt_gb(group[1])} of RAM. Unsaved work in it is lost.  "
                 f"Press x again to confirm - any other key cancels.", "bold #ffffff on #6b1f1f")
        self.redraw_list()
        self.set_timer(ARM_SECONDS, self.arm_timeout)

    def arm_timeout(self):
        if self.s.armed and time.time() - self.s.armed_at >= ARM_SECONDS - 0.1:
            self.disarm()

    def do_kill(self, name, group, force):
        s = self.s
        members = group[3]
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
