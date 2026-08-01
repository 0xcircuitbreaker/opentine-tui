"""Coverage for the opentine 0.4.0 surface: fork-act identity.

0.4.0 made a v2 fork id identify the fork *act* — source lineage, retained slice,
branch, declared intent and a 128-bit nonce — and records that basis in
``metadata.fork`` so a fork can prove its own id.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from opentine.core import Run, RunStatus, StepKind

from opentine_tui.actions import RunActionService
from opentine_tui.repository import RunRepository
from opentine_tui.widgets.step_detail import StepDetail


def _run(run_id: str = "source") -> Run:
    run = Run(id=run_id, model_info="mock-model", user_prompt="hello")
    first = run.add_step(StepKind.think, {"text": "thinking"})
    run.add_step(StepKind.done, {"text": "done"}, parent_ids=[first.id])
    run.status = RunStatus.completed
    return run


@pytest.fixture
def service(tmp_path: Path):
    runs_dir = tmp_path / ".tine_runs"
    runs_dir.mkdir()
    repository = RunRepository(runs_dir)
    return repository, RunActionService(repository), runs_dir


def test_fork_records_a_basis_that_verifies(service):
    repository, actions, _ = service

    result = actions.fork(_run(), None)

    assert result.ok, result.message
    record = repository.inspect_path(result.path)
    lines = "\n".join(StepDetail._fork_lines(record.run))
    assert "identity: [green]verified[/]" in lines
    assert "branch: main" in lines


def test_an_edited_fork_basis_is_reported_as_a_mismatch(service):
    repository, actions, _ = service
    result = actions.fork(_run(), None)

    raw = json.loads(result.path.read_text(encoding="utf-8"))
    raw["metadata"]["fork"]["nonce"] = "0" * 32  # claim a different fork act
    result.path.write_text(json.dumps(raw), encoding="utf-8")

    record = repository.inspect_path(result.path)
    lines = "\n".join(StepDetail._fork_lines(record.run))
    assert "MISMATCH" in lines


def test_a_cached_replay_is_marked_reproducible(service):
    repository, actions, _ = service

    result = actions.cache_replay(_run(), None)

    assert result.ok, result.message
    record = repository.inspect_path(result.path)
    lines = "\n".join(StepDetail._fork_lines(record.run))
    assert "reproducible fork" in lines
    assert "identity: [green]verified[/]" in lines


def test_a_pre_040_fork_gets_no_verdict_rather_than_an_accusation(service):
    """`verify_fork_id` returns None for an artifact with no basis recorded."""
    repository, actions, _ = service
    result = actions.fork(_run(), None)

    raw = json.loads(result.path.read_text(encoding="utf-8"))
    del raw["metadata"]["fork"]  # what a 0.3.0 fork looks like
    result.path.write_text(json.dumps(raw), encoding="utf-8")

    record = repository.inspect_path(result.path)
    lines = "\n".join(StepDetail._fork_lines(record.run))
    assert "MISMATCH" not in lines
    assert "[green]verified[/]" not in lines


def test_a_run_that_never_forked_shows_no_fork_block(service):
    _, _, runs_dir = service
    path = runs_dir / "plain.tine"
    _run("plain").save(path)

    assert StepDetail._fork_lines(Run.load(path)) == []


def test_replay_lands_on_the_same_artifact_the_cli_would_write(service):
    """The intent digest is an input to the fork id, so it has to match `tine`.

    `tine replay --mode cache` forks with `intent={"replay": "cache"}, nonce=""`
    (opentine `_cli_flow.py` and `_runtime_history.py`); a different intent puts
    the dashboard's replay on a different artifact for the same run and step.
    """
    _, actions, _ = service
    run = _run()

    expected = run.fork(run.steps[-1].id, intent={"replay": "cache"}, nonce="").id
    result = actions.cache_replay(run, None)

    assert result.ok, result.message
    assert result.run.id == expected


def test_a_tampered_lineage_is_not_shown_under_a_verified_badge(service):
    """`forked_from` sits outside the digest; the verdict covers `metadata.fork`.

    Reading the display keys and then printing a green badge let an edited
    artifact claim any ancestry it liked and look attested.
    """
    repository, actions, _ = service
    result = actions.fork(_run("honest-source"), None)

    raw = json.loads(result.path.read_text(encoding="utf-8"))
    raw["metadata"]["forked_from"] = "trusted-corporate-baseline"
    raw["metadata"]["fork_point"] = "0" * 64
    result.path.write_text(json.dumps(raw), encoding="utf-8")

    lines = "\n".join(StepDetail._fork_lines(repository.inspect_path(result.path).run))
    assert "identity: [green]verified[/]" in lines
    assert "trusted-corporate-baseline" not in lines
    assert "honest-source" in lines


def test_an_unverifiable_lineage_is_labelled_as_a_claim(service):
    repository, actions, _ = service
    result = actions.fork(_run(), None)

    raw = json.loads(result.path.read_text(encoding="utf-8"))
    del raw["metadata"]["fork"]
    result.path.write_text(json.dumps(raw), encoding="utf-8")

    lines = "\n".join(StepDetail._fork_lines(repository.inspect_path(result.path).run))
    assert "(claimed)" in lines
    assert "no basis recorded" in lines


def test_repository_fork_refuses_to_move_an_existing_ref(tmp_path: Path):
    """`fork_run` compare-and-swaps against the current value, so it would retarget."""
    from opentine import Repo

    from opentine_tui.repo_actions import RepoActionService
    from opentine_tui.v3 import V3Repository

    root = tmp_path / "project"
    repo = Repo.init(root)
    repo.put_run(_run("main-run"), ref="heads/main")
    repository = V3Repository.discover(root)
    service = RepoActionService(repository)
    record = repository.find("heads/main")
    root_event = repository.log("heads/main")[-1].oid
    before = repo.read_ref("heads/main")

    blocked = service.fork(record, root_event, ref="heads/main")
    assert not blocked.ok
    assert "already points at" in blocked.message
    assert repo.read_ref("heads/main") == before

    moved = service.fork(record, root_event, ref="heads/main", overwrite=True)
    assert moved.ok
    assert repo.read_ref("heads/main") != before


def test_a_repository_checked_out_of_git_still_opens(tmp_path: Path):
    """Git and tar drop empty directories; 0.4.0 made `Repo.open` recreate them.

    Without it a repository committed to version control opened as corrupt, which
    is the normal way a team shares one.
    """
    from opentine import Repo

    from opentine_tui.repo_actions import RepoActionService
    from opentine_tui.v3 import V3Repository

    root = tmp_path / "project"
    Repo.init(root).put_run(_run("shared"), ref="heads/main")

    for _ in range(3):  # innermost first, repeatedly, as an archiver would
        for path in sorted(root.rglob("*"), key=lambda item: -len(item.parts)):
            if path.is_dir() and not any(path.iterdir()):
                path.rmdir()

    repository = V3Repository.discover(root)
    assert repository is not None
    assert [record.status for record in repository.list_runs()] == ["completed"]
    assert RepoActionService(repository).fsck().ok


def test_forking_twice_writes_two_artifacts_neither_destroyed(service):
    """The 0.3.0 collision silently destroyed the first fork; 0.4.0 diverges."""
    _, actions, runs_dir = service
    run = _run()

    first = actions.fork(run, None)
    second = actions.fork(run, None)

    assert {first.path, second.path} == set(runs_dir.glob("*.tine")) - {runs_dir / "source.tine"}
    assert first.run.id != second.run.id
    assert first.path.exists() and second.path.exists()


# -- first-run experience --------------------------------------------------


@pytest.mark.asyncio
async def test_arrowing_an_empty_run_list_does_not_kill_the_dashboard(tmp_path: Path):
    """`RowHighlighted` fires on an empty table with `row_key` itself None."""
    from opentine_tui.app import OpentineTUI

    runs_dir = tmp_path / ".tine_runs"
    runs_dir.mkdir()
    app = OpentineTUI(runs_dir=runs_dir, repo_path=tmp_path)

    async with app.run_test() as pilot:
        for key in ("up", "down", "up", "enter"):
            await pilot.press(key)
            await pilot.pause()
        assert app.is_running


@pytest.mark.asyncio
async def test_startup_selection_matches_the_highlighted_row(tmp_path: Path):
    """Both lists highlight on populate, and those messages are queued.

    The repository list's highlight used to land last and take over the shared
    panels, leaving the Files tab showing a highlighted row that no action touched.
    """
    from opentine import Repo

    from opentine_tui.app import OpentineTUI
    from opentine_tui.widgets.run_list import RunList

    runs_dir = tmp_path / ".tine_runs"
    runs_dir.mkdir()
    _run("file-run").save(runs_dir / "file-run.tine")
    Repo.init(tmp_path).put_run(_run("repo-run"), ref="heads/main")

    app = OpentineTUI(runs_dir=runs_dir, repo_path=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert not app.repo_mode
        assert app._selected_repo_record is None
        assert app._selected_record is not None
        assert app._selected_record.key == app.query_one(RunList).highlighted_key


@pytest.mark.asyncio
async def test_switching_source_repoints_the_shared_panels(tmp_path: Path):
    from opentine import Repo

    from opentine_tui.app import OpentineTUI

    runs_dir = tmp_path / ".tine_runs"
    runs_dir.mkdir()
    _run("file-run").save(runs_dir / "file-run.tine")
    Repo.init(tmp_path).put_run(_run("repo-run"), ref="heads/main")

    app = OpentineTUI(runs_dir=runs_dir, repo_path=tmp_path)
    async with app.run_test() as pilot:
        await pilot.press("]")
        await pilot.pause()
        assert app.repo_mode
        assert app._selected_repo_record is not None
        assert app._selected_record is None

        await pilot.press("[")
        await pilot.pause()
        assert not app.repo_mode
        assert app._selected_record is not None
        assert app._selected_repo_record is None


@pytest.mark.asyncio
async def test_file_only_actions_explain_themselves_on_the_repository_tab(tmp_path: Path):
    """ "No run selected" was false there — a run is selected, just not a file one."""
    from opentine import Repo

    from opentine_tui.app import OpentineTUI
    from opentine_tui.widgets.step_detail import StepDetail

    runs_dir = tmp_path / ".tine_runs"
    runs_dir.mkdir()
    Repo.init(tmp_path).put_run(_run("repo-run"), ref="heads/main")

    app = OpentineTUI(runs_dir=runs_dir, repo_path=tmp_path)
    async with app.run_test() as pilot:
        await pilot.press("]")
        await pilot.pause()
        await pilot.press("t")
        await pilot.pause()

        rendered = app.query_one(StepDetail).last_rendered
        assert "No run selected" not in rendered
        assert "Not available for repository runs" in rendered
        assert "Files tab" in rendered  # names the way out


@pytest.mark.asyncio
async def test_a_fresh_repository_is_not_told_to_run_tine_init(tmp_path: Path):
    from opentine import Repo

    from opentine_tui.app import OpentineTUI
    from opentine_tui.widgets.step_detail import StepDetail

    Repo.init(tmp_path)
    runs_dir = tmp_path / ".tine_runs"
    runs_dir.mkdir()

    app = OpentineTUI(runs_dir=runs_dir, repo_path=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        rendered = app.query_one(StepDetail).last_rendered
        assert "Nothing recorded yet" in rendered
        assert "tine init" not in rendered  # advice the user has already taken
