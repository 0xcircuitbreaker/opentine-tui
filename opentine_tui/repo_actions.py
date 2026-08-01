"""Actions against a v3 `.tine` repository: fsck, import, fork, promote, attest, diff."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from opentine_tui.actions import ActionResult
from opentine_tui.formatting import cost_str, duration_str, short_ref
from opentine_tui.v3 import REPO_ERRORS, RepoRunRecord, V3Repository, short_oid

try:  # migrate-v3 refuses a tampered source with this, and it is not a ValueError
    from opentine.signing import SignatureError
except Exception:  # pragma: no cover - defensive

    class SignatureError(Exception): ...


#: Errors an action may surface to the user instead of crashing the dashboard.
ACTION_ERRORS = (*REPO_ERRORS, SignatureError, FileNotFoundError)


@dataclass(slots=True)
class PromoteOptions:
    name: str = ""
    overwrite: bool = False


@dataclass(slots=True)
class EvaluateOptions:
    scores: dict[str, float]
    signer: str = "dashboard"
    note: str = ""


@dataclass(slots=True)
class ImportOptions:
    source: str = ""
    ref: str = "heads/main"
    allow_unverified: bool = False


def _fail(title: str, exc: BaseException) -> ActionResult:
    message = exc.args[0] if isinstance(exc, KeyError) and exc.args else str(exc)
    return ActionResult(False, title, f"{type(exc).__name__}: {message}")


def fsck_text(result: Any) -> str:
    errors = list(getattr(result, "errors", ()) or ())
    lines = [
        f"objects: {getattr(result, 'objects', 0)}    refs: {getattr(result, 'refs', 0)}",
        "",
    ]
    if not errors:
        lines.append("Every object re-hashed, every ref typed, every causal link resolved.")
    else:
        lines.append(f"{len(errors)} problem(s):")
        lines.extend(f"  - {error}" for error in errors[:40])
        if len(errors) > 40:
            lines.append(f"  … and {len(errors) - 40} more")
    return "\n".join(lines)


def diff_text(repo: V3Repository, left: str, right: str, diff: Any) -> str:
    """Render a v3 semantic diff.

    Unlike the v2 field diff this compares *events* by position, so a changed pair
    names the fields whose content hashes differ rather than their values.
    """
    summary = getattr(diff, "summary", {}) or {}
    cost = summary.get("cost") or {}
    latency = summary.get("latency") or {}
    lines = [
        f"Diff: {short_ref(left)} -> {short_ref(right)}",
        (
            f"cost {cost_str(float(cost.get('left') or 0))} -> "
            f"{cost_str(float(cost.get('right') or 0))}    "
            f"latency {duration_str(float(latency.get('left') or 0))} -> "
            f"{duration_str(float(latency.get('right') or 0))}"
        ),
        (
            f"={len(diff.common_events)} common   "
            f"+{len(diff.only_left)} only left   "
            f"-{len(diff.only_right)} only right   "
            f"~{len(diff.changed)} changed"
        ),
    ]
    evaluations = summary.get("evaluations") or {}
    left_scores = _scores(evaluations.get("left"))
    right_scores = _scores(evaluations.get("right"))
    if left_scores or right_scores:
        lines.append(f"scores {left_scores or '-'} -> {right_scores or '-'}")

    lines.extend(["", "Changed events:"])
    if not diff.changed:
        lines.append("  (none)")
    for change in diff.changed[:60]:
        # An empty field list is meaningful: every compared field matched, so the
        # two events differ only in ancestry or timing — a moved step, not a new one.
        fields = ", ".join(change.get("fields") or ()) or "same content, different ancestry"
        lines.append(
            f"  ~ [{change.get('index')}] {short_ref(str(change.get('before')))}"
            f" -> {short_ref(str(change.get('after')))}   {fields}"
        )
    if len(diff.changed) > 60:
        lines.append(f"  … and {len(diff.changed) - 60} more")
    if diff.only_left:
        lines.extend(["", "Only on the left:"])
        lines.extend(f"  + {short_ref(oid)}" for oid in diff.only_left)
    if diff.only_right:
        lines.extend(["", "Only on the right:"])
        lines.extend(f"  - {short_ref(oid)}" for oid in diff.only_right)
    return "\n".join(lines)


def _scores(evaluations: Any) -> str:
    """``quality=0.91, speed=0.7`` from the diff summary's attestation records."""
    if not isinstance(evaluations, list):
        return ""
    merged: dict[str, float] = {}
    for record in evaluations:
        scores = record.get("scores") if isinstance(record, dict) else None
        if not isinstance(scores, dict):
            continue
        for name, value in scores.items():
            if isinstance(value, int | float) and not isinstance(value, bool):
                merged[str(name)] = max(float(value), merged.get(str(name), float("-inf")))
    return ", ".join(f"{name}={value:g}" for name, value in sorted(merged.items()))


