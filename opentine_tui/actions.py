"""Action services for verifying, modifying, and launching opentine runs."""

from __future__ import annotations

import json
import os
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from opentine.core import Run, Step

from opentine_tui.repository import RunRecord, RunRepository, verify_integrity

try:
    import opentine.signing as signing
except Exception:  # pragma: no cover - defensive
    signing = None

try:
    from opentine._canon import FORMAT_VERSION
except Exception:  # pragma: no cover - defensive
    FORMAT_VERSION = 2

try:
    from opentine.migrations import LEGACY_VERSION
except Exception:  # pragma: no cover - defensive
    LEGACY_VERSION = 0

try:
    from opentine.harnesses import (
        ClaudeCodeHarness,
        CodexCLIHarness,
        CursorHarness,
        GenericHarness,
        HermesHarness,
        KimiCodeHarness,
        OpenClawHarness,
        OpenCodeHarness,
        OpentineHarness,
        PiHarness,
    )
except ModuleNotFoundError:
    OpentineHarness = None
    HARNESS_FACTORIES = {}
else:
    HARNESS_FACTORIES = {
        "claude-code": ClaudeCodeHarness,
        "codex": CodexCLIHarness,
        "cursor": CursorHarness,
        "generic": GenericHarness,
        "hermes": HermesHarness,
        "kimi-code": KimiCodeHarness,
        "openclaw": OpenClawHarness,
        "opencode": OpenCodeHarness,
        "pi": PiHarness,
    }


@dataclass(slots=True)
class HarnessOptions:
    harness: str = "codex"
    prompt: str = ""
    cwd: str | None = None
    command_override: str | None = None
    extra_args: tuple[str, ...] = field(default_factory=tuple)
    login_env: bool = False
    env_allowlist: tuple[str, ...] = field(default_factory=tuple)
    save_path: str | Path | None = None


@dataclass(slots=True)
class ActionResult:
    ok: bool
    title: str
    message: str
    run: Run | None = None
    path: Path | None = None
    refresh: bool = False


@dataclass(slots=True)
class BudgetOptions:
    max_cost: float | None = None
    max_steps: int | None = None
    max_duration: float | None = None
    max_usage: int | None = None
    on_breach: str = "stop"


@dataclass(slots=True)
class SignOptions:
    algorithm: str = "hmac-sha256"
    key_env: str | None = None
    key_file: str | None = None
    ed25519_key_file: str | None = None
    key_id: str | None = None
    signer: str | None = None
    save_path: str | None = None
    force: bool = False


@dataclass(slots=True)
class VerifyKeyOptions:
    key_env: str | None = None
    key_file: str | None = None
    pubkey_file: str | None = None
    trust_embedded: bool = False


def short_id(value: str | None) -> str:
    return (value or "")[:12]


def _fail(title: str, message: str, path: str | Path | None = None) -> ActionResult:
    return ActionResult(False, title, message, path=Path(path) if path is not None else None)


def resolve_step_ref(run: Run, ref: str | None) -> str:
    if ref is None:
        if not run.steps:
            raise ValueError("Run has no steps")
        return run.steps[-1].id
    if ref.isdigit():
        index = int(ref)
        if index < 0 or index >= len(run.steps):
            raise ValueError(f"Step index {index} out of range (0-{len(run.steps) - 1})")
        return run.steps[index].id
    return run.graph.resolve(ref)


def run_context(run: Run, from_step: str | None = None) -> dict[str, Any]:
    if from_step is None:
        steps = run.steps
    else:
        start = run.graph.resolve(from_step)
        keep = run.graph.descendant_closure(start)
        steps = [step for step in run.steps if step.id in keep]

    return {
        "source_run": run.id,
        "from_step": from_step,
        "steps": [
            {
                "id": step.id,
                "short_id": short_id(step.id),
                "kind": step.kind.value,
                "inputs": step.inputs,
                "outputs": step.outputs,
            }
            for step in steps
        ],
    }


def display_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, sort_keys=True)
    except TypeError:
        return repr(value)


