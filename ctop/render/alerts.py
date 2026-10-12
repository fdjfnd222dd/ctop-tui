"""One-line alert banner for the top of the screen.

Pure: takes the alert list from ctop/alerts.py and returns one Text row.
"""
from __future__ import annotations

from typing import Optional, Sequence

from rich.cells import cell_len
from rich.text import Text

from ctop.models import Alert
from ctop.ui import DIM, fit

# Whole-line bars: the colour is the message, so the bar still reads on a
# monochrome terminal because the lead symbol differs too (! vs i).
CRIT_STYLE = "bold #ffffff on #8b1a1a"      # white on dark red
WARN_STYLE = "bold #000000 on #f2a93b"      # black on amber
INFO_STYLE = DIM                            # dim grey, no background

_RANK = {"crit": 2, "warn": 1, "info": 0}


def banner(alerts: Sequence[Alert], w: int) -> Optional[Text]:
    """Return the most severe alert as one row exactly `w` cells wide, or None to hide the row."""
    if not alerts or w < 1:
        return None

    # Strictly greater keeps the first of equal severity; T04 sorts by importance.
    top = alerts[0]
    for a in alerts[1:]:
        if _rank(a.level) > _rank(top.level):
            top = a
    rank = _rank(top.level)

    lead = " i " if rank == 0 else " ! "
    style = {2: CRIT_STYLE, 1: WARN_STYLE}.get(rank, INFO_STYLE)
    # Collapse newlines and runs of spaces so a multi-line alert stays one row.
    text = " ".join(top.text.split()) or top.key
    n = len(alerts)
    suffix = f" (+{n - 1} more)" if n > 1 else ""

    msg = lead + text + suffix
    if cell_len(msg) > w:
        # Keep the "(+N more)" count visible; only the sentence gets shortened.
        room = w - cell_len(lead) - cell_len(suffix)
        if room >= 4:
            msg = lead + text[: room - 1] + "…" + suffix
        else:
            # Too narrow for an ellipsis to mean anything; crop the line instead.
            msg = lead + text

    return fit(Text(msg, style=style, no_wrap=True), w)


def _rank(level: str) -> int:
    # Unknown levels are treated as info so a typo never hides an alert.
    return _RANK.get(level, 0)
