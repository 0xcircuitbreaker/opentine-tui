"""Coverage for the opentine 0.5.0 surface: OTel export and trace import."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from opentine.core import Run, RunStatus, StepKind

from opentine_tui.app import OpentineTUI
from opentine_tui.interop import (
    IMPORT_FORMATS,
    ExportOptions,
    InteropService,
    TraceImportOptions,
)
from opentine_tui.repository import RunRepository
from opentine_tui.v3 import V3Repository
from opentine_tui.widgets.step_detail import StepDetail


def _run(run_id: str = "src") -> Run:
    run = Run(id=run_id, model_info="claude-opus-5", user_prompt="do the thing")
    first = run.add_step(
        StepKind.think, {"text": "plan"}, cost=0.01, usage={"input": 100, "output": 50}
    )
    second = run.add_step(
        StepKind.tool,
        {"name": "read", "arguments": {"path": "x.py"}},
        parent_ids=[first.id],
        outputs={"text": "ok"},
    )
    run.add_step(StepKind.done, {"text": "done"}, parent_ids=[second.id])
    run.status = RunStatus.completed
    return run


@pytest.fixture
def workspace(tmp_path: Path):
    runs_dir = tmp_path / ".tine_runs"
    runs_dir.mkdir()
    repository = V3Repository.init(tmp_path)
    service = InteropService(RunRepository(runs_dir), repository)
    return tmp_path, runs_dir, service


# -- format parity ---------------------------------------------------------


def test_import_formats_match_the_cli():
    """A format added to `tine import` should fail here, not go unnoticed."""
    from opentine._cli_import import IMPORT_FORMATS as CLI_FORMATS

    assert set(IMPORT_FORMATS) == set(CLI_FORMATS)


# -- export ----------------------------------------------------------------


def test_export_writes_a_complete_otlp_document(workspace):
    root, _, service = workspace
    destination = root / "run.otel.json"

    result = service.export_otel(_run(), ExportOptions(destination=str(destination)))

    assert result.ok, result.message
    document = json.loads(destination.read_text(encoding="utf-8"))
    spans = document["resourceSpans"][0]["scopeSpans"][0]["spans"]
    assert len(spans) == 3
    attributes = document["resourceSpans"][0]["resource"]["attributes"]
    assert any(item["key"] == "service.name" for item in attributes)


def test_export_honours_the_service_name(workspace):
    root, _, service = workspace
    destination = root / "named.json"

    service.export_otel(
        _run(), ExportOptions(destination=str(destination), service_name="my-agent")
    )

    document = json.loads(destination.read_text(encoding="utf-8"))
    rendered = json.dumps(document["resourceSpans"][0]["resource"])
    assert "my-agent" in rendered


def test_export_refuses_to_clobber_without_overwrite(workspace):
    root, _, service = workspace
    destination = root / "taken.json"
    destination.write_text("do not lose me", encoding="utf-8")

    blocked = service.export_otel(_run(), ExportOptions(destination=str(destination)))
    assert not blocked.ok
    assert destination.read_text(encoding="utf-8") == "do not lose me"

    allowed = service.export_otel(
        _run(), ExportOptions(destination=str(destination), overwrite=True)
    )
    assert allowed.ok


def test_export_accepts_a_repository_run_unchanged(workspace):
    """The exporter takes anything with `.steps`, so v3 needs no translation."""
    root, _, service = workspace
    service.v3.repo.put_run(_run("repo-run"), ref="heads/main")
    loaded = service.v3.load_run("heads/main")

    result = service.export_otel(loaded, ExportOptions(destination=str(root / "repo.json")))

    assert result.ok, result.message
    assert "3 GenAI span(s)" in result.message


# -- import ----------------------------------------------------------------


def test_export_and_import_round_trip(workspace):
    """0.5.0 states import and export are inverses; hold the dashboard to it."""
    root, runs_dir, service = workspace
    exported = root / "round.json"
    service.export_otel(_run(), ExportOptions(destination=str(exported)))

    result = service.import_trace(
        TraceImportOptions(
            source=str(exported),
            source_format="otel-json",
            save_path=str(runs_dir / "back.tine"),
        )
    )

    assert result.ok, result.message
    assert len(Run.load(runs_dir / "back.tine").steps) == 3


def test_import_requires_a_destination(workspace):
    root, _, service = workspace
    source = root / "t.json"
    service.export_otel(_run(), ExportOptions(destination=str(source)))

    result = service.import_trace(TraceImportOptions(source=str(source), source_format="otel-json"))

    assert not result.ok
    assert "Nothing to write" in result.message


def test_import_reports_a_format_mismatch_as_such(workspace):
    """Read fine but recognized nothing — almost always the wrong format."""
    root, runs_dir, service = workspace
    source = root / "spans.json"
    service.export_otel(_run(), ExportOptions(destination=str(source)))

    result = service.import_trace(
        TraceImportOptions(
            source=str(source), source_format="jsonl", save_path=str(runs_dir / "x.tine")
        )
    )

    assert not result.ok
    assert "Nothing to import" in result.title
    assert "format" in result.message


def test_import_into_the_repository_advances_the_ref(workspace):
    root, _, service = workspace
    source = root / "t.json"
    service.export_otel(_run(), ExportOptions(destination=str(source)))

    result = service.import_trace(
        TraceImportOptions(
            source=str(source),
            source_format="otel-json",
            into_repository=True,
            ref="heads/imported",
        )
    )

    assert result.ok, result.message
    assert service.v3.repo.read_ref("heads/imported") is not None


def test_import_without_a_repository_leaves_none_behind(workspace, tmp_path: Path):
    """`--save` alone builds in a throwaway repository, as `tine import` does."""
    root, _, service = workspace
    source = root / "t.json"
    service.export_otel(_run(), ExportOptions(destination=str(source)))
    bare = tmp_path / "elsewhere"
    bare.mkdir()
    lonely = InteropService(RunRepository(bare), None)

    result = lonely.import_trace(
        TraceImportOptions(
            source=str(source), source_format="otel-json", save_path=str(bare / "out.tine")
        )
    )

    assert result.ok, result.message
    assert (bare / "out.tine").is_file()
    assert not (bare / ".tine").exists()


def test_import_refuses_a_missing_source(workspace):
    _, runs_dir, service = workspace

    result = service.import_trace(
        TraceImportOptions(
            source="/nope/missing.json",
            source_format="otel-json",
            save_path=str(runs_dir / "x.tine"),
        )
    )

    assert not result.ok
    assert "Not a file" in result.message


def test_import_refuses_to_clobber_without_overwrite(workspace):
    root, runs_dir, service = workspace
    source = root / "t.json"
    service.export_otel(_run(), ExportOptions(destination=str(source)))
    occupied = runs_dir / "taken.tine"
    occupied.write_text("keep me", encoding="utf-8")

    result = service.import_trace(
        TraceImportOptions(source=str(source), source_format="otel-json", save_path=str(occupied))
    )

    assert not result.ok
    assert occupied.read_text(encoding="utf-8") == "keep me"


def test_jsonl_round_trips_through_the_importer(workspace):
    """The JSONL importer reads a path itself; make sure routing reaches it."""
    from opentine.trace.importers import native_events

    root, runs_dir, service = workspace
    events = native_events(_run())
    source = root / "events.jsonl"
    source.write_text("\n".join(json.dumps(event.to_dict()) for event in events), encoding="utf-8")

    result = service.import_trace(
        TraceImportOptions(
            source=str(source), source_format="jsonl", save_path=str(runs_dir / "j.tine")
        )
    )

    assert result.ok, result.message
    assert len(Run.load(runs_dir / "j.tine").steps) == len(events)


# -- app wiring ------------------------------------------------------------


@pytest.mark.asyncio
async def test_export_and_import_are_reachable_from_the_ui(tmp_path: Path):
    from textual.widgets import Input

    runs_dir = tmp_path / ".tine_runs"
    runs_dir.mkdir()
    _run("alpha").save(runs_dir / "alpha.tine")
    V3Repository.init(tmp_path)
    destination = tmp_path / "exported.json"

    app = OpentineTUI(runs_dir=runs_dir, repo_path=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()

        await pilot.press("E")
        await pilot.pause()
        app.screen.query_one("#dest", Input).value = str(destination)
        app.screen.query_one("#ok").press()
        # These actions hand the work to a thread; wait for the worker rather than
        # for a guessed number of frames, which raced on a slow runner.
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert destination.is_file()

        await pilot.press("I")
        await pilot.pause()
        app.screen.query_one("#source", Input).value = str(destination)
        app.screen.query_one("#save", Input).value = str(runs_dir / "imported.tine")
        app.screen.query_one("#ok").press()
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()

        assert (runs_dir / "imported.tine").is_file()
        # The result must survive the refresh the import triggers.
        assert "Imported" in app.query_one(StepDetail).last_rendered


@pytest.mark.asyncio
async def test_a_repaint_keeps_the_open_step_and_the_last_result(tmp_path: Path):
    """A poll used to re-announce the highlighted row, resetting both."""
    runs_dir = tmp_path / ".tine_runs"
    runs_dir.mkdir()
    _run("alpha").save(runs_dir / "alpha.tine")

    app = OpentineTUI(runs_dir=runs_dir)
    async with app.run_test() as pilot:
        await pilot.pause()
        run = app._active_run()
        app.select_step(run.steps[0].id)
        await pilot.pause()
        assert "Step:" in app.query_one(StepDetail).last_rendered

        app._refresh()
        await pilot.pause()
        await pilot.pause()

        assert app._selected_step_id is not None
        assert "Step:" in app.query_one(StepDetail).last_rendered
