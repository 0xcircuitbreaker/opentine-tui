# Contributing

opentine-tui is a Textual dashboard over [`opentine`](https://github.com/0xcircuitbreaker/opentine).
It is an unfunded beta project: issues are triaged when a maintainer has time,
and there is no response-time guarantee.

## Setup

```bash
pip install -e ".[dev]"   # resolves opentine>=0.5,<0.6 from PyPI
```

## Before opening a pull request

```bash
ruff check .
ruff format --check .
pytest
```

CI runs exactly these across Linux/macOS/Windows on Python 3.11–3.14, plus a
packaging build and a `twine` metadata check.

## Working against opentine

Tests run against the **installed** `opentine`. To test against a source
checkout — a worktree at a release tag, or a branch you are developing —
point `OPENTINE_SRC` at it:

```bash
git -C ../opentine worktree add /tmp/opentine-v050 v0.5.0
OPENTINE_SRC=/tmp/opentine-v050 pytest
```

Target the **released** opentine, not its development branch. This repository
has twice been written against unreleased API, which is why CI resolves opentine
from PyPI rather than from a branch: the pin in `pyproject.toml` is the contract,
and a green CI run means it holds.

## What a good change looks like

- **A defect gets a test that fails without the fix.** `tests/test_regressions.py`
  is a record of bugs that shipped; each test names the failure it prevents.
- **Never let an artifact take the dashboard down.** A `.tine` file is untrusted
  input: it can be truncated, tampered with, written by a newer opentine, or
  contain a budget the library refuses to parse. A malformed run is a row that
  reports its problem, not a traceback. `tests/test_regressions.py` has the
  shapes that have caused crashes before.
- **Escape run content with `opentine_tui.formatting.escape_markup`.** Neither
  `rich.markup.escape` nor `textual.markup.escape` is safe for Textual's markup —
  model output containing `[bold]…[/]` gets through both and raises at render
  time. There is a test for it; do not swap in another helper.
- **Mirror the CLI's write safety.** `tine` refuses to overwrite a destination
  without an explicit flag, and keeps "force despite a failed check" separate
  from "replace this file". The dashboard does the same.
- **Do not block the event loop.** Scanning artifacts and every mutating action
  run in a worker thread; a few hundred runs is seconds of work.
- **Say what the library says.** If opentine cannot price a step, show *unpriced*
  rather than `$0.00`. If a signature is unverified, do not imply otherwise.

## Scope

Bugs, ergonomics, and coverage of opentine features the dashboard does not yet
surface are all welcome. Two areas are deliberately deferred and explained in the
README: remote sync (blocking transfers with no progress reporting) and the live
trace `Recorder` (a new run object per appended event). Both need a design, not a
keybinding.
