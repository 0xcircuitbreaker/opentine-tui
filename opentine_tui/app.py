"""Opentine TUI — Terminal dashboard for managing agent runs.

Three-panel layout inspired by lazygit:
  Left:   Run list — portable ``.tine`` files, or a v3 ``.tine/`` repository
  Center: Step tree for selected run
  Right:  Run/step details + actions

The two sources share the centre and right panels: ``repo.load_run()`` returns the
same ``Run`` the file reader does, so a repository run renders through exactly the
same step tree and detail widgets.
"""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Footer, Header, Static, TabbedContent, TabPane

from opentine_tui.actions import (
    FORMAT_VERSION,
    ActionResult,
    HarnessOptions,
    RunActionService,
)
from opentine_tui.dialogs import (
    BudgetModal,
    ConfirmationModal,
    EvaluateModal,
    HarnessOptionsModal,
    ImportModal,
    MigrateModal,
    PromoteModal,
    SignModal,
    TagEditorModal,
    TextInputModal,
    VerifyKeyModal,
)
from opentine_tui.formatting import escape_markup
from opentine_tui.repo_actions import RepoActionService
from opentine_tui.repository import RunRecord, RunRepository, filter_records
from opentine_tui.v3 import RepoRunRecord, V3Repository
from opentine_tui.widgets.repo_list import RepoList, RepoRunSelected
from opentine_tui.widgets.run_list import RunList, RunSelected
from opentine_tui.widgets.step_detail import StepDetail
from opentine_tui.widgets.step_tree import StepSelected, StepTree

FILES_TAB = "tab-files"
REPO_TAB = "tab-repo"

BRAND = "#FF6900"


