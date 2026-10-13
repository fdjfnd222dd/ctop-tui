"""The Textual app: pages, keys, background collectors, alerts and the stop-a-program flow."""
import signal
import threading
import time

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Static

from ctop.alerts import AlertInput, evaluate
from ctop.collectors.details import program_detail
from ctop.demo import DemoSampler, demo_detail, make_demo_collectors
from ctop.collectors.firewall import FirewallCollector
from ctop.collectors.nmstate import NmCollector
from ctop.collectors.vpn import VpnCollector
from ctop.collectors.wifilink import LinkCollector, channel_report
from ctop.collectors.lan import LanCollector
from ctop.collectors.ping import PingCollector
from ctop.collectors.ports import PortsCollector
from ctop.collectors.thermal import ThermalCollector
from ctop.collectors.wifi import WifiCollector
from ctop.models import FirewallInfo, SecurityData
from ctop.netevents import EventLog
from ctop.netpage import NetPageMixin, NetUI
from ctop.panels.cpu import CpuPanel
from ctop.panels.memory import MemPanel
from ctop.panels.network import NetPanel
from ctop.panels.network_page import EventsBox, LinkBox, SavedBox, ToolsBox, WifiBox
from ctop.panels.power import BatPanel
from ctop.panels.security import ConnPanel, DevicesPanel, ExposedPanel, PortsPanel
from ctop.render.alerts import banner
from ctop.render.details import detail_lines
from ctop.safety import count_alive, is_protected, kill_safety, signal_members
from ctop.system import Sampler
from ctop.ui import DIM, FG, Panel, fmt_gb

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------
ARM_SECONDS = 5.0      # how long the first 'x' stays armed
MIN_GAP = 0.35         # second 'x' must come this much later (a held-down key must not kill)
SPEEDS = [0.5, 1.0, 2.0, 5.0]   # refresh intervals the + / - keys step through (seconds)
SORT_ORDER = ["ram", "cpu", "name"]
SORT_WORDS = {"ram": "RAM (biggest first)", "cpu": "CPU (busiest first; 100% = one full core)",
              "name": "name (A-Z)"}
SORT_TAG = {"ram": "RAM", "cpu": "CPU", "name": "name"}
PAGE_NAMES = {1: "1 Overview", 2: "2 Security", 3: "3 Network"}
HINTS = {
    1: "↑/↓ select  Enter details  x x stop  s sort  / search  Tab next page  space pause  ? help  q quit",
    2: "Tab or 1/2/3 switch page   space pause   +/- speed   ? help   q quit",
    3: "",                     # page 3 has its own key bar; this line is only for messages
}
# seconds between refreshes of each collector while its page is in front / out of sight
PAGE_OF = {"ports": 2, "lan": 2, "firewall": 2, "nmstate": 3, "wifilink": 3, "vpn": 3}
FOREGROUND = {"ports": 5.0, "lan": 10.0, "firewall": 30.0, "nmstate": 8.0, "wifilink": 5.0, "vpn": 15.0}
BACKGROUND = {"ports": 30.0, "lan": 60.0, "firewall": 120.0, "nmstate": 60.0, "wifilink": 30.0, "vpn": 60.0}

HELP_TEXT = """\
 ctop - keys

   Tab, 1, 2, 3            switch page: 1 Overview, 2 Security (ports, connections, devices), 3 Network
   up / down  (or k / j)   choose a program in the list      PgUp / PgDn, Home / End   jump
   Enter                   details of the chosen program (processes, open connections, listening ports)
   x  then  x              stop the chosen program (asks twice; system programs are locked)
   s                       sort the list: RAM -> CPU -> name
   /                       search by name  (Enter keeps it, Esc clears it)
   space                   pause / resume the whole display
   +  /  -                 refresh faster / slower (0.5 s, 1 s, 2 s, 5 s)
   ?                       this help         q   quit

 Numbers: RAM share uses PSS (shared memory is split fairly, so it adds up to your real RAM).
 CPU is % of ONE core, like top: 100% means one core fully busy.
 Nothing is ever scanned: the Security page only reads what this computer already knows.

 Network page (3):  left / right  pick a box (Wi-Fi, Saved, Tools)    up / down  choose    Enter  menu or run
   r rescan Wi-Fi    t trace & ports    d network doctor    w Wi-Fi on/off    Esc stop a running tool
   Every change asks first. Tools send traffic only when you start them.

 Press Esc, Enter or ? to close.
"""


