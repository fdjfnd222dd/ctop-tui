"""Page 3 (Network): focus, cursors, menus and running tools. Mixed into the Ctop app.

Every network change goes through `self.ctl` (ctop/netctl.py, or DemoNetctl in demo mode) on a worker
thread, and only after the person picked it in a menu; anything that could cut the connection asks again.
Tools run on worker threads and publish snapshots; the UI thread only draws them.
"""
import threading
import time

from ctop.panels.network_page import TOOL_MENU
from ctop.screens import InputScreen, MenuScreen, TargetScreen
from rich.text import Text

from ctop.ui import DIM, FG

FOCUS_ORDER = ("wifi", "saved", "tools")
KEY = "bold #39c5cf"
# what Enter does in each box, shown first on the key bar so the page explains itself
ENTER_DOES = {"wifi": "connect / options for this network",
              "saved": "connect, auto-connect, priority, DNS, forget",
              "tools": "run the chosen tool"}
BOX_NAME = {"wifi": "Wi-Fi networks", "saved": "Saved & VPN", "tools": "Tools"}
OK, BAD, INFO = "bold #56d364", "bold #ff7b72", "#7aa2d6"
KIND_WORD = {"wifi": "Wi-Fi", "ethernet": "cable", "vpn": "VPN", "wireguard": "WireGuard", "other": "connection"}


class NetUI:
    """Everything page 3 remembers between frames."""

    def __init__(self):
        self.focus = "wifi"
        self.cursor = {"wifi": 0, "saved": 0, "tools": 0}
        self.kind = None             # which tool view the Tools box shows
        self.result = None           # its latest snapshot
        self.running = None          # menu key of the running tool
        self.cancel = None           # threading.Event of the running tool
        self.gen = 0                 # bumps per run, so a stopped tool's late updates are ignored
        self.last_host = ""
        self.last_ports = "22, 80, 443"
        self.drawn_at = 0.0


def _selectable(i):
    return TOOL_MENU[i][0] != ""


