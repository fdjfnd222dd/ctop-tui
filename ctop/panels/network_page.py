"""The five boxes of the Network page (page 3). Drawing only; the keys live in ctop/netpage.py."""
import time

from rich.text import Text

from ctop.render.net_tools import menu_lines, tool_view_lines
from ctop.render.net_wifi import events_lines, link_lines, saved_lines, wifi_list_lines
from ctop.ui import DIM, Panel, hcat

MENU_W = 19

# (key, label, shortcut); key "" draws a separator. The first block measures, the second changes things.
TOOL_MENU = (
    ("doctor", "Network doctor", "d"),
    ("target", "Trace & ports", "t"),
    ("speed", "Speed test", ""),
    ("dns", "DNS timing", ""),
    ("lan", "Find devices", ""),
    ("", "", ""),
    ("rescan", "Rescan Wi-Fi", "r"),
    ("reconnect", "Fix Wi-Fi", ""),
    ("wifi", "Wi-Fi on/off", "w"),
    ("airplane", "Airplane mode", ""),
    ("tailscale", "Tailscale on/off", ""),
)


class _NetPanel(Panel):
    def build(self, s, w, h):
        ui = getattr(s, "netui", None)
        if ui is None:
            return [Text("starting…", style=DIM)]
        return self.lines(s, ui, w, h)


class WifiBox(_NetPanel):
    def lines(self, s, ui, w, h):
        return wifi_list_lines(s.nm, ui.cursor["wifi"], ui.focus == "wifi", w, h)


class SavedBox(_NetPanel):
    def lines(self, s, ui, w, h):
        return saved_lines(s.nm, s.vpn, ui.cursor["saved"], ui.focus == "saved", w, h, time.time())


class ToolsBox(_NetPanel):
    def lines(self, s, ui, w, h):
        if w < MENU_W + 12:                         # too narrow for two columns: the menu alone
            return menu_lines(TOOL_MENU, ui.cursor["tools"], ui.focus == "tools", ui.running, w, h)
        menu = menu_lines(TOOL_MENU, ui.cursor["tools"], ui.focus == "tools", ui.running, MENU_W, h)
        view = tool_view_lines(ui.kind, ui.result, w - MENU_W - 2, h)
        return hcat([menu, view], [MENU_W, w - MENU_W - 2])


class LinkBox(_NetPanel):
    def lines(self, s, ui, w, h):
        nm = s.nm
        aps = next((n.aps for n in nm.networks if n.in_use), None) if nm else None
        return link_lines(s.link, s.wifi, s.chan, aps, w, h)


class EventsBox(_NetPanel):
    def lines(self, s, ui, w, h):
        return events_lines(s.events.events, w, h, time.time())
