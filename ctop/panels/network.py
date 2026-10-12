"""Network panel."""
from rich.text import Text

from ctop.render.quality import quality_lines
from ctop.ui import DIM, FG, Panel, TRACK, _hex, fmt_mbps, gb, graph, kv, lerp
class NetPanel(Panel):
    def build(self, s, w, h):
        net = s.net
        down_c = lambda f: _hex(lerp((60, 110, 200), (110, 225, 245), f))
        up_c = lambda f: _hex(lerp((160, 70, 170), (250, 150, 225), f))
        gr = max(1, min(5, 1 + (h - 18) // 2))
        pk_d, pk_u = max(max(s.down_hist, default=0), 1.0), max(max(s.up_hist, default=0), 1.0)

        def speed_head(arrow, val, peak, color):
            t = Text(no_wrap=True)
            t.append(f"{arrow} ", color)
            t.append(f"{fmt_mbps(val)} Mbps", f"bold {color}")
            t.append(f"   graph tops out at {peak:.0f} Mbps", DIM)
            return t

        lines = [speed_head("↓ Download", s.down, pk_d, "#6fb3ff")]
        lines += graph(s.down_hist, pk_d, w, gr, down_c)
        lines.append(speed_head("↑ Upload  ", s.up, pk_u, "#f08fd8"))
        lines += graph(s.up_hist, pk_u, w, gr, up_c)
        lines.append(kv("Since boot", f"↓ {gb(s.boot_totals[0]):.2f} GB   ↑ {gb(s.boot_totals[1]):.2f} GB"))
        lines.append(Text("─" * w, style=TRACK))

        if not net:
            lines.append(Text("looking up network details…", style=DIM))
            return lines
        lines += quality_lines(s.ping, s.wifi, w)[:max(0, min(6, h - 12))]
        lines.append(kv("Interface", net.get("iface") or "none (offline)", lw=14))
        lines.append(kv("Your IP", net.get("ip") or "-", "bold", lw=14))
        lines.append(kv("Gateway", f"{net.get('gateway') or '-'}   (your router)", lw=14))
        lines.append(kv("DNS", "   ".join(net.get("dns") or ["-"]) + "   (name lookup)", lw=14))
        if h - len(lines) >= 2:
            lines.append(Text("Interfaces", style=DIM))
        for name, kind, up, v4 in net.get("ifaces", []):
            t = Text(no_wrap=True)
            t.append("● ", "#56d364" if up else "#6e7681")
            t.append(f"{name:<12}", FG)
            t.append(f"{kind:<10}", DIM)
            t.append(f"{'up' if up else 'down':<6}", "#56d364" if up else "#6e7681")
            t.append(v4 or "-", FG if v4 else DIM)
            lines.append(t)
        return lines