def step_label(step: Step) -> str:
    text = step.inputs.get("text", "")
    name = step.inputs.get("name", "")
    args = step.inputs.get("arguments", {})
    if step.kind.value == "tool":
        if isinstance(args, dict):
            args_text = ", ".join(
                f'{key}="{value}"' if isinstance(value, str) else f"{key}={display_value(value)}"
                for key, value in args.items()
            )
        else:
            args_text = display_value(args)
        return f"{short_id(step.id)} tool {display_value(name)}({args_text})"
    if text:
        preview = display_value(text)[:80].replace("\n", " ")
        if len(display_value(text)) > 80:
            preview += "..."
        return f'{short_id(step.id)} {step.kind.value} "{preview}"'
    return f"{short_id(step.id)} {step.kind.value}"


def _diff_value(value: Any, limit: int = 200) -> str:
    text = display_value(value).replace("\n", " ")
    return text[:limit] + ("…" if len(text) > limit else "")


def diff_text(run_a: Run, run_b: Run) -> str:
    if not hasattr(run_a, "diff"):
        return fallback_diff_text(run_a, run_b)

    diff = run_a.diff(run_b)
    changed = list(getattr(diff, "changed", []) or [])
    tokens_a = getattr(run_a, "total_tokens", 0)
    tokens_b = getattr(run_b, "total_tokens", 0)
    lines = [
        f"Diff: {short_id(run_a.id)} vs {short_id(run_b.id)}",
        f"Common ancestor: {short_id(diff.common_ancestor) if diff.common_ancestor else '-'}",
        f"cost {run_a.total_cost:.4f} -> {run_b.total_cost:.4f}    tokens {tokens_a} -> {tokens_b}",
        f"+{len(diff.only_a)} only A   -{len(diff.only_b)} only B   ~{len(changed)} changed",
        "",
        f"Only in {short_id(run_a.id)}:",
    ]
    lines.extend(f"  + {step_label(step)}" for step in diff.only_a)
    if not diff.only_a:
        lines.append("  (none)")
    lines.extend(["", f"Only in {short_id(run_b.id)}:"])
    lines.extend(f"  - {step_label(step)}" for step in diff.only_b)
    if not diff.only_b:
        lines.append("  (none)")

    lines.extend(["", "Changed:"])
    if not changed:
        lines.append("  (none)")
    for change in changed:
        drift_only = change.step_a.id == change.step_b.id
        tag = "drift" if drift_only else "content"
        lines.append(f"  ~ {step_label(change.step_b)}  [{tag}]")
        for delta in change.fields:
            keys = f" {delta.changed_keys}" if getattr(delta, "changed_keys", None) else ""
            lines.append(f"      {delta.name}{keys}:")
            lines.append(f"        - {_diff_value(delta.before)}")
            lines.append(f"        + {_diff_value(delta.after)}")
    return "\n".join(lines)


def fallback_diff_text(run_a: Run, run_b: Run) -> str:
    ids_a = {step.id for step in run_a.steps}
    ids_b = {step.id for step in run_b.steps}
    only_a = [step for step in run_a.steps if step.id not in ids_b]
    only_b = [step for step in run_b.steps if step.id not in ids_a]
    lines = [
        f"Diff: {short_id(run_a.id)} vs {short_id(run_b.id)}",
        "Common ancestor: unavailable",
        "",
        f"Only in {short_id(run_a.id)}:",
    ]
    lines.extend(f"  {step_label(step)}" for step in only_a)
    if not only_a:
        lines.append("  (none)")
    lines.extend(["", f"Only in {short_id(run_b.id)}:"])
    lines.extend(f"  {step_label(step)}" for step in only_b)
    if not only_b:
        lines.append("  (none)")
    return "\n".join(lines)


