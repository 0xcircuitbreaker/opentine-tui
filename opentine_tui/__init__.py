"""opentine-tui — Terminal dashboard for opentine agent runs."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from opentine_tui.app import OpentineTUI

BRAND = "#FF6900"


def _versions() -> str:
    from importlib.metadata import PackageNotFoundError, version

    def installed(name: str) -> str:
        try:
            return version(name)
        except PackageNotFoundError:  # pragma: no cover - source checkout
            return "unknown"

    try:
        from opentine import __version__ as opentine_version
    except Exception:  # pragma: no cover - defensive
        opentine_version = "not installed"
    return f"opentine-tui {installed('opentine-tui')} (opentine {opentine_version})"


def build_parser(prog: str | None = None) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=prog,
        description="Terminal management console for opentine runs",
    )
    parser.add_argument(
        "--runs-dir",
        help="Directory containing .tine artifacts (default: OPENTINE_RUNS_DIR or .tine_runs)",
    )
    parser.add_argument(
        "--repo",
        help=(
            "Path inside a v3 .tine repository (default: search upward from the "
            "working directory, as `tine` does)"
        ),
    )
    parser.add_argument("--version", action="version", version=_versions())
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    app = OpentineTUI(runs_dir=args.runs_dir, repo_path=args.repo)
    app.run()


if __name__ == "__main__":
    main()
