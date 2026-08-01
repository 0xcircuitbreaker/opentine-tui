"""Run list widget — DataTable of saved and corrupt runs."""

from __future__ import annotations

import time

from textual.message import Message
from textual.widgets import DataTable

from opentine_tui.formatting import escape_markup as escape
from opentine_tui.formatting import relative_time
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

# signature state -> (glyph, color). Mirrors opentine SignatureResult.state.
SIGNATURE_GLYPHS = {
    "verified": ("✓", "green"),
    "verified-tofu": ("~", "yellow"),
    "no-key": ("?", "blue"),
    "mismatch": ("x", "red"),
    "error": ("x", "red"),
}


def cost_str(value: float) -> str:
    if not value:
        return ""
    return f"${value:.4f}" if value < 0.01 else f"${value:.3f}"


def record_flags(record: RunRecord) -> str:
    """Compact, color-coded markers: migration / draft / signature."""
    parts: list[str] = []
    if record.is_future or record.version_label == "unsupported":
        parts.append("[red]![/]")
    elif record.is_legacy:
        parts.append("[yellow]L[/]")  # legacy 0.1.0, importable
    elif record.on_disk_version == 1:
        parts.append("[yellow]1[/]")  # v1, re-save upgrades to v2
    if record.is_draft:
        parts.append("[yellow]D[/]")
    if record.has_signature:
        glyph, color = SIGNATURE_GLYPHS.get(record.sig_state, ("?", "blue"))
        parts.append(f"[{color}]{glyph}[/]")
    return " ".join(parts)


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
        # The row the app was last told about. Repainting clears and re-adds every
        # row, which re-highlights and would re-announce the same run — resetting
        # the step the user had open, and overwriting an action's result, on every
        # poll tick.
        self._announced: str | None = None

    def on_mount(self) -> None:
        self._ensure_columns()
        self.cursor_type = "row"

    def update_records(self, records: list[RunRecord]) -> None:
        self._ensure_columns()
        # A background rescan must not move the cursor out from under the user, so
        # the highlighted run is restored by key rather than by row index.
        highlighted = self.highlighted_key
        self._records = records
        self.clear()
        now = time.time()
        for record in records:
            status = record.status_value
            color = STATUS_COLORS.get(status, "white")
            tags = ", ".join(record.tags)
            if len(tags) > 12:
                tags = tags[:11] + "…"
            age = relative_time(now - record.mtime) if record.mtime else ""
            self.add_row(
                escape(record.short_id[:10]),
                f"[{color}]{STATUS_LABELS.get(status, status)}[/]",
                str(record.step_count) if record.run else "",
                cost_str(record.total_cost),
                f"[dim]{escape(age)}[/]",
                f"[dim]{escape(tags)}[/]" if tags else "",
                record_flags(record),
                key=record.key,
            )
        if highlighted is not None:
            self.highlight_key(highlighted)
            self._announced = highlighted

    @property
    def highlighted_key(self) -> str | None:
        """Key of the highlighted row, or None when the table is empty."""
        if not self.is_valid_row_index(self.cursor_row):
            return None
        try:
            return self.coordinate_to_cell_key(self.cursor_coordinate).row_key.value
        except Exception:  # pragma: no cover - defensive
            return None

    def highlight_key(self, key: str) -> bool:
        """Move the cursor back to ``key``; False when that row is gone."""
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
            self.post_message(RunSelected(key))

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        event.stop()
        self.action_select_run()

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        # Browsing with the arrow keys should show the run you are looking at;
        # requiring Enter made the other two panels look broken.
        event.stop()
        # An empty table still emits this, with `row_key` itself None — pressing
        # up on a directory with no runs took the whole dashboard down.
        key = getattr(event.row_key, "value", None)
        # A repaint that reorders rows queues a highlight for whatever row landed
        # under the cursor mid-rebuild; by delivery the cursor has moved back, so
        # that message is stale and acting on it would clobber the open step and
        # any action result on screen.
        if key is None or key != self.highlighted_key or key == self._announced:
            return
        self._announced = key
        self.post_message(RunSelected(key))

    def _ensure_columns(self) -> None:
        if not self._columns_added:
            self.add_columns("Run", "Status", "Stp", "Cost", "Age", "Tags", "Flags")
            self._columns_added = True
