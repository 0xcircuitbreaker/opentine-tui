"""Step detail widget — show run/action/step details."""

from __future__ import annotations

import json
from typing import Any

from opentine.core import Run, Step
from rich.markup import escape
from textual.widgets import Static

from opentine_tui.repository import RunRecord


def _render_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, indent=2, sort_keys=True)
    except TypeError:
        return repr(value)


class StepDetail(Static):
    def __init__(self) -> None:
        super().__init__("[dim]Select a step to view details[/]")
        self._run: Run | None = None
        self.last_rendered = "[dim]Select a step to view details[/]"

    def set_run(self, record: RunRecord) -> None:
        run = record.run
        self._run = run
        if run is None:
            self._set_text(
                "\n".join(
                    [
                        f"[bold red]Corrupt run:[/] {escape(record.path.name)}",
                        f"[bold]Path:[/] {escape(str(record.path))}",
                        "",
                        escape(record.error_message),
                    ]
                )
            )
            return
        if not run.steps:
            self._set_text("[dim](no steps)[/]")
            return
        self._show_summary(record)

    def set_step(self, run: Run, step: Step) -> None:
        parent_ids = getattr(step, "parent_ids", None)
        if parent_ids is None:
            parent_id = getattr(step, "parent_id", None)
            parent_ids = [parent_id] if parent_id else []
        parent_text = ", ".join(parent[:12] for parent in parent_ids) or "-"
        step_short_id = getattr(step, "short_id", step.id[:12])
        lines = [
            f"[bold]Step:[/] {escape(step_short_id)}",
            f"[bold]Kind:[/] {escape(step.kind.value)}",
            f"[bold]Parents:[/] {escape(parent_text)}",
            f"[bold]Model:[/] {escape(step.model_info)}",
            f"[bold]Cost:[/] ${step.cost:.4f}",
            f"[bold]Duration:[/] {step.duration:.1f}s",
            "",
            "[bold #FF6900]Inputs[/]",
            escape(_render_value(step.inputs)),
            "",
            "[bold #FF6900]Outputs[/]",
            escape(_render_value(step.outputs)),
        ]
        error = getattr(step, "error", {})
        if error:
            lines.extend(["", "[bold red]Error[/]", escape(_render_value(error))])
        self._run = run
        self._set_text("\n".join(lines))

    def set_message(self, title: str, message: str, ok: bool = True) -> None:
        color = "green" if ok else "red"
        self._set_text(f"[bold {color}]{escape(title)}[/]\n\n{escape(message)}")

    def _show_summary(self, record: RunRecord) -> None:
        run = record.run
        if run is None:
            return
        resumable = "yes" if getattr(run, "manifest", {}).get("resume", False) else "no"
        lines = [
            f"[bold]Run:[/] {escape(run.id)}",
            f"[bold]File:[/] {escape(record.path.name)}",
            f"[bold]Model:[/] {escape(run.model_info)}",
            f"[bold]Status:[/] {escape(run.status.value)}",
            f"[bold]Steps:[/] {len(run.steps)}",
            f"[bold]Cost:[/] ${run.total_cost:.4f}",
            f"[bold]Duration:[/] {run.total_duration:.1f}s",
            f"[bold]Resumable:[/] {resumable}",
            "",
            "[dim]Prompt:[/]",
            f"  {escape(run.user_prompt[:200])}",
        ]
        if record.is_corrupt:
            lines.extend(["", f"[red]Integrity:[/] {escape(record.error_message)}"])
        if run.metadata.get("forked_from"):
            lines.append(
                f"\n[#FF6900]Forked from:[/] {escape(str(run.metadata['forked_from']))} "
                f"at step {escape(str(run.metadata.get('fork_point', '?')))}"
            )
        self._set_text("\n".join(lines))

    def _set_text(self, text: str) -> None:
        self.last_rendered = text
        self.update(text)
