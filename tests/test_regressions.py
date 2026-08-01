"""Regressions for defects found auditing the dashboard against opentine 0.3.0.

Each test names the failure it prevents, because every one of these shipped.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from opentine.core import Run, RunStatus, StepKind
from textual.content import Content

from opentine_tui.actions import RunActionService, SignOptions, VerifyKeyOptions
from opentine_tui.app import OpentineTUI
from opentine_tui.repository import RunRepository, safe_filename
from opentine_tui.widgets.step_detail import StepDetail
from opentine_tui.widgets.step_tree import StepTree

HOSTILE = "result [not-a-tag] and [bold red]evil[/] and a dangling [ bracket"
HMAC_KEY = b"0123456789abcdef0123456789abcdef"


def _run(run_id: str = "r", prompt: str = "hello") -> Run:
    run = Run(id=run_id, model_info="mock-model", user_prompt=prompt)
    first = run.add_step(StepKind.think, {"text": "thinking"})
    run.add_step(StepKind.done, {"text": "done"}, parent_ids=[first.id])
    run.status = RunStatus.completed
    return run


def _runs_dir(tmp_path: Path) -> Path:
    directory = tmp_path / ".tine_runs"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


# -- markup ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_hostile_markup_in_run_content_does_not_crash_the_app(tmp_path: Path):
    """`[/]` in recorded output used to raise MarkupError out of Static.update."""
    runs_dir = _runs_dir(tmp_path)
    run = Run(id="hostile", model_info="m[odel]", user_prompt=HOSTILE)
    run.add_step(StepKind.think, {"text": HOSTILE})
    run.status = RunStatus.completed
    run.add_tag("weird")
    run.save(runs_dir / "hostile.tine")

    app = OpentineTUI(runs_dir=runs_dir)
    async with app.run_test() as pilot:
        app.select_run(str(runs_dir / "hostile.tine"))
        await pilot.pause()
        app.select_step(run.steps[0].id)
        await pilot.pause()

        rendered = app.query_one(StepDetail).last_rendered
        # Parses as markup *and* keeps the recorded text byte-for-byte.
        assert HOSTILE in Content.from_markup(rendered).plain


# -- step tree ------------------------------------------------------------


@pytest.mark.asyncio
async def test_every_step_is_visible_not_just_the_first(tmp_path: Path):
    """Child nodes are created collapsed; expanding only the root hid the run."""
    run = Run(id="deep", model_info="m", user_prompt="p")
    parent = None
    for index in range(5):
        step = run.add_step(
            StepKind.think, {"text": f"step {index}"}, parent_ids=[parent] if parent else None
        )
        parent = step.id
    run.status = RunStatus.completed

    app = OpentineTUI(runs_dir=_runs_dir(tmp_path))
    async with app.run_test() as pilot:
        tree = app.query_one(StepTree)
        tree.load_run(run)
        await pilot.pause()
        assert len(list(tree._tree_lines)) == 6  # root + five steps


@pytest.mark.asyncio
async def test_merge_step_names_the_parents_it_is_not_drawn_under(tmp_path: Path):
    run = Run(id="merge", model_info="m", user_prompt="p")
    left = run.add_step(StepKind.think, {"text": "left"})
    right = run.add_step(StepKind.think, {"text": "right"})
    run.add_step(StepKind.done, {"text": "merged"}, parent_ids=[left.id, right.id])
    run.status = RunStatus.completed

    app = OpentineTUI(runs_dir=_runs_dir(tmp_path))
    async with app.run_test() as pilot:
        tree = app.query_one(StepTree)
        tree.load_run(run)
        await pilot.pause()
        labels = [str(line.node.label) for line in tree._tree_lines]
        assert any("also from" in label for label in labels)


# -- malformed artifacts --------------------------------------------------


@pytest.mark.parametrize(
    "budget",
    [
        {"max_cost": 1.0, "on_breach": "warn"},
        {"max_usage": 1.5, "on_breach": "stop"},
        {"max_steps": -3, "on_breach": "stop"},
    ],
)
@pytest.mark.asyncio
async def test_a_budget_opentine_refuses_does_not_kill_the_app(tmp_path: Path, budget: dict):
    """`run.budget()` raises on these; it used to raise straight out of select_run."""
    runs_dir = _runs_dir(tmp_path)
    path = runs_dir / "budget.tine"
    _run("budgeted").save(path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw.setdefault("manifest", {})["budget"] = budget
    path.write_text(json.dumps(raw), encoding="utf-8")

    app = OpentineTUI(runs_dir=runs_dir)
    async with app.run_test() as pilot:
        app.select_run(str(path))
        await pilot.pause()
        assert "unreadable" in app.query_one(StepDetail).last_rendered


# -- write safety ---------------------------------------------------------


def test_run_id_cannot_escape_the_runs_directory(tmp_path: Path):
    repository = RunRepository(_runs_dir(tmp_path))
    hostile = _run("../../../../etc/pwned")
    target = repository.path_for_run(hostile)

    assert target.parent == repository.runs_dir
    assert ".." not in target.name


def test_safe_filename_keeps_ordinary_ids_readable():
    assert safe_filename("refactor-parser") == "refactor-parser"
    assert safe_filename("../etc/passwd") == "etc_passwd"
    assert safe_filename("") == "run"


def test_cache_replay_is_reproducible_and_refuses_to_destroy_the_earlier_one(tmp_path: Path):
    """A cached replay reuses recorded steps, so it must land on one id.

    0.4.0 gives every fork act a random nonce; a replay has to opt out with
    ``nonce=""`` or each one writes a new artifact and the refusal never fires.
    """
    repository = RunRepository(_runs_dir(tmp_path))
    service = RunActionService(repository)
    run = _run("source")

    first = service.cache_replay(run, None)
    assert first.ok, first.message
    second = service.cache_replay(run, None)

    assert not second.ok
    assert "already exists" in second.message
    assert first.path is not None and first.path.exists()


def test_forking_the_same_point_twice_yields_two_distinct_runs(tmp_path: Path):
    """0.4.0 makes a fork id identify the fork *act*, not its coordinate.

    Before it, both forks derived one id and one filename, and the second write
    silently destroyed the first.
    """
    runs_dir = _runs_dir(tmp_path)
    service = RunActionService(RunRepository(runs_dir))
    run = _run("source")

    first = service.fork(run, None)
    second = service.fork(run, None)

    assert first.ok and second.ok, (first.message, second.message)
    assert first.run.id != second.run.id
    assert first.path != second.path
    assert len(list(runs_dir.glob("*.tine"))) == 2


def test_fork_still_refuses_a_named_destination_that_exists(tmp_path: Path):
    runs_dir = _runs_dir(tmp_path)
    service = RunActionService(RunRepository(runs_dir))
    occupied = runs_dir / "taken.tine"
    occupied.write_text("do not lose me", encoding="utf-8")

    result = service.fork(_run("source"), None, save_path=occupied)

    assert not result.ok
    assert occupied.read_text(encoding="utf-8") == "do not lose me"


def test_keygen_refuses_to_replace_an_existing_private_key(tmp_path: Path):
    service = RunActionService(RunRepository(_runs_dir(tmp_path)))
    key_path = tmp_path / "signing.key"
    key_path.write_text("existing-secret\n", encoding="utf-8")

    result = service.keygen(key_path)

    assert not result.ok
    assert key_path.read_text(encoding="utf-8") == "existing-secret\n"


def test_signing_elsewhere_refuses_to_clobber_without_overwrite(tmp_path: Path):
    runs_dir = _runs_dir(tmp_path)
    repository = RunRepository(runs_dir)
    service = RunActionService(repository)
    source = runs_dir / "source.tine"
    _run("signable").save(source)
    occupied = tmp_path / "already-there.tine"
    occupied.write_text("do not lose me", encoding="utf-8")

    record = repository.inspect_path(source)
    options = SignOptions(key_env=None, key_file=None, save_path=str(occupied))
    options.key_file = str(tmp_path / "hmac.key")
    (tmp_path / "hmac.key").write_bytes(HMAC_KEY)

    result = service.sign(record, options)

    assert not result.ok
    assert occupied.read_text(encoding="utf-8") == "do not lose me"


def test_two_hmac_key_sources_are_refused_rather_than_ranked(tmp_path: Path):
    """`tine verify` refuses the pair; picking one lets the artifact choose its judge."""
    runs_dir = _runs_dir(tmp_path)
    repository = RunRepository(runs_dir)
    service = RunActionService(repository)
    path = runs_dir / "signed.tine"
    _run("signed").save(path, sign_key=HMAC_KEY)
    record = repository.inspect_path(path)

    result = service.verify_signature(
        record, VerifyKeyOptions(key_env="OPENTINE_KEY", key_file=str(tmp_path / "k"))
    )

    assert not result.ok
    assert "pass the one you mean" in result.message


def test_a_failed_tag_write_does_not_leave_the_tag_on_the_cached_run(tmp_path: Path, monkeypatch):
    runs_dir = _runs_dir(tmp_path)
    repository = RunRepository(runs_dir)
    service = RunActionService(repository)
    path = runs_dir / "tagme.tine"
    _run("tagme").save(path)
    record = repository.inspect_path(path)

    def explode(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(repository, "write_tags", explode)
    result = service.set_tags(record, ["prod"], [])

    assert not result.ok
    # The next successful save must not smuggle in the tag the user was told failed.
    assert record.run.tags == []


def test_a_credential_shaped_tag_is_flagged_as_redaction_bait(tmp_path: Path):
    runs_dir = _runs_dir(tmp_path)
    repository = RunRepository(runs_dir)
    service = RunActionService(repository)
    path = runs_dir / "creds.tine"
    _run("creds").save(path)
    record = repository.inspect_path(path)

    result = service.set_tags(record, ["api_key=sk-live-1"], [])

    assert result.ok
    assert "redactor" in result.message
