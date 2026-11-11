"""Action services for verifying, modifying, and launching opentine runs."""

from __future__ import annotations

import contextlib
import json
import os
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from opentine.core import Run, Step

from opentine_tui import keys
from opentine_tui.cli_bridge import arg_path, cli_error, run_tine
from opentine_tui.formatting import short_ref
from opentine_tui.repository import (
    ARTIFACT_V4_VERSION,
    RunRecord,
    RunRepository,
    current_format,
    safe_filename,
    verify_integrity,
)

try:
    import opentine.signing as signing
except Exception:  # pragma: no cover - defensive
    signing = None

#: Kept for callers that imported it: the format a save writes *now* (4 since
#: opentine 0.26, unless OPENTINE_FORMAT=2 pins the deprecated writer).
FORMAT_VERSION = current_format()

try:
    from opentine.redaction import redact_value
except Exception:  # pragma: no cover - defensive
    redact_value = None

try:
    from opentine.migrations import LEGACY_VERSION
except Exception:  # pragma: no cover - defensive
    LEGACY_VERSION = 0

try:
    from opentine.harnesses import OpentineHarness
except ModuleNotFoundError:  # pragma: no cover - opentine without harnesses
    OpentineHarness = None

try:
    # The CLI's own registry: 25 harnesses in 0.34 (aider, amp, goose, kiro, ...).
    # Reading it instead of a hand-kept list means a preset opentine adds shows up
    # in the dashboard without a release here.
    from opentine._cli_common import HARNESS_FACTORIES as _CLI_FACTORIES
except Exception:  # pragma: no cover - defensive
    _CLI_FACTORIES = {}

HARNESS_FACTORIES: dict[str, Any] = dict(_CLI_FACTORIES) if OpentineHarness is not None else {}

#: Harnesses that have no default command and need the user to name one.
COMMAND_REQUIRED = frozenset({"generic", "pi"})

try:  # the context the CLI hands a fork/rerun: the history LEADING TO the point
    from opentine._cli_common import _run_context as _cli_run_context
except Exception:  # pragma: no cover - defensive
    _cli_run_context = None


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
    # None keeps opentine's own bound (an hour); a harness that hangs otherwise
    # holds a worker thread for as long as the dashboard is open.
    timeout_seconds: float | None = None
    # A replay sends the run's recorded prompt only when the user vouches for it
    # (`tine replay --trust-recorded-prompt`, opentine 0.35).
    trust_recorded_prompt: bool = False


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
    # 0.3.0: treat a step opentine could not price as a cost breach, instead of
    # letting an unpriced call slip under a cost ceiling as $0.
    strict_cost: bool = False


@dataclass(slots=True)
class SignOptions:
    algorithm: str = "hmac-sha256"
    key_env: str | None = None
    key_file: str | None = None
    ed25519_key_file: str | None = None
    key_id: str | None = None
    signer: str | None = None
    save_path: str | None = None
    force: bool = False  # sign despite a failed integrity check
    overwrite: bool = False  # replace an existing destination file
    # For an encrypted Ed25519 key (0.41+). Never logged, never shown, never
    # passed to a child process; OPENTINE_KEY_PASSPHRASE is the fallback.
    passphrase: str | None = field(default=None, repr=False)


@dataclass(slots=True)
class VerifyKeyOptions:
    key_env: str | None = None
    key_file: str | None = None
    pubkey_file: str | None = None
    trust_embedded: bool = False


def short_id(value: str | None) -> str:
    """Abbreviate a run or step id, including v3 object ids."""
    return short_ref(value)


def _fork(run: Run, step_id: str, **kwargs: Any) -> Run:
    """``run.fork`` that tolerates an opentine without the 0.4.0 fork-act options.

    0.4.0 added ``intent=`` and ``nonce=`` and made a fork id identify the fork
    *act*; on an older build those keywords do not exist and the old, coordinate-
    derived id is the only behaviour available.
    """
    try:
        return run.fork(step_id, **kwargs)
    except TypeError:  # pragma: no cover - pre-0.4.0 opentine
        return run.fork(step_id)


