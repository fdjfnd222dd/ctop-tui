"""The four boxes of the Security page (page 2)."""
from rich.text import Text

from ctop.render.security_lan import devices_lines, summary_lines
from ctop.render.security_ports import connections_lines, listeners_lines
from ctop.ui import DIM, Panel

WAITING = "collecting... (takes a few seconds)"


class _SecPanel(Panel):
    def build(self, s, w, h):
        if s.sec is None:
            return [Text(WAITING, style=DIM)]
        return self.lines(s, w, h)


class PortsPanel(_SecPanel):
    def lines(self, s, w, h):
        return listeners_lines(s.sec.listeners, s.sec.firewall, w, h)


class ConnPanel(_SecPanel):
    def lines(self, s, w, h):
        return connections_lines(s.sec.connections, w, h)


class DevicesPanel(_SecPanel):
    def lines(self, s, w, h):
        return devices_lines(s.sec.devices, w, h)


class ExposedPanel(_SecPanel):
    def lines(self, s, w, h):
        return summary_lines(s.sec, s.wifi, w, h)
