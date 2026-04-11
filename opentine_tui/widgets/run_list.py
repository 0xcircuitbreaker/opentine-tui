"""Run list widget — sortable DataTable of all runs."""

from __future__ import annotations

from textual.widgets import DataTable
from textual.message import Message

from opentine.core import Run, RunStatus

BRAND = "#FF6900"
STATUS_COLORS = {
    "completed": "green",
    "failed": "red",
    "paused": "yellow",
    "running": "cyan",
}


class RunSelected(Message):
    def __init__(self, run_id: str) -> None:
        super().__init__()
        self.run_id = run_id


class RunList(DataTable):
    BINDINGS = [
        ("enter", "select_run", "View run"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._runs: list[Run] = []

    def on_mount(self) -> None:
        self.add_columns("ID", "Status", "Model", "Steps", "Cost")
        self.cursor_type = "row"

    def update_runs(self, runs: list[Run]) -> None:
        self._runs = runs
        self.clear()
        for run in runs:
            sc = STATUS_COLORS.get(run.status.value, "white")
            self.add_row(
                run.id,
                f"[{sc}]{run.status.value}[/]",
                run.model_info,
                str(len(run.steps)),
                f"${run.total_cost:.4f}",
            )

    def action_select_run(self) -> None:
        if self.cursor_row < len(self._runs):
            run = self._runs[self.cursor_row]
            self.post_message(RunSelected(run.id))
