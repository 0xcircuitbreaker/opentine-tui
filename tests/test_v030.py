"""Coverage for the opentine 0.3.0 surface: the v3 repository and its actions."""

from __future__ import annotations

from pathlib import Path

import pytest
from opentine.core import Run, RunStatus, StepKind
from textual.widgets import Static, TabbedContent

from opentine_tui.app import FILES_TAB, REPO_TAB, OpentineTUI
from opentine_tui.dialogs import parse_scores
from opentine_tui.formatting import (
    billing_status,
    short_ref,
    step_cost,
    token_summary,
    token_total,
)
from opentine_tui.repo_actions import (
    EvaluateOptions,
    ImportOptions,
    PromoteOptions,
    RepoActionService,
)
from opentine_tui.v3 import V3Repository, short_oid
from opentine_tui.widgets.repo_list import RepoList
from opentine_tui.widgets.step_detail import StepDetail
from opentine_tui.widgets.step_tree import StepTree


def _run(run_id: str, prompt: str = "ship it") -> Run:
    run = Run(id=run_id, model_info="claude-opus-5", user_prompt=prompt)
    first = run.add_step(
        StepKind.think,
        {"text": "plan"},
        cost=0.01,
        usage={"input": 100, "output": 50, "cache_read": 20, "reasoning": 30},
    )
    second = run.add_step(
        StepKind.tool,
        {"name": "write", "arguments": {"path": "x.py"}},
        parent_ids=[first.id],
        outputs={"text": "ok"},
    )
    run.add_step(StepKind.done, {"text": "done"}, parent_ids=[second.id])
    run.status = RunStatus.completed
    return run


@pytest.fixture
def workspace(tmp_path: Path):
    """A directory holding both a `.tine_runs` folder and a v3 repository."""
    runs_dir = tmp_path / ".tine_runs"
    runs_dir.mkdir()
    _run("file-run").save(runs_dir / "file-run.tine")

    repository = V3Repository.init(tmp_path)
    repository.repo.put_run(_run("repo-main"), ref="heads/main")
    repository.repo.put_run(_run("repo-exp", "ship it differently"), ref="heads/experiment")
    repository.invalidate()
    return tmp_path, runs_dir, repository


# -- discovery -------------------------------------------------------------


def test_discovery_walks_up_like_the_cli(workspace):
    root, _runs_dir, _ = workspace
    nested = root / "a" / "b"
    nested.mkdir(parents=True)

    found = V3Repository.discover(nested)

    assert found is not None
    assert found.path == (root / ".tine").resolve()


def test_discovery_returns_none_without_a_repository(tmp_path: Path):
    assert V3Repository.discover(tmp_path) is None


# -- read model ------------------------------------------------------------


def test_list_runs_reports_refs_status_and_cost(workspace):
    _, _, repository = workspace

    records = repository.list_runs()

    assert len(records) == 2
    by_ref = {record.ref_label: record for record in records}
    assert set(by_ref) == {"main", "experiment"}
    assert by_ref["main"].status == "completed"
    assert by_ref["main"].event_count == 3
    assert by_ref["main"].cost == pytest.approx(0.01)
    assert by_ref["main"].source_run_id == "repo-main"
    assert by_ref["main"].load_error is None


def test_list_runs_is_cached_until_the_object_set_changes(workspace, monkeypatch):
    _, _, repository = workspace
    repository.list_runs()

    calls: list[str] = []
    original = repository.repo.get
    monkeypatch.setattr(repository.repo, "get", lambda oid: (calls.append(oid), original(oid))[1])

    repository.list_runs()
    assert calls == []  # served from the fingerprint cache

    repository.repo.put_run(_run("repo-third", "a third run"), ref="heads/third")
    assert len(repository.list_runs()) == 3


def test_a_promotion_ref_outranks_a_branch_head(workspace):
    _, _, repository = workspace
    main = repository.find("heads/main")
    repository.repo.promote(main.oid, "production")
    repository.invalidate()

    assert repository.find("heads/main").ref_label == "production"


