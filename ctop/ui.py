"""Drawing helpers shared by every panel: colours, meters, graphs, the ring chart, Panel base."""
import math

from rich.text import Text
from textual.widgets import Static

GIB = 1024 ** 3
HIST = 300                      # samples of history kept = 5 minutes at 1 s
DIM = "#7d8590"
FG = "#d0d4da"
TRACK = "#262a31"               # the "empty" part of meters and rings
PALETTE = ["#4ea1ff", "#ff7b72", "#f2cc60", "#56d364", "#bc8cff", "#ff9bce", "#39c5cf"]
OTHER_COLOR = "#7b8494"         # all remaining programs
SYSTEM_COLOR = "#4a4f5a"        # kernel, caches in use, drivers - not a program

# --------------------------------------------------------------------------
# Small drawing helpers (colour gradients, meters, graphs, the ring)
# --------------------------------------------------------------------------
STOPS = [(0.0, (80, 200, 100)), (0.5, (235, 205, 70)), (1.0, (235, 75, 65))]


def _hex(c):
    return "#%02x%02x%02x" % tuple(int(v) for v in c)


def lerp(a, b, t):
    return tuple(a[i] + (b[i] - a[i]) * t for i in range(3))


def grad(t):
    """btop-style gradient: 0 -> green, 0.5 -> yellow, 1 -> red."""
    t = min(max(t, 0.0), 1.0)
    for (t0, c0), (t1, c1) in zip(STOPS, STOPS[1:]):
        if t <= t1:
            return _hex(lerp(c0, c1, (t - t0) / (t1 - t0)))
    return _hex(STOPS[-1][1])


def fit(t, width):
    """Return a copy of Text `t` cropped/padded to exactly `width` cells."""
    t = t.copy()
    t.truncate(width, overflow="crop", pad=True)
    return t


def hcat(cols, widths, gap=2):
    """Glue blocks of lines side by side (each block is a list of Text)."""
    n = max((len(c) for c in cols), default=0)
    out = []
    for i in range(n):
        line = Text(no_wrap=True)
        for j, (c, w) in enumerate(zip(cols, widths)):
            line.append_text(fit(c[i] if i < len(c) else Text(), w))
            if j < len(cols) - 1:
                line.append(" " * gap)
        out.append(line)
    return out


def kv(label, value, vstyle=FG, lw=11):
    t = Text(no_wrap=True)
    t.append(f"{label:<{lw}}", DIM)
    t.append(value, vstyle)
    return t


def runs(pairs):
    """Build a Text from (char, style) pairs, merging same-style neighbours.

    One span per character makes Textual re-style thousands of cells every
    second; merged runs cut ctop's own CPU use by roughly half.
    """
    t = Text(no_wrap=True)
    buf, cur = "", None
    for ch, st in pairs:
        if st == cur:
            buf += ch
        else:
            if buf:
                t.append(buf, cur or "")
            buf, cur = ch, st
    if buf:
        t.append(buf, cur or "")
    return t


def meter(frac, width, color_at=grad):
    """btop-style bar of squares; colour follows position along the bar."""
    frac = min(max(frac, 0.0), 1.0)
    n = round(frac * width)
    return runs(("■", color_at(i / max(width - 1, 1)) if i < n else TRACK) for i in range(width))


BLOCKS = " ▁▂▃▄▅▆▇█"


def graph(vals, vmax, width, rows, color_at):
    """Multi-row block graph of the most recent `width` values."""
    vals = list(vals)[-width:]
    vals = [0.0] * (width - len(vals)) + vals
    vmax = max(vmax, 1e-9)
    cells = [[] for _ in range(rows)]
    for v in vals:
        f = min(v / vmax, 1.0)
        h = int(round(f * rows * 8))
        col = color_at(f)
        for r in range(rows):                    # r = 0 is the top row
            level = h - (rows - 1 - r) * 8
            if level <= 0:
                # flat baseline when idle, otherwise empty
                cells[r].append(("▁", TRACK) if h == 0 and r == rows - 1 else (" ", None))
            else:
                cells[r].append((BLOCKS[min(level, 8)], col))
    return [runs(row) for row in cells]


# Braille dot -> bit, so one terminal cell holds a 2x4 grid of dots. With the
# usual 1:2 cell shape those dots are square, so circles look round.
BRAILLE = {(0, 0): 1, (0, 1): 2, (0, 2): 4, (1, 0): 8,
           (1, 1): 16, (1, 2): 32, (0, 3): 64, (1, 3): 128}


def ring(segs, diameter, center, inner=0.68):
    """Draw a ring chart as a list of Text rows.

    segs   - [(fraction, colour), ...] clockwise from 12 o'clock; the rest of
             the circle is drawn as the dim track.
    center - up to a few (text, style) lines written inside the hole.
    """
    cols, rows = diameter // 2, diameter // 4
    R = diameter / 2
    ri = R * inner
    bounds, acc = [], 0.0
    for f, c in segs:
        acc += f
        bounds.append((acc, c))
    grid = []
    for cy in range(rows):
        row = []
        for cx in range(cols):
            bits, counts = 0, {}
            for (dx, dy), b in BRAILLE.items():
                x = cx * 2 + dx + 0.5 - R
                y = cy * 4 + dy + 0.5 - R
                if ri <= math.hypot(x, y) <= R:
                    bits |= b
                    ang = (math.atan2(x, -y) / (2 * math.pi)) % 1.0
                    col = TRACK
                    for lim, c in bounds:
                        if ang < lim:
                            col = c
                            break
                    counts[col] = counts.get(col, 0) + 1
            row.append([chr(0x2800 + bits), max(counts, key=counts.get)] if bits else [" ", None])
        grid.append(row)
    # write the centre labels into the hole
    start = rows // 2 - len(center) // 2
    for k, (txt, style) in enumerate(center):
        r = start + k
        if 0 <= r < rows:
            txt = txt[: max(cols - 4, 1)]
            x0 = (cols - len(txt)) // 2
            for i, ch in enumerate(txt):
                grid[r][x0 + i] = [ch, style]
    return [runs(row) for row in grid]


def fmt_dur(hours):
    if hours is None or hours < 0 or hours > 99:
        return "-"
    m = int(round(hours * 60))
    return f"{m // 60}h {m % 60:02d}m"


def fmt_mbps(v):
    if v <= 0:
        return "0.0"
    return "<0.1" if v < 0.05 else f"{v:.1f}"


def gb(nbytes):
    return nbytes / GIB


def fmt_gb(nbytes):
    v = gb(nbytes)
    return "<0.01 GB" if 0 < v < 0.005 else f"{v:.2f} GB"


# --------------------------------------------------------------------------
# Panels - each one turns the Sampler's numbers into lines of coloured Text
# --------------------------------------------------------------------------
def _cropped(t, width):
    t = t.copy()
    t.truncate(width, overflow="crop")
    return t


class Panel(Static):
    def __init__(self, title, **kw):
        super().__init__(**kw)
        self.border_title = title
        self._state = None

    def draw(self, s=None):
        if s is not None:
            self._state = s
        size = self.content_size
        if self._state is None or size.width < 8 or size.height < 3:
            return
        try:
            lines = self.build(self._state, size.width, size.height)
            # a line wider than the box would wrap and push everything down: crop instead
            lines = [l if l.cell_len <= size.width else _cropped(l, size.width) for l in lines[: size.height]]
            self.update(Text("\n").join(lines))
        except Exception as e:                      # a bad frame must never kill the dashboard
            self.update(Text(f"draw error: {e!r}", style="red"))

    def on_resize(self, event):
        self.draw()
