"""Memory ring + program list (the part you navigate and stop programs from)."""
from rich.text import Text

from ctop.safety import is_protected
from ctop.ui import DIM, FG, OTHER_COLOR, PALETTE, Panel, SYSTEM_COLOR, TRACK, fit, fmt_gb, gb, grad, hcat, kv, meter, ring
class MemPanel(Panel):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.prog_colors = {}                       # program name -> stable ring colour
        self.first_row = 0                             # first program row shown (scrolling)
        self.rows_visible = 10

    def _color(self, name, top_names):
        if name not in self.prog_colors:
            used = {self.prog_colors[n] for n in top_names if n in self.prog_colors}
            free = [c for c in PALETTE if c not in used]
            self.prog_colors[name] = free[0] if free else PALETTE[len(self.prog_colors) % len(PALETTE)]
        return self.prog_colors[name]

    def build(self, s, w, h):
        vm, sw = s.vm, s.swap
        total = vm.total
        used = total - vm.available
        used_pct = used / total * 100
        groups = s.groups

        # Scale program numbers so they never add up to more than "used"
        # (PSS can slightly overshoot because of rounding and shared pages).
        psum = sum(g[1] for g in groups) or 1
        k = min(1.0, used / psum)
        top = groups[: len(PALETTE)]
        top_names = [g[0] for g in top]
        segs = [(g[1] * k / total, self._color(g[0], top_names)) for g in top]
        rest = sum(g[1] for g in groups[len(PALETTE):]) * k
        system = max(0, used - psum * k) if groups else used
        segs += [(rest / total, OTHER_COLOR), (system / total, SYSTEM_COLOR)]

        # ---- left column: ring + RAM / swap numbers
        lw = max(24, min(w // 3, 38))
        stats = [
            kv("RAM used", f"{gb(used):.1f} GB  ({used_pct:.0f}%)", f"bold {grad(used_pct / 100)}"),
            meter(used / total, lw),
            kv("Available", f"{gb(vm.available):.1f} GB"),
            kv("Cache", f"{gb(getattr(vm, 'cached', 0)):.1f} GB (freed when needed)"),
            kv("Swap", f"{gb(sw.used):.1f} / {gb(sw.total):.1f} GB", grad(sw.percent / 100)),
            meter(sw.percent / 100, lw),
        ]
        diameter = min(lw * 2, (h - len(stats) - 1) * 4, 64) // 4 * 4
        left = []
        if diameter >= 16:
            center = [(f"{used_pct:.0f}%", f"bold {grad(used_pct / 100)}"),
                      (f"{gb(used):.1f}/{gb(total):.1f} GB", FG)]
            for t in ring(segs, diameter, center):
                pad = Text(" " * ((lw - diameter // 2) // 2))
                pad.append_text(t)
                left.append(pad)
            left.append(Text())
        left += stats

        # ---- right column: the programs (this is the ring's legend)
        rw = w - lw - 2
        bar_w = max(0, min(26, rw - 3 - 22 - 7 - 11 - 8 - 4 - 10))
        head = Text(no_wrap=True)
        head.append("   " + f"{'Program':<22}{'Procs':>6} {'RAM':>10} {'Share':>7}", DIM)
        right = [head, Text("─" * rw, style=TRACK)]

        # scrolling window that always keeps the selected program in view
        n_rows = max(h - 3, 1)
        self.rows_visible = n_rows
        names = [g[0] for g in groups]
        if s.cursor in names:
            i = names.index(s.cursor)
            if i < self.first_row:
                self.first_row = i
            elif i >= self.first_row + n_rows:
                self.first_row = i - n_rows + 1
        self.first_row = max(0, min(self.first_row, max(len(groups) - n_rows, 0)))
        entries = [(n, b, c, self.prog_colors.get(n, OTHER_COLOR) if n in top_names else OTHER_COLOR)
                   for n, b, c, _m in groups[self.first_row:self.first_row + n_rows]]
        entries.append(("System & kernel", system, 0, SYSTEM_COLOR))
        if groups:
            where = names.index(s.cursor) + 1 if s.cursor in names else 0
            self.border_subtitle = f"↑↓ select   x x stop   {where or '-'}/{len(groups)}"

        for name, nbytes, count, color in entries:
            share = nbytes / total
            picked = name == s.cursor and count > 0
            t = Text(no_wrap=True)
            t.append("▶ " if picked else "  ", "bold #ffffff")
            t.append("● ", color)
            t.append(f"{name[:21]:<22}", FG)
            t.append(f"{('×' + str(count)) if count else '':>6} ", DIM)
            t.append(f"{fmt_gb(nbytes):>10} ", "bold")
            t.append(f"{share * 100:>6.1f}% ", grad(share * 2.5))
            if bar_w >= 4:
                t.append_text(meter(share, bar_w, lambda _t, c=color: c))
            if count and is_protected(name):
                t.append(" locked", "#8b5a5a")
            if picked:
                armed = s.armed == name
                t = fit(t, rw)
                t.stylize("bold on #6b1f1f" if armed else "on #223049")
            right.append(t)
        return hcat([left, right], [lw, rw], gap=2)
