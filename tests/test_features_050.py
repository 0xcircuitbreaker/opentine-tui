"""opentine 0.35-0.50 features: explain, compliance, evidence, audit kit,
redaction check, op log, undo, holds and merge."""

from __future__ import annotations

from pathlib import Path

import pytest
from opentine.core import Run, RunStatus, StepKind

from opentine_tui.actions import RunActionService
from opentine_tui.app import OpentineTUI
from opentine_tui.repo_actions import ImportOptions, RepoActionService
from opentine_tui.repository import RunRepository
from opentine_tui.v3 import V3Repository
from opentine_tui.widgets.step_detail import StepDetail


def _run(run_id: str = "f50", text: str = "z") -> Run:
    run = Run(id=run_id, model_info="m", user_prompt="summarise the repo")
    first = run.add_step(StepKind.think, {"text": "a"})
    tool = run.add_step(
        StepKind.tool, {"name": "read", "arguments": {"p": "x"}}, parent_id=first.id
    )
    run.add_step(StepKind.done, {"text": text}, parent_id=tool.id)
    run.status = RunStatus.completed
    return run


@pytest.fixture
def service(tmp_path: Path) -> RunActionService:
    runs = tmp_path / "runs"
    runs.mkdir()
    return RunActionService(RunRepository(runs))


@pytest.fixture
def record(service):
    return service.repository.inspect_path(service.repository.save(_run()))


def test_explain_summarises_without_writing(service, record):
    before = record.path.read_bytes()
    result = service.explain(record)
    assert result.ok, result.message
    assert "asked: summarise the repo" in result.message
    assert "outcome: completed" in result.message
    assert record.path.read_bytes() == before  # --no-store: explaining is a read


def test_compliance_lists_profiles_and_checks_one(service, record):
    names = [name for name, _ in service.compliance_profiles()]
    assert "eu-ai-act" in names
    result = service.compliance(record, "eu-ai-act")
    assert "evidenced" in result.message
    assert "not legal advice" in result.message
    assert "Art. 12" in result.message


def test_evidence_report_writes_html_and_refuses_to_clobber(service, record, tmp_path):
    out = tmp_path / "ev.html"
    assert service.evidence_report(record, str(out)).ok
    assert out.read_text(encoding="utf-8").lower().lstrip().startswith("<!doctype html")
    assert not service.evidence_report(record, str(out)).ok
    assert service.evidence_report(record, str(out), overwrite=True).ok
    assert not service.evidence_report(record, str(out), fmt="exe").ok


def test_audit_kit_builds_a_folder_and_zip(service, record, tmp_path):
    out = tmp_path / "kit"
    result = service.audit_kit(record, str(out))
    assert result.ok, result.message
    assert (out / "kit.json").is_file() and out.with_suffix(".zip").is_file()
    assert not service.audit_kit(record, str(out)).ok


@pytest.fixture
def repo(tmp_path: Path):
    repository = V3Repository.init(tmp_path / "repo")
    service = RepoActionService(repository)
    for name in ("a", "b"):
        path = tmp_path / f"{name}.tine"
        _run(name, text=name).save(path)
        assert service.import_v2(ImportOptions(source=str(path), ref=f"heads/{name}")).ok
    repository.invalidate()
    return repository, service


def test_redaction_check_reports_a_clean_run(repo):
    repository, service = repo
    record = repository.list_runs()[0]
    result = service.redact_check(record)
    assert result.ok, result.message
    assert "clean" in result.message


def test_op_log_and_undo(repo):
    repository, service = repo
    log = service.op_log()
    assert log.ok and "import" in log.message
    before = repository.repo.read_ref("heads/b")
    undone = service.undo()
    assert undone.ok, undone.message
    assert repository.repo.read_ref("heads/b") != before
    assert not service.undo("--force").ok  # an option is never an op id


def test_hold_toggles(repo):
    repository, service = repo
    record = repository.list_runs()[0]
    assert service.toggle_hold(record).title == "Run held"
    assert record.oid in service.holds()
    assert service.toggle_hold(record).title == "Hold released"
    assert record.oid not in service.holds()


def test_merge_makes_a_two_parent_run(repo):
    repository, service = repo
    a = repository.find("heads/a")
    result = service.merge(a, "heads/b", "heads/merged")
    assert result.ok, result.message
    assert repository.repo.read_ref("heads/merged")
    assert not service.merge(a, "--evil").ok


@pytest.mark.asyncio
async def test_new_keys_are_reachable_from_the_ui(tmp_path: Path):
    runs = tmp_path / "runs"
    runs.mkdir()
    RunRepository(runs).save(_run())
    app = OpentineTUI(runs_dir=runs, repo_path=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("X")
        for _ in range(100):
            await pilot.pause(0.05)
            if "asked:" in app.query_one(StepDetail).last_rendered:
                break
        assert "asked: summarise the repo" in app.query_one(StepDetail).last_rendered
        # repository-only keys are disabled on the Files tab
        assert app.check_action("op_log", ()) is not True
        assert app.check_action("merge_runs", ()) is not True
