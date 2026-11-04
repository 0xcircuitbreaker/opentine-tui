"""QOL app wiring: context-aware bindings, palette, copy, filter, search, busy guard."""

from __future__ import annotations

import threading
from collections import Counter
from pathlib import Path

import pytest
from opentine.core import Run, RunStatus, StepKind
from textual.binding import Binding
from textual.command import CommandPalette
from textual.widgets import Input

from opentine_tui.actions import ActionResult
from opentine_tui.app import OpentineTUI
from opentine_tui.dialogs import CopyMenuModal, ExportModal, GotoRunModal, SearchModal
from opentine_tui.widgets.run_list import RunList
from opentine_tui.widgets.step_tree import StepTree


def _run(run_id: str, tags: tuple[str, ...] = ()) -> Run:
    run = Run(id=run_id, model_info="mock-model", user_prompt="hello")
    run.add_step(StepKind.think, {"text": "thinking"})
    run.add_step(StepKind.done, {"text": "done"})
    run.status = RunStatus.completed
    for tag in tags:
        run.add_tag(tag)
    return run


def _app(tmp_path: Path, ids=("alpha-1111", "beta-2222", "gamma-3333")) -> OpentineTUI:
    runs = tmp_path / ".tine_runs"
    runs.mkdir(exist_ok=True)
    for i, run_id in enumerate(ids):
        _run(run_id, tags=("bug",) if i == 0 else ()).save(runs / f"{run_id}.tine")
    return OpentineTUI(runs_dir=runs)


async def _settle(pilot, screen_type, tries: int = 40) -> bool:
    for _ in range(tries):
        await pilot.pause()
        if isinstance(pilot.app.screen, screen_type):
            await pilot.pause()
            return True
    return False


def test_no_duplicate_app_keys():
    keys: Counter[str] = Counter()
    for binding in OpentineTUI.BINDINGS:
        assert isinstance(binding, Binding)
        for key in binding.key.split(","):
            keys[key.strip()] += 1
    assert [k for k, n in keys.items() if n > 1] == []
    for new in ("escape", "S", "g", "Y"):
        assert keys[new] == 1


@pytest.mark.asyncio
async def test_theme_is_opentine_and_light_theme_switches(tmp_path: Path):
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        assert app.theme == "opentine"
        assert app.current_theme.primary.upper() == "#FF6900"
        app.theme = "textual-light"
        await pilot.pause()
        app.theme = "opentine"
        await pilot.pause()


@pytest.mark.asyncio
async def test_repo_only_actions_disabled_on_files_tab(tmp_path: Path):
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert not app.repo_mode
        for action in ("promote", "evaluate", "repo_log", "inspect_object"):
            assert app.check_action(action, ()) is None
        assert app.check_action("tag", ()) is True
        assert app.check_action("clear_filter", ()) is False


@pytest.mark.asyncio
async def test_border_titles_show_counts(tmp_path: Path):
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert "Runs [1] (3/3)" in str(app.query_one("#left-panel").border_title)
        await pilot.press("2")
        await pilot.pause()
        assert isinstance(app.focused, StepTree)
        assert "(2)" in str(app.query_one("#center-panel").border_title)
        assert str(app.query_one("#right-panel").border_title).startswith("Detail [3]")


@pytest.mark.asyncio
async def test_search_modal_history_and_escape_clears(tmp_path: Path):
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("slash")
        assert await _settle(pilot, SearchModal)
        app.screen.query_one("#query", Input).value = "tag:bug"
        await pilot.press("enter")
        await pilot.pause()
        assert app._query == "tag:bug"
        assert app._query_history == ["tag:bug"]
        assert app.query_one(RunList).row_count == 1
        assert "(1/3)" in str(app.query_one("#left-panel").border_title)
        assert "esc clears" in str(app.query_one("#left-panel").border_subtitle)
        assert app._count_matches("tag:bug") == 1

        await pilot.press("escape")
        await pilot.pause()
        assert app._query == ""
        assert app.query_one(RunList).row_count == 3

        # The next search offers the previous query through history.
        await pilot.press("slash")
        assert await _settle(pilot, SearchModal)
        assert app.screen.history == ["tag:bug"]
        await pilot.press("escape")
        await pilot.pause()
        assert app._query == ""


@pytest.mark.asyncio
async def test_yank_copies_run_id(tmp_path: Path):
    app = _app(tmp_path)
    copied: list[str] = []
    async with app.run_test() as pilot:
        await pilot.pause()
        app.copy_to_clipboard = copied.append  # type: ignore[method-assign]
        record = app._selected_record
        assert record is not None
        await pilot.press("Y")
        assert await _settle(pilot, CopyMenuModal)
        await pilot.press("enter")  # first choice: the run id
        await pilot.pause()
        assert copied == [record.run_id]


@pytest.mark.asyncio
async def test_goto_run_clears_hiding_filter(tmp_path: Path):
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        app._query = "tag:bug"
        app._refresh()
        assert app.query_one(RunList).row_count == 1
        await pilot.press("g")
        assert await _settle(pilot, GotoRunModal)
        for char in "gamm":
            await pilot.press(char)
        await pilot.press("enter")
        await pilot.pause()
        assert app._query == ""
        assert app._selected_record is not None
        assert app._selected_record.run_id == "gamma-3333"
        assert app.query_one(RunList).highlighted_key == app._selected_record.key


@pytest.mark.asyncio
async def test_cycle_sort_updates_subtitle(tmp_path: Path):
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("S")
        await pilot.pause()
        label = app.query_one(RunList).sort_label
        assert label.startswith("cost")
        assert label in str(app.query_one("#left-panel").border_subtitle)


@pytest.mark.asyncio
async def test_busy_guard_blocks_second_action(tmp_path: Path):
    app = _app(tmp_path)
    gate = threading.Event()
    calls: list[str] = []

    def slow(record):
        calls.append(record.run_id)
        gate.wait(5)
        return ActionResult(True, "Replay", "ok")

    async with app.run_test() as pilot:
        await pilot.pause()
        app.actions.replay_verify = slow  # type: ignore[method-assign]
        await pilot.press("y")
        for _ in range(20):
            await pilot.pause(0.05)
            if app._busy:
                break
        assert app._busy
        assert app.query_one("#detail-scroll").loading
        assert app.check_action("replay_verify", ()) is None  # shown disabled, never run
        assert app.check_action("search", ()) is True
        await pilot.press("y")
        await pilot.pause()
        gate.set()
        for _ in range(40):
            await pilot.pause(0.05)
            if not app._busy:
                break
        assert calls == [app._selected_record.run_id]
        assert not app._busy
        assert not app.query_one("#detail-scroll").loading


@pytest.mark.asyncio
async def test_command_palette_runs_export(tmp_path: Path):
    app = _app(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.press("ctrl+p")
        assert await _settle(pilot, CommandPalette)
        for char in "export otel":
            await pilot.press("space" if char == " " else char)
        for _ in range(20):
            await pilot.pause(0.05)
        await pilot.press("enter")
        assert await _settle(pilot, ExportModal)
