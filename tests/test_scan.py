"""Coverage for the run-directory scan: caching, invalidation, cursor stability."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from opentine.core import Run, RunStatus, StepKind

from opentine_tui.app import OpentineTUI
from opentine_tui.repository import RunRepository
from opentine_tui.widgets.run_list import RunList


def _write(path: Path, run_id: str) -> Run:
    run = Run(id=run_id, model_info="mock", user_prompt="prompt")
    run.add_step(StepKind.think, {"text": "thinking"})
    run.add_step(StepKind.done, {"text": "done"})
    run.status = RunStatus.completed
    run.save(path)
    return run


def test_unchanged_artifacts_are_not_reinspected(tmp_path: Path, monkeypatch):
    runs_dir = tmp_path / ".tine_runs"
    runs_dir.mkdir()
    for index in range(3):
        _write(runs_dir / f"run-{index}.tine", f"run-{index}")

    repository = RunRepository(runs_dir)
    calls: list[Path] = []
    original = repository.inspect_path
    monkeypatch.setattr(
        repository,
        "inspect_path",
        lambda path: (calls.append(Path(path)), original(path))[1],
    )

    assert len(repository.list_records()) == 3
    assert len(calls) == 3
    calls.clear()

    assert len(repository.list_records()) == 3
    assert calls == []  # every row served from the (mtime, size) cache


def test_rewritten_artifact_is_reinspected(tmp_path: Path):
    runs_dir = tmp_path / ".tine_runs"
    runs_dir.mkdir()
    path = runs_dir / "run.tine"
    _write(path, "run-a")

    repository = RunRepository(runs_dir)
    assert repository.list_records()[0].run.id == "run-a"

    _write(path, "run-b")  # different id => different size and mtime
    assert repository.list_records()[0].run.id == "run-b"


def test_explicit_invalidation_forces_a_reread(tmp_path: Path):
    runs_dir = tmp_path / ".tine_runs"
    runs_dir.mkdir()
    path = runs_dir / "run.tine"
    _write(path, "run-a")

    repository = RunRepository(runs_dir)
    repository.list_records()
    # Same byte length and (on a coarse clock) the same mtime: only the explicit
    # invalidation the action layer performs can be trusted here.
    rewritten = path.read_text(encoding="utf-8").replace("mock", "moKk")
    stat = path.stat()
    path.write_text(rewritten, encoding="utf-8")
    os.utime(path, (stat.st_atime, stat.st_mtime))

    assert repository.list_records()[0].run.model_info == "mock"  # stale, as designed
    repository.invalidate(path)
    assert repository.list_records()[0].run.model_info == "moKk"


def test_deleted_artifacts_leave_the_cache(tmp_path: Path):
    runs_dir = tmp_path / ".tine_runs"
    runs_dir.mkdir()
    path = runs_dir / "run.tine"
    _write(path, "run-a")

    repository = RunRepository(runs_dir)
    repository.list_records()
    path.unlink()

    assert repository.list_records() == []
    assert repository._cache == {}


@pytest.mark.asyncio
async def test_rescan_keeps_the_cursor_on_the_same_run(tmp_path: Path):
    runs_dir = tmp_path / ".tine_runs"
    runs_dir.mkdir()
    for index in range(4):
        _write(runs_dir / f"run-{index}.tine", f"run-{index}")

    app = OpentineTUI(runs_dir=runs_dir)
    async with app.run_test():
        table = app.query_one(RunList)
        table.move_cursor(row=2)
        selected = table.highlighted_key
        assert selected is not None

        # A newer run arriving at the top must not drag the cursor with it.
        _write(runs_dir / "run-new.tine", "run-new")
        app._refresh()

        assert table.row_count == 5
        assert table.highlighted_key == selected


@pytest.mark.asyncio
async def test_enter_selects_the_highlighted_row_not_a_stale_index(tmp_path: Path):
    runs_dir = tmp_path / ".tine_runs"
    runs_dir.mkdir()
    for index in range(3):
        _write(runs_dir / f"run-{index}.tine", f"run-{index}")

    app = OpentineTUI(runs_dir=runs_dir)
    async with app.run_test() as pilot:
        table = app.query_one(RunList)
        table.move_cursor(row=1)
        expected = table.highlighted_key
        table.action_select_run()
        await pilot.pause()

        assert app._selected_record is not None
        assert app._selected_record.key == expected
