"""Step detail widget — show inputs, outputs, cost, duration for a selected step."""

from __future__ import annotations

from textual.widgets import Static

from opentine.core import Run


class StepDetail(Static):
    def __init__(self) -> None:
        super().__init__("[dim]Select a step to view details[/]")
        self._run: Run | None = None

    def set_run(self, run: Run) -> None:
        self._run = run
        if not run.steps:
            self.update("[dim](no steps)[/]")
            return
        self._show_summary(run)

    def _show_summary(self, run: Run) -> None:
        lines = [
            f"[bold]Run:[/] {run.id}",
            f"[bold]Model:[/] {run.model_info}",
            f"[bold]Status:[/] {run.status.value}",
            f"[bold]Steps:[/] {len(run.steps)}",
            f"[bold]Cost:[/] ${run.total_cost:.4f}",
            f"[bold]Duration:[/] {run.total_duration:.1f}s",
            "",
            f"[dim]Prompt:[/]",
            f"  {run.user_prompt[:200]}",
        ]
        if run.metadata.get("forked_from"):
            lines.append(
                f"\n[#FF6900]Forked from:[/] {run.metadata['forked_from']} "
                f"at step {run.metadata.get('fork_point', '?')}"
            )
        self.update("\n".join(lines))
