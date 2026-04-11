"""Opentine TUI — Terminal dashboard for managing agent runs.

Three-panel layout inspired by lazygit:
  Left:   Run list (DataTable)
  Center: Step tree for selected run
  Right:  Run/step details + actions
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Header, Footer, Static

from opentine.core import Run, RunStatus
from opentine.pool import RunPool

from opentine_tui.widgets.run_list import RunList, RunSelected
from opentine_tui.widgets.step_tree import StepTree
from opentine_tui.widgets.step_detail import StepDetail

BRAND = "#FF6900"

RUNS_DIR = Path(".tine_runs")


def _load_saved_runs() -> list[Run]:
    runs = []
    if RUNS_DIR.exists():
        for f in sorted(RUNS_DIR.glob("*.tine"), key=lambda f: f.stat().st_mtime, reverse=True):
            try:
                runs.append(Run.load(f))
            except Exception:
                pass
    return runs


class OpentineTUI(App):
    CSS = """
    Screen {
        layout: horizontal;
    }
    #left-panel {
        width: 1fr;
        min-width: 30;
        border-right: solid #FF6900;
    }
    #center-panel {
        width: 2fr;
        border-right: solid #FF6900;
    }
    #right-panel {
        width: 1fr;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
        Binding("1", "focus_left", "Focus runs"),
        Binding("2", "focus_center", "Focus steps"),
        Binding("3", "focus_right", "Focus detail"),
    ]

    TITLE = "opentine"
    SUB_TITLE = "agent run manager"

    def __init__(self) -> None:
        super().__init__()
        self._runs: list[Run] = []
        self._pool: RunPool | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            with Vertical(id="left-panel"):
                yield Static("[bold #FF6900]Runs[/]", id="runs-title")
                yield RunList()
            with Vertical(id="center-panel"):
                yield Static("[bold #FF6900]Steps[/]", id="steps-title")
                yield StepTree()
            with Vertical(id="right-panel"):
                yield Static("[bold #FF6900]Details[/]", id="detail-title")
                yield StepDetail()
        yield Footer()

    def on_mount(self) -> None:
        self._runs = _load_saved_runs()
        run_list = self.query_one(RunList)
        run_list.update_runs(self._runs)
        self.set_interval(2.0, self._refresh)

    def on_run_selected(self, event: RunSelected) -> None:
        run = next((r for r in self._runs if r.id == event.run_id), None)
        if run:
            step_tree = self.query_one(StepTree)
            step_tree.load_run(run)
            step_detail = self.query_one(StepDetail)
            step_detail.set_run(run)

    def _refresh(self) -> None:
        self._runs = _load_saved_runs()
        run_list = self.query_one(RunList)
        run_list.update_runs(self._runs)

    def action_refresh(self) -> None:
        self._refresh()

    def action_focus_left(self) -> None:
        self.query_one(RunList).focus()

    def action_focus_center(self) -> None:
        self.query_one(StepTree).focus()

    def action_focus_right(self) -> None:
        self.query_one(StepDetail).focus()
