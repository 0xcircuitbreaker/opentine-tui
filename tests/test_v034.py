"""opentine 0.34: format v4, compressed artifacts, and the CLI-backed features.

Everything here runs with opentine's *default* writer (format v4) and, where it
matters, with compression on — the shape users' artifacts actually have now.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from opentine.core import Run, RunStatus, StepKind

from opentine_tui.actions import HARNESS_FACTORIES, RunActionService, run_context
from opentine_tui.app import OpentineTUI
from opentine_tui.formatting import escape_markup, plain_text
from opentine_tui.interop import IMPORT_FORMATS, NATIVE_FORMATS, InteropService, TraceImportOptions
from opentine_tui.repository import RunRepository
from opentine_tui.widgets.step_detail import StepDetail

HMAC_KEY = b"0123456789abcdef0123456789abcdef"


def _run(run_id: str = "r34") -> Run:
    run = Run(id=run_id, model_info="mock-model", user_prompt="hello")
    think = run.add_step(StepKind.think, {"text": "thinking"})
    tool = run.add_step(
        StepKind.tool, {"name": "search", "arguments": {"q": "opentine"}}, parent_id=think.id
    )
    run.add_step(StepKind.done, {"text": "done"}, parent_id=tool.id)
    run.status = RunStatus.completed
    return run


@pytest.fixture
def service(tmp_path: Path) -> RunActionService:
    runs = tmp_path / ".tine_runs"
    runs.mkdir()
    return RunActionService(RunRepository(runs))


# -- reading what 0.34 writes ----------------------------------------------


def test_a_compressed_v4_artifact_is_read_not_reported_corrupt(service, monkeypatch):
    monkeypatch.setenv("OPENTINE_COMPRESS", "zlib")
    path = service.repository.save(_run())
    assert path.read_bytes().startswith(b"TINEZ1")

    record = service.repository.inspect_path(path)

    assert not record.is_corrupt, record.error_message
    assert record.on_disk_version == 4
    assert record.version_label == "v4"
    assert record.integrity.ok
    assert not record.can_upgrade


def test_a_future_format_is_flagged_not_corrupt(service):
    path = service.repository.runs_dir / "future.tine"
    path.write_text(json.dumps({"format_version": 99, "kind": "x"}), encoding="utf-8")
    record = service.repository.inspect_path(path)
    assert record.is_future
    assert not record.is_corrupt


def test_verify_of_a_v4_run_shows_the_root_digest(service):
    path = service.repository.save(_run())
    result = service.verify(path)
    assert result.ok
    assert "run:sha256" not in result.message  # the comparable hex, not the oid prefix


def test_tags_on_a_signed_v4_run_report_the_dropped_signature(service):
    path = service.repository.runs_dir / "s.tine"
    _run().save(path, sign_key=HMAC_KEY, key_id="k1")
    record = service.repository.inspect_path(path)
    assert record.is_v4 and record.has_signature

    result = service.set_tags(record, ["prod"], [])

    assert result.ok, result.message
    assert "removed" in result.message
    assert "prod" in Run.load(path).tags
    assert Run.verify_integrity(path).ok


# -- audit fixes -------------------------------------------------------------


def test_terminal_control_bytes_are_stripped_from_displayed_text():
    hostile = "ok\x1b]8;;http://evil\x07link\x1b[2J‮reversed\nnext\tline"
    cleaned = plain_text(hostile)
    assert "\x1b" not in cleaned and "\x07" not in cleaned and "‮" not in cleaned
    assert "\n" in cleaned and "\t" in cleaned
    assert escape_markup("[bold]x\x1b[0m") == r"\[bold]x\[0m"


def test_harness_context_is_the_history_before_the_fork_point():
    run = _run()
    middle = run.steps[1].id
    ids = {step["id"] for step in run_context(run, middle)["steps"]}
    assert run.steps[0].id in ids and middle in ids
    assert run.steps[2].id not in ids  # the discarded future is not sent


def test_the_harness_list_is_opentines_own():
    from opentine._cli_common import HARNESS_FACTORIES as CLI

    assert set(HARNESS_FACTORIES) == set(CLI)
    assert len(HARNESS_FACTORIES) > 11


@pytest.mark.asyncio
async def test_an_action_that_raises_does_not_kill_the_app(tmp_path: Path, monkeypatch):
    runs = tmp_path / ".tine_runs"
    runs.mkdir()
    RunRepository(runs).save(_run())
    app = OpentineTUI(runs_dir=runs, repo_path=tmp_path)

    def boom(*_args, **_kwargs):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(app.actions, "verify_report", boom)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("V")
        for _ in range(40):
            await pilot.pause()
            if "disk on fire" in app.query_one(StepDetail).last_rendered:
                break
        assert "disk on fire" in app.query_one(StepDetail).last_rendered
        assert app.is_running


# -- CLI-backed features -----------------------------------------------------


def test_offline_verify_report(service):
    record = service.repository.inspect_path(service.repository.save(_run()))
    result = service.verify_report(record)
    assert result.ok, result.message
    assert "digest: pass" in result.message


def test_replay_fidelity(service):
    record = service.repository.inspect_path(service.repository.save(_run()))
    result = service.replay_verify(record)
    assert result.ok, result.message
    assert "overall fidelity: exact" in result.message


def test_fork_with_an_edited_step(service):
    source = _run()
    record = service.repository.inspect_path(service.repository.save(source))
    out = service.repository.runs_dir / "edited.tine"

    result = service.fork_edit(
        record, source.steps[1].id, ['inputs.arguments.q="tine"'], [], str(out)
    )

    assert result.ok, result.message
    forked = Run.load(out)
    assert forked.metadata["edit"]["changes"][0]["path"] == "inputs.arguments.q"
    assert forked.steps[-1].inputs["arguments"]["q"] == "tine"
    # never overwrites
    again = service.fork_edit(record, source.steps[1].id, ['inputs.arguments.q="x"'], [], str(out))
    assert not again.ok


def test_rewind_is_a_dry_run_by_default(service, tmp_path: Path):
    record = service.repository.inspect_path(service.repository.save(_run()))
    result = service.rewind(record, None, str(tmp_path))
    assert result.ok, result.message
    assert "dry run" in result.message


def test_view_writes_html_and_refuses_to_clobber(service, tmp_path: Path):
    record = service.repository.inspect_path(service.repository.save(_run()))
    out = tmp_path / "run.html"
    assert service.view_html(record, str(out)).ok
    assert out.read_text(encoding="utf-8").lstrip().lower().startswith("<!doctype html")
    assert not service.view_html(record, str(out)).ok
    assert service.view_html(record, str(out), overwrite=True).ok


def test_a_path_that_looks_like_a_flag_is_not_a_flag(service):
    path = service.repository.runs_dir / "--force.tine"
    _run().save(path)
    result = service.verify_report(service.repository.inspect_path(path))
    assert result.ok, result.message


def test_cli_only_import_formats_are_offered_and_routed(tmp_path: Path):
    assert "claude-code-transcript" in IMPORT_FORMATS
    assert set(NATIVE_FORMATS) < set(IMPORT_FORMATS)
    source = tmp_path / "trace.json"
    from opentine import to_otel_genai_document

    source.write_text(json.dumps(to_otel_genai_document(_run())), encoding="utf-8")
    out = tmp_path / "imported.tine"
    interop = InteropService(RunRepository(tmp_path))
    result = interop.import_trace(
        TraceImportOptions(source=str(source), source_format="auto", save_path=str(out))
    )
    assert result.ok, result.message
    assert Run.load(out).steps


def test_a_v4_file_imports_into_the_repository(tmp_path: Path):
    from opentine_tui.repo_actions import ImportOptions, RepoActionService
    from opentine_tui.v3 import V3Repository

    repo = V3Repository.init(tmp_path)
    path = tmp_path / "r.tine"
    _run().save(path)
    result = RepoActionService(repo).import_v2(ImportOptions(source=str(path)))
    assert result.ok, result.message
    repo.invalidate()
    assert any(record.refs for record in repo.list_runs())
