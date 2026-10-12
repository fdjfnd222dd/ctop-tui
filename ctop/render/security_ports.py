"""Security page tables A: which programs accept connections, and which ones are talking out.

Plain words for a non-expert. Pure functions: no I/O, no DNS, no name lookups.
"""
from __future__ import annotations

from typing import List, Optional, Sequence

from rich.text import Text

from ctop.models import Connection, FirewallInfo, Listener
from ctop.ui import DIM, FG, fit

GREEN = "#56d364"
RED = "#ff7b72"
YELLOW = "#f2cc60"
BLUE = "#4ea1ff"
# Anything else (TIME_WAIT, CLOSE_WAIT, ...) is a closing socket, not a live conversation.
LIVE = frozenset({"ESTABLISHED", "SYN_SENT", "NONE", ""})
OTHER_USER = "(other user)"
EXPOSURE_RANK = {"network": 0, "specific": 1, "local": 2}
ELLIPSIS = "…"


def listeners_lines(listeners: "Sequence[Listener]", firewall: Optional[FirewallInfo],
                    w: int, h: int) -> "List[Text]":
    if w < 1:
        return []
    if not listeners:
        head = Text("Nothing is listening for connections.", style=DIM, no_wrap=True)
        return _finish(_assemble(head, [], [], h), w)

    # The Program column grows with the terminal; a narrow one needs room for the reach text.
    prog_w = 12 if w < 56 else 16 if w < 80 else 20
    full = w - 13 - prog_w >= 24
    protected = (firewall is not None and firewall.active is not False
                 and firewall.default_incoming in ("deny", "reject"))
    unprotected = (firewall is not None
                   and (firewall.active is False or firewall.default_incoming == "allow"))

    # The same program on IPv4 and IPv6 is one row, not two.
    seen = set()
    unique: List[Listener] = []
    for item in listeners:
        key = (item.proto, item.port, item.process, item.exposure)
        if key not in seen:
            seen.add(key)
            unique.append(item)
    unique.sort(key=lambda item: (EXPOSURE_RANK.get(item.exposure, 3), item.port, item.proto))

    rows: List[Text] = []
    need_foot = False
    for item in unique:
        if item.exposure == "local":
            who = "only this computer" if full else "this computer"
            color = GREEN
        elif item.exposure == "network":
            who = "everyone on your network" if full else "your network"
            # Open to the whole network while the firewall is off or allows everything: alarm colour.
            color = RED if unprotected else YELLOW
        else:
            who = "one network address" if full else "one address"
            color = YELLOW

        t = Text(no_wrap=True)
        t.append(str(item.port).ljust(6), style=("bold " + FG) if item.exposure == "network" else FG)
        t.append(item.proto.upper().ljust(7), style=DIM)
        if item.process is None:
            name_cell = _cut(OTHER_USER, prog_w - 1).ljust(prog_w)
            t.append(name_cell, style=DIM)
        else:
            t.append(_cut(item.process, prog_w - 1).ljust(prog_w), style=FG)
        t.append(who, style=color)
        if protected and item.exposure != "local":
            # Wide terminals explain each row; narrow ones get a single footer instead.
            if w >= 110:
                t.append(" (firewall blocks it unless a rule allows)", style=DIM)
            else:
                need_foot = True
        rows.append(t)

    foot: List[Text] = []
    if need_foot:
        foot.append(Text("Firewall: blocks these unless a rule allows them.", style=DIM, no_wrap=True))

    head = Text("Port".ljust(6) + "Proto".ljust(7) + "Program".ljust(prog_w) + "Who can reach it",
                style="bold " + DIM, no_wrap=True)
    return _finish(_assemble(head, rows, foot, h), w)


def connections_lines(conns: "Sequence[Connection]", w: int, h: int) -> "List[Text]":
    if w < 1:
        return []

    groups = {}
    for conn in conns:
        if conn.status not in LIVE:
            continue
        name = conn.process if conn.process is not None else OTHER_USER
        groups.setdefault(name, []).append(conn)

    summary = []
    for name, items in groups.items():
        if any(not c.remote_is_private for c in items):
            tag, color = "internet", BLUE
        elif all(c.remote_ip.startswith("127.") or c.remote_ip == "::1" for c in items):
            tag, color = "this computer", DIM
        else:
            tag, color = "local network", GREEN
        summary.append((name, len(items), tag, color, items))
    # Internet talkers first: they are the ones worth a second look.
    summary.sort(key=lambda s: (s[2] != "internet", -s[1], s[0]))

    if not summary:
        head = Text("No open connections right now.", style=DIM, no_wrap=True)
        return _finish(_assemble(head, [], [], h), w)

    name_w = 16 if w < 60 else 20
    ex_w = w - name_w - 20
    rows: List[Text] = []
    for name, count, tag, color, items in summary:
        t = Text(no_wrap=True)
        t.append(_cut(name, name_w - 1).ljust(name_w), style=FG)
        t.append(" " + str(count).rjust(4) + " ")
        if w >= 60:
            # Narrow examples cannot show much, so one address reads better than a cut pair.
            examples = _examples(items, 2 if ex_w >= 40 else 1)
            t.append(examples[:ex_w].ljust(ex_w) + " ")
        t.append(tag, style=color)
        rows.append(t)

    head_text = "Program".ljust(name_w) + " " + "Open".rjust(4) + " "
    if w >= 60:
        head_text += "Talking to".ljust(ex_w) + " "
    head = Text(head_text + "Where", style="bold " + DIM, no_wrap=True)
    return _finish(_assemble(head, rows, [], h), w)


def _examples(items: "Sequence[Connection]", limit: int) -> str:
    """Up to `limit` distinct remote addresses, public ones first."""
    remotes = {}
    for conn in items:
        remotes.setdefault((conn.remote_ip, conn.remote_port), conn.remote_is_private)
    # False sorts before True, so public addresses come first; sort is stable within each group.
    ordered = sorted(remotes.items(), key=lambda pair: pair[1])
    parts = []
    for (ip, port), _private in ordered[:limit]:
        # IPv6 needs brackets, or the port would look like part of the address.
        parts.append("[%s]:%d" % (ip, port) if ":" in ip else "%s:%d" % (ip, port))
    return ", ".join(parts)


def _cut(text: str, width: int) -> str:
    """Shorten to `width` characters, ending in an ellipsis when cut."""
    if len(text) <= width:
        return text
    return text[: width - 1] + ELLIPSIS


def _more(count: int) -> Text:
    return Text("... +%d more" % count, style=DIM, no_wrap=True)


def _assemble(head: Text, rows: List[Text], foot: List[Text], h: int) -> List[Text]:
    """Fit the table into `h` lines, dropping the footer first, then saying how many rows were cut."""
    if h <= 0:
        return []
    if 1 + len(rows) + len(foot) <= h:
        return [head] + rows + foot
    if 1 + len(rows) <= h:
        return [head] + rows
    if h == 1:
        return [_more(len(rows))]
    keep = h - 2
    return [head] + rows[:keep] + [_more(len(rows) - keep)]


def _finish(lines: List[Text], w: int) -> List[Text]:
    return [fit(t, w) for t in lines]