def test_load_run_renders_through_the_v2_widgets(workspace):
    _, _, repository = workspace

    run = repository.load_run("heads/main")

    assert run.id == "repo-main"
    assert len(run.steps) == 3
    # A repository step id is the event oid it came from — that is what makes
    # "fork from the highlighted step" work without any translation.
    assert all(step.id.startswith("event:sha256:") for step in run.steps)
    assert short_ref(run.steps[0].id) != "event:sha256"


def test_find_resolves_refs_oids_and_short_prefixes(workspace):
    _, _, repository = workspace
    record = repository.find("heads/main")

    assert repository.find(record.oid) is record or repository.find(record.oid).oid == record.oid
    assert repository.find(record.short_id[:8]).oid == record.oid
    assert repository.find("heads/nope") is None


# -- actions ---------------------------------------------------------------


def test_fsck_reports_a_healthy_repository(workspace):
    _, _, repository = workspace

    result = RepoActionService(repository).fsck()

    assert result.ok
    assert "every ref typed" in result.message


def test_import_brings_a_v2_file_into_the_repository(workspace):
    _, runs_dir, repository = workspace
    service = RepoActionService(repository)

    result = service.import_v2(ImportOptions(source=str(runs_dir / "file-run.tine")))

    assert result.ok, result.message
    assert "3 events" in result.message
    repository.invalidate()
    assert any(record.source_run_id == "file-run" for record in repository.list_runs())


def test_import_refuses_a_tampered_artifact(workspace):
    import json

    _, runs_dir, repository = workspace
    tampered = runs_dir / "tampered.tine"
    _run("tampered").save(tampered)
    raw = json.loads(tampered.read_text(encoding="utf-8"))
    raw["status"] = "failed"
    tampered.write_text(json.dumps(raw), encoding="utf-8")

    result = RepoActionService(repository).import_v2(ImportOptions(source=str(tampered)))

    assert not result.ok
    assert "tampered" in result.message


def test_promote_refuses_to_move_an_existing_promotion_silently(workspace):
    _, _, repository = workspace
    service = RepoActionService(repository)
    main = repository.find("heads/main")
    experiment = repository.find("heads/experiment")

    assert service.promote(main, PromoteOptions(name="production")).ok

    blocked = service.promote(experiment, PromoteOptions(name="production"))
    assert not blocked.ok
    assert "already points at" in blocked.message

    moved = service.promote(experiment, PromoteOptions(name="production", overwrite=True))
    assert moved.ok
    assert repository.repo.read_ref("promotions/production") == experiment.oid


def test_promote_reports_an_illegal_ref_name(workspace):
    _, _, repository = workspace
    main = repository.find("heads/main")

    result = RepoActionService(repository).promote(main, PromoteOptions(name="Prod Release"))

    assert not result.ok
    assert "invalid ref name" in result.message


def test_evaluate_records_a_score_the_run_list_can_show(workspace):
    _, _, repository = workspace
    service = RepoActionService(repository)
    main = repository.find("heads/main")

    result = service.evaluate(main, EvaluateOptions(scores={"quality": 0.9}, signer="bench"))

    assert result.ok
    repository.invalidate()
    assert repository.find("heads/main").score == pytest.approx(0.9)


def test_fork_needs_an_event_and_produces_a_new_run(workspace):
    _, _, repository = workspace
    service = RepoActionService(repository)
    main = repository.find("heads/main")

    assert not service.fork(main, None).ok

    root_event = repository.log("heads/main")[-1].oid
    result = service.fork(main, root_event, ref="experiments/retry")

    assert result.ok, result.message
    assert repository.repo.read_ref("experiments/retry") is not None


def test_semantic_diff_summarizes_two_runs(workspace):
    _, _, repository = workspace

    result = RepoActionService(repository).diff("heads/main", "heads/experiment")

    assert result.ok
    assert "Diff: heads/main -> heads/experiment" in result.message
    assert "changed" in result.message


