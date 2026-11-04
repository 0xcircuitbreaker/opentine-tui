"""Regression tests for the 0.34 security and correctness audit.

Each test names the finding it pins. They run with compression on (conftest).
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest
from opentine.core import Run, RunStatus, StepKind

from opentine_tui import cli_bridge, interop
from opentine_tui.actions import RunActionService
from opentine_tui.app import OpentineTUI
from opentine_tui.repo_actions import ImportOptions, RepoActionService
from opentine_tui.repository import RunRepository
from opentine_tui.v3 import V3Repository
from opentine_tui.widgets.step_detail import StepDetail
from tests._artifacts import read_doc, write_doc

HMAC_KEY = b"0123456789abcdef0123456789abcdef"


def _run(run_id: str = "audit") -> Run:
    run = Run(id=run_id, model_info="m", user_prompt="p")
    first = run.add_step(StepKind.think, {"text": "a"})
    second = run.add_step(StepKind.tool, {"name": "t", "arguments": {"q": 1}}, parent_id=first.id)
    run.add_step(StepKind.done, {"text": "z"}, parent_id=second.id)
    run.status = RunStatus.completed
    return run


@pytest.fixture
def service(tmp_path: Path) -> RunActionService:
    runs = tmp_path / "runs"
    runs.mkdir()
    return RunActionService(RunRepository(runs))


# -- keygen: symlinks, permissions ------------------------------------------


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions and symlinks")
def test_keygen_never_writes_through_a_symlink(service, tmp_path: Path):
    victim = tmp_path / "victim.txt"
    victim.write_text("untouched", encoding="utf-8")
    link = tmp_path / "key"
    link.symlink_to(victim)

    for force in (False, True):
        result = service.keygen(link, force=force)
        assert not result.ok
    assert victim.read_text(encoding="utf-8") == "untouched"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_forced_keygen_over_a_world_readable_file_is_private_from_the_start(service, tmp_path):
    key = tmp_path / "key"
    key.write_text("old\n", encoding="utf-8")
    key.chmod(0o644)

    result = service.keygen(key, force=True)

    assert result.ok, result.message
    assert stat.S_IMODE(key.stat().st_mode) == 0o600
    assert key.read_text(encoding="utf-8") != "old\n"
    assert not [p for p in tmp_path.iterdir() if p.name.startswith(".key.")]  # no temp left


# -- fork-edit: artifact-controlled step ids --------------------------------


def test_a_hostile_step_id_cannot_steer_the_fork_path(service, tmp_path: Path, legacy_v2):
    path = service.repository.runs_dir / "evil.tine"
    _run().save(path)
    data = read_doc(path)
    hostile = "../../../escaped"
    old = data["graph"]["order"][-1]
    text = __import__("json").dumps(data).replace(old, hostile)
    write_doc(path, __import__("json").loads(text))
    record = service.repository.inspect_path(path)
    assert record.run is not None and record.run.steps[-1].id == hostile

    service.fork_edit(record, None, ["inputs.text=1"], [])

    assert not (tmp_path / "escaped.tine").exists()
    assert not list(tmp_path.glob("**/escaped.tine"))
    outside = [p for p in tmp_path.rglob("*.tine") if service.repository.runs_dir not in p.parents]
    assert outside == []


def test_a_step_id_that_looks_like_a_flag_stays_a_value(service, legacy_v2):
    path = service.repository.runs_dir / "flag.tine"
    _run().save(path)
    data = read_doc(path)
    old = data["graph"]["order"][-1]
    import json

    write_doc(path, json.loads(json.dumps(data).replace(old, "--force")))
    record = service.repository.inspect_path(path)
    if record.run is None:
        pytest.skip("this opentine refuses the id outright")
    result = service.fork_edit(record, "--force", ["inputs.text=1"], [])
    # Whatever tine decides, "--force" never reached it as a flag.
    assert "unrecognized arguments" not in result.message


# -- the CLI child cannot be hijacked from the working directory -------------


def test_a_package_planted_in_the_cwd_is_not_imported(tmp_path: Path):
    planted = tmp_path / "opentine"
    planted.mkdir()
    marker = tmp_path / "pwned"
    (planted / "__init__.py").write_text("", encoding="utf-8")
    (planted / "cli.py").write_text(
        f"open({str(marker)!r}, 'w').write('x')\ndef main():\n    pass\n", encoding="utf-8"
    )
    result = cli_bridge.run_tine(["--version"], cwd=tmp_path)
    assert not marker.exists()
    assert result.ok, result.text


# -- dialogs: markup in error text -------------------------------------------


@pytest.mark.asyncio
async def test_a_bracketed_error_in_a_dialog_does_not_crash(tmp_path: Path):
    from opentine_tui.dialogs import HarnessOptionsModal

    app = OpentineTUI(runs_dir=tmp_path, repo_path=tmp_path)
    async with app.run_test() as pilot:
        modal = HarnessOptionsModal()
        app.push_screen(modal)
        await pilot.pause()
        modal.query_one("#extra").value = '"[bold unterminated'
        modal._parse_options()
        await pilot.pause()
        assert app.is_running


# -- rewind: no step means "before the run" -----------------------------------


def test_rewind_without_a_step_targets_before_the_run(service, tmp_path: Path):
    record = service.repository.inspect_path(service.repository.save(_run()))
    result = service.rewind(record, None, str(tmp_path))
    assert result.ok, result.message
    assert "(before run)" in result.message


# -- tags on v4 under a pinned v2 writer ------------------------------------


def test_tagging_a_v4_file_keeps_it_v4_even_with_the_v2_writer_pinned(service, monkeypatch):
    path = service.repository.save(_run())
    assert service.repository.inspect_path(path).is_v4
    monkeypatch.setenv("OPENTINE_FORMAT", "2")

    result = service.set_tags(service.repository.inspect_path(path), ["x"], [])

    assert result.ok, result.message
    assert service.repository.inspect_path(path).on_disk_version == 4


# -- resume reports a dropped signature ---------------------------------------


def test_resume_says_the_signature_was_removed(service):
    run = _run()
    run.manifest["resume"] = True
    path = service.repository.runs_dir / "s.tine"
    run.save(path, sign_key=HMAC_KEY, key_id="k")
    result = service.resume(Run.load(path), path)
    assert result.ok, result.message
    assert "signature was removed" in result.message


# -- trace import size cap ----------------------------------------------------


def test_an_oversized_trace_is_refused_before_it_is_read(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(interop, "MAX_TRACE_BYTES", 64)
    source = tmp_path / "big.json"
    source.write_text("[" + ",".join(["{}"] * 100) + "]", encoding="utf-8")
    with pytest.raises(ValueError, match="refused"):
        interop.read_events(source, "otel-json")


# -- v4 import does not move a ref silently -----------------------------------


def test_a_v4_import_onto_an_existing_ref_needs_consent(tmp_path: Path):
    repo = V3Repository.init(tmp_path)
    service = RepoActionService(repo)
    first, second = tmp_path / "a.tine", tmp_path / "b.tine"
    _run("a").save(first)
    other = _run("b")
    other.add_step(StepKind.think, {"text": "different"})
    other.save(second)

    assert service.import_v2(ImportOptions(source=str(first), ref="heads/main")).ok
    before = repo.repo.read_ref("heads/main")
    blocked = service.import_v2(ImportOptions(source=str(second), ref="heads/main"))
    assert not blocked.ok and "already points at" in blocked.message
    assert repo.repo.read_ref("heads/main") == before
    assert not service.import_v2(ImportOptions(source=str(second), ref="")).ok

    moved = service.import_v2(ImportOptions(source=str(second), ref="heads/main", overwrite=True))
    assert moved.ok, moved.message
    assert repo.repo.read_ref("heads/main") != before


# -- app: polls neither cancel scans nor drop the open step -------------------


@pytest.mark.asyncio
async def test_a_live_repaint_keeps_the_open_step(tmp_path: Path):
    runs = tmp_path / "runs"
    runs.mkdir()
    path = RunRepository(runs).save(_run())
    app = OpentineTUI(runs_dir=runs, repo_path=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        run = app._selected_record.run
        app.select_step(run.steps[1].id)
        # the run grows on disk, as a live harness would make it
        grown = Run.load(path)
        grown.add_step(StepKind.think, {"text": "more"}, parent_id=grown.steps[-1].id)
        grown.save(path)
        os.utime(path, (path.stat().st_atime, path.stat().st_mtime + 5))
        app._refresh()
        await pilot.pause()
        assert app._selected_step_id == run.steps[1].id
        assert "Step:" in app.query_one(StepDetail).last_rendered


@pytest.mark.asyncio
async def test_a_poll_does_not_cancel_a_running_scan(tmp_path: Path):
    app = OpentineTUI(runs_dir=tmp_path, repo_path=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        app._scan()
        running = [w for w in app.workers if w.group == "scan"]
        app._poll()
        await pilot.pause()
        assert all(not w.is_cancelled for w in running)


@pytest.mark.asyncio
async def test_step_labels_survive_backslash_bracket_payloads(tmp_path: Path):
    """Rich reads `\\\\[` as an escaped backslash followed by a live tag; labels
    are built from plain spans, so recorded text can never open a style."""
    runs = tmp_path / "runs"
    runs.mkdir()
    run = Run(id="evil\\", model_info="m\\[bold]")
    run.add_step(StepKind.tool, {"name": "x\\", "arguments": {"q": "\\[red]boom\\[/]"}})
    run.add_step(StepKind.think, {"text": "\\[link=http://e]x\x1b]8;;"})
    run.status = RunStatus.completed
    RunRepository(runs).save(run)
    app = OpentineTUI(runs_dir=runs, repo_path=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        from opentine_tui.widgets.step_tree import StepTree

        tree = app.query_one(StepTree)
        labels = [str(node.label) for node in tree.root.children]
        assert any("[red" in label for label in labels)  # shown literally, not styled
        assert all("\x1b" not in label for label in labels)
        assert "evil\\" in str(tree.root.label)
        assert app.is_running
