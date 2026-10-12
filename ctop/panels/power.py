"""Battery panel."""
from rich.text import Text

from ctop.ui import DIM, Panel, fmt_dur, grad, graph, kv, meter
class BatPanel(Panel):
    def build(self, s, w, h):
        b = s.bat
        if not b or b.get("pct") is None:
            return [Text("No battery found", style=DIM)]
        pct, status, watts = b["pct"], b["status"], b["watts"] or 0.0
        recent = list(s.watt_hist)[-5:]
        avg = sum(recent) / len(recent) if recent else watts

        if status == "Discharging":
            state, scol, verb = "On battery - draining", "#ff9e64", "draining"
        elif status == "Charging":
            state, scol, verb = "Charging", "#56d364", "charging"
        elif status == "Full":
            state, scol, verb = "Full - plugged in", "#56d364", "idle"
        else:
            state, scol, verb = f"Plugged in - {status.lower()}", "#f2cc60", "idle"

        head = Text(no_wrap=True)
        head.append(f"{pct}%", f"bold {grad(1 - pct / 100)}")
        head.append("  " + state, f"bold {scol}")
        bar_col = grad(1 - pct / 100)
        lines = [head, meter(pct / 100, w, lambda t: bar_col), Text()]

        if status == "Discharging":
            lines.append(kv("Using", f"{watts:.1f} W", "bold #ff9e64"))
            if b["wh_full"] and avg > 0.3:
                lines.append(kv("", f"about {avg / b['wh_full'] * 100:.0f}% of the battery per hour", DIM))
            lines.append(kv("Left", fmt_dur(b["wh_now"] / avg if avg > 0.3 else None), "bold"))
        elif status == "Charging":
            lines.append(kv("Charging", f"{watts:.1f} W", "bold #56d364"))
            to_full = (b["wh_full"] - b["wh_now"]) / avg if avg > 0.3 else None
            lines.append(kv("Full in", fmt_dur(to_full), "bold"))
        else:
            lines.append(kv("Power", f"{watts:.1f} W ({verb})"))
        lines.append(kv("Energy", f"{b['wh_now']:.1f} / {b['wh_full']:.1f} Wh"))
        if b["wh_design"]:
            health = b["wh_full"] / b["wh_design"] * 100
            extra = f"   {b['cycles']} charge cycles" if b["cycles"] is not None else ""
            lines.append(kv("Health", f"{health:.0f}% of new{extra}", grad(1 - health / 100)))

        room = h - len(lines) - 1
        if room >= 2:
            peak = max(max(s.watt_hist, default=0), 5.0)
            lines.append(kv("", f"power over the last 5 min (top = {peak:.0f} W)", DIM, lw=0))
            col = "#ff9e64" if status == "Discharging" else "#56d364"
            lines += graph(s.watt_hist, peak, w, room, lambda f: col)
        return lines
