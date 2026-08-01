"""Step detail widget — show run/action/step details."""

from __future__ import annotations

import json
from typing import Any

from opentine.core import Run, Step
from textual.widgets import Static

from opentine_tui.formatting import (
    billing_status,
    cost_str,
    duration_str,
    short_ref,
    step_cost,
    token_summary,
    token_total,
)
from opentine_tui.formatting import (
    escape_markup as escape,
)
from opentine_tui.repository import RunRecord
from opentine_tui.v3 import RepoRunRecord, short_oid

try:
    from opentine.core import Budget
except Exception:  # pragma: no cover - defensive
    Budget = None

try:  # 0.4.0: a fork can prove its own id from the basis it records
    from opentine._fork_identity import verify_fork_id
except Exception:  # pragma: no cover - pre-0.4.0 opentine
    verify_fork_id = None

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


def _timestamp(value: float) -> str:
    from datetime import datetime

    try:
        return datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M:%S")
    except (OSError, OverflowError, ValueError):  # pragma: no cover - defensive
        return str(value)


def _billing_line(step: Step) -> list[str]:
    """Explain a cost figure: ``$0.0000`` alone cannot say 'free' from 'unpriced'."""
    status = billing_status(step)
    if status is None:
        return []
    _, label, color = status
    billing = step.billing
    line = f"[bold]Billing:[/] [{color}]{escape(label)}[/]"
    card = billing.get("rate_card_id") or billing.get("catalog_id")
    if card:
        line += f"  [dim]{escape(str(card))}[/]"
    lines = [line]
    warnings = billing.get("warnings")
    if isinstance(warnings, list) and warnings:
        lines.extend(f"  [yellow]! {escape(str(warning))}[/]" for warning in warnings[:3])
    return lines


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

    def set_repo_run(
        self, record: RepoRunRecord, run: Run | None, error: str | None = None
    ) -> None:
        """Summarize a run object from a v3 repository.

        There is no integrity/signature/draft block here: v3 objects are
        content-addressed and opentine verifies them on every read, so a row that
        renders at all has already been checked.
        """
        self._record = None
        self._run = run
        lines = [
            f"[bold]Run object:[/] {escape(record.short_id)}",
            f"[dim]{escape(record.oid)}[/]",
        ]
        if record.source_run_id:
            lines.append(f"[bold]Source run id:[/] {escape(record.source_run_id)}")
        refs = ", ".join(record.refs) or "(unreferenced — reachable only by object id)"
        lines.append(f"[bold]Refs:[/] {escape(refs)}")
        lines.append(f"[bold]Model:[/] {escape(record.model)}")
        lines.append(f"[bold]Status:[/] {escape(record.status)}")
        tokens = getattr(run, "total_tokens", None) if run is not None else None
        summary = f"[bold]Events:[/] {record.event_count}    [bold]Cost:[/] {cost_str(record.cost)}"
        if tokens:
            summary += f"    [bold]Tokens:[/] {tokens}"
        lines.append(summary)
        lines.append(f"[bold]Duration:[/] {duration_str(record.latency)}")
        if record.created_at:
            lines.append(f"[bold]Created:[/] {escape(_timestamp(record.created_at))}")
        if record.score is not None:
            lines.append(
                f"[bold]Evaluation:[/] {record.score:.2f} [dim](highest attested score)[/]"
            )
        if record.forked_from:
            lines.append(f"\n[#FF6900]Forked from:[/] {escape(short_oid(record.forked_from))}")
        if record.shallow:
            lines.append(
                "\n[yellow]Shallow:[/] some events were cut by a clone boundary — "
                "fork and resume are refused across it."
            )
        if record.load_error:
            lines.append(f"\n[red]Object error:[/] {escape(record.load_error)}")
        if error:
            lines.append(f"\n[red]Could not materialize the run:[/] {escape(error)}")
        elif run is not None and run.user_prompt:
            lines.extend(["", "[dim]Prompt:[/]", f"  {escape(run.user_prompt[:200])}"])
        self._set_text("\n".join(lines))

    def set_step(self, run: Run, step: Step) -> None:
        parent_ids = getattr(step, "parent_ids", None)
        if parent_ids is None:
            parent_id = getattr(step, "parent_id", None)
            parent_ids = [parent_id] if parent_id else []
        parent_text = ", ".join(short_ref(parent) for parent in parent_ids) or "-"
        step_short_id = short_ref(step.id)
        kind = escape(step.kind.value)
        v3_kind = getattr(step, "v3_kind", None)
        if v3_kind and v3_kind != step.kind.value:
            # A v3 event kind the v2 model has no slot for, preserved on load.
            kind += f"  [dim](v3: {escape(str(v3_kind))})[/]"
        lines = [
            f"[bold]Step:[/] {escape(step_short_id)}",
            f"[bold]Kind:[/] {kind}",
            f"[bold]Parents:[/] {escape(parent_text)}",
            f"[bold]Model:[/] {escape(step.model_info)}",
            f"[bold]Cost:[/] ${step_cost(step):.4f}",
            f"[bold]Duration:[/] {step.duration:.1f}s",
        ]
        lines.extend(_billing_line(step))
        tokens = token_summary(getattr(step, "usage", {}))
        if tokens:
            lines.append(f"[bold]Tokens:[/] {escape(tokens)}")
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
        total_tokens = getattr(run, "total_tokens", None)
        if total_tokens is None:  # pragma: no cover - defensive
            total_tokens = self._run_tokens(run)
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
        lines += self._pricing_lines(run)
        lines += self._integrity_lines(record)
        lines += self._signature_lines(record)

        lines += ["", "[dim]Prompt:[/]", f"  {escape(run.user_prompt[:200])}"]

        lines += self._fork_lines(run)
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
    def _fork_lines(run: Run) -> list[str]:
        """Ancestry, plus whether the fork id matches the basis the run records."""
        metadata = getattr(run, "metadata", {}) or {}
        record = metadata.get("fork")
        source = metadata.get("forked_from")
        if not source and not isinstance(record, dict):
            return []

        # True: the id re-derives from the record. False: the record was edited.
        # None: nothing to check — an explicit id, a pre-0.4.0 fork, or a v3 rewrite.
        verdict = verify_fork_id(run) if verify_fork_id is not None else None

        out = ["", "[bold #FF6900]Fork[/]"]
        # Whose word the lineage is on matters more than the lineage itself. The
        # verdict covers `metadata.fork` and nothing else, while `forked_from` /
        # `fork_point` are separate keys sitting outside the integrity digest — so
        # printing those under a green badge would let an edited artifact claim any
        # ancestry it liked and look attested. When the record verifies, the record
        # is the source of truth; otherwise the lineage is labelled as a claim.
        if verdict is True and isinstance(record, dict):
            out.append(
                f"  from {escape(short_ref(str(record.get('source', '?'))))} "
                f"at {escape(short_ref(str(record.get('point', '?'))))}"
            )
        elif source:
            point = metadata.get("fork_point", "?")
            out.append(
                f"  from {escape(short_ref(str(source)))} at {escape(short_ref(str(point)))} "
                "[dim](claimed)[/]"
            )
        reason = metadata.get("fork_reason")
        if reason:
            # 0.4.0 deliberately leaves fork_reason outside the signature, so it is
            # a note from whoever forked, not an attested fact.
            out.append(f"  reason: {escape(str(reason)[:120])} [dim](unsigned)[/]")
        if isinstance(record, dict):
            branch = record.get("branch")
            if branch:
                suffix = "" if verdict is True else " [dim](claimed)[/]"
                out.append(f"  branch: {escape(str(branch))}{suffix}")
            if record.get("nonce") == "" and verdict is True:
                out.append("  [dim]reproducible fork (no nonce) — a replay, not a divergence[/]")
        if verify_fork_id is None:
            return out
        if verdict is True:
            out.append(
                "  identity: [green]verified[/] "
                "[dim]— id matches this basis; anything above is from it[/]"
            )
        elif verdict is False:
            out.append("  identity: [red]MISMATCH[/] — the recorded basis does not produce this id")
        elif isinstance(record, dict):
            out.append("  identity: [dim]not checkable (basis recorded by a newer opentine)[/]")
        elif source:
            out.append("  identity: [dim]no basis recorded — lineage is unverified[/]")
        return out

    @staticmethod
    def _run_tokens(run: Run) -> int:
        return sum(token_total(getattr(step, "usage", {})) for step in run.steps)

    @staticmethod
    def _pricing_lines(run: Run) -> list[str]:
        """Surface ``manifest.pricing`` provenance behind the cost figure.

        Shapes matter here: ``catalogs`` is a *list* of snapshot records and
        ``rate_cards`` is keyed by *step id* (opentine/_graph_pricing.py), so
        printing either one's keys as names yields catalog ids nobody recognizes
        and 64-character hashes labelled 'rate cards'.
        """
        pricing = getattr(run, "manifest", {}).get("pricing")
        if not isinstance(pricing, dict) or not pricing:
            return []
        complete = pricing.get("complete")
        state = (
            "[green]complete[/]"
            if complete is True
            else "[yellow]incomplete — some steps are unpriced[/]"
            if complete is False
            else "[dim]unrecorded[/]"
        )
        out = ["", "[bold #FF6900]Pricing provenance[/]", f"  coverage: {state}"]
        catalog_id = pricing.get("catalog_id")
        catalog_hash = pricing.get("catalog_hash")
        if catalog_id:
            digest = f" [dim]{escape(short_ref(str(catalog_hash)))}[/]" if catalog_hash else ""
            out.append(f"  catalog: {escape(str(catalog_id))}{digest}")
        cards = pricing.get("rate_cards")
        if isinstance(cards, dict) and cards:
            named = sorted(
                {
                    str(card.get("id") or card.get("rate_card_id"))
                    for card in cards.values()
                    if isinstance(card, dict) and (card.get("id") or card.get("rate_card_id"))
                }
            )
            if named:
                out.append(f"  rate cards: {escape(', '.join(named[:6]))}")
            else:
                out.append(f"  rate cards: {len(cards)} priced step(s)")
        provenance = pricing.get("catalog_provenance")
        if isinstance(provenance, list) and provenance:
            out.append(f"  provenance: {len(provenance)} signed source(s)")
        elif isinstance(provenance, dict) and provenance:
            out.append(f"  provenance: {escape(', '.join(sorted(provenance)[:4]))}")
        catalogs = pricing.get("catalogs")
        if isinstance(catalogs, list) and catalogs:
            for snapshot in catalogs[:3]:
                if not isinstance(snapshot, dict):
                    continue
                name = snapshot.get("catalog_id") or "(unnamed catalog)"
                effective = snapshot.get("effective_at") or snapshot.get("effective_from") or ""
                suffix = f"  [dim]effective {escape(str(effective))}[/]" if effective else ""
                out.append(f"  snapshot: {escape(str(name))}{suffix}")
        return out

    def _budget_lines(self, run: Run) -> list[str]:
        # `budget()` re-validates manifest.budget and raises on a limit opentine
        # would refuse to write (a fractional max_usage, a negative max_steps). The
        # artifact still loads, so the dashboard has to report the bad budget rather
        # than die on the way to drawing the panel.
        try:
            budget = run.budget() if hasattr(run, "budget") else None
        except (ValueError, TypeError) as exc:
            return [
                "",
                "[bold #FF6900]Budget[/]",
                f"  [red]unreadable:[/] {escape(str(exc))}",
                "  [dim]manifest.budget holds a value opentine will not accept[/]",
            ]
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
        if getattr(budget, "strict_cost", False):
            out.append(
                "  strict cost: [green]on[/] "
                "[dim]— a step opentine could not price counts as a breach[/]"
            )
        if breach is not None:
            out.append(f"  [red]breached {breach.dimension}: {breach.incurred} > {breach.limit}[/]")
        # `Budget.check` only re-derives the four numeric dimensions; a
        # cost_completeness breach is recorded at run time and cannot be recomputed
        # here, so the stored (unsigned, advisory) record is the only witness.
        recorded = getattr(run, "metadata", {}).get("budget_state")
        if isinstance(recorded, dict):
            dimension = recorded.get("dimension")
            if dimension and dimension != breached_dim:
                out.append(
                    f"  [yellow]recorded breach:[/] {escape(str(dimension))} "
                    f"[dim](from the run itself — not re-derivable, not signed)[/]"
                )
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
        # opentine folds cache reads/writes into `input_tokens` and reasoning into
        # `output_tokens`, so calling them "in"/"out" understates what each covers.
        out.append(
            f"  total: ${cb.total_cost:.4f}  "
            f"tokens {cb.total_tokens} "
            f"(prompt+cache {cb.input_tokens} / completion+reasoning {cb.output_tokens})"
        )
        if cb.by_model:
            out.append("  by model:")
            for model, cost in sorted(cb.by_model.items(), key=lambda kv: -kv[1]):
                out.append(f"    {escape(str(model))}: ${cost:.4f}")
        if cb.by_kind:
            kinds = "  ".join(f"{escape(str(k))}=${v:.4f}" for k, v in sorted(cb.by_kind.items()))
            out.append(f"  by kind: {kinds}")
        by_ref = getattr(cb, "by_ref", None)  # 0.3.0: cost attributed per named tip
        if by_ref:
            refs = "  ".join(
                f"{escape(str(name))}=${value:.4f}"
                for name, value in sorted(by_ref.items(), key=lambda kv: -kv[1])[:6]
            )
            out.append(f"  by ref: {refs}")
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
