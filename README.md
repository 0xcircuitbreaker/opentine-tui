# opentine-tui

Terminal management console for [opentine](https://github.com/0xcircuitbreaker/opentine)
agent runs.

Built with [Textual](https://textual.textualize.io/).

## Install

```bash
pip install opentine-tui
```

Requires [`opentine`](https://github.com/0xcircuitbreaker/opentine) `>=0.2`, which
ships the `.tine` **format v2** surface (migration, tags + search, cost + budget,
autosave/drafts, signing, field-level diff) the dashboard manages. Until `opentine`
0.2 is published to PyPI, install it from source first:

```bash
pip install "opentine @ git+https://github.com/0xcircuitbreaker/opentine.git@main"
pip install opentine-tui
```

Ed25519 signing/keygen in the dashboard needs the optional crypto extra (HMAC-SHA256
signing works without it):

```bash
pip install "opentine-tui[crypto]"
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

The run list adds **Cost**, **Tags**, and a compact **Flags** column that marks
on-disk state at a glance: `1`/`L` = a v1/legacy file a re-save would upgrade,
`D` = an autosave draft checkpoint, and a signature glyph (`✓` verified,
`~` verified-tofu, `?` signed-but-no-key, `x` mismatch). Corrupt `.tine` files
remain visible with their load or integrity error in the details panel, which now
also surfaces tags, token usage, a cost/budget breakdown, the integrity digest,
and an authenticity (signature) block.

## Keybindings

| Key | Action |
|---|---|
| `q` | Quit |
| `r` | Refresh run list |
| `/` | Search / filter runs (`tag:` `model:` `status:` `cost:>0.01` `after:` + free text) |
| `1` `2` `3` | Focus runs / steps / details panel |
| `Enter` | View selected run |
| `v` | Verify integrity — and, when signed, the signature (prompts for a key) |
| `t` | Edit tags on the selected run (kept outside the digest) |
| `m` | Migrate a v1/legacy `.tine` to v2 (one-way; warns before signature drop) |
| `b` | Set a budget (`max_cost`/`max_usage`/`max_steps`/`max_duration` + breach mode) |
| `s` | Sign the selected run (HMAC-SHA256 or Ed25519, via env var / key file) |
| `k` | Generate an Ed25519 keypair |
| `f` | Fork selected run from the selected step |
| `c` | Write a cached replay from the selected step |
| `d` | Diff selected run against another (field-level: per-step before/after + drift) |
| `x` | Resume selected run when `manifest.resume` is true |
| `h` | Launch a new external harness run |
| `Ctrl+F` | Fork selected step and launch a harness |
| `Ctrl+R` | Replay from selected step with a harness |

Write operations and external harness launches require an explicit confirmation
modal before they run.

## What's new for opentine 0.2.0

The dashboard now manages the full `.tine` **format v2** surface:

- **Migration** — detects each file's on-disk version from raw JSON (not the loaded
  run, which always reports v2) and upgrades v1/legacy `0.1.0` artifacts to v2.
- **Tags + search** — per-run tags and a live filter bar backed by the opentine
  search DSL.
- **Cost + budget** — per-step token usage, a `cost_breakdown` (by model/kind), and
  budgets with a re-derived breach state.
- **Autosave / drafts** — draft checkpoints are badged and never treated as final
  or signed.
- **Signing (`tine-sig/1`)** — sign, verify, and generate keys; the details panel
  distinguishes `verified` / `verified-tofu` / `no-key` / `mismatch`, and labels
  `signer` as display-only.
- **Field-level diff** — per-field before/after across aligned steps, including
  cost/usage drift on otherwise-identical steps.

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
