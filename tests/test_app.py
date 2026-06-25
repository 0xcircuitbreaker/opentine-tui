"""Textual run_test coverage for the TUI widgets."""

from __future__ import annotations

from pathlib import Path

import pytest
from opentine.core import Run, RunStatus, StepKind

from opentine_tui.app import OpentineTUI
from opentine_tui.widgets.run_list import RunList
from opentine_tui.widgets.step_detail import StepDetail
from opentine_tui.widgets.step_tree import StepTree


def _write_run(path: Path, run_id: str = "ui-run") -> Run:
    run = Run(id=run_id, model_info="mock-ui", user_prompt="ui prompt")
    run.add_step(StepKind.think, {"text": "first thought"})
    run.add_step(StepKind.done, {"text": "finished"})
    run.status = RunStatus.completed
    run.save(path)
    return run


@pytest.mark.asyncio
async def test_empty_runs_dir_loads(tmp_path: Path):
    app = OpentineTUI(runs_dir=tmp_path / ".tine_runs")

    async with app.run_test():
        assert app.query_one(RunList).row_count == 0


@pytest.mark.asyncio
async def test_valid_run_list_loads(tmp_path: Path):
    runs_dir = tmp_path / ".tine_runs"
    runs_dir.mkdir()
    _write_run(runs_dir / "ui-run.tine")
    app = OpentineTUI(runs_dir=runs_dir)

    async with app.run_test():
        assert app.query_one(RunList).row_count == 1
        assert len(app._records) == 1
        assert app._records[0].run is not None


@pytest.mark.asyncio
async def test_corrupt_tine_row_is_visible(tmp_path: Path):
    runs_dir = tmp_path / ".tine_runs"
    runs_dir.mkdir()
    (runs_dir / "bad.tine").write_text("not json", encoding="utf-8")
    app = OpentineTUI(runs_dir=runs_dir)

    async with app.run_test():
        assert app.query_one(RunList).row_count == 1
        assert app._records[0].is_corrupt


@pytest.mark.asyncio
async def test_run_selection_populates_steps_and_details(tmp_path: Path):
    runs_dir = tmp_path / ".tine_runs"
    runs_dir.mkdir()
    run = _write_run(runs_dir / "ui-run.tine")
    app = OpentineTUI(runs_dir=runs_dir)

    async with app.run_test():
        app.select_run(str(runs_dir / "ui-run.tine"))
        detail = app.query_one(StepDetail)
        tree = app.query_one(StepTree)

        assert run.id in detail.last_rendered
        assert tree._run is not None
        assert tree._run.id == run.id


@pytest.mark.asyncio
async def test_step_selection_populates_step_detail(tmp_path: Path):
    runs_dir = tmp_path / ".tine_runs"
    runs_dir.mkdir()
    run = _write_run(runs_dir / "ui-run.tine")
    app = OpentineTUI(runs_dir=runs_dir)

    async with app.run_test():
        app.select_run(str(runs_dir / "ui-run.tine"))
        app.select_step(run.steps[0].id)

        detail = app.query_one(StepDetail)
        assert "first thought" in detail.last_rendered
        assert "Inputs" in detail.last_rendered
