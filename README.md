# opentine-tui

Terminal management console for [opentine](https://github.com/0xcircuitbreaker/opentine)
agent runs.

Built with [Textual](https://textual.textualize.io/).

## Install

```bash
pip install opentine-tui
```

Requires [`opentine`](https://github.com/0xcircuitbreaker/opentine) `>=0.1.1`, the
first release that ships the harness integration layer the dashboard uses. Until
`opentine` 0.1.1 is published to PyPI, install it from source first:

```bash
pip install "opentine @ git+https://github.com/0xcircuitbreaker/opentine.git@main"
pip install opentine-tui
```

## Usage

```bash
tine-dashboard
opentine-tui
```

By default the TUI reads `.tine` artifacts from `.tine_runs`. Override that with:

```bash
tine-dashboard --runs-dir path/to/.tine_runs
OPENTINE_RUNS_DIR=path/to/.tine_runs tine-dashboard
```

## Layout

```
┌── Runs ───────┐ ┌── Steps ───────────────┐ ┌── Details ─────────┐
│ a3f8 done     │ │ * think "I'll search"  │ │ Run: a3f8          │
│ b7c1 fail     │ │ > tool search(q="...") │ │ Model: claude      │
│ c9d2 corrupt  │ │ * think "The mass..."  │ │ Steps: 4           │
│               │ │ + done                 │ │ Cost: $0.003       │
│               │ │                        │ │ Duration: 12.3s    │
└──────────────────┘ └──────────────────────────────┘ └─────────────────────┘
```

Corrupt `.tine` files remain visible in the run list with their load or integrity
error in the details panel.

## Keybindings

| Key | Action |
|---|---|
| `q` | Quit |
| `r` | Refresh run list |
| `1` | Focus runs panel |
| `2` | Focus steps panel |
| `3` | Focus details panel |
| `Enter` | View selected run |
| `v` | Verify selected `.tine` integrity |
| `f` | Fork selected run from the selected step |
| `c` | Write a cached replay from the selected step |
| `d` | Diff selected run against another run |
| `x` | Resume selected run when `manifest.resume` is true |
| `h` | Launch a new external harness run |
| `Ctrl+F` | Fork selected step and launch a harness |
| `Ctrl+R` | Replay from selected step with a harness |

Write operations and external harness launches require an explicit confirmation
modal before they run.

## Development

```bash
# install opentine (from source until 0.1.1 is on PyPI) and the TUI with dev extras
pip install "opentine @ git+https://github.com/0xcircuitbreaker/opentine.git@main"
pip install -e ".[dev]"

ruff check .
ruff format --check .
pytest
```

CI runs lint, format, the test suite, a packaging build, and a `twine` metadata
check across Linux/macOS/Windows on Python 3.11–3.13. Pushing a `v*` tag builds
signed release artifacts and attaches them to a GitHub release.

## License

Apache 2.0