# --------------------------------------------------------------------------
# Pop-ups
# --------------------------------------------------------------------------
class HelpScreen(ModalScreen):
    BINDINGS = [("escape,enter,question_mark,q", "app.pop_screen", "Close")]
    CSS = """
    HelpScreen { align: center middle; background: #000000 60%; }
    #help { width: 104; height: auto; padding: 1 2; background: #131820; border: round #4f8fd6; color: #d0d4da; }
    """

    def compose(self) -> ComposeResult:
        yield Static(HELP_TEXT, id="help")


class DetailScreen(ModalScreen):
    """Everything about one program. Data is gathered on a worker thread, never on the UI thread."""

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("x", "app.kill", "Stop program"),
        Binding("q", "app.quit", "Quit"),
    ]
    CSS = """
    DetailScreen { align: center middle; background: #000000 60%; }
    #dbox { background: #0c0e12; border: round #4ea1ff; border-title-color: #4ea1ff; padding: 0 1; }
    #dbody { height: 1fr; }
    #dmsg { height: 1; }
    """

    def __init__(self, name):
        super().__init__()
        self.prog = name
        self.detail = None
        self._busy = False

    def compose(self) -> ComposeResult:
        with Vertical(id="dbox"):
            yield Static(id="dbody")
            yield Static(id="dmsg")

    def on_mount(self):
        box = self.query_one("#dbox")
        box.border_title = self.prog
        box.styles.width = max(40, min(self.app.size.width - 8, 100))
        box.styles.height = max(12, min(self.app.size.height - 6, 30))
        self.paint()
        self.load()
        self.set_interval(2.0, self.load)

    def action_close(self):
        self.app.pop_screen()

    def set_msg(self, text, style=FG):
        self.query_one("#dmsg", Static).update(Text(text, style=style, no_wrap=True))

    def load(self):
        if not self._busy:
            self._busy = True
            threading.Thread(target=self._work, daemon=True).start()

    def _work(self):
        try:
            app = self.app
            group = next((g for g in app.s.groups if g.name == self.prog), None)
            listeners, connections = app.ports.data or ((), ())
            if app.demo and group:
                detail = demo_detail(self.prog, group)
            else:
                detail = program_detail(self.prog, group.members if group else [], listeners, connections)
            app.call_from_thread(self.show, detail)
        except Exception:                    # screen closed or app quitting: nothing to update
            pass
        finally:
            self._busy = False

    def show(self, detail):
        self.detail = detail
        self.paint()

    def on_resize(self, event):
        self.paint()

    def paint(self):
        body = self.query_one("#dbody", Static)
        size = body.content_size
        if self.detail is None:
            body.update(Text("collecting...", style=DIM))
        elif size.width >= 20 and size.height >= 3:
            lines = detail_lines(self.detail, size.width, size.height, now=time.time(),
                                 locked=is_protected(self.prog))
            body.update(Text("\n").join(lines))