def log_text(entries: list[Any]) -> str:
    lines = []
    for entry in entries:
        payload = getattr(entry, "payload", {}) or {}
        kind = payload.get("kind") or getattr(entry, "object_type", "?")
        model = payload.get("model") or ""
        cost = float(payload.get("cost") or 0)
        parents = payload.get("parent_ids") or []
        lines.append(
            f"{short_ref(entry.oid)}  {kind!s:<8} {cost_str(cost) if cost else '':>9}"
            f"  {model}"
            + (f"  ^{', '.join(short_ref(p) for p in parents)}" if parents else "  (root)")
        )
    return "\n".join(lines) or "(no events)"


class RepoActionService:
    """Coordinate v3 repository actions for the dashboard."""

    def __init__(self, repo: V3Repository) -> None:
        self.repo = repo

    def fsck(self, deep: bool = True) -> ActionResult:
        try:
            result = self.repo.fsck(deep=deep)
        except ACTION_ERRORS as exc:
            return _fail("fsck failed", exc)
        ok = bool(getattr(result, "ok", False))
        return ActionResult(
            ok,
            "Repository OK" if ok else "Repository has errors",
            fsck_text(result),
        )

    def import_v2(self, options: ImportOptions) -> ActionResult:
        """Import a portable v2 artifact as v3 objects (`tine migrate-v3`).

        Fail-closed by default: a source whose integrity check fails is refused
        unless the caller explicitly opts out, because migrating a tampered
        artifact would launder it into a verified object graph.
        """
        source = Path(options.source).expanduser()
        if not source.is_file():
            return ActionResult(False, "Import failed", f"Not a file: {source}")
        try:
            result = self.repo.repo.migrate_v2(
                source,
                ref=options.ref or None,
                strict=not options.allow_unverified,
            )
        except ACTION_ERRORS as exc:
            return _fail("Import refused", exc)
        note = (
            "\n[unverified import — the v2 source did not pass its integrity check]"
            if options.allow_unverified
            else ""
        )
        return ActionResult(
            True,
            "Imported into the repository",
            (
                f"{source.name} -> {short_oid(result.run_id)}\n"
                f"{len(result.event_map)} events, ref {options.ref or '(none)'}\n"
                f"The original bytes are retained as a legacy blob.{note}"
            ),
            refresh=True,
        )

    def fork(
        self,
        record: RepoRunRecord,
        event_oid: str | None,
        ref: str = "",
        overwrite: bool = False,
    ) -> ActionResult:
        if not event_oid:
            return ActionResult(False, "Fork failed", "Select the event to fork from first.")
        if record.shallow:
            return ActionResult(
                False,
                "Fork refused",
                "This run crosses a shallow clone boundary; fetch it in full first.",
            )
        # `fork_run` compare-and-swaps against whatever the ref currently holds, so
        # naming an existing ref moves it — typing `heads/main` would retarget the
        # branch. Promotion already requires an explicit decision here; so does this.
        existing = None
        if ref:
            try:
                existing = self.repo.repo.read_ref(ref)
            except ACTION_ERRORS:
                existing = None
            if existing is not None and not overwrite:
                return ActionResult(
                    False,
                    "Fork blocked",
                    (
                        f"{ref} already points at {short_oid(existing)}.\n"
                        "Forking onto it would move the ref off that run. Choose "
                        "another name, or re-run accepting the move."
                    ),
                )
        try:
            # v3 objects are content-addressed, so an identical fork of an identical
            # slice is deduplicated rather than created. Saying "new run" then would
            # claim work that did not happen, so note what was there beforehand.
            before = set(self.repo.all_oids())
            new_id = self.repo.repo.fork(record.oid, event_oid, ref=ref or None)
            deduped = new_id in before
        except ACTION_ERRORS as exc:
            return _fail("Fork failed", exc)
        headline = "Forked" if not deduped else "Fork already existed"
        return ActionResult(
            True,
            headline,
            (
                f"Forked {record.short_id} at {short_ref(event_oid)}\n"
                f"{'Existing' if deduped else 'New'} run: {short_oid(new_id)}"
                + (f"\nRef: {ref}" if ref else "\n(not referenced — note the object id)")
                + (
                    f"\n[moved {ref} off {short_oid(existing)}]"
                    if ref and existing is not None
                    else ""
                )
            ),
            refresh=True,
        )

    def promote(self, record: RepoRunRecord, options: PromoteOptions) -> ActionResult:
        """Point `promotions/<name>` at this run.

        The ref update is a compare-and-swap, so replacing an existing promotion
        has to name the value being replaced — that is the whole protection
        against clobbering someone else's promotion, and it must be a deliberate
        choice rather than a retry.
        """
        name = options.name.strip()
        if not name:
            return ActionResult(False, "Promote failed", "A promotion name is required.")
        try:
            current = self.repo.repo.read_ref(f"promotions/{name}")
        except ACTION_ERRORS:
            current = None
        if current is not None and not options.overwrite:
            return ActionResult(
                False,
                "Promote blocked",
                (
                    f"promotions/{name} already points at {short_oid(current)}.\n"
                    "Re-run with overwrite to move it."
                ),
            )
        try:
            self.repo.repo.promote(record.oid, name, expected_old=current)
        except ACTION_ERRORS as exc:
            return _fail("Promote failed", exc)
        return ActionResult(
            True,
            "Promoted",
            f"promotions/{name} -> {record.short_id}",
            refresh=True,
        )

    def evaluate(self, record: RepoRunRecord, options: EvaluateOptions) -> ActionResult:
        """Attach an evaluation attestation, which is what `Score` reads."""
        if not options.scores:
            return ActionResult(False, "Evaluate failed", "At least one score is required.")
        claim: dict[str, Any] = {"kind": "evaluation", "scores": options.scores}
        if options.note:
            claim["note"] = options.note
        try:
            oid = self.repo.repo.attest(record.oid, claim, signer=options.signer or "dashboard")
        except ACTION_ERRORS as exc:
            return _fail("Evaluate failed", exc)
        rendered = ", ".join(f"{name}={value:g}" for name, value in sorted(options.scores.items()))
        return ActionResult(
            True,
            "Evaluation recorded",
            (
                f"{rendered}\nattestation {short_oid(oid)} signed by "
                f"{options.signer or 'dashboard'}\n"
                "[dim]signer is a label, not a verified identity[/]"
            ),
            refresh=True,
        )

    def diff(self, left: str, right: str) -> ActionResult:
        try:
            diff = self.repo.diff(left, right)
        except ACTION_ERRORS as exc:
            return _fail("Diff failed", exc)
        return ActionResult(True, "Semantic diff", diff_text(self.repo, left, right, diff))

    def log(self, ref: str, limit: int | None = 200) -> ActionResult:
        try:
            entries = self.repo.log(ref, limit=limit)
        except ACTION_ERRORS as exc:
            return _fail("Log failed", exc)
        return ActionResult(True, f"Event log — {ref}", log_text(entries))

    def inspect(self, oid: str, resolve_blobs: bool = False) -> ActionResult:
        try:
            data = self.repo.inspect(oid, resolve_blobs=resolve_blobs)
        except ACTION_ERRORS as exc:
            return _fail("Inspect failed", exc)
        return ActionResult(True, f"Object {short_oid(oid)}", json.dumps(data, indent=2)[:20000])