class NetPageMixin:
    # ---- helpers
    @property
    def ui(self):
        return self.s.netui

    def net_items(self, box):
        nm = self.s.nm
        if box == "wifi":
            return nm.networks if nm and nm.available else ()
        if box == "saved":
            return nm.saved if nm and nm.available else ()
        return TOOL_MENU

    def net_redraw(self, *ids):
        for i in ids or ("net-wifi", "net-saved", "net-tools", "net-link", "net-events"):
            self.query_one(f"#{i}").draw(self.s)

    def net_set_focus(self, box):
        self.ui.focus = box
        for b in FOCUS_ORDER:
            self.query_one(f"#net-{b}").set_class(b == box, "focus")
        self.net_keybar()
        self.net_redraw("net-wifi", "net-saved", "net-tools")

    def net_keybar(self):
        """The always-visible key guide at the bottom of page 3; it follows the selected box.

        Long labels when the terminal is wide, short ones when it is not, so it never gets cut off.
        """
        ui = self.ui
        enter_long = ENTER_DOES[ui.focus]
        enter_short = {"wifi": "connect/options", "saved": "options", "tools": "run"}[ui.focus]
        long_pairs = [("Enter", enter_long), ("←/→", "change box"), ("↑/↓", "choose"), ("r", "rescan"),
                      ("t", "trace & ports"), ("d", "doctor"), ("w", "Wi-Fi on/off"), ("Tab", "next page"),
                      ("?", "help"), ("q", "quit")]
        short_pairs = [("Enter", enter_short), ("←→", "box"), ("↑↓", "pick"), ("r", "scan"), ("t", "trace"),
                       ("d", "doctor"), ("w", "Wi-Fi"), ("?", "help")]
        if ui.running:
            long_pairs.insert(0, ("Esc", "stop the tool"))
            short_pairs.insert(0, ("Esc", "stop"))
        width = self.size.width - 2

        def build(pairs, sep, name):
            t = Text(no_wrap=True)
            if name:
                t.append(f"{BOX_NAME[ui.focus]}  ", "bold #e6edf3")
            for i, (k, what) in enumerate(pairs):
                if i:
                    t.append(sep, DIM)
                t.append(k, KEY)
                t.append(f" {what}", FG)
            return t
        t = build(long_pairs, "  ·  ", True)
        for pairs, sep, name in ((long_pairs, "  ", True), (short_pairs, "  ", True), (short_pairs, " ", False)):
            if t.cell_len <= width:
                break
            t = build(pairs, sep, name)
        self.query_one("#netkeys").update(t)

    def net_refresh_soon(self):
        """Make NetworkManager state refresh on the next tick (after a change)."""
        self.nmstate.last_run = 0.0

    # ---- keys (called from the app's actions when page 3 is in front)
    def net_focus_step(self, delta):
        i = (FOCUS_ORDER.index(self.ui.focus) + delta) % len(FOCUS_ORDER)
        self.net_set_focus(FOCUS_ORDER[i])

    def net_move(self, delta, jump=None):
        ui, box = self.ui, self.ui.focus
        n = len(self.net_items(box))
        if not n:
            return
        i = ui.cursor[box]
        if jump is not None:
            i = 0 if jump == 0 else n - 1
        else:
            i = max(0, min(n - 1, i + delta))
        if box == "tools":                       # never park on a separator row
            step = 1 if (delta > 0 or jump == 0) else -1
            while 0 <= i < n and not _selectable(i):
                i += step
            if not 0 <= i < n:
                i = ui.cursor[box]
        ui.cursor[box] = i
        self.net_redraw(f"net-{box}")

    def net_enter(self):
        box = self.ui.focus
        items = self.net_items(box)
        if not items:
            self.net_empty_hint()
            return
        item = items[min(self.ui.cursor[box], len(items) - 1)]
        if box == "wifi":
            self.wifi_menu(item)
        elif box == "saved":
            self.saved_menu(item)
        else:
            self.run_menu_item(item[0])

    def net_empty_hint(self):
        nm = self.s.nm
        if nm is None:
            self.say("Still reading NetworkManager…", DIM)
        elif not nm.available:
            self.say(nm.reason or "NetworkManager is not available.", "#f2cc60")
        elif nm.wifi_enabled is False:
            self.say("Wi-Fi is off. Press w to turn it on.", "#f2cc60")
        else:
            self.say("Nothing here yet - press r to scan.", DIM)

    def net_escape(self):
        ui = self.ui
        if ui.running and ui.cancel is not None:
            ui.cancel.set()
            self.say("Stopping…", "#f2cc60")
        elif ui.kind is not None:
            ui.kind, ui.result = None, None
            self.net_redraw("net-tools")

    # ---- menus
    def ask(self, title, items, then, subtitle="", danger=(), current=None):
        def done(value):
            if value is not None:
                then(value)
        self.push_screen(MenuScreen(title, items, subtitle=subtitle, danger=danger, current=current), done)

    def confirm(self, title, yes_label, then, subtitle=""):
        self.ask(title, [("yes", yes_label), ("no", "Cancel")], lambda v: v == "yes" and then(), subtitle=subtitle)

    def saved_for(self, ssid):
        nm = self.s.nm
        for c in (nm.saved if nm else ()):
            if c.kind == "wifi" and (c.ssid or c.name) == ssid:
                return c
        return None

    def wifi_menu(self, net):
        name = net.ssid or "(hidden network)"
        profile = self.saved_for(net.ssid) if net.ssid else None
        items = []
        if net.in_use and profile:
            items.append(("disconnect", "Disconnect"))
            items.append(("reconnect", "Reconnect (fixes most stuck connections)"))
        elif net.ssid:
            items.append(("connect", "Connect"))
        if profile:
            items.append(("profile", "Saved settings (auto-connect, priority, DNS)…"))
            items.append(("forget", "Forget this network…"))
        items.append(("details", "Details"))
        sec = net.security or "open (no password)"
        sub = f"{net.band} · channel {net.channel or '?'} · signal {net.signal_pct or 0}% · {sec}"

        def pick(v):
            if v == "connect":
                self.connect_network(net, profile)
            elif v == "disconnect":
                self.confirm(f"Disconnect from {name}?", "Yes, disconnect",
                             lambda: self.net_do(self.ctl.disconnect, profile.uuid, name=name),
                             subtitle="You will be offline until you connect again.")
            elif v == "reconnect":
                self.net_do(self.ctl.reconnect, profile.uuid, name=name, busy=f"Reconnecting to {name}…")
            elif v == "profile":
                self.saved_menu(profile)
            elif v == "forget":
                self.forget(profile)
            elif v == "details":
                self.show_message(self.network_details(net, profile))
        self.ask(name, items, pick, subtitle=sub, danger=("forget",))

    def network_details(self, net, profile):
        lines = [net.ssid or "(hidden network)",
                 f"Band {net.band}, channel {net.channel or '?'}" + (f", {net.freq_mhz} MHz" if net.freq_mhz else ""),
                 f"Signal {net.signal_pct if net.signal_pct is not None else '?'}%   max rate {net.rate_mbps or '?'} Mbps",
                 f"Security {net.security or 'none - anyone nearby can read unencrypted traffic'}",
                 f"Strongest access point {net.bssid}" + (f"  ({net.aps} broadcast this name)" if net.aps > 1 else "")]
        if profile:
            lines.append(f"Saved as '{profile.name}', auto-connect {'on' if profile.autoconnect else 'off'}, "
                         f"priority {profile.priority}")
        return lines

    def connect_network(self, net, profile):
        name = net.ssid
        if profile:
            self.net_do(self.ctl.connect_saved, profile.uuid, name=name, busy=f"Connecting to {name}…")
            return
        sec = net.security or ""
        if "802.1X" in sec:
            self.say("Enterprise (802.1X) networks need your desktop's network settings.", "#f2cc60")
            return
        iface = (self.s.nm.wifi_device if self.s.nm else None) or "wlan0"
        if not sec:
            self.confirm(f"Join open network {name}?", "Yes, join it",
                         lambda: self.net_do(self.ctl.connect_new, name, "", "", iface, busy=f"Joining {name}…"),
                         subtitle="Open network: others nearby can see traffic that is not encrypted (https is still safe).")
            return

        def got(password):
            if password is not None:
                self.net_do(self.ctl.connect_new, name, password, sec, iface, busy=f"Connecting to {name}…")
        self.push_screen(InputScreen(f"Password for {name}", f"{sec} network. The password is passed to "
                                     "NetworkManager privately, never on a command line.", password=True,
                                     validate=lambda p: self.ctl.valid_password(p, sec)), got)

    def saved_menu(self, c):
        active = c.active
        items = [("down", "Disconnect") if active else ("up", "Connect")]
        if active:
            items.append(("reconnect", "Reconnect"))
        items.append(("auto", f"Auto-connect: {'on - turn off' if c.autoconnect else 'off - turn on'}"))
        items.append(("prio+", f"Priority up (now {c.priority}; higher wins when several are in range)"))
        items.append(("prio-", "Priority down"))
        if c.kind in ("wifi", "ethernet"):
            items.append(("dns", "DNS servers…"))
        items.append(("forget", "Forget…"))
        sub = f"{KIND_WORD.get(c.kind, c.kind)} profile" + (f" on {c.device}" if c.device else "")

        def pick(v):
            n = c.name
            if v == "up":
                self.net_do(self.ctl.connect_saved, c.uuid, name=n, busy=f"Connecting {n}…")
            elif v == "down":
                self.confirm(f"Disconnect {n}?", "Yes, disconnect", lambda: self.net_do(self.ctl.disconnect, c.uuid, name=n))
            elif v == "reconnect":
                self.net_do(self.ctl.reconnect, c.uuid, name=n, busy=f"Reconnecting {n}…")
            elif v == "auto":
                self.net_do(self.ctl.set_autoconnect, c.uuid, not c.autoconnect, name=n)
            elif v in ("prio+", "prio-"):
                self.net_do(self.ctl.set_priority, c.uuid, c.priority + (1 if v == "prio+" else -1), name=n)
            elif v == "dns":
                self.dns_menu(c)
            elif v == "forget":
                self.forget(c)
        self.ask(c.name, items, pick, subtitle=sub, danger=("forget",))

    def forget(self, c):
        self.confirm(f"Forget {c.name}?", "Yes, forget it",
                     lambda: self.net_do(self.ctl.forget, c.uuid, name=c.name),
                     subtitle="Its saved password and settings are deleted. You can join again later.")

    def dns_menu(self, c):
        def work():
            current = self.ctl.get_dns(c.uuid)
            self.call_from_thread(self._dns_menu, c, current)
        threading.Thread(target=work, daemon=True).start()

    def _dns_menu(self, c, current):
        presets = self.ctl.DNS_PRESETS
        servers = tuple(current[0]) if current else ()
        cur = next((str(i) for i, (_, s) in enumerate(presets) if tuple(s[:len(servers)]) == servers and
                    (servers or not (current and current[1]))), None)
        items = [(str(i), label) for i, (label, _) in enumerate(presets)] + [("custom", "Custom…")]
        now = ", ".join(servers) if servers else "automatic"

        def pick(v):
            if v == "custom":
                def got(text):
                    if text is not None:
                        self.net_do(self.ctl.set_dns, c.uuid, self.ctl.parse_dns_list(text), name=c.name,
                                    busy="Applying DNS…")
                self.push_screen(InputScreen("Custom DNS", "Up to 3 addresses, separated by commas.",
                                             initial=", ".join(servers),
                                             validate=lambda t: None if self.ctl.parse_dns_list(t) else
                                             "Type 1 to 3 IP addresses, for example the ones your provider gave you."), got)
                return
            label, s = presets[int(v)]
            self.confirm(f"Use {label.split('  ')[0]} for {c.name}?", "Yes, switch DNS",
                         lambda: self.net_do(self.ctl.set_dns, c.uuid, s, name=c.name, busy="Applying DNS…"),
                         subtitle="The connection blinks for a second while it is re-applied.")
        self.ask(f"DNS for {c.name}", items, pick, subtitle=f"Now: {now}", current=cur)

    # ---- the Tools menu (also r / t / d / w)
    def run_menu_item(self, key):
        ui = self.ui
        if key == "doctor":
            self.start_tool("doctor", self.tool_fns["doctor"], net=dict(self.s.net or {}), nm=self.s.nm, wifi=self.s.wifi)
        elif key == "target":
            def got(r):
                if r is not None:
                    host, ports = r
                    ui.last_host = host
                    ui.last_ports = ", ".join(map(str, ports)) if ports else ""
                    self.start_target(host, ports)
            self.push_screen(TargetScreen(self.tool_fns["presets"], self.tool_fns["parse_ports"],
                                          host=ui.last_host, ports_text=ui.last_ports), got)
        elif key == "speed":
            self.confirm("Run a speed test?", "Yes, run it (about 35 MB of data)",
                         lambda: self.start_tool("speed", self.tool_fns["speed"]),
                         subtitle="Downloads and uploads test data to Cloudflare for about 20 seconds.")
        elif key == "dns":
            self.start_tool("dns", self.tool_fns["dns"], gateway=(self.s.net or {}).get("gateway"))
        elif key == "lan":
            self.confirm("Find devices on your home network?", "Yes, look",
                         lambda: self.start_tool("lan", self.tool_fns["lan"]),
                         subtitle="Sends one empty packet to each address of your own network (at most 254). Nothing else.")
        elif key == "rescan":
            iface = self.s.nm.wifi_device if self.s.nm else None
            self.net_do(self.ctl.rescan, iface, busy="Scanning for Wi-Fi networks…", refresh_after=3.0)
        elif key == "reconnect":
            active = next((c for c in (self.s.nm.saved if self.s.nm else ()) if c.kind == "wifi" and c.active), None)
            if active is None:
                self.say("Not connected to a Wi-Fi network - pick one in the Wi-Fi box.", "#f2cc60")
                return
            self.confirm(f"Reconnect to {active.name}?", "Yes, reconnect",
                         lambda: self.net_do(self.ctl.reconnect, active.uuid, name=active.name, busy="Reconnecting…"),
                         subtitle="Drops and rejoins the network: fixes most stuck connections. Takes a few seconds.")
        elif key == "wifi":
            on = bool(self.s.nm and self.s.nm.wifi_enabled)
            if on:
                self.confirm("Turn Wi-Fi off?", "Yes, turn it off", lambda: self.net_do(self.ctl.wifi_radio, False),
                             subtitle="You will be offline unless a cable is plugged in.")
            else:
                self.net_do(self.ctl.wifi_radio, True, busy="Turning Wi-Fi on…")
        elif key == "airplane":
            self.ask("Airplane mode", [("on", "Turn every radio off"), ("off", "Turn radios back on")],
                     lambda v: self.net_do(self.ctl.airplane, v == "on"),
                     subtitle="Wi-Fi and mobile radios. A network cable keeps working.")
        elif key == "tailscale":
            vpn = self.s.vpn
            if vpn is None or vpn.tailscale is None:
                self.say("Tailscale is not installed.", DIM)
                return
            running = vpn.tailscale == "Running"
            self.confirm(f"Turn Tailscale {'off' if running else 'on'}?", "Yes",
                         lambda: self.net_do(self.ctl.tailscale, not running, refresh_after=2.0))

    # ---- running changes
    def net_do(self, fn, *args, busy="Working…", refresh_after=0.0, **kw):
        self.say(busy, INFO)

        def work():
            msg = "Failed: no answer"
            try:
                out = fn(*args, **kw)
            except Exception as e:                     # never let a failed change kill the page
                out, msg = None, f"Failed: {e}"
            try:
                self.call_from_thread(self._net_done, out, None if out else msg, refresh_after)
            except RuntimeError:                       # app is closing
                pass
        threading.Thread(target=work, daemon=True).start()

    def _net_done(self, out, err, refresh_after):
        text = out.message if out else err
        good = bool(out and out.ok)
        self.say(text, OK if good else BAD)
        self.s.events.add(time.time(), "info" if good else "warn", text)
        self.net_refresh_soon()
        self.vpn.last_run = 0.0
        if refresh_after:
            self.set_timer(refresh_after, self.net_refresh_soon)
        self.net_redraw("net-events")

    def show_message(self, lines):
        ui = self.ui
        ui.kind, ui.result = "message", tuple(lines)
        self.net_redraw("net-tools")

    # ---- running tools
    def _begin(self, kind, result=None):
        ui = self.ui
        if ui.cancel is not None:
            ui.cancel.set()                          # one tool at a time: stop the old one
        ui.gen += 1
        ui.kind, ui.result, ui.running = kind, result, kind
        ui.cancel = threading.Event()
        self.net_keybar()
        self.net_redraw("net-tools")
        self.say("Running… Esc stops it.", INFO)
        return ui.gen, ui.cancel

    def _push(self, gen, result=None, part=None):
        """Called on a tool thread with a new snapshot. Redraws at most 10 times a second."""
        ui = self.ui
        if gen != ui.gen:
            return
        if part is None:
            ui.result = result
        else:                                        # Trace & ports: (trace, ports) updated separately
            t, p = ui.result or (None, None)
            ui.result = (result, p) if part == 0 else (t, result)
        now = time.monotonic()
        if now - ui.drawn_at >= 0.1:
            ui.drawn_at = now
            try:
                self.call_from_thread(self.net_redraw, "net-tools")
            except RuntimeError:                     # app is closing
                pass

    def _finish(self, gen):
        def end():
            ui = self.ui
            if gen == ui.gen:
                ui.running, ui.cancel = None, None
                self.net_keybar()
                self.say("Done.", DIM)
                self.net_redraw("net-tools")
        try:
            self.call_from_thread(end)
        except RuntimeError:
            pass

    def start_tool(self, kind, fn, **kw):
        gen, cancel = self._begin(kind)

        def work():
            try:
                final = fn(on_update=lambda r: self._push(gen, r), cancel=cancel, **kw)
                self._push(gen, final)
                self.ui.drawn_at = 0.0
            except Exception as e:
                self._push(gen, None)
                self.call_from_thread(self.say, f"Tool failed: {e}", BAD)
            finally:
                self._finish(gen)
        threading.Thread(target=work, daemon=True).start()

    def start_target(self, host, ports):
        gen, cancel = self._begin("target", (None, None))
        left = [2 if ports else 1]
        lock = threading.Lock()

        def run(part, fn, *args):
            try:
                final = fn(*args, on_update=lambda r: self._push(gen, r, part), cancel=cancel)
                self.ui.drawn_at = 0.0
                self._push(gen, final, part)
            except Exception as e:
                self.call_from_thread(self.say, f"Tool failed: {e}", BAD)
            finally:
                with lock:
                    left[0] -= 1
                    last = left[0] == 0
                if last:
                    self._finish(gen)
        threading.Thread(target=run, args=(0, self.tool_fns["trace"], host), daemon=True).start()
        if ports:
            threading.Thread(target=run, args=(1, self.tool_fns["ports"], host, ports), daemon=True).start()