# --------------------------------------------------------------------------
# The app
# --------------------------------------------------------------------------
class Ctop(NetPageMixin, App):
    TITLE = "ctop"
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("ctrl+c", "quit", "Quit", show=False),
        Binding("tab", "next_page", "Page", show=False, priority=True),
        Binding("1", "page(1)", "Overview", show=False),
        Binding("2", "page(2)", "Security", show=False),
        Binding("3", "page(3)", "Network", show=False),
        Binding("left,h", "net_focus(-1)", "Box left", show=False),
        Binding("right,l", "net_focus(1)", "Box right", show=False),
        Binding("r", "net_key('rescan')", "Rescan", show=False),
        Binding("t", "net_key('target')", "Trace", show=False),
        Binding("d", "net_key('doctor')", "Doctor", show=False),
        Binding("w", "net_key('wifi')", "Wi-Fi", show=False),
        Binding("up,k", "move(-1)", "Up", show=False),
        Binding("down,j", "move(1)", "Down", show=False),
        Binding("pageup", "move(-10)", "Page up", show=False),
        Binding("pagedown", "move(10)", "Page down", show=False),
        Binding("home", "jump(0)", "Top", show=False),
        Binding("end", "jump(-1)", "Bottom", show=False),
        Binding("enter", "details", "Details", show=False),
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
    #alerts { height: 1; display: none; }
    #page1, #page2, #page3 { height: 1fr; }
    #page2, #page3 { display: none; }
    #netA { height: 1fr; min-height: 12; }
    #netB { height: 1fr; min-height: 15; }
    #netright { width: 2fr; min-width: 46; }
    #netkeys { height: 1; padding: 0 1; background: #161b26; }
    #net-wifi { width: 3fr; border: round #39c5cf; border-title-color: #39c5cf; }
    #net-saved { width: 2fr; border: round #4f8fd6; border-title-color: #4f8fd6; }
    #net-tools { width: 3fr; border: round #bc8cff; border-title-color: #bc8cff; }
    #net-link { height: 11; border: round #5fa86b; border-title-color: #5fa86b; }
    #net-events { height: 1fr; border: round #d9a441; border-title-color: #d9a441; }
    #net-wifi.focus, #net-saved.focus, #net-tools.focus { border: heavy #e6edf3; border-title-color: #e6edf3; }
    #top { height: 13; }
    #bottom { height: 1fr; }
    #secA { height: 17; }
    #secB { height: 1fr; }
    #statusrow { height: 1; background: #131820; }
    #status { width: 1fr; padding: 0 1; }
    #tags { width: auto; padding: 0 1; }
    #cpu { width: 3fr; border: round #5fa86b; border-title-color: #5fa86b; }
    #bat { width: 2fr; border: round #d9a441; border-title-color: #d9a441; }
    #mem { width: 3fr; border: round #4f8fd6; border-title-color: #4f8fd6; }
    #net { width: 2fr; border: round #c0627a; border-title-color: #c0627a; }
    #sec-ports { width: 1fr; border: round #c0627a; border-title-color: #c0627a; }
    #sec-conn { width: 1fr; border: round #4f8fd6; border-title-color: #4f8fd6; }
    #sec-lan { width: 3fr; border: round #5fa86b; border-title-color: #5fa86b; }
    #sec-exposed { width: 2fr; border: round #d9a441; border-title-color: #d9a441; }
    """

    def compose(self) -> ComposeResult:
        yield Static(id="alerts")
        with Vertical(id="page1"):
            with Horizontal(id="top"):
                yield CpuPanel("CPU", id="cpu")
                yield BatPanel("Battery", id="bat")
            with Horizontal(id="bottom"):
                yield MemPanel("Memory by program", id="mem")
                yield NetPanel("Network", id="net")
        with Vertical(id="page2"):
            with Horizontal(id="secA"):
                yield PortsPanel("Listening ports - who can reach this computer", id="sec-ports")
                yield ConnPanel("Connections - who this computer is talking to", id="sec-conn")
            with Horizontal(id="secB"):
                yield DevicesPanel("Devices on your network", id="sec-lan")
                yield ExposedPanel("How exposed am I?", id="sec-exposed")
        with Vertical(id="page3"):
            with Horizontal(id="netA"):
                yield WifiBox("Wi-Fi networks", id="net-wifi")
                yield SavedBox("Saved networks & VPN", id="net-saved")
            with Horizontal(id="netB"):
                yield ToolsBox("Tools", id="net-tools")
                with Vertical(id="netright"):
                    yield LinkBox("Your Wi-Fi link", id="net-link")
                    yield EventsBox("Events", id="net-events")
            yield Static(id="netkeys")
        with Horizontal(id="statusrow"):
            yield Static(id="status")
            yield Static(id="tags")

    # ---- start-up
    def __init__(self, demo=False):
        super().__init__()
        self.demo = demo            # made-up numbers, nothing is ever stopped (see ctop/demo.py)

    def on_mount(self):
        self.s = DemoSampler() if self.demo else Sampler()
        self.page = 1
        self.speed_i = 1
        self._last_procs = self._last_net = 0.0
        self._sec_key = None
        # background data sources; each runs on its own worker thread
        if self.demo:
            fake = make_demo_collectors()
            self.thermal, self.ping, self.wifi = fake["thermal"], fake["ping"], fake["wifi"]
            self.ports, self.lan, self.firewall = fake["ports"], fake["lan"], fake["firewall"]
        else:
            self.thermal = ThermalCollector()
            self.ping = PingCollector()
            self.wifi = WifiCollector()
            self.ports = PortsCollector()
            self.lan = LanCollector()
            self.firewall = FirewallCollector()
        self.setup_network()
        self.collectors = [self.thermal, self.ping, self.wifi, self.ports, self.lan, self.firewall,
                           self.nmstate, self.wifilink, self.vpn]
        self.query_one("#sec-lan").border_subtitle = "passive: nothing is scanned"
        self.set_page(1)
        self.tick()
        self._timer = self.set_interval(SPEEDS[self.speed_i], self.tick)

    def setup_network(self):
        """Page 3: its collectors, the change layer and the tools (fakes in demo mode)."""
        s = self.s
        s.netui, s.events = NetUI(), EventLog()
        s.nm = s.link = s.vpn = s.chan = None
        self._chan_key = None
        if self.demo:
            from ctop import demo_net
            fake = demo_net.make_demo_net_collectors()
            self.nmstate, self.wifilink, self.vpn = fake["nmstate"], fake["wifilink"], fake["vpn"]
            self.ctl = demo_net.DemoNetctl()
            fns = {"trace": demo_net.demo_trace, "ports": demo_net.demo_ports, "dns": demo_net.demo_dns,
                   "speed": demo_net.demo_speed, "doctor": demo_net.demo_doctor, "lan": demo_net.demo_lanscan}
        else:
            from ctop import netctl
            from ctop.tools import dnscheck, doctor, lanscan, portcheck, speedtest, trace
            self.nmstate, self.wifilink, self.vpn = NmCollector(), LinkCollector(), VpnCollector()
            self.ctl = netctl
            fns = {"trace": trace.run_trace, "ports": portcheck.run_ports, "dns": dnscheck.run_dns,
                   "speed": speedtest.run_speed, "doctor": doctor.run_doctor, "lan": lanscan.run_lanscan}
        from ctop.tools.portcheck import PRESETS, parse_ports
        fns["presets"], fns["parse_ports"] = PRESETS, parse_ports
        self.tool_fns = fns

    def update_network(self, now):
        s = self.s
        s.nm, s.link, s.vpn = self.nmstate.data, self.wifilink.data, self.vpn.data
        key = (id(s.nm), id(s.link))
        if key != self._chan_key and s.nm is not None:
            self._chan_key = key
            s.chan = channel_report(s.nm.networks, s.link)
        s.events.observe(now, wifi=s.wifi, link=s.link, ping=s.ping, net=s.net or None, nm=s.nm, vpn=s.vpn)
        if self.page == 3:
            nm = s.nm
            box = self.query_one("#net-wifi")
            if nm and nm.available and nm.networks:
                box.border_subtitle = f"{min(s.netui.cursor['wifi'] + 1, len(nm.networks))}/{len(nm.networks)}  r rescan"
            else:
                box.border_subtitle = ""
            self.query_one("#net-tools").border_subtitle = "Esc stops it" if s.netui.running else ""

    def on_resize(self, event):
        if getattr(self, "s", None) is not None and getattr(self.s, "netui", None) is not None:
            self.net_keybar()

    def check_action(self, action, parameters):
        # while typing a search, letters must go to the search box, not trigger shortcuts
        if getattr(self, "s", None) and self.s.search_mode:
            return False
        return True

    # ---- the heartbeat
    def tick(self):
        s, now = self.s, time.time()
        if s.paused:
            return
        s.interval = SPEEDS[self.speed_i]
        s.thermal = self.thermal.data
        s.sample_fast()
        # slow scans run on their own clock (seconds), whatever the refresh speed is
        if now - self._last_procs >= 5 and not s.procs_busy:
            self._last_procs = now
            threading.Thread(target=s.scan_procs, daemon=True).start()
        if now - self._last_net >= 15 and not s.net_busy:
            self._last_net = now
            threading.Thread(target=s.scan_net, daemon=True).start()
        for c in self.collectors:
            if c.due(now):
                threading.Thread(target=c.run, daemon=True).start()
        s.ping, s.wifi = self.ping.data, self.wifi.data
        self.update_security(now)
        self.update_network(now)
        self.update_alerts()
        self.draw_page()

    def update_security(self, now):
        ports = self.ports.data
        key = (id(ports), id(self.lan.data), id(self.firewall.data))
        if ports is None or key == self._sec_key:
            return
        self._sec_key = key
        listeners, connections = ports
        self.s.sec = SecurityData(listeners, connections, self.lan.data or (),
                                  self.firewall.data or FirewallInfo(None, None, None, False), now)

    def update_alerts(self):
        s = self.s
        total = s.vm.total or 1
        s.alerts = evaluate(AlertInput(
            ram_pct=(total - s.vm.available) / total * 100,
            swap_pct=s.swap.percent if s.swap.total else None,
            battery=s.battery, thermal=s.thermal, cpu_temp=s.temp, ping=s.ping, wifi=s.wifi))
        bar = self.query_one("#alerts", Static)
        text = banner(s.alerts, self.size.width)
        bar.display = text is not None
        if text is not None:
            bar.update(text)

    def draw_page(self):
        for panel in self.query_one(f"#page{self.page}").query(Panel):
            panel.draw(self.s)

    # ---- pages
    def set_page(self, n):
        self.page = n
        self.query_one("#page1").display = n == 1
        self.query_one("#page2").display = n == 2
        self.query_one("#page3").display = n == 3
        for c in (self.ports, self.lan, self.firewall, self.nmstate, self.wifilink, self.vpn):
            c.interval = (FOREGROUND if PAGE_OF[c.name] == n else BACKGROUND)[c.name]
            if PAGE_OF[c.name] == n and c.data is None:
                c.last_run = 0.0                     # first visit: fetch now, not at the slow interval
        if n == 3:
            self.net_set_focus(self.s.netui.focus)
        self.say(HINTS[n], DIM)
        self.refresh_tags()
        self.call_after_refresh(self.draw_page)

    def action_next_page(self):
        if len(self.screen_stack) > 1:           # a pop-up is open: leave the page behind it alone
            return
        self.disarm(quiet=True)
        self.set_page(self.page % 3 + 1)

    def action_page(self, n: int):
        if n != self.page:
            self.disarm(quiet=True)
            self.set_page(n)

    def refresh_tags(self):
        s = self.s
        t = Text(no_wrap=True)
        t.append(PAGE_NAMES[self.page], DIM)
        if self.page == 1:
            t.append(f"   sort: {SORT_TAG[s.sort_key]}", DIM)
        t.append(f"   every {SPEEDS[self.speed_i]:g} s", DIM)
        if s.filter_text:
            t.append(f'   filter: "{s.filter_text}"', "#39c5cf")
        if s.paused:
            t.append("   PAUSED ", "bold #000000 on #f2cc60")
        if self.demo:
            t.append("   DEMO ", "bold #000000 on #7aa2d6")
        self.query_one("#tags", Static).update(t)

    # ---- status line (and the pop-up's own message line when one is open)
    def say(self, text, style=FG):
        if isinstance(self.screen, DetailScreen):
            self.screen.set_msg(text, style)
            return
        self.query_one("#status", Static).update(Text(text, style=style, no_wrap=True))

    def redraw_list(self):
        self.query_one("#mem", Panel).draw(self.s)

    # ---- sort / search / pause / speed / help
    def action_sort(self):
        if self.page != 1:
            return
        s = self.s
        s.sort_key = SORT_ORDER[(SORT_ORDER.index(s.sort_key) + 1) % len(SORT_ORDER)]
        self.say(f"Sorted by {SORT_WORDS[s.sort_key]}", "#7aa2d6")
        self.refresh_tags()
        self.redraw_list()

    def action_search(self):
        if self.page != 1:
            return
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
        self.refresh_tags()
        self.redraw_list()

    def action_pause(self):
        s = self.s
        s.paused = not s.paused
        if s.paused:
            self.say("PAUSED - the display is frozen. Press space to resume.", "bold #f2cc60")
        else:
            s._net_last = None                   # so the first sample after a pause is not a huge spike
            self.say(HINTS[self.page], DIM)
            self.tick()
        self.refresh_tags()

    def action_speed(self, delta: int):
        i = max(0, min(len(SPEEDS) - 1, self.speed_i - delta))     # + = faster = shorter interval
        if i == self.speed_i:
            self.say(f"Already at the {'fastest' if delta > 0 else 'slowest'} speed ({SPEEDS[i]:g} s).", DIM)
            return
        self.speed_i = i
        self._timer.stop()
        self._timer = self.set_interval(SPEEDS[i], self.tick)
        self.say(f"Refreshing every {SPEEDS[i]:g} s", "#7aa2d6")
        self.refresh_tags()

    def action_help(self):
        self.push_screen(HelpScreen())

    # ---- selecting
    def disarm(self, quiet=False):
        if self.s.armed:
            self.s.armed = None
            if not quiet:
                self.say(HINTS[self.page], DIM)
            self.redraw_list()

    def action_net_focus(self, delta: int):
        if self.page == 3:
            self.net_focus_step(delta)

    def action_net_key(self, key: str):
        if self.page == 3:
            self.run_menu_item(key)

    def action_move(self, delta: int):
        if self.page == 3:
            self.net_move(delta)
            return
        if self.page != 1:
            return
        self.disarm()
        names = [g.name for g in self.s.visible_groups()]
        if not names:
            return
        i = names.index(self.s.cursor) if self.s.cursor in names else -1
        i = 0 if i < 0 else max(0, min(len(names) - 1, i + delta))
        self.s.cursor = names[i]
        self.redraw_list()

    def action_jump(self, where: int):
        if self.page == 3:
            self.net_move(0, jump=where)
            return
        if self.page != 1:
            return
        self.disarm()
        names = [g.name for g in self.s.visible_groups()]
        if names:
            self.s.cursor = names[where]
            self.redraw_list()

    def action_cancel(self):
        if self.page == 3:
            self.net_escape()
            return
        self.disarm()

    def action_details(self):
        if self.page == 3:
            self.net_enter()
            return
        if self.page != 1:
            return
        name = self.s.cursor
        if name is None or not any(g.name == name for g in self.s.groups):
            self.say("Pick a program first with ↑/↓, then press Enter.", "#f2cc60")
            return
        self.disarm(quiet=True)
        self.push_screen(DetailScreen(name))

    def on_key(self, event):
        s = self.s
        if s.search_mode:                                    # the search box owns the keyboard
            event.stop()
            event.prevent_default()
            k = event.key
            if k == "escape":
                s.filter_text, s.search_mode = "", False
                self.say(HINTS[self.page], DIM)
                self.refresh_tags()
                self.redraw_list()
                return
            if k == "enter":
                s.search_mode = False
                self.say(f"Showing programs matching '{s.filter_text}'. Press / to edit, / then Esc to clear."
                         if s.filter_text else HINTS[self.page], "#7aa2d6" if s.filter_text else DIM)
                self.refresh_tags()
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
        if self.page != 1 and not isinstance(self.screen, DetailScreen):
            return
        if s.paused:
            self.say("Resume (space) first - the list may be out of date.", "#f2cc60")
            return
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
        self.say(f"{what} {name}? {group.count} process{'es' if group.count != 1 else ''}, "
                 f"{fmt_gb(group.bytes)}. Unsaved work is lost.  x again = confirm, any other key = cancel",
                 "bold #ffffff on #6b1f1f")
        self.redraw_list()
        self.set_timer(ARM_SECONDS, self.arm_timeout)

    def arm_timeout(self):
        if self.s.armed and time.time() - self.s.armed_at >= ARM_SECONDS - 0.1:
            self.disarm()

    def do_kill(self, name, group, force):
        s = self.s
        if self.demo:                                        # fake programs: pretend, send nothing
            s.armed = None
            self.say(f"Demo mode: pretending to stop {name} - nothing was touched.", "#f2cc60")
            self.redraw_list()
            return
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


def main(argv=None):
    import argparse
    from ctop import __version__
    ap = argparse.ArgumentParser(prog="ctop", description="A btop-style terminal dashboard.")
    ap.add_argument("--demo", action="store_true",
                    help="run with made-up numbers (safe to try; reads nothing, stops nothing)")
    ap.add_argument("--version", action="version", version=f"ctop {__version__}")
    args = ap.parse_args(argv)
    Ctop(demo=args.demo).run()
