"""Service-level coverage for run management actions."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from opentine.core import Run, RunStatus, StepKind
from opentine.harnesses import HarnessStep

from opentine_tui import actions
from opentine_tui.actions import HarnessOptions, RunActionService
from opentine_tui.repository import RunRepository


class DummyHarness:
    name = "dummy"
    model_info = "dummy-model"
    supports_resume = False
    seen: dict[str, Any] = {}
    should_fail = False

    def __init__(
        self,
        *,
        command=None,
        extra_args=(),
        cwd=None,
        login_env=False,
        env_allowlist=(),
    ):
        type(self).seen = {
            "command": command,
            "extra_args": tuple(extra_args),
            "cwd": cwd,
            "login_env": login_env,
            "env_allowlist": tuple(env_allowlist),
        }

    async def execute(self, task: str, context=None, step_callback=None):
        if step_callback:
            step_callback(
                HarnessStep(
                    kind=StepKind.think,
                    inputs={"text": f"working on {task}", "context": context},
                )
            )
        if type(self).should_fail:
            raise RuntimeError("dummy failure")
        return {"ok": True, "task": task}


@pytest.fixture
def service(tmp_path: Path) -> RunActionService:
    return RunActionService(RunRepository(tmp_path / ".tine_runs"))


@pytest.fixture
def source_run() -> Run:
    run = Run(id="source-run", model_info="mock-model", user_prompt="test prompt")
    run.add_step(StepKind.think, {"text": "thinking"})
    run.add_step(StepKind.tool, {"name": "search", "arguments": {"q": "opentine"}})
    run.add_step(StepKind.done, {"text": "done"})
    run.status = RunStatus.completed
    return run


def test_verify_ok_and_failure(service: RunActionService, source_run: Run):
    path = service.repository.save(source_run)

    ok = service.verify(path)
    assert ok.ok
    assert "sha256" in ok.message

    path.write_text(path.read_text(encoding="utf-8").replace("done", "changed", 1))
    failed = service.verify(path)
    assert not failed.ok
    assert "digest mismatch" in failed.message


def test_fork_writes_new_run(service: RunActionService, source_run: Run):
    result = service.fork(source_run, source_run.steps[1].id)

    assert result.ok
    assert result.path is not None
    forked = Run.load(result.path)
    assert forked.metadata["forked_from"] == source_run.id
    assert len(forked.steps) == 2


def test_cache_replay_writes_replay_run(service: RunActionService, source_run: Run):
    result = service.cache_replay(source_run, source_run.steps[-1].id)

    assert result.ok
    assert result.path is not None
    replayed = Run.load(result.path)
    assert replayed.metadata["replay"]["mode"] == "cache"
    assert replayed.metadata["replay"]["source_run"] == source_run.id
    assert replayed.status == source_run.status


def test_diff_displays_only_and_common_steps(service: RunActionService, source_run: Run):
    service.repository.save(source_run)
    forked = source_run.fork(source_run.steps[1].id, new_run_id="forked")
    forked.add_step(StepKind.done, {"text": "alternate"})
    fork_path = service.repository.save(forked)

    result = service.diff(source_run, fork_path)

    assert result.ok
    assert "Common ancestor" in result.message
    assert "Only in source-run" in result.message
    assert "alternate" in result.message


def test_resume_rejects_non_resumable_run(service: RunActionService, source_run: Run):
    path = service.repository.save(source_run)

    result = service.resume(source_run, path)

    assert not result.ok
    assert "not resumable" in result.message


def test_resume_updates_resumable_run(service: RunActionService, source_run: Run):
    source_run.manifest["resume"] = True
    path = service.repository.save(source_run)
    source_run.pause(path)

    result = service.resume(source_run, path)

    assert result.ok
    assert result.path is not None
    assert Run.load(result.path).status == RunStatus.running


def test_harness_launch_uses_options_and_saves_completed_run(
    monkeypatch: pytest.MonkeyPatch,
    service: RunActionService,
):
    monkeypatch.setitem(actions.HARNESS_FACTORIES, "codex", DummyHarness)
    DummyHarness.should_fail = False

    result = service.run_harness(
        HarnessOptions(
            harness="codex",
            prompt="inspect repo",
            cwd="/tmp/work",
            command_override="agent run",
            extra_args=("--json",),
            login_env=True,
            env_allowlist=("TOKEN",),
        )
    )

    assert result.ok
    assert result.path is not None
    saved = Run.load(result.path)
    assert saved.status == RunStatus.completed
    assert saved.metadata["harness"] == "dummy"
    assert DummyHarness.seen == {
        "command": ["agent", "run"],
        "extra_args": ("--json",),
        "cwd": "/tmp/work",
        "login_env": True,
        "env_allowlist": ("TOKEN",),
    }


def test_harness_launch_saves_failed_partial_run(
    monkeypatch: pytest.MonkeyPatch,
    service: RunActionService,
):
    monkeypatch.setitem(actions.HARNESS_FACTORIES, "codex", DummyHarness)
    DummyHarness.should_fail = True

    result = service.run_harness(HarnessOptions(harness="codex", prompt="fail"))

    assert not result.ok
    assert result.path is not None
    saved = Run.load(result.path)
    assert saved.status == RunStatus.failed
    assert "Saved failed run" in result.message

    DummyHarness.should_fail = False
