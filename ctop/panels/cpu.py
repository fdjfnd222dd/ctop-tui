"""CPU panel."""
from rich.text import Text

from ctop.render.thermal import thermal_lines
from ctop.ui import DIM, Panel, grad, graph, hcat, kv, meter
class CpuPanel(Panel):
    def build(self, s, w, h):
        n = max(len(s.cores), 1)
        colw = min(30, max((w // 2 - 2) // 2, 14))
        right_w = colw * 2 + 2
        left_w = max(w - right_w - 2, 10)
        total = sum(s.cores) / n if s.cores else 0

        head = Text(no_wrap=True)
        head.append("Total ", DIM)
        head.append(f"{total:>3.0f}%", f"bold {grad(total / 100)}")
        head.append(f"   last {s.history_minutes:g} min", DIM)
        left = [head] + graph(s.cpu_hist, 100, left_w, max(h - 1, 1), grad)

        half = (n + 1) // 2
        cells = []
        for i, pct in enumerate(s.cores):
            t = Text(no_wrap=True)
            t.append(f"C{i + 1:<2}", DIM)
            t.append_text(meter(pct / 100, colw - 8))
            t.append(f" {pct:>3.0f}%", grad(pct / 100))
            cells.append(t)
        right = hcat([cells[:half], cells[half:]], [colw, colw], gap=2)
        room = h - half - 2                         # rows left after the core meters, Speed and Load
        if getattr(s, "thermal", None) is not None:
            th = thermal_lines(s.thermal, right_w)
            if room >= 5:
                right += [Text()] + th[:room - 1]
            elif room >= 1:
                right += th[:room]
        else:                                       # collector not ready yet: the plain temperature
            right.append(Text())
            if s.temp is not None:
                right.append(kv("Temp", f"{s.temp:.0f} °C", grad((s.temp - 40) / 55)))
        if s.freq:
            right.append(kv("Speed", f"{s.freq:.1f} GHz"))
        right.append(kv("Load", "  ".join(f"{x:.1f}" for x in s.load) + "   (1 / 5 / 15 min)"))
        return hcat([left, right], [left_w, right_w], gap=2)
