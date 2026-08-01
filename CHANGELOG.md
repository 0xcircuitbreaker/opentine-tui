# Changelog

All notable changes to opentine-tui are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.5.0] - 2026-08-01

Tracks opentine 0.5.0, the Ecosystem Release. Requires `opentine>=0.5,<0.6`.

### Added
- **Export to OpenTelemetry** (`E`). Renders the selected run as GenAI spans in a
  complete OTLP/JSON document via `to_otel_genai_document`, so a verified run can
  be shipped to whatever observability backend runs beside it. A portable
  artifact and a repository run both work unchanged — the exporter takes anything
  with steps.
- **Import a foreign trace** (`I`). OpenTelemetry (OTLP/JSON or spans), OpenTine
  JSONL, or a framework's logs (LangChain, LlamaIndex, AutoGen, CrewAI,
  OpenAI-Agents), written as a portable `.tine` artifact, into the v3 repository,
  or both. It mirrors `tine import`: at least one destination is required, an
  artifact-only import builds in a throwaway repository so none is left behind,
  and capture stays off because the provenance belongs to the machine that
  produced the trace. A test pins the format list to the CLI's own, so a format
  added there fails the suite instead of going unnoticed.
- A round-trip test holds the dashboard to opentine's claim that import and
  export are inverses.

### Fixed
- **A poll discarded the step you were reading.** Repainting the run list clears
  and re-adds every row, which re-highlighted the cursor and re-announced the
  same run — so every five seconds the open step was deselected and the detail
  panel snapped back to the run summary. A row is now announced only when the
  selection actually changes, and a highlight queued mid-repaint is discarded as
  stale. This also stops an action's result being overwritten a moment after it
  appears, and saves a full step-tree rebuild on every tick.

## [0.4.0] - 2026-07-31

Tracks opentine 0.4.0, which changed how a portable fork's identity is derived.
Requires `opentine>=0.4,<0.5`.

### Added
- **Fork provenance in the details panel.** 0.4.0 derives a v2 fork id from the
  fork *act* and records that basis in `metadata.fork`. The panel shows the
  source, branch and reason, and reports whether the id re-derives from the
  record — `verified`, `MISMATCH` when the record was edited, and no verdict at
  all for a pre-0.4.0 fork, which is never presented as tampering.
- `tests/test_v040.py` covers the new identity surface, including an edited basis
  and an artifact with no basis recorded.

### Fixed
- **Cached replay was no longer reproducible.** 0.4.0 gives every fork act a random
  nonce, so the dashboard's replay wrote a fresh artifact each time and its
  overwrite refusal never fired. Replay now forks with
  `intent={"replay": "cache"}, nonce=""` — byte-identical to what
  `tine replay --mode cache` does, so both land on the same artifact for the same
  run and step, and the second attempt is still refused.
- **The fork panel could show a lineage the verified badge did not cover.**
  `metadata.forked_from` / `fork_point` sit outside the integrity digest, while
  the identity verdict covers `metadata.fork`; reading the first and printing the
  second let an edited artifact display any ancestry under a green *verified*.
  The lineage now comes from the verified record, and is marked *(claimed)*
  whenever there is no verdict.
- **Pressing ↑ on an empty run list crashed the dashboard out to the shell.**
  Textual emits `RowHighlighted` on an empty table with `row_key` itself `None`,
  which the new arrow-key browsing dereferenced — so the very first keystroke of
  a first-time user, on a directory with no runs, killed the app.
- **Startup left the highlight and the selection on different runs.** Both lists
  highlight a row as soon as they are populated and those messages are queued, so
  the repository list's took over the shared panels while the Files tab showed a
  highlighted row; every file action then reported "No run selected". The panels
  now follow the active source, and switching tabs re-points them.
- **File-only actions on the Repository tab claimed nothing was selected.** They
  now say what they actually are — edits to a portable artifact, which a
  content-addressed v3 object is not — and name the way out.
- A freshly `tine init`-ed repository with no runs is no longer told to run
  `tine init`.
- **Forking onto an existing ref in the repository moved it silently.**
  `fork_run` compare-and-swaps against the ref's current value, so typing
  `heads/main` retargeted the branch. It is now refused unless the move is
  confirmed, matching what promotion already did. A fork that opentine
  deduplicated is also reported as an existing run rather than a new one.
- Forking the same point twice is now expected to succeed with two distinct runs,
  so the dashboard no longer treats the second fork as a collision. The overwrite
  refusal still applies to an explicitly named destination.

### Changed
- `tests/conftest.py` no longer prepends a sibling `../opentine` checkout to
  `sys.path`. That shadowed whatever CI installed and made the version pin
  untestable; tests now run against the installed package, with `OPENTINE_SRC`
  to point at a source checkout deliberately.
