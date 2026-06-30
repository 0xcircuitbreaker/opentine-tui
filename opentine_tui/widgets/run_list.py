"""Run list widget — DataTable of saved and corrupt runs."""

from __future__ import annotations

from rich.markup import escape
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
            tags = ", ".join(record.tags)
            if len(tags) > 16:
                tags = tags[:15] + "…"
            self.add_row(
                escape(record.short_id[:10]),
                f"[{color}]{STATUS_LABELS.get(status, status)}[/]",
                str(record.step_count) if record.run else "",
                cost_str(record.total_cost),
                f"[dim]{escape(tags)}[/]" if tags else "",
                record_flags(record),
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
            self.add_columns("Run", "Status", "Stp", "Cost", "Tags", "Flags")
            self._columns_added = True
