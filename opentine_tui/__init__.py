"""opentine-tui — Terminal dashboard for opentine agent runs."""

from opentine_tui.app import OpentineTUI

BRAND = "#FF6900"


def main() -> None:
    app = OpentineTUI()
    app.run()


if __name__ == "__main__":
    main()