- CI and the release workflow no longer install opentine from git at all. They
  had used an unpinned `@main`, which resolved to a development line ahead of the
  release; opentine 0.4.0 is on PyPI, so the pin in `pyproject.toml` now does the
  work and CI tests exactly what `pip install opentine-tui` produces.
- The CI matrix and the classifiers agree: Python 3.11–3.14, `requires-python`
  `>=3.11,<3.15`, matching opentine's own range. `textual` gained an upper bound.
- Stricter gates: ruff adds bugbear/comprehension/simplify/pathlib/ruff rules, and
  pytest now fails on warnings and unknown markers/config.
- The release workflow now publishes to PyPI via Trusted Publishing. It only ever
  attached artifacts to a GitHub release, which is why PyPI still served 0.1.0
  while the project shipped 0.2 and 0.3 — `pip install opentine-tui`, the very
  first line of the README, installed something three minor versions behind. The
  one-time PyPI/GitHub environment setup is documented in the workflow.
- Added `SECURITY.md` (with an explicit scope split against opentine's own
  reporting) and `CONTRIBUTING.md`.

## [0.3.0] - 2026-07-30

Support for opentine 0.3.0: the git-shaped v3 `.tine/` repository beside the
portable v2 files, plus the accounting and write-safety gaps that upgrade exposed.
Requires `opentine>=0.3`.

### Added
- **v3 repository source** — the left panel is now two tabs (`[` / `]`): *Files*
  for portable `.tine` artifacts and *Repository* for run objects in a `.tine/`
  repository, discovered by walking up from the working directory the way `tine`
  does, or named with `--repo`. Both feed the same step tree and detail panels,
  because `repo.load_run()` returns the same run the file reader does.
- **Repository actions** — deep `fsck` on `v`, event log on `l`, verified object
  inspection on `o`, semantic diff on `d`, fork from the highlighted event on `f`,
  promotion on `p` (compare-and-swap: moving an existing promotion is a separate,
  confirmed decision), evaluation attestations on `e`, and `migrate-v3` import on
  `i`, which stays fail-closed on a source that does not verify.
- **`--repo` and `--version`** flags, and a help panel on `?` with a tooltip on
  every binding — 16 of 22 actions were previously hidden with no way to find them.
- **Age column**, arrow-key browsing (the panels follow the highlight instead of
  waiting for Enter), and guidance when the runs directory is missing, empty, or
  filtered to nothing.

### Fixed
- **Recorded output containing `[/]` crashed the dashboard.** `rich.markup.escape`
  is not a correct escape for Textual 8's markup — and neither is
  `textual.markup.escape`; both leave brackets the `Content` parser then reads as
  tags, so a model that printed `[bold red]…[/]` raised `MarkupError` out of the
  detail panel. All four widgets now share one escape that neutralizes every `[`
  and leaves the text otherwise byte-identical.
- **The step tree showed only the first step.** Child nodes are created collapsed,
  and only the root was expanded, so a five-step run rendered as one line. Merge
  steps now also name the parents they are not drawn under instead of silently
  dropping those edges.
- **A budget opentine refuses killed the app.** `run.budget()` raises on an
  `on_breach: "warn"`, a fractional `max_usage` or a negative `max_steps` that a
  valid-loading artifact can still contain; it raised straight out of run
  selection. The panel now reports the bad budget.
- **Token counts ignored most of what is billed.** Cache reads, 5m/1h cache writes
  and reasoning tokens were dropped, matching neither `Run.total_tokens` nor the
  bill. Per-step cost now reads `billing.known_subtotal_usd` the way
  `Run.total_cost` does, and a step opentine could not price is marked *unpriced*
  rather than rendering as `$0.0000`.
- **Pricing provenance never rendered** — `manifest.pricing.catalogs` is a list,
  not a mapping, and `rate_cards` is keyed by step id, so the block was dead code
  printing 64-character hashes as "rate cards".
- **Budgets dropped `strict_cost`** on every edit, disarming the stop-on-unpriced
  guard, and the panel could never show a `cost_completeness` breach because
  `Budget.check` cannot re-derive one. Both are now editable and displayed.
- **Write safety now matches the CLI's refuse-by-default posture.** A cached
  replay wrote a fixed `<id>-replay.tine` that destroyed the previous one; a run
  id from an untrusted artifact became a filename, so `../../…` escaped the runs
  directory; `keygen` replaced an existing private key without asking and left the
  seed briefly world-readable; signing to an existing path clobbered it. Two HMAC
  key sources are now refused rather than ranked, as `tine verify` does. A failed
  tag or budget write no longer leaves the edit on the cached run for a later save
  to commit silently.