def test_log_lists_events_root_last(workspace):
    _, _, repository = workspace

    result = RepoActionService(repository).log("heads/main")

    assert result.ok
    assert result.message.strip().splitlines()[-1].endswith("(root)")


# -- app wiring ------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_repository_tab_shares_the_step_and_detail_panels(workspace):
    root, runs_dir, _ = workspace
    app = OpentineTUI(runs_dir=runs_dir, repo_path=root)

    async with app.run_test() as pilot:
        assert app.v3 is not None
        assert len(app._repo_records) == 2

        await pilot.press("]")
        await pilot.pause()
        assert app.repo_mode
        assert isinstance(app.focused, RepoList)

        app.query_one(RepoList).move_cursor(row=0)
        app.query_one(RepoList).action_select_run()
        await pilot.pause()

        assert app._selected_repo_record is not None
        assert app.query_one(StepTree)._run is not None
        assert "Run object:" in app.query_one(StepDetail).last_rendered

        await pilot.press("[")
        await pilot.pause()
        assert not app.repo_mode
        assert app.query_one("#sources", TabbedContent).active == FILES_TAB


@pytest.mark.asyncio
async def test_without_a_repository_the_tab_explains_itself(tmp_path: Path):
    runs_dir = tmp_path / ".tine_runs"
    runs_dir.mkdir()
    app = OpentineTUI(runs_dir=runs_dir, repo_path=tmp_path)

    async with app.run_test() as pilot:
        assert app.v3 is None
        await pilot.press("]")
        await pilot.pause()
        # Toggling is a no-op with a warning rather than an empty, broken table.
        assert app.query_one("#sources", TabbedContent).active == FILES_TAB
        assert "No v3 repository" in str(app.query_one("#repo-empty", Static).content)


@pytest.mark.asyncio
async def test_verify_runs_fsck_in_repository_mode(workspace):
    root, runs_dir, _ = workspace
    app = OpentineTUI(runs_dir=runs_dir, repo_path=root)

    async with app.run_test() as pilot:
        await pilot.press("]")
        await pilot.pause()
        await pilot.press("v")
        # fsck runs in a thread; wait for the worker rather than a guessed number
        # of frames, which races on a slow runner.
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()

        assert "Repository OK" in app.query_one(StepDetail).last_rendered


@pytest.mark.asyncio
async def test_repo_tab_id_constants_match_the_panes(workspace):
    root, runs_dir, _ = workspace
    app = OpentineTUI(runs_dir=runs_dir, repo_path=root)
    async with app.run_test():
        panes = {pane.id for pane in app.query("TabPane")}
        assert {FILES_TAB, REPO_TAB} <= panes


# -- 0.3.0 accounting on the v2 path ---------------------------------------


def test_step_cost_prefers_the_billing_subtotal():
    class _Step:
        cost = 0.0
        billing = {"status": "complete", "known_subtotal_usd": "0.0425"}

    assert step_cost(_Step()) == pytest.approx(0.0425)


def test_unpriced_steps_are_distinguishable_from_free_ones():
    class _Unpriced:
        cost = 0.0
        billing = {"status": "unknown", "amount_usd": None}

    class _Free:
        cost = 0.0
        billing = {}

    assert billing_status(_Unpriced())[1] == "unpriced"
    assert billing_status(_Free()) is None


def test_usage_summary_counts_cache_and_reasoning():
    usage = {"input": 100, "output": 50, "cache_read": 20, "reasoning": 30}
    assert token_total(usage) == 200
    assert "cache-r 20" in token_summary(usage)


def test_short_oid_and_short_ref_agree_on_object_ids():
    oid = "run:sha256:" + "ab" * 32
    assert short_oid(oid) == short_ref(oid) == "abababababab"


def test_parse_scores_accepts_pairs_and_rejects_junk():
    assert parse_scores("quality=0.9, speed=0.7") == {"quality": 0.9, "speed": 0.7}
    assert parse_scores("") == {}
    with pytest.raises(ValueError):
        parse_scores("quality")
    with pytest.raises(ValueError):
        parse_scores("quality=high")
