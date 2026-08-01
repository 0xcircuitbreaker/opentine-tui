"""Repository run list — DataTable of run objects in a v3 `.tine` repository."""

from __future__ import annotations

from textual.message import Message
from textual.widgets import DataTable

from opentine_tui.formatting import cost_str
from opentine_tui.formatting import escape_markup as escape
from opentine_tui.v3 import RepoRunRecord

STATUS_COLORS = {
    "completed": "green",
    "failed": "red",
    "paused": "yellow",
    "running": "cyan",
    "unknown": "white",
}
STATUS_LABELS = {
    "completed": "done",
    "failed": "fail",
    "paused": "pause",
    "running": "run",
    "unknown": "?",
}


def score_str(score: float | None) -> str:
    """Evaluation score from attestations, or blank when nothing evaluated it."""
    if score is None:
        return ""
    color = "green" if score >= 0.8 else "yellow" if score >= 0.5 else "red"
    return f"[{color}]{score:.2f}[/]"


def record_flags(record: RepoRunRecord) -> str:
    parts: list[str] = []
    if record.load_error:
        parts.append("[red]![/]")
    if record.is_fork:
        parts.append("[#FF6900]Y[/]")
    if record.shallow:
        parts.append("[yellow]~[/]")  # events cut by a shallow boundary
    return " ".join(parts)


class RepoRunSelected(Message):
    def __init__(self, oid: str) -> None:
        super().__init__()
        self.oid = oid


class RepoList(DataTable):
    BINDINGS = [
        ("enter", "select_run", "View run"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._records: list[RepoRunRecord] = []
        self._columns_added = False
        # See RunList: repainting re-highlights, and re-announcing the same run
        # would reset the open step and clobber an action's result every poll.
        self._announced: str | None = None

    def on_mount(self) -> None:
        self._ensure_columns()
        self.cursor_type = "row"

    def update_records(self, records: list[RepoRunRecord]) -> None:
        self._ensure_columns()
        highlighted = self.highlighted_key
        self._records = records
        self.clear()
        for record in records:
            color = STATUS_COLORS.get(record.status, "white")
            label = STATUS_LABELS.get(record.status, record.status)
            ref = record.ref_label
            if len(ref) > 14:
                ref = ref[:13] + "…"
            self.add_row(
                escape(record.short_id[:10]),
                f"[{color}]{label}[/]",
                str(record.event_count),
                cost_str(record.cost) if record.cost else "",
                score_str(record.score),
                f"[dim]{escape(ref)}[/]" if ref else "",
                record_flags(record),
                key=record.key,
            )
        if highlighted is not None:
            self.highlight_key(highlighted)
            self._announced = highlighted

    @property
    def highlighted_key(self) -> str | None:
        if not self.is_valid_row_index(self.cursor_row):
            return None
        try:
            return self.coordinate_to_cell_key(self.cursor_coordinate).row_key.value
        except Exception:  # pragma: no cover - defensive
            return None

    def highlight_key(self, key: str) -> bool:
        try:
            row_index = self.get_row_index(key)
        except Exception:
            return False
        self.move_cursor(row=row_index)
        return True

    def action_select_run(self) -> None:
        key = self.highlighted_key
        if key is not None:
            self._announced = key
            self.post_message(RepoRunSelected(key))

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        event.stop()
        self.action_select_run()

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        event.stop()
        # An empty table emits this with `row_key` itself None.
        key = getattr(event.row_key, "value", None)
        # See RunList: a highlight queued during a repaint is stale by delivery.
        if key is None or key != self.highlighted_key or key == self._announced:
            return
        self._announced = key
        self.post_message(RepoRunSelected(key))

    def _ensure_columns(self) -> None:
        if not self._columns_added:
            self.add_columns("Run", "Status", "Ev", "Cost", "Score", "Ref", "")
            self._columns_added = True