class RunActionService:
    """Coordinate opentine run actions for the TUI."""

    def __init__(self, repository: RunRepository) -> None:
        self.repository = repository

    def verify(self, ref: str | Path) -> ActionResult:
        try:
            path = self.repository.find_path(ref)
        except FileNotFoundError:
            path = Path(ref)
        except ValueError as exc:
            return ActionResult(False, "Verify failed", str(exc))

        result = verify_integrity(path)
        if result.ok:
            digest = result.actual or result.expected or ""
            return ActionResult(
                True,
                "Verify OK",
                f"OK {path}\nsha256:{digest[:12]}",
                path=path,
            )

        lines = [f"FAILED {path}: {result.reason}"]
        if result.expected:
            lines.append(f"expected: {result.expected}")
        if result.actual:
            lines.append(f"actual:   {result.actual}")
        return ActionResult(False, "Verify failed", "\n".join(lines), path=path)

    def fork(
        self, run: Run, step_ref: str | None, save_path: str | Path | None = None
    ) -> ActionResult:
        try:
            step_id = resolve_step_ref(run, step_ref)
            forked = run.fork(step_id)
            path = self.repository.save(forked, save_path)
        except Exception as exc:
            return ActionResult(False, "Fork failed", str(exc))

        return ActionResult(
            True,
            "Forked",
            (
                f"Forked {short_id(run.id)} from {short_id(step_id)}\n"
                f"New run: {short_id(forked.id)}\nSaved: {path}"
            ),
            run=forked,
            path=path,
            refresh=True,
        )

    def cache_replay(
        self,
        run: Run,
        step_ref: str | None,
        save_path: str | Path | None = None,
    ) -> ActionResult:
        try:
            step_id = resolve_step_ref(run, step_ref)
            replayed = run.fork(step_id, new_run_id=f"{run.id}-replay")
            replayed.metadata["replay"] = {
                "mode": "cache",
                "source_run": run.id,
                "reused_steps": len(replayed.steps),
            }
            replayed.status = run.status
            path = self.repository.save(replayed, save_path)
        except Exception as exc:
            return ActionResult(False, "Replay failed", str(exc))

        return ActionResult(
            True,
            "Cached replay",
            f"Cached replay reused {len(replayed.steps)} recorded steps\nSaved: {path}",
            run=replayed,
            path=path,
            refresh=True,
        )

    def diff(self, run: Run, other_ref: str | Path) -> ActionResult:
        try:
            other = self.repository.load(other_ref)
        except Exception as exc:
            return ActionResult(False, "Diff failed", str(exc))
        return ActionResult(True, "Diff", diff_text(run, other), run=other)

    # -- v0.2.0 actions ----------------------------------------------------

    def migrate(self, record: RunRecord, force: bool = False) -> ActionResult:
        """Upgrade a v1/legacy artifact to the current format (one-way)."""
        path = record.path
        if record.is_future:
            return _fail(
                "Migrate refused", "Written by a newer opentine — upgrade the tool instead.", path
            )
        if record.run is None:
            return _fail("Migrate failed", record.error_message or "unreadable", path)
        if record.on_disk_version is not None and record.on_disk_version >= FORMAT_VERSION:
            return _fail("Migrate skipped", f"Already current (v{FORMAT_VERSION}).", path)
        # Non-legacy sources must pass integrity first (parity with `tine migrate`).
        if record.on_disk_version != LEGACY_VERSION:
            integrity = record.integrity
            if integrity is not None and not integrity.ok and not force:
                return _fail(
                    "Migrate blocked",
                    "Source integrity check failed; re-run with Force.",
                    path,
                )
        try:
            run = Run.load(path)  # auto-migrates in memory
            saved = self.repository.save(run, path)  # persists the upgrade
        except Exception as exc:
            return _fail("Migrate failed", f"{type(exc).__name__}: {exc}", path)
        note = "\nSignature was dropped — re-sign with 's'." if record.has_signature else ""
        if record.is_legacy:
            note += "\nLegacy import recomputed step ids (best-effort)."
        return ActionResult(
            True,
            "Migrated",
            f"Upgraded {record.version_label} -> v{FORMAT_VERSION}\nSaved: {saved}{note}",
            run=run,
            path=saved,
            refresh=True,
        )

    def set_tags(self, record: RunRecord, add: list[str], remove: list[str]) -> ActionResult:
        run = record.run
        if run is None:
            return _fail("Tag failed", "Run is not loaded.", record.path)
        changed = False
        for tag in add:
            if run.add_tag(tag):
                changed = True
        for tag in remove:
            if run.remove_tag(tag):
                changed = True
        tags = ", ".join(run.tags) or "(none)"
        if not changed:
            return ActionResult(True, "Tags unchanged", f"Tags: {tags}", run=run, path=record.path)
        # Persist via a raw metadata.tags edit so the on-disk digest, any signature,
        # and the on-disk format version are all preserved (tags are outside both
        # the digest and the signed view). Re-saving through Run.save would instead
        # recompute the digest, drop the signature, and upgrade a v1 file to v2.
        try:
            saved = self.repository.write_tags(record.path, run.tags)
        except Exception as exc:
            return _fail("Tag save failed", str(exc), record.path)
        return ActionResult(
            True,
            "Tags updated",
            f"Tags: {tags}\nSaved: {saved}\n(outside digest/signature — both preserved)",
            run=run,
            path=saved,
            refresh=True,
        )

    def set_budget(self, record: RunRecord, options: BudgetOptions) -> ActionResult:
        run = record.run
        if run is None:
            return _fail("Set budget failed", "Run is not loaded.", record.path)
        limits = (options.max_cost, options.max_steps, options.max_duration, options.max_usage)
        if all(value is None for value in limits):
            return _fail("Set budget failed", "Set at least one limit.", record.path)
        try:
            run.set_budget(
                max_cost=options.max_cost,
                max_steps=options.max_steps,
                max_duration=options.max_duration,
                max_usage=options.max_usage,
                on_breach=options.on_breach,
            )
            saved = self.repository.save(run, record.path)
        except Exception as exc:
            return _fail("Set budget failed", f"{type(exc).__name__}: {exc}", record.path)
        note = " and dropped the signature" if record.has_signature else ""
        migr = (
            f"\nUpgraded {record.version_label} -> v{FORMAT_VERSION}."
            if record.needs_migration
            else ""
        )
        return ActionResult(
            True,
            "Budget set",
            f"Saved: {saved}\nRewrote the integrity digest{note} (budget is inside it).{migr}",
            run=run,
            path=saved,
            refresh=True,
        )

    def sign(self, record: RunRecord, options: SignOptions) -> ActionResult:
        run = record.run
        if run is None:
            return _fail("Sign failed", "Run is not loaded.", record.path)
        if record.is_draft:
            return _fail("Sign refused", "Draft checkpoints cannot be signed.", record.path)
        if run.status.value not in ("completed", "failed"):
            return _fail(
                "Sign refused",
                f"Only completed/failed runs can be signed (status={run.status.value}).",
                record.path,
            )
        integrity = record.integrity
        if integrity is not None and not integrity.ok and not options.force:
            return _fail("Sign blocked", "Integrity check failed; re-run with Force.", record.path)
        try:
            key = self._resolve_sign_key(options)
            out = Path(options.save_path).expanduser() if options.save_path else record.path
            saved = run.save(
                out,
                sign_key=key,
                sign_algorithm=options.algorithm,
                key_id=options.key_id or None,
                signer=options.signer or None,
            )
        except Exception as exc:
            return _fail("Sign failed", f"{type(exc).__name__}: {exc}", record.path)
        migr = (
            f"\nUpgraded {record.version_label} -> v{FORMAT_VERSION}."
            if record.needs_migration
            else ""
        )
        return ActionResult(
            True,
            "Signed",
            f"Signed with {options.algorithm}\nSaved: {saved}{migr}",
            run=run,
            path=saved,
            refresh=True,
        )

    def verify_signature(self, record: RunRecord, options: VerifyKeyOptions) -> ActionResult:
        if signing is None or not hasattr(Run, "verify_signature"):
            return _fail("Verify unavailable", "No signing support installed.", record.path)
        try:
            kwargs: dict[str, Any] = {}
            if options.pubkey_file:
                kwargs["public_key"] = signing.ed25519_public_from_file(options.pubkey_file)
            if options.key_env:
                kwargs["hmac_key"] = signing.hmac_key_from_env(options.key_env)
            elif options.key_file:
                kwargs["hmac_key"] = signing.hmac_key_from_file(options.key_file)
            if options.trust_embedded:
                kwargs["trust_embedded"] = True
            result = Run.verify_signature(record.path, **kwargs)
        except Exception as exc:
            return _fail("Verify failed", f"{type(exc).__name__}: {exc}", record.path)
        message = (
            f"state: {result.state}\n"
            f"algorithm: {result.algorithm or '-'}\n"
            f"key id: {result.key_id or '-'}\n"
            f"signer: {result.signer or '-'} (display only — not a verified identity)\n"
            f"signed at: {result.signed_at or '-'}\n"
            f"{result.reason}"
        )
        title = "Signature verified" if result.ok else f"Signature {result.state}"
        return ActionResult(result.ok, title, message, path=record.path)

    def keygen(self, out_path: str | Path) -> ActionResult:
        if signing is None or not getattr(signing, "HAS_ED25519", False):
            return _fail("Keygen unavailable", "Ed25519 needs: pip install opentine[crypto]")
        try:
            private_hex, public_hex = signing.generate_ed25519()
            out = Path(out_path).expanduser()
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(private_hex + "\n", encoding="utf-8")
            os.chmod(out, 0o600)
            pub = out.with_name(out.name + ".pub")
            pub.write_text(public_hex + "\n", encoding="utf-8")
        except Exception as exc:
            return ActionResult(False, "Keygen failed", f"{type(exc).__name__}: {exc}")
        return ActionResult(
            True,
            "Key generated",
            f"private: {out} (chmod 0600 — keep secret)\npublic: {pub}\npublic key: {public_hex}",
        )

    def _resolve_sign_key(self, options: SignOptions) -> Any:
        if signing is None:
            raise ValueError("opentine signing support is unavailable")
        if options.algorithm == "ed25519":
            if not getattr(signing, "HAS_ED25519", False):
                raise ValueError("ed25519 unavailable — pip install opentine[crypto]")
            if not options.ed25519_key_file:
                raise ValueError("ed25519 signing requires a private key file")
            return signing.ed25519_private_from_file(options.ed25519_key_file)
        if options.key_env:
            return signing.hmac_key_from_env(options.key_env)
        if options.key_file:
            return signing.hmac_key_from_file(options.key_file)
        raise ValueError("HMAC signing requires a key (env var or file)")

    def resume(self, run: Run, path: str | Path) -> ActionResult:
        manifest = getattr(run, "manifest", {})
        if not manifest.get("resume", False):
            kind = manifest.get("kind", "unknown")
            return ActionResult(
                False,
                "Resume rejected",
                f"Run is not resumable: manifest kind={kind!r} does not declare resume support.",
                path=Path(path),
            )
        try:
            resumed = Run.resume(path)
            saved = self.repository.save(resumed, path)
        except Exception as exc:
            return ActionResult(False, "Resume failed", str(exc), path=Path(path))

        return ActionResult(
            True,
            "Resumed",
            (
                f"Loaded resumable run {short_id(resumed.id)} "
                f"({len(resumed.steps)} steps)\nSaved: {saved}"
            ),
            run=resumed,
            path=saved,
            refresh=True,
        )

    def run_harness(self, options: HarnessOptions) -> ActionResult:
        if not options.prompt:
            return ActionResult(False, "Harness rejected", "Prompt is required.")
        if OpentineHarness is None:
            return ActionResult(
                False,
                "Harness unavailable",
                "Installed opentine does not expose external harness support.",
            )
        try:
            harness = self._harness_from_options(options)
        except Exception as exc:
            return ActionResult(False, "Harness rejected", str(exc))

        wrapped = OpentineHarness(harness)
        out = Path(options.save_path) if options.save_path else None
        try:
            run = wrapped.run_sync(options.prompt, context={"cwd": options.cwd}, save_path=out)
        except Exception as exc:
            return self._save_failed_harness(wrapped, out, "Harness failed", exc)

        out = out or self.repository.path_for_run(run)
        path = self.repository.save(run, out)
        return ActionResult(
            True,
            "Harness completed",
            f"Ran {options.harness} harness\nSaved: {path}",
            run=run,
            path=path,
            refresh=True,
        )

    def fork_harness(
        self,
        run: Run,
        step_ref: str | None,
        options: HarnessOptions,
    ) -> ActionResult:
        if not options.prompt:
            return ActionResult(False, "Harness rejected", "Prompt is required.")
        if OpentineHarness is None:
            return ActionResult(
                False,
                "Harness unavailable",
                "Installed opentine does not expose external harness support.",
            )
        try:
            step_id = resolve_step_ref(run, step_ref)
            forked = run.fork(step_id)
            forked.metadata["next_harness"] = options.harness
            harness = self._harness_from_options(options)
        except Exception as exc:
            return ActionResult(False, "Fork harness rejected", str(exc))

        out = Path(options.save_path) if options.save_path else self.repository.path_for_run(forked)
        wrapped = OpentineHarness(harness, run=forked)
        context = {
            **run_context(run, step_id),
            "forked_from": run.id,
            "fork_point": step_id,
        }
        try:
            forked = wrapped.run_sync(options.prompt, context=context, save_path=out)
        except Exception as exc:
            return self._save_failed_harness(wrapped, out, "Fork harness failed", exc)

        path = self.repository.save(forked, out)
        return ActionResult(
            True,
            "Fork harness completed",
            f"Forked {short_id(run.id)} and ran {options.harness}\nSaved: {path}",
            run=forked,
            path=path,
            refresh=True,
        )

    def replay_harness(
        self,
        run: Run,
        step_ref: str | None,
        options: HarnessOptions,
    ) -> ActionResult:
        task = options.prompt or run.user_prompt
        if not task:
            return ActionResult(False, "Harness rejected", "Prompt is required.")
        if OpentineHarness is None:
            return ActionResult(
                False,
                "Harness unavailable",
                "Installed opentine does not expose external harness support.",
            )
        try:
            start = resolve_step_ref(run, step_ref) if step_ref is not None else None
            harness = self._harness_from_options(options)
        except Exception as exc:
            return ActionResult(False, "Replay harness rejected", str(exc))

        wrapped = OpentineHarness(harness)
        out = Path(options.save_path) if options.save_path else None
        try:
            replayed = wrapped.run_sync(task, context=run_context(run, start), save_path=out)
        except Exception as exc:
            return self._save_failed_harness(wrapped, out, "Replay harness failed", exc)

        out = out or self.repository.path_for_run(replayed)
        path = self.repository.save(replayed, out)
        return ActionResult(
            True,
            "Replay harness completed",
            f"Replayed {short_id(run.id)} with {options.harness}\nSaved: {path}",
            run=replayed,
            path=path,
            refresh=True,
        )

    def _harness_from_options(self, options: HarnessOptions):
        if options.harness not in HARNESS_FACTORIES:
            choices = ", ".join(sorted(HARNESS_FACTORIES))
            raise ValueError(f"Unknown harness {options.harness!r}; choose one of {choices}")
        command = shlex.split(options.command_override) if options.command_override else None
        if options.harness in {"generic", "pi"} and not command:
            raise ValueError(f"Command override is required for {options.harness}")
        factory = HARNESS_FACTORIES[options.harness]
        return factory(
            command=command,
            extra_args=options.extra_args,
            cwd=options.cwd,
            login_env=options.login_env,
            env_allowlist=options.env_allowlist,
        )

    def _save_failed_harness(
        self,
        wrapped: OpentineHarness,
        out: Path | None,
        title: str,
        exc: Exception,
    ) -> ActionResult:
        run = wrapped.run
        if run is None:
            return ActionResult(False, title, f"{type(exc).__name__}: {exc}")
        path = self.repository.save(run, out or self.repository.path_for_run(run))
        return ActionResult(
            False,
            title,
            f"{type(exc).__name__}: {exc}\nSaved failed run: {path}",
            run=run,
            path=path,
            refresh=True,
        )
