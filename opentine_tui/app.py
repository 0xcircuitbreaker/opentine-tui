"""Opentine TUI — Terminal dashboard for managing agent runs.

Three-panel layout inspired by lazygit:
  Left:   Run list (DataTable)
  Center: Step tree for selected run
  Right:  Run/step details + actions
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Header, Static

from opentine_tui.actions import ActionResult, HarnessOptions, RunActionService
from opentine_tui.dialogs import ConfirmationModal, HarnessOptionsModal, TextInputModal
from opentine_tui.repository import RunRecord, RunRepository
from opentine_tui.widgets.run_list import RunList, RunSelected
from opentine_tui.widgets.step_detail import StepDetail
from opentine_tui.widgets.step_tree import StepSelected, StepTree

BRAND = "#FF6900"


class OpentineTUI(App):
    CSS = """
    Screen {
        layout: vertical;
    }
    #main {
        height: 1fr;
    }
    #left-panel {
        width: 1fr;
        min-width: 24;
        max-width: 34;
        border-right: solid #FF6900;
    }
    #center-panel {
        width: 1fr;
        min-width: 22;
        border-right: solid #FF6900;
    }
    #right-panel {
        width: 1fr;
        min-width: 24;
    }
    #runs-title,
    #steps-title,
    #detail-title {
        height: 1;
        padding-left: 1;
    }
    RunList,
    StepTree,
    StepDetail {
        height: 1fr;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
        Binding("1", "focus_left", "Runs", show=False),
        Binding("2", "focus_center", "Steps", show=False),
        Binding("3", "focus_right", "Detail", show=False),
        Binding("v", "verify", "Verify"),
        Binding("f", "fork", "Fork", show=False),
        Binding("c", "cache_replay", "Cache replay", show=False),
        Binding("d", "diff", "Diff", show=False),
        Binding("x", "resume", "Resume", show=False),
        Binding("h", "launch_harness", "Harness", show=False),
        Binding("ctrl+f", "fork_harness", "Fork+harness", show=False),
        Binding("ctrl+r", "replay_harness", "Replay+harness", show=False),
    ]

    TITLE = "opentine"
    SUB_TITLE = "agent run manager"

    def __init__(self, runs_dir: str | Path | None = None) -> None:
        super().__init__()
        self.repository = RunRepository(runs_dir)
        self.actions = RunActionService(self.repository)
        self._records: list[RunRecord] = []
        self._selected_record: RunRecord | None = None
        self._selected_step_id: str | None = None
        self._refresh_timer = None

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="main"):
            with Vertical(id="left-panel"):
                yield Static("[bold #FF6900]Runs[/]", id="runs-title")
                yield RunList()
            with Vertical(id="center-panel"):
                yield Static("[bold #FF6900]Steps[/]", id="steps-title")
                yield StepTree()
            with Vertical(id="right-panel"):
                yield Static("[bold #FF6900]Details[/]", id="detail-title")
                yield StepDetail()
        yield Footer()

    def on_mount(self) -> None:
        self._refresh()
        self.query_one(RunList).focus()
        self._refresh_timer = self.set_interval(5.0, self._refresh)

    def on_run_selected(self, event: RunSelected) -> None:
        self.select_run(event.run_key)

    def on_step_selected(self, event: StepSelected) -> None:
        self.select_step(event.step_id)

    def select_run(self, run_key: str) -> None:
        record = next((candidate for candidate in self._records if candidate.key == run_key), None)
        if record is None:
            return

        self._selected_record = record
        self._selected_step_id = None
        step_tree = self.query_one(StepTree)
        step_detail = self.query_one(StepDetail)

        if record.run is None:
            step_tree.clear_run("[red]Corrupt run[/]")
            step_detail.set_run(record)
            return

        step_tree.load_run(record.run)
        step_detail.set_run(record)

    def select_step(self, step_id: str) -> None:
        record = self._selected_record
        if record is None or record.run is None:
            return
        step = record.run.get_step(step_id)
        if step is None:
            return
        self._selected_step_id = step.id
        self.query_one(StepDetail).set_step(record.run, step)

    def _refresh(self) -> None:
        selected_key = self._selected_record.key if self._selected_record else None
        self._records = self.repository.list_records()
        self.query_one(RunList).update_records(self._records)

        if selected_key:
            replacement = next(
                (record for record in self._records if record.key == selected_key),
                None,
            )
            if replacement:
                self._selected_record = replacement

    def action_refresh(self) -> None:
        self._refresh()

    def action_focus_left(self) -> None:
        self.query_one(RunList).focus()

    def action_focus_center(self) -> None:
        self.query_one(StepTree).focus()

    def action_focus_right(self) -> None:
        self.query_one(StepDetail).focus()

    def action_verify(self) -> None:
        record = self._require_selected()
        if record is None:
            return
        self._show_result(self.actions.verify(record.path))

    async def action_fork(self) -> None:
        record = self._require_loaded()
        if record is None:
            return
        if not await self._confirm(
            "Fork run",
            "This will write a new .tine artifact from the selected step.",
        ):
            return
        self._show_result(self.actions.fork(record.run, self._selected_step_id))

    async def action_cache_replay(self) -> None:
        record = self._require_loaded()
        if record is None:
            return
        if not await self._confirm(
            "Cache replay",
            "This will write a new .tine artifact that reuses recorded steps.",
        ):
            return
        self._show_result(self.actions.cache_replay(record.run, self._selected_step_id))

    async def action_diff(self) -> None:
        record = self._require_loaded()
        if record is None:
            return
        other = await self.push_screen_wait(
            TextInputModal("Diff against run", "Run ID, prefix, or .tine path")
        )
        if not other:
            return
        self._show_result(self.actions.diff(record.run, other))

    async def action_resume(self) -> None:
        record = self._require_loaded()
        if record is None:
            return
        if not getattr(record.run, "manifest", {}).get("resume", False):
            self._show_result(self.actions.resume(record.run, record.path))
            return
        if not await self._confirm(
            "Resume run",
            "This will update the selected .tine artifact and mark it running.",
        ):
            return
        self._show_result(self.actions.resume(record.run, record.path))

    async def action_launch_harness(self) -> None:
        options = await self._request_harness_options("Run harness")
        if options is None:
            return
        if not await self._confirm(
            "Launch harness",
            "This will start an external harness process and write a .tine artifact.",
        ):
            return
        self._show_result(await self._run_in_thread(self.actions.run_harness, options))

    async def action_fork_harness(self) -> None:
        record = self._require_loaded()
        if record is None:
            return
        options = await self._request_harness_options("Fork and run harness")
        if options is None:
            return
        if not await self._confirm(
            "Fork and launch harness",
            "This will write a forked .tine artifact and start an external harness process.",
        ):
            return
        result = await self._run_in_thread(
            self.actions.fork_harness,
            record.run,
            self._selected_step_id,
            options,
        )
        self._show_result(result)

    async def action_replay_harness(self) -> None:
        record = self._require_loaded()
        if record is None:
            return
        options = await self._request_harness_options("Replay with harness")
        if options is None:
            return
        if not await self._confirm(
            "Replay with harness",
            "This will start an external harness process and write a .tine artifact.",
        ):
            return
        result = await self._run_in_thread(
            self.actions.replay_harness,
            record.run,
            self._selected_step_id,
            options,
        )
        self._show_result(result)

    def _require_selected(self) -> RunRecord | None:
        if self._selected_record is None:
            self.query_one(StepDetail).set_message("No run selected", "Select a run first.", False)
            return None
        return self._selected_record

    def _require_loaded(self) -> RunRecord | None:
        record = self._require_selected()
        if record is None:
            return None
        if record.run is None:
            self.query_one(StepDetail).set_run(record)
            return None
        return record

    def _show_result(self, result: ActionResult) -> None:
        self.query_one(StepDetail).set_message(result.title, result.message, result.ok)
        if result.refresh:
            self._refresh()

    async def _confirm(self, title: str, message: str) -> bool:
        return bool(await self.push_screen_wait(ConfirmationModal(title, message)))

    async def _request_harness_options(self, title: str) -> HarnessOptions | None:
        cwd = str(Path.cwd())
        prompt = ""
        if self._selected_record and self._selected_record.run:
            prompt = self._selected_record.run.user_prompt
        return await self.push_screen_wait(
            HarnessOptionsModal(title=title, default_prompt=prompt, default_cwd=cwd)
        )

    async def _run_in_thread(self, func, *args) -> ActionResult:
        self.query_one(StepDetail).set_message("Working", "Action is running...")
        return await asyncio.to_thread(func, *args)
