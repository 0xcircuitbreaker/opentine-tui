"""Action services for verifying, modifying, and launching opentine runs."""

from __future__ import annotations

import json
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from opentine.core import Run, Step

from opentine_tui.repository import RunRepository, verify_integrity

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


def short_id(value: str | None) -> str:
    return (value or "")[:12]


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


def diff_text(run_a: Run, run_b: Run) -> str:
    if not hasattr(run_a, "diff"):
        return fallback_diff_text(run_a, run_b)

    diff = run_a.diff(run_b)
    lines = [
        f"Diff: {short_id(run_a.id)} vs {short_id(run_b.id)}",
        f"Common ancestor: {short_id(diff.common_ancestor) if diff.common_ancestor else '-'}",
        "",
        f"Only in {short_id(run_a.id)}:",
    ]
    lines.extend(f"  {step_label(step)}" for step in diff.only_a)
    if not diff.only_a:
        lines.append("  (none)")
    lines.extend(["", f"Only in {short_id(run_b.id)}:"])
    lines.extend(f"  {step_label(step)}" for step in diff.only_b)
    if not diff.only_b:
        lines.append("  (none)")
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
