# Changelog

All notable changes to opentine-tui are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

[0.1.0]: https://github.com/0xcircuitbreaker/opentine-tui/releases/tag/v0.1.0
