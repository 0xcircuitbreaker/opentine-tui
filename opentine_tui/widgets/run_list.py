"""Run list widget — DataTable of saved and corrupt runs."""

from __future__ import annotations

from textual.message import Message
from textual.widgets import DataTable

from opentine_tui.repository import RunRecord

BRAND = "#FF6900"
STATUS_COLORS = {
    "completed": "green",
    "failed": "red",
    "paused": "yellow",
    "running": "cyan",
    "corrupt": "red",
    "unknown": "white",
}
STATUS_LABELS = {
    "completed": "done",
    "failed": "fail",
    "paused": "pause",
    "running": "run",
    "corrupt": "corrupt",
    "unknown": "?",
}


class RunSelected(Message):
    def __init__(self, run_key: str) -> None:
        super().__init__()
        self.run_key = run_key


class RunList(DataTable):
    BINDINGS = [
        ("enter", "select_run", "View run"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._records: list[RunRecord] = []
        self._columns_added = False

    def on_mount(self) -> None:
        self._ensure_columns()
        self.cursor_type = "row"

    def update_records(self, records: list[RunRecord]) -> None:
        self._ensure_columns()
        self._records = records
        self.clear()
        for record in records:
            status = record.status_value
            color = STATUS_COLORS.get(status, "white")
            self.add_row(
                record.short_id[:10],
                f"[{color}]{STATUS_LABELS.get(status, status)}[/]",
                str(record.step_count) if record.run else "",
            )

    def action_select_run(self) -> None:
        if 0 <= self.cursor_row < len(self._records):
            record = self._records[self.cursor_row]
            self.post_message(RunSelected(record.key))

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        event.stop()
        self.action_select_run()

    def _ensure_columns(self) -> None:
        if not self._columns_added:
            self.add_columns("Run", "Status", "Steps")
            self._columns_added = True
