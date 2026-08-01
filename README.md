# opentine-tui

Terminal management console for [opentine](https://github.com/0xcircuitbreaker/opentine)
agent runs.

Built with [Textual](https://textual.textualize.io/).

## Install

```bash
pip install opentine-tui
```

That pulls in [`opentine`](https://github.com/0xcircuitbreaker/opentine)
`>=0.5,<0.6`. Portable `.tine` files are **format v2** (migration, tags + search,
cost + budget, autosave/drafts, signing, field-level diff); 0.3 added the
git-shaped **v3 repository** (content-addressed objects, refs, fsck, semantic
diff, attestations), 0.4 made a fork id identify the **fork act**, and 0.5 added
**OpenTelemetry GenAI export** and the **trace importers** — the dashboard
manages all of it.

Ed25519 signing and keygen work out of the box: `opentine` 0.4 depends on
`cryptography` directly. The `crypto` extra is kept as a no-op so
`opentine-tui[crypto]` keeps resolving, and so the requirement stays explicit if
opentine ever makes it optional again.

## Usage

```bash
tine-dashboard
opentine-tui
```

By default the TUI reads `.tine` artifacts from `.tine_runs`, and looks upward from
the working directory for a v3 `.tine/` repository the way `tine` itself does.
Override either with:

```bash
tine-dashboard --runs-dir path/to/.tine_runs
tine-dashboard --repo path/inside/a/repository
OPENTINE_RUNS_DIR=path/to/.tine_runs tine-dashboard
tine-dashboard --version
```

The left panel has two tabs — **Files** (portable `.tine` artifacts) and
**Repository** (run objects in a `.tine/` repository) — and `[` / `]` switches
between them. Both feed the same step tree and detail panels, because
`repo.load_run()` returns the same run the file reader does.

## Layout

```
┌─ Runs ─────────────────┐ ┌── Steps ───────────────┐ ┌── Details ─────────┐
│ Files │ Repository     │ │ * think "I'll search"  │ │ Run: a3f8          │
│ a3f8 done  4m  ✓       │ │ > tool search(q="...") │ │ Model: claude      │
│ b7c1 fail  1h          │ │ * think "The mass..."  │ │ Steps: 4           │
│ c9d2 corrupt           │ │ + done                 │ │ Cost: $0.003       │
│                        │ │                        │ │ Duration: 12.3s    │
└────────────────────────┘ └────────────────────────┘ └────────────────────┘
```

**Files** lists portable artifacts with **Cost**, **Age**, **Tags**, and a compact
**Flags** column that marks on-disk state at a glance: `1`/`L` = a v1/legacy file a
re-save would upgrade, `D` = an autosave draft checkpoint, and a signature glyph
(`✓` verified, `~` verified-tofu, `?` signed-but-no-key, `x` mismatch). Corrupt
`.tine` files remain visible with their load or integrity error in the details
panel, which also surfaces tags, token usage, a cost/budget breakdown, pricing
provenance, the integrity digest, and an authenticity (signature) block.

**Repository** lists run objects with their event count, cost, evaluation score
and ref, flagged `Y` for a fork and `~` when a shallow clone boundary cut some of
its events. There is no integrity or signature column there: v3 objects are
content-addressed and opentine verifies them on every read.

## Keybindings

Press `?` for the full list inside the app — every binding carries a tooltip.

| Key | Action |
|---|---|
| `q` | Quit |
| `?` | Help panel (every binding, including the hidden ones) |
| `r` | Refresh — drop the scan cache and rescan |
| `/` | Search / filter runs (`tag:` `model:` `status:completed` `cost:>0.01` `after:` + free text) |
| `[` `]` | Switch the run list between **Files** and **Repository** |
| `1` `2` `3` | Focus runs / steps / details panel |
| `↑` `↓` | Browse runs — the step and detail panels follow the highlight |
| `v` | Files: verify integrity + signature. Repository: deep `fsck` |
| `t` | Edit tags on the selected run (kept outside the digest) |
| `m` | Migrate a v1/legacy `.tine` to v2 (one-way; in place or to a new path) |
| `b` | Set a budget (`max_cost`/`max_usage`/`max_steps`/`max_duration`, breach mode, strict cost) |
| `s` | Sign the selected run (HMAC-SHA256 or Ed25519, via env var / key file) |
| `k` | Generate an Ed25519 keypair (refuses to replace an existing key) |
| `f` | Fork — from the selected step (file) or the selected event (repository) |
| `c` | Write a cached replay from the selected step |
| `d` | Files: field-level diff. Repository: semantic diff between two runs |
| `x` | Resume selected run when `manifest.resume` is true |
| `h` | Launch a new external harness run |
| `Ctrl+F` | Fork selected step and launch a harness |
| `Ctrl+R` | Replay from selected step with a harness |
| `i` | Import a `.tine` file into the repository (`tine migrate-v3`) |
| `p` | Promote a repository run to `promotions/<name>` |
| `e` | Record an evaluation attestation (scores) on a repository run |
| `l` | Walk a ref's event ancestry |
| `o` | Inspect a verified v3 object by id |
| `E` | Export the selected run as an OpenTelemetry GenAI (OTLP/JSON) document |
| `I` | Import an OTel / JSONL / framework trace as a run |

Write operations and external harness launches require an explicit confirmation
modal before they run.

## What's new for opentine 0.5.0

- **Export to OpenTelemetry** (`E`) — renders the selected run as GenAI spans in a
  complete OTLP/JSON document, so a verified run can be shipped to whatever
  observability backend runs beside it. Works on a portable artifact and a
  repository run alike, because the exporter takes anything with steps.
- **Import a foreign trace** (`I`) — OpenTelemetry (OTLP/JSON or spans), OpenTine
  JSONL, or a framework's logs (LangChain, LlamaIndex, AutoGen, CrewAI,
  OpenAI-Agents), written as a portable `.tine` artifact, into the v3 repository,
  or both. It mirrors `tine import` exactly: at least one destination is
  required, a `--save`-only import builds in a throwaway repository so none is
  left behind, and capture stays off because the provenance belongs to the
  machine that produced the trace.
- Import and export are inverses, and there is a test holding the dashboard to
  that: a run exported and re-imported comes back with the same steps.

## What's new for opentine 0.4.0

- **Fork provenance.** 0.4.0 derives a v2 fork id from the fork *act* — source
  lineage, retained slice, branch, declared intent and a 128-bit nonce — and
  records that basis in the artifact. The details panel shows it and reports
  whether the id re-derives from the record: `verified`, `MISMATCH` (the record
  was edited), or no verdict at all for a pre-0.4.0 fork, which is never
  presented as tampering.
- **Forking the same point twice now works.** It used to produce one id and one
  filename, so the second fork silently destroyed the first. The dashboard no
  longer needs its own guard against that, and the run list shows both.
- **Cached replay stays reproducible.** A replay reuses recorded steps and
  produces nothing new, so it forks with an empty nonce and lands on one id;
  replaying twice is still refused rather than duplicated, and the panel labels it
  "reproducible fork — a replay, not a divergence".

## What's new for opentine 0.3.0

- **v3 repository browser** — a second source tab lists the run objects in a
  `.tine/` repository with their refs, event counts, cost, evaluation score, fork
  and shallow-boundary markers. Selecting one materializes it through the same
  step tree and detail panels the file list uses.
- **Repository actions** — deep `fsck` (`v`), event log (`l`), object inspection
  (`o`), semantic diff (`d`), fork from an event (`f`), promotion with the
  compare-and-swap opentine actually enforces (`p`), evaluation attestations
  (`e`), and `migrate-v3` import of a portable artifact (`i`), which stays
  fail-closed on a source that does not verify.
- **Honest accounting** — token counts include cache reads, 5m/1h cache writes and
  reasoning tokens rather than input+output alone; per-step cost reads
  `billing.known_subtotal_usd` the way `Run.total_cost` does; and a step opentine
  could not price is marked *unpriced* instead of rendering as `$0.0000`. The run
  summary shows pricing provenance and the new `by_ref` cost attribution.
- **Budgets** — `strict_cost` (an unpriced step counts as a breach) is editable and
  displayed, and a breach recorded in the run is surfaced alongside the re-derived
  one, since `cost_completeness` cannot be recomputed from the artifact.

### Non-goals for this release

Remote sync (`fetch` / `push` / `clone` / `serve`) and the live trace `Recorder`
are deliberately not wired up: the transfer path is fully blocking with no
progress reporting, and the recorder mints a new run object per appended event.
Both need a design of their own rather than a keybinding.

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
pip install -e ".[dev]"   # resolves opentine>=0.5,<0.6 from PyPI

ruff check .
ruff format --check .
pytest
```

Tests run against the **installed** opentine, so a green suite says something
about what users get. To test against a source checkout instead — a worktree at a
release tag, say — point `OPENTINE_SRC` at it:

```bash
git -C ../opentine worktree add /tmp/opentine-v050 v0.5.0
OPENTINE_SRC=/tmp/opentine-v050 pytest
```

CI runs lint, format, the test suite, a packaging build, and a `twine` metadata
check across Linux/macOS/Windows on Python 3.11–3.14, resolving opentine from
PyPI through the pin in `pyproject.toml` — so CI tests what users install.
Pushing a `v*` tag builds release artifacts, attests their provenance, and
attaches them to a GitHub release.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup, the gates CI runs, and the
conventions that keep a malformed artifact from taking the dashboard down.
Security issues go to [SECURITY.md](SECURITY.md), not the issue tracker.

## License

Apache 2.0
