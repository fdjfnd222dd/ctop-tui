# Model change requests (UI/render architect)

PROPOSED MODEL CHANGE: add `captured_at: float = 0.0` (epoch seconds, `time.time()` when the collector built it) to `ProgramDetail`
because the details popup shows "running since 3h 12m" and today the renderer needs the current time; T14 therefore takes an optional
`now=` argument and falls back to `time.time()`. With `captured_at` the popup would be a pure function of the snapshot and the age would not jump while the popup is paused. Not required: T14 works with the current models.py.

PROPOSED OTHER CHANGE (not models.py): `Sampler.scan_procs()` should also publish `group_cpu: Dict[str, float]` (program name -> summed CPU %, from `psutil.Process.cpu_percent(None)`, primed on the first scan)
because the new `s` key sorts programs by RAM -> CPU -> name and `Sampler.groups` tuples `(name, bytes, count, members)` carry no CPU number. Until it exists, `s` cycles RAM <-> name only (see PAGES.md section 4).

PROPOSED OTHER CHANGE (pyproject.toml): list `rich` explicitly in `dependencies` (e.g. `"rich>=13"`) because `ctop/ui.py` and every renderer import `rich.text` directly and the docs promise "psutil, rich, textual"; today it only arrives as a dependency of Textual.