_escape_markup = escape_markup


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
        min-width: 32;
        max-width: 64;
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
    RepoList,
    StepTree {
        height: 1fr;
    }
    #detail-scroll {
        height: 1fr;
    }
    StepDetail {
        height: auto;
    }
    #repo-empty {
        padding: 1 1;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding(
            "question_mark",
            "show_help_panel",
            "Help",
            tooltip="List every key binding, including the hidden ones",
        ),
        Binding("r", "refresh", "Refresh", tooltip="Drop the scan cache and rescan"),
        Binding("slash", "search", "Search", tooltip="Filter runs with the opentine query DSL"),
        Binding(
            "left_square_bracket,right_square_bracket",
            "toggle_source",
            "Files/Repo",
            tooltip="Switch the run list between .tine files and the v3 repository",
        ),
        Binding("1", "focus_left", "Runs", show=False),
        Binding("2", "focus_center", "Steps", show=False),
        Binding("3", "focus_right", "Detail", show=False),
        Binding(
            "v",
            "verify",
            "Verify",
            tooltip="Files: integrity + signature. Repository: deep fsck",
        ),
        Binding("t", "tag", "Tags", tooltip="Edit tags without touching the digest or signature"),
        Binding(
            "b",
            "budget",
            "Budget",
            show=False,
            tooltip="Set cost/token/step/duration limits (rewrites the digest)",
        ),
        Binding(
            "m",
            "migrate",
            "Migrate",
            show=False,
            tooltip="Upgrade a v1/legacy artifact to v2 (one-way)",
        ),
        Binding("s", "sign", "Sign", show=False, tooltip="Sign a completed run (HMAC or Ed25519)"),
        Binding("k", "keygen", "Keygen", show=False, tooltip="Generate an Ed25519 keypair"),
        Binding(
            "f",
            "fork",
            "Fork",
            show=False,
            tooltip="Branch a new run from the selected step/event",
        ),
        Binding(
            "c",
            "cache_replay",
            "Cache replay",
            show=False,
            tooltip="Write a new artifact reusing the recorded steps",
        ),
        Binding(
            "d",
            "diff",
            "Diff",
            show=False,
            tooltip="Files: field-level diff. Repository: semantic diff",
        ),
        Binding(
            "x", "resume", "Resume", show=False, tooltip="Continue a run whose manifest allows it"
        ),
        Binding(
            "h",
            "launch_harness",
            "Harness",
            show=False,
            tooltip="Run an external agent CLI and record it",
        ),
        Binding("ctrl+f", "fork_harness", "Fork+harness", show=False),
        Binding("ctrl+r", "replay_harness", "Replay+harness", show=False),
        # v3 repository
        Binding(
            "i",
            "import_run",
            "Import to repo",
            show=False,
            tooltip="migrate-v3: import a .tine file as verified v3 objects",
        ),
        Binding(
            "p",
            "promote",
            "Promote",
            show=False,
            tooltip="Point promotions/<name> at the selected run object",
        ),
        Binding(
            "e",
            "evaluate",
            "Evaluate",
            show=False,
            tooltip="Attach an evaluation attestation (scores)",
        ),
        Binding("l", "repo_log", "Event log", show=False, tooltip="Walk a ref's event ancestry"),
        Binding(
            "o",
            "inspect_object",
            "Inspect object",
            show=False,
            tooltip="Show a verified v3 object by id",
        ),
    ]

    TITLE = "opentine"
    SUB_TITLE = "agent run manager"

    def __init__(
        self,
        runs_dir: str | Path | None = None,
        repo_path: str | Path | None = None,
    ) -> None:
        super().__init__()
        self.repository = RunRepository(runs_dir)
        self.actions = RunActionService(self.repository)
        # A v3 repository is optional: `Repo.open` walks up from the given path
        # (or the cwd) to the first `.tine/config.json`, exactly as `tine` does.
        self.v3 = V3Repository.discover(repo_path)
        self.repo_actions = RepoActionService(self.v3) if self.v3 is not None else None
        self._records: list[RunRecord] = []
        self._all_records: list[RunRecord] = []
        self._selected_record: RunRecord | None = None
        self._repo_records: list[RepoRunRecord] = []
        self._selected_repo_record: RepoRunRecord | None = None
        self._repo_run = None
        self._selected_step_id: str | None = None
        self._query: str = ""
        self._refresh_timer = None

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="main"):
            with Vertical(id="left-panel"):
                yield Static("[bold #FF6900]Runs[/]", id="runs-title")
                with TabbedContent(id="sources"):
                    with TabPane("Files", id=FILES_TAB):
                        yield RunList()
                    with TabPane("Repository", id=REPO_TAB):
                        if self.v3 is None:
                            yield Static(
                                "[dim]No v3 repository here.\n\n"
                                "`tine init` creates one; run the dashboard from a "
                                "directory beneath it, or pass --repo.[/]",
                                id="repo-empty",
                            )
                        else:
                            yield RepoList()
            with Vertical(id="center-panel"):
                yield Static("[bold #FF6900]Steps[/]", id="steps-title")
                yield StepTree()
            with Vertical(id="right-panel"):
                yield Static("[bold #FF6900]Details[/]", id="detail-title")
                with VerticalScroll(id="detail-scroll"):
                    yield StepDetail()
        yield Footer()

    def on_mount(self) -> None:
        self._refresh()
        self._refresh_repo()
        self.query_one(RunList).focus()
        self._select_first_run()
        self._refresh_timer = self.set_interval(5.0, self._poll)

    def _select_first_run(self) -> None:
        """Open the newest run, or explain an empty list instead of showing nothing."""
        if self._records:
            key = self.query_one(RunList).highlighted_key
            if key is not None:
                self.select_run(key)
            return
        if self.v3 is not None and not self._repo_records:
            # `tine init` and nothing since. Telling this user to run `tine init`
            # is advice they have already taken.
            self.query_one(StepDetail).set_message(
                "Nothing recorded yet",
                f"The repository at {self.v3.path} has no runs, and "
                f"{self.repository.runs_dir} has no .tine artifacts.\n\n"
                "Record one with `tine run`, press 'h' to launch a harness from here, "
                "or press 'i' to import an existing .tine file into the repository.",
                True,
            )
            return
        directory = self.repository.runs_dir
        if not directory.exists():
            message = (
                f"{directory} does not exist.\n\n"
                "Point the dashboard at your artifacts with --runs-dir, or set "
                "OPENTINE_RUNS_DIR. `tine run` writes there by default."
            )
        elif self._all_records:
            message = f"No run matches the filter {self._query!r}. Press '/' to change it."
        else:
            message = (
                f"{directory} holds no .tine artifacts yet.\n\n"
                "Record one with `tine run`, press 'h' to launch a harness from here, "
                "or press ']' to browse a v3 repository."
            )
        self.query_one(StepDetail).set_message("No runs", message, True)

    # Both lists highlight a row as soon as they are populated, and those messages
    # are queued — so without this gate the repository list's startup highlight
    # arrives after the file list's and takes over the shared panels, leaving the
    # Files tab showing a highlighted row that nothing acts on. The centre and
    # right panels always follow the *active* source.
    def on_run_selected(self, event: RunSelected) -> None:
        if self.repo_mode:
            return
        self.select_run(event.run_key)

    def on_repo_run_selected(self, event: RepoRunSelected) -> None:
        if not self.repo_mode:
            return
        self.select_repo_run(event.oid)

    def on_step_selected(self, event: StepSelected) -> None:
        self.select_step(event.step_id)

    def select_run(self, run_key: str) -> None:
        # Look in every scanned record, not just the filtered view: a run stays
        # selected (and repaintable) after a filter hides its row.
        record = next(
            (candidate for candidate in self._all_records if candidate.key == run_key), None
        )
        if record is None:
            return

        self._selected_record = record
        self._selected_repo_record = None
        self._repo_run = None
        self._selected_step_id = None
        panels = self._panels()
        if panels is None:  # a highlight message landing during teardown
            return
        step_tree, step_detail = panels

        if record.run is None:
            step_tree.clear_run("[red]Corrupt run[/]")
            step_detail.set_run(record)
            return

        step_tree.load_run(record.run)
        step_detail.set_run(record)

    def select_repo_run(self, oid: str) -> None:
        record = next((candidate for candidate in self._repo_records if candidate.key == oid), None)
        if record is None or self.v3 is None:
            return

        self._selected_repo_record = record
        self._selected_record = None
        self._selected_step_id = None
        panels = self._panels()
        if panels is None:  # a highlight message landing during teardown
            return
        step_tree, step_detail = panels

        try:
            run = self.v3.load_run(record.oid)
        except Exception as exc:
            step_tree.clear_run("[red]Unreadable run object[/]")
            step_detail.set_repo_run(record, None, f"{type(exc).__name__}: {exc}")
            return

        self._repo_run = run
        step_tree.load_run(run)
        step_detail.set_repo_run(record, run)

    def select_step(self, step_id: str) -> None:
        run = self._active_run()
        if run is None:
            return
        step = run.get_step(step_id)
        if step is None:
            return
        self._selected_step_id = step.id
        self.query_one(StepDetail).set_step(run, step)

    def _panels(self) -> tuple[StepTree, StepDetail] | None:
        """The centre and right panels, or None once they are gone.

        Row-highlight messages are queued, so one can be delivered after the app
        has started tearing down; a missing panel is not an error there.
        """
        try:
            return self.query_one(StepTree), self.query_one(StepDetail)
        except Exception:
            return None

    def _active_run(self):
        """The run the centre/right panels are currently showing, whatever its source."""
        if self._selected_record is not None:
            return self._selected_record.run
        if self._selected_repo_record is not None:
            return getattr(self, "_repo_run", None)
        return None

    def _refresh(self) -> None:
        """Rescan the runs directory on the caller's thread and repaint the list."""
        self._apply_records(self.repository.list_records())

    def _refresh_repo(self) -> None:
        if self.v3 is None:
            return
        self._apply_repo_records(self.v3.list_runs())

    @work(exclusive=True, group="scan")
    async def _poll(self) -> None:
        """Periodic rescan, off the event loop.

        Inspecting an artifact reads, integrity-checks, signature-checks and loads
        it; a few hundred runs is seconds of work, which would otherwise freeze the
        UI on every tick. The repository caches by (mtime, size), so a quiet
        directory costs a stat per file. Walking a repository's objects is likewise
        unbounded work, so it goes to the same thread.
        """
        records = await asyncio.to_thread(self.repository.list_records)
        self._apply_records(records)
        if self.v3 is not None:
            repo_records = await asyncio.to_thread(self.v3.list_runs)
            self._apply_repo_records(repo_records)

    def _apply_repo_records(self, records: list[RepoRunRecord]) -> None:
        selected_key = self._selected_repo_record.key if self._selected_repo_record else None
        self._repo_records = records
        try:
            self.query_one(RepoList).update_records(records)
        except Exception:  # no repository pane mounted
            return
        if selected_key:
            replacement = next(
                (record for record in records if record.key == selected_key),
                None,
            )
            if replacement:
                self._selected_repo_record = replacement

    def _apply_records(self, records: list[RunRecord]) -> None:
        previous = self._selected_record
        self._all_records = records
        self._records, error = filter_records(self._all_records, self._query)
        self.query_one(RunList).update_records(self._records)
        self._update_runs_title(error)

        if previous is None:
            return
        replacement = next(
            (record for record in self._all_records if record.key == previous.key), None
        )
        if replacement is None:
            # The artifact was deleted or moved. Keeping the stale record live means
            # the next action rewrites a file the user can no longer see.
            self._selected_record = None
            self._selected_step_id = None
            self.query_one(StepTree).clear_run("[dim]Run is gone[/]")
            self.query_one(StepDetail).set_message(
                "Run is gone",
                f"{previous.path} is no longer in the runs directory.",
                False,
            )
            return
        self._selected_record = replacement
        if replacement.mtime != previous.mtime:
            # The file changed underneath us — a live harness appending steps, or
            # another process. Repaint rather than freeze the DAG at selection time.
            self.select_run(replacement.key)

    def _update_runs_title(self, error: str | None = None) -> None:
        title = "[bold #FF6900]Runs[/]"
        if error:
            title += f"  [red]{_escape_markup(error)}[/]"
        elif self._query:
            title += (
                f"  [dim]filter:[/] {_escape_markup(self._query)} [dim]({len(self._records)})[/]"
            )
        widget = self.query_one("#runs-title", Static)
        try:
            widget.update(title)
        except Exception:  # a panel title must never crash the dashboard
            widget.update("Runs")

    @work(exclusive=True, group="scan")
    async def action_refresh(self) -> None:
        """Force a full rescan — cache dropped, still off the event loop."""
        self.repository.invalidate()
        if self.v3 is not None:
            self.v3.invalidate()
        records = await asyncio.to_thread(self.repository.list_records)
        self._apply_records(records)
        if self.v3 is not None:
            self._apply_repo_records(await asyncio.to_thread(self.v3.list_runs))

    @property
    def repo_mode(self) -> bool:
        """True when the repository tab is the active source."""
        try:
            return self.query_one("#sources", TabbedContent).active == REPO_TAB
        except Exception:  # pragma: no cover - before mount
            return False

    def action_focus_left(self) -> None:
        widget = RepoList if self.repo_mode else RunList
        with contextlib.suppress(Exception):  # the pane may not be mounted
            self.query_one(widget).focus()

    def action_toggle_source(self) -> None:
        """Swap the run list between portable `.tine` files and the v3 repository."""
        if self.v3 is None:
            self.notify(
                "No v3 repository found — run `tine init`, or start the dashboard with --repo.",
                title="Repository",
                severity="warning",
            )
            return
        self.show_source(FILES_TAB if self.repo_mode else REPO_TAB)

    def show_source(self, tab_id: str) -> None:
        """Activate a source tab and move focus into it.

        Focus has to follow: a TabbedContent re-activates whichever pane holds the
        focused widget, so setting `active` alone snaps straight back.
        """
        tabs = self.query_one("#sources", TabbedContent)
        tabs.active = tab_id
        try:
            table = self.query_one(RepoList if tab_id == REPO_TAB else RunList)
        except Exception:  # pragma: no cover - pane absent
            self.query_one("#left-panel").focus()
            return
        table.focus()
        # Switching source re-points the shared panels at whatever that list is
        # already highlighting; the widget will not re-emit for an unchanged row.
        key = table.highlighted_key
        if key is None:
            return
        if tab_id == REPO_TAB:
            self.select_repo_run(key)
        else:
            self.select_run(key)

    @work
    async def action_search(self) -> None:
        value = await self.push_screen_wait(
            TextInputModal(
                "Search / filter runs",
                # `status:` matches the RunStatus value, not the short label the
                # list column prints, so `status:done` can never match anything.
                "tag:x model:y status:completed cost:>0.01 after:2026-01-01 free text",
                value=self._query,
            )
        )
        if value is None:  # cancelled — leave the active filter unchanged
            return
        self._query = value  # empty string clears the filter
        self._refresh()

    def action_focus_center(self) -> None:
        self.query_one(StepTree).focus()

    def action_focus_right(self) -> None:
        # focus the scroll container (the Static itself is not focusable/scrollable)
        self.query_one("#detail-scroll").focus()

    @work
    async def action_verify(self) -> None:
        if self.repo_mode:
            # A repository's equivalent of "verify this artifact" is fsck: re-hash
            # every object, type every ref, resolve every causal link.
            self._show_result(await self._run_in_thread(self._repo_service().fsck))
            return
        record = self._require_selected()
        if record is None:
            return
        integrity = self.actions.verify(record.path)
        if record.has_signature:
            options = await self.push_screen_wait(VerifyKeyModal())
            if options is not None:
                signature = self.actions.verify_signature(record, options)
                self._show_result(
                    ActionResult(
                        integrity.ok and signature.ok,
                        "Verify",
                        f"{integrity.message}\n\n[signature]\n{signature.message}",
                        path=record.path,
                    )
                )
                return
            # signed, but the user skipped the key — don't imply the signature passed
            self._show_result(
                ActionResult(
                    integrity.ok,
                    "Integrity OK — signature NOT verified" if integrity.ok else "Verify failed",
                    f"{integrity.message}\n\nsignature present but not verified (no key supplied)",
                    path=record.path,
                )
            )
            return
        self._show_result(integrity)

    @work
    async def action_tag(self) -> None:
        record = self._require_loaded()
        if record is None:
            return
        result = await self.push_screen_wait(TagEditorModal(record.run.tags))
        if not result:
            return
        self._show_result(
            await self._run_in_thread(
                self.actions.set_tags, record, result["add"], result["remove"]
            )
        )

    @work
    async def action_budget(self) -> None:
        record = self._require_loaded()
        if record is None:
            return
        current = record.run.budget() if hasattr(record.run, "budget") else None
        options = await self.push_screen_wait(BudgetModal(current))
        if options is None:
            return
        if not await self._confirm(
            "Set budget",
            "This rewrites the integrity digest (and invalidates any signature).",
        ):
            return
        self._show_result(await self._run_in_thread(self.actions.set_budget, record, options))

    @work
    async def action_migrate(self) -> None:
        record = self._require_selected()
        if record is None:
            return
        if record.run is None or record.is_future:
            self._show_result(await self._run_in_thread(self.actions.migrate, record))
            return
        if record.on_disk_version is not None and record.on_disk_version >= FORMAT_VERSION:
            self._show_result(await self._run_in_thread(self.actions.migrate, record))
            return
        result = await self.push_screen_wait(
            MigrateModal(
                version_label=record.version_label,
                has_signature=record.has_signature,
                integrity_ok=bool(record.integrity and record.integrity.ok),
                is_legacy=record.is_legacy,
            )
        )
        if result is None:
            return
        self._show_result(
            await self._run_in_thread(
                self.actions.migrate,
                record,
                result.get("force", False),
                result.get("save_path"),
            )
        )

    @work
    async def action_sign(self) -> None:
        record = self._require_loaded()
        if record is None:
            return
        options = await self.push_screen_wait(SignModal())
        if options is None:
            return
        self._show_result(await self._run_in_thread(self.actions.sign, record, options))

    @work
    async def action_keygen(self) -> None:
        out = await self.push_screen_wait(
            TextInputModal(
                "Generate ed25519 keypair",
                "path for private seed",
                value="opentine_ed25519.key",
            )
        )
        if not out:
            return
        result = self.actions.keygen(out)
        if not result.ok and "already exists" in result.message:
            if not await self._confirm(
                "Replace an existing private key",
                "Overwriting it destroys the only copy of that signing identity and "
                "makes every artifact signed with it permanently unverifiable.",
            ):
                self._show_result(result)
                return
            result = self.actions.keygen(out, force=True)
        self._show_result(result)

    @work
    async def action_fork(self) -> None:
        if self.repo_mode:
            await self._fork_repo_run()
            return
        record = self._require_loaded()
        if record is None:
            return
        if not await self._confirm(
            "Fork run",
            "This will write a new .tine artifact from the selected step.",
        ):
            return
        self._show_result(
            await self._run_in_thread(self.actions.fork, record.run, self._selected_step_id)
        )

    async def _fork_repo_run(self) -> None:
        record = self._require_repo_record()
        if record is None:
            return
        # A repository step id IS the event id it was materialized from, so the
        # step highlighted in the tree is exactly the fork point opentine wants.
        if not self._selected_step_id:
            self._show_result(
                ActionResult(False, "Fork failed", "Select the event to fork from first.")
            )
            return
        ref = await self.push_screen_wait(
            TextInputModal(
                "Fork run object",
                "ref for the fork, e.g. experiments/retry (blank: no ref)",
                value="experiments/",
            )
        )
        if ref is None:
            return
        result = await self._run_in_thread(
            self._repo_service().fork, record, self._selected_step_id, ref.strip()
        )
        if not result.ok and "already points at" in result.message:
            if not await self._confirm(
                "Move an existing ref",
                f"{ref.strip()} already names a run. Forking onto it moves the ref "
                "off that run; anything tracking the name follows the fork instead.",
            ):
                self._show_result(result)
                return
            result = await self._run_in_thread(
                self._repo_service().fork, record, self._selected_step_id, ref.strip(), True
            )
        self._show_result(result)

    @work
    async def action_cache_replay(self) -> None:
        record = self._require_loaded()
        if record is None:
            return
        if not await self._confirm(
            "Cache replay",
            "This will write a new .tine artifact that reuses recorded steps.",
        ):
            return
        self._show_result(
            await self._run_in_thread(self.actions.cache_replay, record.run, self._selected_step_id)
        )

    @work
    async def action_diff(self) -> None:
        if self.repo_mode:
            record = self._require_repo_record()
            if record is None:
                return
            other = await self.push_screen_wait(
                TextInputModal("Semantic diff against", "ref name or run object id")
            )
            if not other:
                return
            self._show_result(
                await self._run_in_thread(self._repo_service().diff, record.oid, other)
            )
            return
        record = self._require_loaded()
        if record is None:
            return
        other = await self.push_screen_wait(
            TextInputModal("Diff against run", "Run ID, prefix, or .tine path")
        )
        if not other:
            return
        self._show_result(await self._run_in_thread(self.actions.diff, record.run, other))

    @work
    async def action_resume(self) -> None:
        record = self._require_loaded()
        if record is None:
            return
        if not getattr(record.run, "manifest", {}).get("resume", False):
            self._show_result(
                await self._run_in_thread(self.actions.resume, record.run, record.path)
            )
            return
        if not await self._confirm(
            "Resume run",
            "This will update the selected .tine artifact and mark it running.",
        ):
            return
        self._show_result(await self._run_in_thread(self.actions.resume, record.run, record.path))

    @work
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

    @work
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

    @work
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

    # -- v3 repository actions ---------------------------------------------

    @work
    async def action_import_run(self) -> None:
        """Import a portable v2 artifact into the repository (`tine migrate-v3`)."""
        if self.v3 is None:
            self._show_result(
                ActionResult(False, "No repository", "There is no v3 repository to import into.")
            )
            return
        default = ""
        if not self.repo_mode and self._selected_record is not None:
            default = str(self._selected_record.path)
        options = await self.push_screen_wait(ImportModal(default_source=default))
        if options is None:
            return
        if options.allow_unverified and not await self._confirm(
            "Import an unverified artifact",
            "The source failed (or will skip) verification. Importing it records the "
            "failure alongside the objects, but the repository will contain content "
            "nobody can vouch for.",
        ):
            return
        result = await self._run_in_thread(self._repo_service().import_v2, options)
        self._show_result(result)

    @work
    async def action_promote(self) -> None:
        record = self._require_repo_record()
        if record is None:
            return
        options = await self.push_screen_wait(
            PromoteModal(run_label=record.display_name, existing=record.ref_label)
        )
        if options is None:
            return
        if options.overwrite and not await self._confirm(
            "Move an existing promotion",
            f"promotions/{options.name} already names a run. Moving it retires that "
            "claim; anything tracking the promotion follows this run instead.",
        ):
            return
        self._show_result(await self._run_in_thread(self._repo_service().promote, record, options))

    @work
    async def action_evaluate(self) -> None:
        record = self._require_repo_record()
        if record is None:
            return
        options = await self.push_screen_wait(EvaluateModal())
        if options is None:
            return
        self._show_result(await self._run_in_thread(self._repo_service().evaluate, record, options))

    @work
    async def action_repo_log(self) -> None:
        if self.v3 is None:
            self._show_result(ActionResult(False, "No repository", "No v3 repository is open."))
            return
        default = ""
        if self._selected_repo_record is not None:
            default = self._selected_repo_record.refs[0] if self._selected_repo_record.refs else ""
            default = default or self._selected_repo_record.oid
        ref = await self.push_screen_wait(
            TextInputModal("Event log for", "ref name or run object id", value=default)
        )
        if not ref:
            return
        self._show_result(await self._run_in_thread(self._repo_service().log, ref))

    @work
    async def action_inspect_object(self) -> None:
        if self.v3 is None:
            self._show_result(ActionResult(False, "No repository", "No v3 repository is open."))
            return
        default = self._selected_step_id or ""
        if not default and self._selected_repo_record is not None:
            default = self._selected_repo_record.oid
        oid = await self.push_screen_wait(
            TextInputModal("Inspect object", "run:/event:/blob:/attestation: id", value=default)
        )
        if not oid:
            return
        # Blobs stay unresolved: `inspect` deliberately does not auto-resolve the
        # unredacted legacy blob, and the dashboard should not widen that.
        self._show_result(await self._run_in_thread(self._repo_service().inspect, oid))

    def _repo_service(self) -> RepoActionService:
        assert self.repo_actions is not None  # guarded by _require_repo_record / self.v3
        return self.repo_actions

    def _require_repo_record(self) -> RepoRunRecord | None:
        if self.v3 is None or self.repo_actions is None:
            self.query_one(StepDetail).set_message(
                "No repository",
                "This action needs a v3 repository. Run `tine init`, or start with --repo.",
                False,
            )
            return None
        if self._selected_repo_record is None:
            self.query_one(StepDetail).set_message(
                "No run object selected",
                "Switch to the Repository tab with ']' and select a run.",
                False,
            )
            return None
        return self._selected_repo_record

    def _require_selected(self) -> RunRecord | None:
        if self._selected_record is None:
            # On the Repository tab a run *is* selected, just not a file one. Saying
            # "no run selected" there is false and names no way forward; these
            # actions edit a portable artifact, which a repository object is not.
            if self.repo_mode:
                self.query_one(StepDetail).set_message(
                    "Not available for repository runs",
                    "This edits a portable .tine file — tags, budgets, signatures and\n"
                    "migration all live in the artifact, not in a v3 object, which is\n"
                    "immutable and content-addressed.\n\n"
                    "Press '[' for the Files tab, or 'i' to import a file into the "
                    "repository.",
                    False,
                )
            else:
                self.query_one(StepDetail).set_message(
                    "No run selected", "Select a run first.", False
                )
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
        # The panel keeps the full text (a diff or an fsck report is the point of
        # running it); the toast is what tells the user something happened without
        # making them read the panel to find out.
        self.query_one(StepDetail).set_message(result.title, result.message, result.ok)
        first_line = result.message.strip().splitlines()[0] if result.message.strip() else ""
        self.notify(
            escape_markup(first_line[:200]),
            title=escape_markup(result.title),
            severity="information" if result.ok else "error",
            timeout=6 if result.ok else 10,
        )
        if result.refresh:
            # An action rewrote this artifact; never trust the mtime/size fingerprint
            # the record scan caches it under.
            self.repository.invalidate(result.path)
            if self.v3 is not None:
                self.v3.invalidate()
            self._refresh()
            self._refresh_repo()

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