- **The dashboard no longer freezes on every poll.** Scanning caches by
  (mtime, size), the search index is built once per artifact instead of per filter
  call, the repository listing is cached against the object set, and the scan plus
  every mutating action runs off the event loop. A 200-run directory cost ~2.6 s of
  blocked UI every 5 s; a warm rescan is now ~5 ms.
- Selecting a run no longer freezes its DAG at selection time, a deleted artifact
  drops its selection instead of being recreated by the next action, and the
  cursor stays on the run you were looking at across a background rescan.
- `status:done` in the search hint could never match; the status values are
  `completed`/`failed`/`running`/`paused`.
- The harness list is read from the installed opentine (0.3.0 adds `grok` and
  `gemini`) instead of a hard-coded copy, and harness/algorithm/breach-mode
  choices are `Select`s and `Switch`es rather than free-text `true/false` fields.

## [0.2.0] - 2026-06-29

Support for the opentine `.tine` **format v2** surface. Requires `opentine>=0.2`.

### Added
- **Format migration** — the run list classifies each file's on-disk version from
  raw JSON (`detect_version`/`is_legacy_linear`), since a loaded run always reports
  v2. A `Flags` column marks v1/legacy (upgradeable) files, and `m` migrates a
  v1/legacy artifact to v2 in place (one-way; warns when a signature will be
  dropped, and gates non-legacy sources on an integrity check with a Force escape).
- **Run tags + search** — a `Tags` column, a `t` tag editor (add/remove, persisted
  outside the digest so integrity/signatures survive), and a `/` search bar backed
  by the opentine query DSL (`tag:`/`model:`/`status:`/`cost:`/`after:`/`before:` +
  free text). Corrupt rows stay visible while filtering.
- **Cost + budget** — per-step token usage in the step tree, total tokens and a
  `cost_breakdown` (by model and kind) in the details panel, and a `b` modal to set
  a budget (`max_cost`/`max_usage`/`max_steps`/`max_duration` + breach mode). The
  breach state is re-derived from the loaded run rather than trusting the stored,
  unsigned `metadata.budget_state`.
- **Autosave / drafts** — draft checkpoints are detected from the artifact (not the
  loaded run), badged in the list and details, and never offered for signing.
- **Signing (`tine-sig/1`)** — `v` now also verifies a present signature (prompting
  for an HMAC key, an Ed25519 public key, or trust-on-first-use), `s` signs a
  terminal run (HMAC-SHA256 always; Ed25519 with `opentine-tui[crypto]`), and `k`
  generates an Ed25519 keypair. The authenticity block distinguishes
  `verified`/`verified-tofu`/`no-key`/`mismatch` and labels `signer` as display-only.
- **Field-level diff** — `d` now renders the new `RunDiff.changed` list with
  per-field before/after deltas and a cost/usage drift summary, plus drift-only vs
  content change detection.

### Fixed
- Modal-driving actions (including the existing fork/diff/resume/harness actions)
  now run under `@work`. Under Textual 8.x, `push_screen_wait` raises
  `NoActiveWorker` outside a worker, which had silently broken every confirmation
  and input modal.

### Changed
- Dependency bumped to `opentine>=0.2,<0.3`; added an optional `crypto` extra
  (`opentine-tui[crypto]`) for Ed25519. The details panel is now scrollable and the
  run list widened to fit the new columns.

## [0.1.0] - 2026-06-25

First public release.

### Added
- Three-panel Textual dashboard (runs / steps / details) for `.tine` run artifacts.
- Run list that keeps corrupt `.tine` files visible with their load or integrity error.
- Step DAG view with per-step cost and duration, and a detail panel for run and step
  metadata, inputs, outputs, and errors.
- Integrity verification (`v`) backed by `opentine`'s SHA-256 manifest digest.
- Write actions with confirmation modals: fork (`f`), cache replay (`c`),
  diff (`d`), and scoped resume (`x`).
- External harness launches: run (`h`), fork + harness (`Ctrl+F`), and
  replay + harness (`Ctrl+R`), covering the codex, claude-code, cursor, opencode,
  openclaw, kimi-code, hermes, pi, and generic adapters.
- `--runs-dir` flag and `OPENTINE_RUNS_DIR` environment variable for selecting the
  artifact directory.
- GitHub Actions CI (lint, format, tests, build, metadata check) across
  Linux/macOS/Windows on Python 3.11–3.13, and a tag-driven release-artifacts
  workflow.

### Requirements
- Requires `opentine>=0.1.1`, the first opentine release that ships the
  `opentine.harnesses` integration layer the dashboard depends on.

[0.2.0]: https://github.com/0xcircuitbreaker/opentine-tui/releases/tag/v0.2.0
[0.1.0]: https://github.com/0xcircuitbreaker/opentine-tui/releases/tag/v0.1.0
