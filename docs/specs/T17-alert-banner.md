# T17 - Alert banner line

## 1 Purpose
One line at the very top of the screen that tells the owner the most important thing that is wrong right now
("Battery at 9% - plug in the charger"), with "(+N more)" when several alerts are active.
Pure function over the list that `ctop/alerts.py` (T04) produces. No I/O.

## 2 Files
Create: `ctop/render/alerts.py`, `tests/test_render_alerts.py`.
READ first: `docs/ARCHITECTURE.md`, `ctop/models.py` (`Alert`), `ctop/ui.py` (`fit`, `DIM`).
Touch nothing else (the maintainer adds the widget to `ctop/app.py`).

## 3 Public API
```python
from __future__ import annotations
from typing import Optional, Sequence
from rich.text import Text
from ctop.models import Alert

CRIT_STYLE = "bold #ffffff on #8b1a1a"      # white on dark red
WARN_STYLE = "bold #000000 on #f2a93b"      # black on amber
INFO_STYLE = DIM                            # import DIM from ctop.ui; dim grey, no background

def banner(alerts: "Sequence[Alert]", w: int) -> Optional[Text]: ...
```
Python 3.9: no `X | Y`, no `match`.

## 4 Visual design
- `alerts` empty, or `w < 1` -> return `None` (the maintainer hides the row).
- Severity rank: `crit` 2, `warn` 1, `info` 0; any other `level` string counts as `info`. Pick the highest rank; among equals pick the FIRST in the sequence (T04 sorts by importance).
- Message = `lead + text + suffix`:
  `lead` = `" ! "` for crit and warn, `" i "` for info (a symbol that survives colour-blindness and monochrome terminals);
  `text` = `alert.text` with every run of whitespace (including newlines) collapsed to one space and stripped; if that is empty use `alert.key`;
  `suffix` = `f" (+{n - 1} more)"` when there are `n > 1` alerts, else `""`.
- Style by rank: crit `CRIT_STYLE`, warn `WARN_STYLE`, info `INFO_STYLE`. The WHOLE line (including the padding) has that one style: create `Text(msg, style=STYLE, no_wrap=True)` and then `fit`, so the coloured bar spans the full width.
- Too long: if `cell_len(lead + text + suffix) > w`, shorten ONLY `text` so the suffix stays visible: `room = w - cell_len(lead) - cell_len(suffix)`; if `room >= 4` use `text[: room - 1] + "…"`; otherwise (very narrow) drop the suffix and crop `lead + text` to `w` with no ellipsis.
- Wording is never changed or translated; the alert text comes from T04 already in plain English.

Mock, width 90, one critical alert (`[...]` marks the coloured bar, white on dark red; trailing spaces are part of the bar):
```
[ ! Battery is at 9% and not charging - plug in the charger soon                          ]
```
Mock, width 90, a warn plus two others (warn is the highest):
```
[ ! RAM is 91% full - close a big program (+2 more)                                       ]
```
Mock, width 40, long crit text (shortened, suffix kept):
```
[ ! Battery is at 9% and not c… (+1 more)]
```
Mock, width 40, info only (dim grey, no background):
```
 i Wi-Fi signal is weak
```

## 5 Rules
- Return value is a single `Text` (not a list), `no_wrap=True`, `cell_len(t.plain) == w` exactly (via `ui.fit`), or `None`.
- Never raises on odd input: empty text, huge text, unknown level, 1-cell width.
- No I/O, no clock, no colour other than the three constants above. Only characters: ASCII plus `…`.

## 6 Test plan (`tests/test_render_alerts.py`)
Fixtures: `CRIT = Alert("crit","battery-low","Battery is at 9% and not charging - plug in the charger soon")`, `WARN = Alert("warn","ram-high","RAM is 91% full - close a big program")`,
`INFO = Alert("info","wifi-weak","Wi-Fi signal is weak")`.
- `test_none_when_empty`: `banner([], 90) is None`; `banner([CRIT], 0) is None`.
- `test_single_alert_text`: `banner([WARN], 90).plain.rstrip() == " ! RAM is 91% full - close a big program"`.
- `test_most_severe_wins`: `banner([INFO, WARN, CRIT], 120).plain` starts with " ! Battery is at 9%" and ends (rstrip) with "(+2 more)".
- `test_first_of_equal_severity`: two warns -> the first one's text is shown.
- `test_styles`: `banner([CRIT], 90).style == CRIT_STYLE`; WARN -> `WARN_STYLE`; INFO -> `INFO_STYLE`; unknown level `"fatal"` -> `INFO_STYLE`.
- `test_info_uses_i_marker`: INFO plain starts with " i ".
- `test_exact_width_and_no_wrap`: for w in (1, 5, 10, 40, 90, 200): `cell_len(banner([CRIT, WARN], w).plain) == w` and `no_wrap` is True.
- `test_shortened_keeps_suffix`: `banner([CRIT, WARN], 40).plain.rstrip()` ends with "(+1 more)" and contains "…".
- `test_very_narrow_drops_suffix`: w=8 -> width 8, no "…", starts with " ! ".
- `test_whitespace_and_newlines_collapsed`: `Alert("warn","k","a\n  b")` -> contains "a b".
- `test_empty_text_uses_key`: `Alert("warn","ram-high","")` -> contains "ram-high".
- `test_no_None_text`: never contains "None".

## 7 Done when
- [ ] `python -m unittest tests.test_render_alerts -v` passes on Python 3.9+.
- [ ] `python -m pyflakes ctop/render/alerts.py tests/test_render_alerts.py` is silent.
- [ ] Only the two listed files created; no new dependency.