def _redacts(tags: list[str]) -> bool:
    """True when opentine's redactor would rewrite one of these tags on save.

    Tags are written outside the digest by a raw JSON edit, so they survive
    verbatim — until any other action re-saves the run through opentine, which
    redacts credential-shaped strings anywhere in the document.
    """
    if redact_value is None:  # pragma: no cover - defensive
        return False
    try:
        return list(redact_value(list(tags))) != list(tags)
    except Exception:  # pragma: no cover - defensive
        return False


def _write_private(target: Path, data: bytes, mode: int) -> None:
    """Write ``data`` to ``target`` atomically, created at ``mode`` from the start.

    A fresh ``mkstemp`` file (0600, ``O_EXCL``, never a symlink) in the same
    directory is filled and then renamed over the target. Opening the target
    itself would follow a symlink planted there, and ``O_TRUNC`` on an existing
    0644 file keeps 0644 — leaving the seed world-readable until a chmod.
    """
    import tempfile

    descriptor, temp_name = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.")
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        temp = Path(temp_name)
        temp.chmod(mode)
        temp.replace(target)
    except BaseException:
        with contextlib.suppress(OSError):
            Path(temp_name).unlink()
        raise


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
    """What a harness is told about the run it continues or re-runs.

    The context of a fork is the history *leading to* the fork point — its
    ancestor closure — which is what ``tine fork --harness`` sends. Before 0.34
    this sent the descendant closure instead: the discarded future after the
    fork point, i.e. exactly the steps the user had chosen to throw away.
    """
    if _cli_run_context is not None:
        return _cli_run_context(run, from_step)
    if from_step is None:
        steps = run.steps
    else:
        start = run.graph.resolve(from_step)
        keep = {start, *run.graph.ancestors(start)} if hasattr(run.graph, "ancestors") else {start}
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
            # A v4 file's digest is its root, `run:sha256:<hex>`; a v2 one is bare
            # hex. Either way the comparable part is the hex.
            return ActionResult(
                True,
                "Verify OK",
                f"OK {path}\nsha256:{short_ref(digest)}",
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
            path = self.repository.save(forked, save_path, overwrite=False)
        except FileExistsError as exc:
            return _fail("Fork blocked", f"{exc} already exists; choose another save path.")
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
            # A cached replay reuses recorded steps and produces nothing new, so it
            # must be reproducible: `nonce=""` opts out of the 0.4.0 fork-act random
            # nonce and yields one id per (run, step). Replaying twice then lands on
            # the same artifact, where the overwrite refusal below is the guard.
            # (A fixed `<id>-replay` name, which 0.3.0 used, silently destroyed the
            # previous replay instead.)
            # Must match what `tine replay --mode cache` passes (_cli_flow.py and
            # _runtime_history.py both use exactly this): the intent digest is an
            # input to the fork id, so a different intent lands the dashboard's
            # replay on a different artifact than the CLI's for the same run+step.
            replayed = _fork(run, step_id, intent={"replay": "cache"}, nonce="")
            replayed.metadata["replay"] = {
                "mode": "cache",
                "source_run": run.id,
                "reused_steps": len(replayed.steps),
            }
            replayed.status = run.status
            path = self.repository.save(replayed, save_path, overwrite=False)
        except FileExistsError as exc:
            return _fail("Replay blocked", f"{exc} already exists; choose another save path.")
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

    def migrate(
        self,
        record: RunRecord,
        force: bool = False,
        save_path: str | Path | None = None,
        in_place: bool = False,
    ) -> ActionResult:
        """Upgrade a v1/v2/legacy artifact to format v4 — ``tine migrate``.

        Since 0.26 ``tine migrate`` writes ``NAME.v4.tine`` beside the original and
        never touches it; ``in_place`` is the explicit ``--in-place``, which keeps
        a ``NAME.tine.v2.bak``. The CLI is run as-is so the upgrade carries the
        same ``upgraded_from`` provenance (source digest and the source signature
        verdict, recorded as a claim) that ``tine migrate`` records.
        """
        path = record.path
        if record.is_future:
            return _fail(
                "Migrate refused", "Written by a newer opentine — upgrade the tool instead.", path
            )
        if record.run is None:
            return _fail("Migrate failed", record.error_message or "unreadable", path)
        if record.is_v4:
            return _fail("Migrate skipped", f"Already format v{ARTIFACT_V4_VERSION}.", path)
        if record.is_draft:
            return _fail(
                "Migrate refused",
                "Draft checkpoints stay v2 (v4 has no draft marker); migrate the final run.",
                path,
            )
        if not record.is_legacy:
            integrity = record.integrity
            if integrity is not None and not integrity.ok and not force:
                return _fail(
                    "Migrate blocked", "Source integrity check failed; re-run with Force.", path
                )
        if in_place and save_path:
            return _fail("Migrate refused", "Choose a save path or in-place, not both.", path)
        destination = (
            path
            if in_place
            else Path(save_path).expanduser()
            if save_path
            else path.with_name(f"{path.name.removesuffix('.tine')}.v4.tine")
        )
        if not in_place and destination.exists() and not force:
            return _fail(
                "Migrate blocked",
                f"{destination} already exists; enable Force to replace it.",
                path,
            )
        argv = ["migrate", arg_path(path), "--to", "v4"]
        if in_place:
            argv.append("--in-place")
        else:
            argv += ["--output", arg_path(destination)]
        if force:
            argv.append("--force")
        result = run_tine(argv, env=self._child_env())
        if not result.ok:
            return _fail("Migrate failed", result.text or f"tine exited {result.returncode}", path)
        note = ""
        if record.has_signature:
            note += (
                "\nThe source signature was checked and recorded as a claim in "
                "upgraded_from; the v4 file itself is unsigned — re-sign with 's'."
            )
        if record.is_legacy:
            note += "\nLegacy import recomputed step ids (best-effort)."
        if in_place:
            note += f"\nBackup: {path.name}.v2.bak"
        return ActionResult(
            True,
            "Migrated",
            f"Upgraded {record.version_label} -> v{ARTIFACT_V4_VERSION}\n"
            f"Saved: {destination}{note}\n\n{result.text}",
            path=destination,
            refresh=True,
        )

    def _child_env(self) -> dict[str, str]:
        """Point a child ``tine`` at the directory this dashboard is showing."""
        return {"OPENTINE_RUNS_DIR": str(self.repository.runs_dir.resolve())}

    def set_tags(self, record: RunRecord, add: list[str], remove: list[str]) -> ActionResult:
        run = record.run
        if run is None:
            return _fail("Tag failed", "Run is not loaded.", record.path)
        previous = list(run.tags)
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
            saved, signature_kept = self.repository.write_tags(record.path, run.tags)
        except Exception as exc:
            # The mutation lives on the cached Run the dashboard keeps handing out;
            # leaving it applied after a failed write means a later, unrelated save
            # commits an edit the user was told did not happen.
            run.tags = previous
            return _fail("Tag save failed", str(exc), record.path)
        note = ""
        if _redacts(run.tags):
            note = (
                "\nOne of these tags looks like a credential. It is written "
                "verbatim here, but opentine's redactor rewrites it the next time "
                "anything re-saves this run."
            )
        if not signature_kept:
            where = (
                "(outside the integrity digest; the signature covered the tags and was "
                "removed — re-sign with 's')"
            )
        elif record.has_signature:
            where = "(outside digest and signature — both preserved)"
        else:
            where = "(outside the integrity digest — preserved)"
        return ActionResult(
            True,
            "Tags updated",
            f"Tags: {tags}\nSaved: {saved}\n{where}{note}",
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
        manifest = getattr(run, "manifest", None)
        previous = dict(manifest) if isinstance(manifest, dict) else None
        try:
            run.set_budget(
                max_cost=options.max_cost,
                max_steps=options.max_steps,
                max_duration=options.max_duration,
                max_usage=options.max_usage,
                on_breach=options.on_breach,
                strict_cost=options.strict_cost,
            )
            saved = self.repository.save(run, record.path)
        except Exception as exc:
            # Same reason as tags: an unsaved budget must not survive on the cached
            # Run and ride along on the next successful write.
            if previous is not None:
                run.manifest.clear()
                run.manifest.update(previous)
            return _fail("Set budget failed", f"{type(exc).__name__}: {exc}", record.path)
        note = " and dropped the signature" if record.has_signature else ""
        migr = (
            f"\nUpgraded {record.version_label} -> v{current_format()}."
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
        self._plaintext_key = False
        try:
            key = self._resolve_sign_key(options)
            out = Path(options.save_path).expanduser() if options.save_path else record.path
            # `--force` (sign despite a failed integrity check) and `--overwrite`
            # (replace a different file) are separate decisions in `tine sign`, and
            # conflating them would let "yes, replace that file" quietly mean
            # "yes, sign this tampered artifact".
            if out != record.path and out.exists() and not options.overwrite:
                return _fail(
                    "Sign refused",
                    f"{out} already exists; enable Overwrite to replace it.",
                    record.path,
                )
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
            f"\nUpgraded {record.version_label} -> v{current_format()}."
            if record.needs_migration
            else ""
        )
        return ActionResult(
            True,
            "Signed",
            f"Signed with {options.algorithm}\nSaved: {saved}{migr}"
            + (
                "\nThe key file is plaintext hex (deprecated since opentine 0.41): "
                "encrypt it with ctrl+k."
                if getattr(self, "_plaintext_key", False)
                else ""
            ),
            run=run,
            path=saved,
            refresh=True,
        )

    def verify_signature(self, record: RunRecord, options: VerifyKeyOptions) -> ActionResult:
        if signing is None or not hasattr(Run, "verify_signature"):
            return _fail("Verify unavailable", "No signing support installed.", record.path)
        # One signature is checked against one key. Supplying several and letting a
        # precedence rule pick lets the artifact decide which key it is judged by,
        # so `tine verify` refuses the combination outright and so does this.
        if options.key_env and options.key_file:
            return _fail(
                "Verify refused",
                "Both an env var and a file name an HMAC key; pass the one you mean.",
                record.path,
            )
        if options.pubkey_file and (options.key_env or options.key_file):
            return _fail(
                "Verify refused",
                "An Ed25519 public key and an HMAC key were both supplied; pass one.",
                record.path,
            )
        if options.pubkey_file and options.trust_embedded:
            return _fail(
                "Verify refused",
                "Trust-on-first-use accepts the artifact's own key; do not also pin one.",
                record.path,
            )
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

    def keygen(
        self,
        out_path: str | Path,
        force: bool = False,
        passphrase: str | None = None,
        unencrypted: bool = False,
    ) -> ActionResult:
        """``tine keygen``: an encrypted PKCS#8 key by default (opentine 0.41).

        ``unencrypted`` writes the deprecated plaintext-hex form, as
        ``--unencrypted`` does; it must be asked for, never fallen back to.
        """
        if signing is None or not getattr(signing, "HAS_ED25519", False):
            return _fail("Keygen unavailable", "Ed25519 needs: pip install opentine[crypto]")
        out = Path(out_path).expanduser()
        # Overwriting a private key destroys the only copy of a signing identity
        # and makes every artifact it signed unverifiable, which is why
        # `tine keygen` refuses without --force.
        pub = out.with_name(out.name + ".pub")
        if (out.exists() or out.is_symlink()) and not force:
            return _fail("Keygen refused", f"{out} already exists; enable Force to replace it.")
        for target in (out, pub):
            if target.is_symlink():
                # Writing "through" a link would put the seed wherever it points.
                return _fail(
                    "Keygen refused", f"{target} is a symbolic link; refusing to follow it."
                )
        try:
            private_bytes, public_hex = keys.generate(passphrase, unencrypted=unencrypted)
        except keys.KeyError_ as exc:
            return _fail("Keygen refused", str(exc))
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            _write_private(out, private_bytes, 0o600)
            _write_private(pub, (public_hex + "\n").encode(), 0o644)
        except Exception as exc:
            return ActionResult(False, "Keygen failed", f"{type(exc).__name__}: {exc}")
        form = (
            "PLAINTEXT hex (deprecated — anyone who can read the file can sign as you)"
            if unencrypted
            else "encrypted PKCS#8 (AES-256)"
        )
        return ActionResult(
            True,
            "Key generated",
            f"private: {out} (0600, {form})\npublic: {pub}\npublic key: {public_hex}",
        )

    def encrypt_key(self, path: str | Path, passphrase: str | None) -> ActionResult:
        """``tine key encrypt``: rewrite a plaintext-hex key as encrypted PKCS#8."""
        target = Path(path).expanduser()
        if target.is_symlink():
            return _fail("Encrypt refused", f"{target} is a symbolic link; refusing to follow it.")
        try:
            data = keys.encrypt_existing(target, passphrase)
            _write_private(target, data, 0o600)
        except keys.KeyError_ as exc:
            return _fail("Encrypt refused", str(exc))
        except Exception as exc:
            return _fail("Encrypt failed", f"{type(exc).__name__}: {exc}")
        return ActionResult(True, "Key encrypted", f"{target} is now encrypted PKCS#8 (0600).")

    def _resolve_sign_key(self, options: SignOptions) -> Any:
        if signing is None:
            raise ValueError("opentine signing support is unavailable")
        if options.key_env and options.key_file:
            raise ValueError("Both an env var and a file name an HMAC key; pass the one you mean.")
        if options.algorithm == "ed25519":
            if not getattr(signing, "HAS_ED25519", False):
                raise ValueError("ed25519 unavailable — pip install opentine[crypto]")
            if not options.ed25519_key_file:
                raise ValueError("ed25519 signing requires a private key file")
            loaded = keys.load_signing_key(options.ed25519_key_file, options.passphrase)
            self._plaintext_key = loaded.plaintext
            return loaded.signer
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
        signed = self.repository.inspect_path(path).has_signature
        try:
            resumed = Run.resume(path)
            saved = self.repository.save(resumed, path)
        except Exception as exc:
            return ActionResult(False, "Resume failed", str(exc), path=Path(path))
        note = "\nThe signature was removed by this re-save — re-sign with 's'." if signed else ""
        return ActionResult(
            True,
            "Resumed",
            (
                f"Loaded resumable run {short_id(resumed.id)} "
                f"({len(resumed.steps)} steps)\nSaved: {saved}{note}"
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
            self._check_destination(options)
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
            self._check_destination(options)
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
        # opentine 0.35: the recorded prompt is data from the artifact, and a
        # replay used to forward it to an agent CLI unasked. It is sent only when
        # the user vouches for it; otherwise a prompt has to be typed.
        task = options.prompt or (run.user_prompt if options.trust_recorded_prompt else "")
        if not task:
            return ActionResult(
                False,
                "Harness rejected",
                "Type a prompt, or turn on 'send the recorded prompt' to vouch for the "
                "one stored in the run.",
            )
        if OpentineHarness is None:
            return ActionResult(
                False,
                "Harness unavailable",
                "Installed opentine does not expose external harness support.",
            )
        try:
            start = resolve_step_ref(run, step_ref) if step_ref is not None else None
            self._check_destination(options)
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

    # -- opentine 0.22-0.34 features, driven through `tine` ---------------

    def _tine(self, title: str, argv: list[str]) -> tuple[Any, ActionResult | None]:
        result = run_tine(argv, env=self._child_env())
        payload = result.json() if "--json" in argv else None
        message = cli_error(payload)
        if message is not None:
            # opentine 0.36: under --json a failure is {"error": {code, message}}.
            return None, _fail(title, message)
        if payload is None and not result.ok:
            return None, _fail(title, result.text or f"tine exited {result.returncode}")
        return (payload if payload is not None else result), None

    def verify_report(self, record: RunRecord, strict: bool = False) -> ActionResult:
        argv = ["verify", arg_path(record.path), "--offline", "--json"]
        if strict:
            argv.append("--strict")
        payload, failure = self._tine("Verify report failed", argv)
        if failure:
            return failure
        if not isinstance(payload, dict):
            return _fail("Verify report failed", "tine returned no report", record.path)
        ok = payload.get("verdict") == "pass" or bool(payload.get("ok"))
        return ActionResult(
            ok,
            f"Offline verify: {payload.get('verdict', '?')}",
            verify_report_text(payload),
            path=record.path,
        )

    def replay_verify(self, record: RunRecord) -> ActionResult:
        payload, failure = self._tine(
            "Replay verify failed", ["replay", arg_path(record.path), "--verify", "--json"]
        )
        if failure:
            return failure
        if not isinstance(payload, dict):
            return _fail("Replay verify failed", "tine returned no result", record.path)
        overall = (payload.get("fidelity") or {}).get("overall", "?")
        return ActionResult(
            bool(payload.get("ok")),
            f"Replay fidelity: {overall}",
            fidelity_text(payload),
            path=record.path,
        )

    def fork_edit(
        self,
        record: RunRecord,
        step_ref: str | None,
        sets: list[str],
        unsets: list[str],
        save_path: str | None = None,
    ) -> ActionResult:
        """``tine fork --from-step K --set PATH=VALUE --unset PATH`` (0.28/0.33)."""
        run = record.run
        if run is None:
            return _fail("Fork failed", "Run is not loaded.", record.path)
        if not sets and not unsets:
            return _fail("Fork failed", "Give at least one --set or --unset edit.", record.path)
        try:
            step_id = resolve_step_ref(run, step_ref)
        except Exception as exc:
            return _fail("Fork failed", str(exc), record.path)
        out = (
            Path(save_path).expanduser()
            if save_path
            else (
                self.repository.runs_dir
                / f"{safe_filename(record.path.stem)}.edit-{safe_filename(short_id(step_id))}.tine"
            )
        )
        if out.exists():
            return _fail(
                "Fork blocked", f"{out} already exists; choose another save path.", record.path
            )
        # `--opt=value` form throughout: a step id comes out of the artifact and an
        # edit is typed by the user, and either could start with "-".
        argv = ["fork", arg_path(record.path), f"--from-step={step_id}", f"--save={arg_path(out)}"]
        argv += [f"--set={item}" for item in sets]
        argv += [f"--unset={item}" for item in unsets]
        result = run_tine(argv, env=self._child_env())
        if not result.ok:
            return _fail(
                "Fork failed", result.text or f"tine exited {result.returncode}", record.path
            )
        return ActionResult(
            True,
            "Forked with edits",
            f"Saved: {out}\nThe edited step is recorded unexecuted; "
            f"the edit log is bound into the fork id.\n\n{result.text}",
            path=out,
            refresh=True,
        )

    def rewind(
        self,
        record: RunRecord,
        step_ref: str | None,
        root: str,
        apply: bool = False,
    ) -> ActionResult:
        """``tine rewind RUN@STEP --root DIR [--apply]`` (0.22/0.32). Dry run by default."""
        run = record.run
        if run is None:
            return _fail("Rewind failed", "Run is not loaded.", record.path)
        target = arg_path(record.path)
        if step_ref is not None:
            try:
                step_id = resolve_step_ref(run, step_ref)
            except Exception as exc:
                return _fail("Rewind failed", str(exc), record.path)
            index = next(i for i, step in enumerate(run.steps) if step.id == step_id)
            target = f"{target}@{index}"
        elif "@" in target:
            return _fail(
                "Rewind failed",
                "Select a step: this path contains '@', which tine reads as a step marker.",
                record.path,
            )
        directory = Path(root).expanduser()
        if not directory.is_dir():
            return _fail("Rewind failed", f"Not a directory: {root}", record.path)
        argv = ["rewind", target, f"--root={arg_path(directory)}", "--json"]
        if apply:
            argv.append("--apply")
        payload, failure = self._tine("Rewind failed", argv)
        if failure:
            return failure
        if not isinstance(payload, dict):
            return _fail("Rewind failed", "tine returned no plan", record.path)
        blocked = bool(payload.get("conflict"))
        title = (
            "Rewound"
            if payload.get("applied")
            else "Rewind blocked by conflicts"
            if apply
            else "Rewind plan"
        )
        return ActionResult(not (apply and blocked), title, rewind_text(payload), path=record.path)

    def view_html(self, record: RunRecord, out: str, overwrite: bool = False) -> ActionResult:
        """``tine view RUN -o FILE`` (0.24): a self-contained HTML page for the run."""
        target = Path(out).expanduser()
        if target.is_dir():
            return _fail(
                "View failed", f"{target} is a directory; give an .html path.", record.path
            )
        if target.exists() and not overwrite:
            return _fail("View blocked", f"{target} already exists; enable Overwrite.", record.path)
        result = run_tine(
            ["view", arg_path(record.path), "--output", arg_path(target)], env=self._child_env()
        )
        if not result.ok:
            return _fail(
                "View failed", result.text or f"tine exited {result.returncode}", record.path
            )
        return ActionResult(True, "HTML written", f"Saved: {target}", path=target)

    def _harness_from_options(self, options: HarnessOptions):
        if options.harness not in HARNESS_FACTORIES:
            choices = ", ".join(sorted(HARNESS_FACTORIES))
            raise ValueError(f"Unknown harness {options.harness!r}; choose one of {choices}")
        command = shlex.split(options.command_override) if options.command_override else None
        if options.harness in COMMAND_REQUIRED and not command:
            raise ValueError(f"Command override is required for {options.harness}")
        if options.cwd and not Path(options.cwd).expanduser().is_dir():
            raise ValueError(f"cwd is not a directory: {options.cwd}")
        factory = HARNESS_FACTORIES[options.harness]
        kwargs: dict[str, Any] = {
            "command": command,
            "extra_args": options.extra_args,
            "cwd": options.cwd,
            "login_env": options.login_env,
            "env_allowlist": options.env_allowlist,
        }
        if options.timeout_seconds is not None:
            if not options.timeout_seconds > 0:
                raise ValueError("timeout must be a positive number of seconds")
            kwargs["timeout_seconds"] = options.timeout_seconds
        return factory(**kwargs)

    def _check_destination(self, options: HarnessOptions) -> None:
        """Refuse before launching, not after: a harness run is not repeatable."""
        if options.save_path and Path(options.save_path).expanduser().exists():
            raise FileExistsError(f"{options.save_path} already exists; choose another path")

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
        try:
            path = self.repository.save(run, out or self.repository.path_for_run(run))
        except Exception as save_exc:
            return ActionResult(
                False,
                title,
                f"{type(exc).__name__}: {exc}\n"
                f"The partial run could not be saved either: {type(save_exc).__name__}: {save_exc}",
            )
        return ActionResult(
            False,
            title,
            f"{type(exc).__name__}: {exc}\nSaved failed run: {path}",
            run=run,
            path=path,
            refresh=True,
        )


# -- opentine 0.25-0.34 features, driven through `tine` --------------------

_OUTCOME_GLYPHS = {
    "pass": "✓",
    "fail": "✗",
    "unavailable-offline": "·",
    "not-applicable": "-",
}


#: opentine's assurance tiers (`opentine._assurance`), in words.
ASSURANCE_TIERS = {
    "T0": "self-reported (the agent's own transcript)",
    "T1": "hook-captured, turn by turn",
    "T2": "witnessed and signed by a proxy key",
    "T3": "witnessed, plus an RFC 3161 timestamp",
}


def verify_report_text(report: dict[str, Any]) -> str:
    """Render a ``tine-verify-report/1`` or ``/2`` as rows the panel can show."""
    lines = [
        f"verdict: {report.get('verdict', '?')}" + ("  (strict)" if report.get("strict") else ""),
    ]
    assurance = report.get("assurance")
    if isinstance(assurance, dict):
        # opentine 0.39: how much of the run something other than the agent saw.
        verified = str(assurance.get("verified", "?"))
        claimed = str(assurance.get("claimed", "?"))
        lines.append(
            f"assurance: {verified} verified"
            + (f" (claims {claimed})" if claimed != verified else "")
            + f" — {ASSURANCE_TIERS.get(verified, 'unknown tier')}"
        )
        lines += [f"    {reason}" for reason in (assurance.get("reasons") or [])[:4]]
    lines.append("")
    for check in report.get("checks") or []:
        if not isinstance(check, dict):
            continue
        outcome = str(check.get("outcome", "?"))
        if outcome == "not-applicable":
            continue
        glyph = _OUTCOME_GLYPHS.get(outcome, "?")
        required = " (required)" if check.get("required") else ""
        lines.append(f"{glyph} {check.get('name', '?')}: {outcome}{required}")
        detail = check.get("detail")
        if detail:
            lines.append(f"    {detail}")
        basis = check.get("trust_basis")
        if basis and outcome == "pass":
            lines.append(f"    trust: {basis}")
    missing = report.get("unavailable_required") or []
    if missing:
        lines += ["", f"Required but not checkable offline: {', '.join(map(str, missing))}"]
    stamps = report.get("timestamps") or []
    if stamps:
        lines += ["", "Timestamps:"]
        for stamp in stamps:
            if isinstance(stamp, dict):
                lines.append(f"  {stamp.get('gen_time', '?')}  {stamp.get('tsa', '')}")
    return "\n".join(lines)


def fidelity_text(payload: dict[str, Any]) -> str:
    fidelity = payload.get("fidelity") or {}
    counts = fidelity.get("counts") or {}
    lines = [
        f"reproduced: {'yes' if payload.get('ok') else 'NO'}",
        f"overall fidelity: {fidelity.get('overall', '?')}",
        "  "
        + "  ".join(
            f"{name}={counts.get(name, 0)}"
            for name in ("exact", "recomputed-match", "unavailable", "diverged")
        ),
    ]
    drift = payload.get("drift") or {}
    for name in ("structural", "only_source", "only_replay", "accounting"):
        items = drift.get(name) or []
        if items:
            lines.append(f"{name.replace('_', ' ')} drift: {len(items)}")
    weak = [
        s
        for s in fidelity.get("steps") or []
        if isinstance(s, dict) and s.get("label") not in ("exact", None)
    ]
    if weak:
        lines += ["", "Steps below exact:"]
        lines += [f"  {short_id(str(s.get('id')))}  {s.get('label')}" for s in weak[:40]]
    return "\n".join(lines)


def rewind_text(payload: dict[str, Any]) -> str:
    lines = [
        f"root: {payload.get('root')}",
        f"to step {payload.get('step_index')} "
        f"{short_id(payload.get('step') or '') or '(before run)'}",
        "applied" if payload.get("applied") else "dry run — nothing written",
        "",
    ]
    for status in ("restore", "unchanged", "conflict", "unrecoverable", "refused"):
        rows = payload.get(status) or []
        if not rows:
            continue
        lines.append(f"{status} ({len(rows)}):")
        for row in rows[:60]:
            mode = row.get("mode")
            reason = row.get("reason")
            lines.append(
                f"  {row.get('path')}"
                + (f"  [{mode}]" if mode else "")
                + (f"  {reason}" if reason else "")
            )
    if len(lines) == 4:
        lines.append("No file edits recorded by this run.")
    return "\n".join(lines)
