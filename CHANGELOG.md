# Changelog

All notable changes to opentine-tui are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
