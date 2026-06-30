"""Step detail widget — show run/action/step details."""

from __future__ import annotations

import json
from typing import Any

from opentine.core import Run, Step
from rich.markup import escape
from textual.widgets import Static

from opentine_tui.repository import RunRecord

try:
    from opentine.core import Budget
except Exception:  # pragma: no cover - defensive
    Budget = None

# signature state -> display color. Mirrors opentine SignatureResult.state.
SIG_STATE_COLORS = {
    "verified": "green",
    "verified-tofu": "yellow",
    "no-key": "blue",
    "mismatch": "red",
    "error": "red",
    "unsigned": "dim",
}


def _render_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, indent=2, sort_keys=True)
    except TypeError:
        return repr(value)


def _tokens(usage: Any) -> tuple[int, int]:
    usage = usage or {}
    return int(usage.get("input", 0) or 0), int(usage.get("output", 0) or 0)


class StepDetail(Static):
    def __init__(self) -> None:
        super().__init__("[dim]Select a step to view details[/]")
        self._run: Run | None = None
        self._record: RunRecord | None = None
        self.last_rendered = "[dim]Select a step to view details[/]"

    def set_run(self, record: RunRecord) -> None:
        self._record = record
        run = record.run
        self._run = run
        if run is None:
            if record.is_future:
                self._set_text(
                    "\n".join(
                        [
                            f"[bold yellow]Newer format:[/] {escape(record.path.name)}",
                            f"[bold]On disk:[/] {escape(record.version_label)}",
                            "",
                            "Written by a newer opentine — upgrade the tool to read it.",
                        ]
                    )
                )
                return
            self._set_text(
                "\n".join(
                    [
                        f"[bold red]Corrupt run:[/] {escape(record.path.name)}",
                        f"[bold]Path:[/] {escape(str(record.path))}",
                        f"[bold]On disk:[/] {escape(record.version_label)}",
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
        in_tok, out_tok = _tokens(getattr(step, "usage", {}))
        lines = [
            f"[bold]Step:[/] {escape(step_short_id)}",
            f"[bold]Kind:[/] {escape(step.kind.value)}",
            f"[bold]Parents:[/] {escape(parent_text)}",
            f"[bold]Model:[/] {escape(step.model_info)}",
            f"[bold]Cost:[/] ${step.cost:.4f}",
            f"[bold]Duration:[/] {step.duration:.1f}s",
        ]
        if in_tok or out_tok:
            lines.append(f"[bold]Tokens:[/] {in_tok + out_tok} (in {in_tok} / out {out_tok})")
        lines.extend(
            [
                "",
                "[bold #FF6900]Inputs[/]",
                escape(_render_value(step.inputs)),
                "",
                "[bold #FF6900]Outputs[/]",
                escape(_render_value(step.outputs)),
            ]
        )
        error = getattr(step, "error", {})
        if error:
            lines.extend(["", "[bold red]Error[/]", escape(_render_value(error))])
        self._run = run
        self._set_text("\n".join(lines))

    def set_message(self, title: str, message: str, ok: bool = True) -> None:
        color = "green" if ok else "red"
        self._set_text(f"[bold {color}]{escape(title)}[/]\n\n{escape(message)}")

    # -- run summary -------------------------------------------------------

    def _show_summary(self, record: RunRecord) -> None:
        run = record.run
        if run is None:
            return
        resumable = "yes" if getattr(run, "manifest", {}).get("resume", False) else "no"
        in_tok, out_tok = self._run_tokens(run)
        total_tokens = getattr(run, "total_tokens", in_tok + out_tok)
        status_line = f"[bold]Status:[/] {escape(run.status.value)}"
        if record.is_draft:
            status_line += "  [yellow]◆ draft checkpoint[/]"

        lines = [
            f"[bold]Run:[/] {escape(run.id)}",
            f"[bold]File:[/] {escape(record.path.name)}  [dim]({escape(record.version_label)})[/]",
            f"[bold]Model:[/] {escape(run.model_info)}",
            status_line,
            f"[bold]Steps:[/] {len(run.steps)}    [bold]Cost:[/] ${run.total_cost:.4f}"
            f"    [bold]Tokens:[/] {total_tokens}",
            f"[bold]Duration:[/] {run.total_duration:.1f}s    [bold]Resumable:[/] {resumable}",
            f"[bold]Tags:[/] {escape(', '.join(run.tags)) if run.tags else '[dim](none)[/]'}",
        ]

        if record.needs_migration:
            lines.append(
                "[yellow]Migratable:[/] re-save upgrades to v2 "
                "(one-way; 0.1.x can no longer read it). Press 'm'."
            )

        lines += self._budget_lines(run)
        lines += self._cost_breakdown_lines(run)
        lines += self._integrity_lines(record)
        lines += self._signature_lines(record)

        lines += ["", "[dim]Prompt:[/]", f"  {escape(run.user_prompt[:200])}"]

        if run.metadata.get("forked_from"):
            lines.append(
                f"\n[#FF6900]Forked from:[/] {escape(str(run.metadata['forked_from']))} "
                f"at step {escape(str(run.metadata.get('fork_point', '?')))}"
            )
        migration = run.metadata.get("migration")
        if migration:
            lines.append("\n[#FF6900]Upgrade history[/]")
            for entry in migration if isinstance(migration, list) else [migration]:
                if isinstance(entry, dict):
                    hop = f"{entry.get('from', '?')} -> {entry.get('to', '?')}"
                    tool = entry.get("tool") or entry.get("by") or ""
                    lines.append(f"  {escape(hop)}  [dim]{escape(str(tool))}[/]")
                else:
                    lines.append(f"  {escape(str(entry))}")
        self._set_text("\n".join(lines))

    @staticmethod
    def _run_tokens(run: Run) -> tuple[int, int]:
        in_tok = out_tok = 0
        for step in run.steps:
            i, o = _tokens(getattr(step, "usage", {}))
            in_tok += i
            out_tok += o
        return in_tok, out_tok

    def _budget_lines(self, run: Run) -> list[str]:
        budget = run.budget() if hasattr(run, "budget") else None
        if budget is None:
            return []
        breach = None
        if Budget is not None and hasattr(budget, "check"):
            try:
                breach = budget.check(
                    cost=run.total_cost,
                    usage=getattr(run, "total_tokens", 0),
                    steps=len(run.steps),
                    duration=run.total_duration,
                )
            except Exception:  # pragma: no cover - defensive
                breach = None
        breached_dim = breach.dimension if breach is not None else None

        def fmt(name: str, value: float) -> str:
            if name == "cost":
                return f"${value:.4f}"
            if name == "duration":
                return f"{value:.1f}s"
            return str(int(value))

        out = ["", "[bold #FF6900]Budget[/]"]
        dims = [
            ("cost", budget.max_cost, run.total_cost),
            ("usage", budget.max_usage, getattr(run, "total_tokens", 0)),
            ("steps", budget.max_steps, len(run.steps)),
            ("duration", budget.max_duration, run.total_duration),
        ]
        for name, limit, current in dims:
            if limit is None:
                continue
            label = "tokens" if name == "usage" else name
            marker = "  [red]OVER[/]" if breached_dim == name else ""
            out.append(f"  {label}: {fmt(name, current)} / {fmt(name, limit)}{marker}")
        out.append(f"  on breach: {escape(str(getattr(budget, 'on_breach', 'stop')))}")
        if breach is not None:
            out.append(f"  [red]breached {breach.dimension}: {breach.incurred} > {breach.limit}[/]")
        return out

    def _cost_breakdown_lines(self, run: Run) -> list[str]:
        if not hasattr(run, "cost_breakdown"):
            return []
        try:
            cb = run.cost_breakdown()
        except Exception:  # pragma: no cover - defensive
            return []
        if not cb.total_cost and not cb.total_tokens:
            return []
        out = ["", "[bold #FF6900]Cost breakdown[/]"]
        out.append(
            f"  total: ${cb.total_cost:.4f}  "
            f"tokens {cb.total_tokens} (in {cb.input_tokens} / out {cb.output_tokens})"
        )
        if cb.by_model:
            out.append("  by model:")
            for model, cost in sorted(cb.by_model.items(), key=lambda kv: -kv[1]):
                out.append(f"    {escape(str(model))}: ${cost:.4f}")
        if cb.by_kind:
            kinds = "  ".join(f"{escape(str(k))}=${v:.4f}" for k, v in sorted(cb.by_kind.items()))
            out.append(f"  by kind: {kinds}")
        return out

    def _integrity_lines(self, record: RunRecord) -> list[str]:
        integrity = record.integrity
        if integrity is None:
            return []
        out = ["", "[bold #FF6900]Integrity[/]"]
        if integrity.ok:
            digest = (integrity.actual or integrity.expected or "")[:12]
            out.append(f"  [green]OK[/] sha256:{digest}")
        elif record.is_legacy:
            out.append("  [yellow]legacy 0.1.0[/] — no verifiable digest; migrate to import")
        else:
            out.append(f"  [red]FAILED[/] {escape(integrity.reason)}")
        if getattr(integrity, "draft", False):
            out.append("  [yellow]draft checkpoint — not a finished artifact (unsigned)[/]")
        return out

    def _signature_lines(self, record: RunRecord) -> list[str]:
        sig = record.signature
        if sig is None or getattr(sig, "state", "unsigned") == "unsigned":
            return []
        color = SIG_STATE_COLORS.get(sig.state, "white")
        out = ["", "[bold #FF6900]Authenticity[/]"]
        out.append(f"  state: [{color}]{escape(sig.state)}[/]  [dim]{escape(sig.reason)}[/]")
        out.append(f"  algorithm: {escape(str(sig.algorithm or '-'))}")
        out.append(f"  key id: {escape(str(sig.key_id or '-'))}")
        out.append(
            f"  signer: {escape(str(sig.signer or '-'))} "
            "[dim](display only — not a verified identity)[/]"
        )
        out.append(f"  signed at: {escape(str(sig.signed_at or '-'))}")
        if sig.state == "no-key":
            out.append("  [blue]key needed — press 'v' to verify with a key[/]")
        elif sig.state == "verified-tofu":
            out.append("  [yellow]self-asserted embedded key (TOFU) — not a trusted identity[/]")
        return out

    def _set_text(self, text: str) -> None:
        self.last_rendered = text
        self.update(text)
