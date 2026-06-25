"""opentine-tui — Terminal dashboard for opentine agent runs."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from opentine_tui.app import OpentineTUI

BRAND = "#FF6900"


def build_parser(prog: str | None = None) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=prog,
        description="Terminal management console for opentine .tine runs",
    )
    parser.add_argument(
        "--runs-dir",
        help="Directory containing .tine artifacts (default: OPENTINE_RUNS_DIR or .tine_runs)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    app = OpentineTUI(runs_dir=args.runs_dir)
    app.run()


if __name__ == "__main__":
    main()
