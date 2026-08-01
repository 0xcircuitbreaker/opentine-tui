"""Coverage for the opentine 0.2.0 features surfaced by the dashboard.

Format-v2 migration, tags + search, cost + budget, autosave/drafts, signing
(tine-sig/1), and field-level diff.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from opentine._canon import _integrity_digest
from opentine.core import Run, RunStatus, StepKind
from textual.widgets import Input

from opentine_tui.actions import (
    BudgetOptions,
    RunActionService,
    SignOptions,
    VerifyKeyOptions,
    diff_text,
)
from opentine_tui.app import OpentineTUI
from opentine_tui.dialogs import MigrateModal, SignModal, TagEditorModal
from opentine_tui.repository import RunRepository, filter_records
from opentine_tui.widgets.run_list import RunList, record_flags
from opentine_tui.widgets.step_detail import StepDetail

try:
    from opentine.signing import HAS_ED25519
except Exception:  # pragma: no cover
    HAS_ED25519 = False

HMAC_KEY = b"0123456789abcdef0123456789abcdef"  # 32 bytes


def _completed_run(
    run_id: str = "r",
    tags: list[str] | None = None,
    usage: dict[str, int] | None = None,
) -> Run:
    run = Run(id=run_id, model_info="mock-model", user_prompt="hello world")
    run.add_step(StepKind.think, {"text": "thinking"}, usage=usage)
    run.add_step(StepKind.done, {"text": "done"})
    run.status = RunStatus.completed
    for tag in tags or []:
        run.add_tag(tag)
    return run


def _write_v1(path: Path, run_id: str = "v1-run") -> Path:
    """A valid on-disk v1 artifact (a v2 run with format_version downgraded and a
    recomputed v1 digest). Generated in-test so CI does not need the opentine
    source checkout. The 1->2 migration is additive, so this loads/migrates cleanly."""
    _completed_run(run_id).save(path)  # v2
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["format_version"] = 1
    raw.setdefault("metadata", {})["integrity"] = {
        "algorithm": "sha256",
        "digest": _integrity_digest(raw),
    }
    path.write_text(json.dumps(raw, indent=2, sort_keys=True), encoding="utf-8")
    return path


def _write_legacy(path: Path, run_id: str = "legacy-run") -> Path:
    """A 0.1.0 pre-versioned "linear" artifact (type==Run, flat steps, no graph)."""
    data = {
        "type": "Run",
        "id": run_id,
        "model_info": "mock-legacy",
        "user_prompt": "p",
        "status": "completed",
        "steps": [
            {
                "id": "s1",
                "kind": "think",
                "inputs": {"text": "x"},
                "outputs": {},
                "parent_id": None,
            },
            {"id": "s2", "kind": "done", "inputs": {"text": "d"}, "outputs": {}, "parent_id": "s1"},
        ],
    }
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


# --- repository: on-disk classification --------------------------------------


def test_record_detects_v2_native(tmp_path: Path):
    repo = RunRepository(tmp_path)
    path = repo.save(_completed_run(usage={"input": 10, "output": 5}))
    record = repo.inspect_path(path)
    assert record.on_disk_version == 2
    assert record.version_label == "v2"
    assert not record.needs_migration
    assert record.total_tokens == 15
    assert not record.is_corrupt


def test_record_detects_v1_needs_migration(tmp_path: Path):
    dest = _write_v1(tmp_path / "v1.tine")
    record = RunRepository(tmp_path).inspect_path(dest)
    assert record.on_disk_version == 1
    assert record.needs_migration
    assert record.run is not None  # loads via in-memory migration
    assert not record.is_corrupt


def test_record_detects_legacy_not_corrupt(tmp_path: Path):
    dest = _write_legacy(tmp_path / "legacy.tine")
    record = RunRepository(tmp_path).inspect_path(dest)
    assert record.is_legacy
    assert record.on_disk_version == 0
    assert record.needs_migration
    # a legacy file has no digest verifiable under current rules — that is expected
    assert not record.is_corrupt


def test_record_detects_draft(tmp_path: Path):
    path = tmp_path / "draft.tine"
    _completed_run().save(path, draft=True)
    assert RunRepository(tmp_path).inspect_path(path).is_draft


def test_record_detects_signature_presence(tmp_path: Path):
    path = tmp_path / "signed.tine"
    _completed_run().save(path, sign_key=HMAC_KEY, key_id="k1", signer="alice")
    record = RunRepository(tmp_path).inspect_path(path)
    assert record.has_signature
    assert record.sig_state == "no-key"  # present, but no key supplied at scan time


# --- search / filter ---------------------------------------------------------


def test_filter_records_by_tag(tmp_path: Path):
    repo = RunRepository(tmp_path)
    repo.save(_completed_run("a", tags=["bug"]))
    repo.save(_completed_run("b", tags=["feature"]))
    filtered, error = filter_records(repo.list_records(), "tag:bug")
    assert error is None
    ids = {record.run_id for record in filtered if record.run}
    assert ids == {"a"}


def test_filter_bad_query_reports_error_and_keeps_records(tmp_path: Path):
    repo = RunRepository(tmp_path)
    repo.save(_completed_run("a"))
    records = repo.list_records()
    filtered, error = filter_records(records, "cost:notanumber")
    assert error is not None
    assert filtered == records


# --- migration ---------------------------------------------------------------


def test_migrate_upgrades_v1_in_place(tmp_path: Path):
    repo = RunRepository(tmp_path)
    service = RunActionService(repo)
    dest = _write_v1(tmp_path / "v1.tine")

    result = service.migrate(repo.inspect_path(dest))

    assert result.ok
    assert repo.inspect_path(dest).on_disk_version == 2


def test_migrate_skips_current(tmp_path: Path):
    repo = RunRepository(tmp_path)
    service = RunActionService(repo)
    path = repo.save(_completed_run())
    result = service.migrate(repo.inspect_path(path))
    assert not result.ok
    assert "current" in result.message.lower()


# --- tags --------------------------------------------------------------------


def test_set_tags_add_and_remove_preserves_integrity(tmp_path: Path):
    repo = RunRepository(tmp_path)
    service = RunActionService(repo)
    path = repo.save(_completed_run("a", tags=["old"]))

    result = service.set_tags(repo.inspect_path(path), ["New"], ["old"])

    assert result.ok
    assert Run.load(path).tags == ["new"]  # normalized, lower-cased
    assert repo.inspect_path(path).integrity.ok  # tags live outside the digest


# --- cost / budget -----------------------------------------------------------


def test_set_budget_persists(tmp_path: Path):
    repo = RunRepository(tmp_path)
    service = RunActionService(repo)
    path = repo.save(_completed_run())

    record = repo.inspect_path(path)
    result = service.set_budget(record, BudgetOptions(max_cost=1.5, on_breach="stop"))

    assert result.ok
    budget = Run.load(path).budget()
    assert budget is not None
    assert budget.max_cost == 1.5


def test_set_budget_requires_a_limit(tmp_path: Path):
    repo = RunRepository(tmp_path)
    service = RunActionService(repo)
    path = repo.save(_completed_run())
    result = service.set_budget(repo.inspect_path(path), BudgetOptions())
    assert not result.ok


# --- signing -----------------------------------------------------------------


def test_sign_then_verify_hmac_roundtrip(tmp_path: Path):
    repo = RunRepository(tmp_path)
    service = RunActionService(repo)
    path = repo.save(_completed_run())
    keyfile = tmp_path / "hmac.key"
    keyfile.write_bytes(HMAC_KEY)

    signed = service.sign(repo.inspect_path(path), SignOptions(key_file=str(keyfile), key_id="k1"))
    assert signed.ok, signed.message

    record = repo.inspect_path(path)
    assert record.has_signature

    good = service.verify_signature(record, VerifyKeyOptions(key_file=str(keyfile)))
    assert good.ok
    assert "verified" in good.message

    wrong = tmp_path / "wrong.key"
    wrong.write_bytes(b"x" * 32)
    bad = service.verify_signature(record, VerifyKeyOptions(key_file=str(wrong)))
    assert not bad.ok
    assert "mismatch" in bad.message


def test_sign_refuses_draft(tmp_path: Path):
    repo = RunRepository(tmp_path)
    service = RunActionService(repo)
    path = tmp_path / "draft.tine"
    _completed_run().save(path, draft=True)
    keyfile = tmp_path / "hmac.key"
    keyfile.write_bytes(HMAC_KEY)
    result = service.sign(repo.inspect_path(path), SignOptions(key_file=str(keyfile)))
    assert not result.ok
    assert "draft" in result.message.lower()


@pytest.mark.skipif(not HAS_ED25519, reason="needs opentine[crypto]")
def test_keygen_sign_verify_ed25519(tmp_path: Path):
    repo = RunRepository(tmp_path)
    service = RunActionService(repo)

    keys = service.keygen(tmp_path / "ed")
    assert keys.ok
    assert (tmp_path / "ed").exists()
    assert (tmp_path / "ed.pub").exists()

    path = repo.save(_completed_run())
    signed = service.sign(
        repo.inspect_path(path),
        SignOptions(algorithm="ed25519", ed25519_key_file=str(tmp_path / "ed")),
    )
    assert signed.ok, signed.message

    record = repo.inspect_path(path)
    pubkey = str(tmp_path / "ed.pub")
    with_pub = service.verify_signature(record, VerifyKeyOptions(pubkey_file=pubkey))
    assert with_pub.ok
    tofu = service.verify_signature(record, VerifyKeyOptions(trust_embedded=True))
    assert tofu.ok
    assert "tofu" in tofu.message


# --- field-level diff --------------------------------------------------------


def test_diff_text_renders_cost_drift_as_changed():
    run_a = Run(id="a", model_info="m", user_prompt="p")
    run_a.add_step(StepKind.think, {"text": "x"}, cost=0.0)
    run_a.status = RunStatus.completed
    run_b = Run(id="b", model_info="m", user_prompt="p")
    run_b.add_step(StepKind.think, {"text": "x"}, cost=0.05)
    run_b.status = RunStatus.completed

    text = diff_text(run_a, run_b)

    assert "Changed:" in text
    assert "drift" in text  # identical step id, cost differs
    assert "cost" in text
    assert "tokens" in text  # header drift summary


# --- widgets / app -----------------------------------------------------------


def test_record_flags_marks_signature(tmp_path: Path):
    path = tmp_path / "s.tine"
    _completed_run("a", tags=["x"]).save(path, sign_key=HMAC_KEY)
    record = RunRepository(tmp_path).inspect_path(path)
    assert "?" in record_flags(record)  # signed but unverified -> 'no-key' glyph


def test_step_detail_signature_and_budget_lines(tmp_path: Path):
    path = tmp_path / "s.tine"
    run = _completed_run("a")
    run.set_budget(max_cost=2.0)
    run.save(path, sign_key=HMAC_KEY, key_id="k1", signer="alice")
    record = RunRepository(tmp_path).inspect_path(path)

    detail = StepDetail()
    signature_lines = "\n".join(detail._signature_lines(record))
    assert "Authenticity" in signature_lines
    assert "k1" in signature_lines
    assert "alice" in signature_lines

    budget_lines = "\n".join(detail._budget_lines(record.run))
    assert "Budget" in budget_lines
    assert "cost" in budget_lines


@pytest.mark.asyncio
async def test_app_detail_renders_v020_fields(tmp_path: Path):
    runs_dir = tmp_path / ".tine_runs"
    runs_dir.mkdir()
    run = _completed_run("ui", tags=["bug"], usage={"input": 3, "output": 4})
    run.save(runs_dir / "ui.tine", sign_key=HMAC_KEY, key_id="k1")
    app = OpentineTUI(runs_dir=runs_dir)

    async with app.run_test():
        app.select_run(str(runs_dir / "ui.tine"))
        rendered = app.query_one(StepDetail).last_rendered
        assert "Tags:" in rendered
        assert "bug" in rendered
        assert "Tokens:" in rendered
        assert "Authenticity" in rendered


@pytest.mark.asyncio
async def test_app_search_filters_run_list(tmp_path: Path):
    runs_dir = tmp_path / ".tine_runs"
    runs_dir.mkdir()
    _completed_run("a", tags=["bug"]).save(runs_dir / "a.tine")
    _completed_run("b", tags=["feature"]).save(runs_dir / "b.tine")
    app = OpentineTUI(runs_dir=runs_dir)

    async with app.run_test():
        assert app.query_one(RunList).row_count == 2
        app._query = "tag:bug"
        app._refresh()
        assert app.query_one(RunList).row_count == 1


# --- interactive keybinding -> modal -> action flows -------------------------
# These guard the @work decorator on modal-driving actions: under Textual 8.x
# push_screen_wait raises NoActiveWorker unless the action runs in a worker.


async def _settle(app: OpentineTUI, pilot, screen_type, tries: int = 40) -> bool:
    for _ in range(tries):
        await pilot.pause()
        if isinstance(app.screen, screen_type):
            await pilot.pause()  # let the modal's children mount
            return True
    return False


@pytest.mark.asyncio
async def test_tag_keypress_round_trip(tmp_path: Path):
    runs_dir = tmp_path / ".tine_runs"
    runs_dir.mkdir()
    _completed_run("ui").save(runs_dir / "ui.tine")
    app = OpentineTUI(runs_dir=runs_dir)

    async with app.run_test() as pilot:
        await pilot.pause()
        app.select_run(next(r.key for r in app._records if r.run_id == "ui"))
        await pilot.press("t")
        assert await _settle(app, pilot, TagEditorModal)
        app.screen.query_one("#add", Input).value = "smoke"
        await pilot.click("#ok")
        await pilot.pause()
        assert "smoke" in Run.load(runs_dir / "ui.tine").tags


@pytest.mark.asyncio
async def test_migrate_keypress_round_trip(tmp_path: Path):
    runs_dir = tmp_path / ".tine_runs"
    runs_dir.mkdir()
    _write_v1(runs_dir / "v1.tine")
    app = OpentineTUI(runs_dir=runs_dir)

    async with app.run_test() as pilot:
        await pilot.pause()
        app.select_run(app._records[0].key)
        await pilot.press("m")
        assert await _settle(app, pilot, MigrateModal)
        await pilot.click("#ok")
        await pilot.pause()
        assert app.repository.inspect_path(runs_dir / "v1.tine").on_disk_version == 2


@pytest.mark.asyncio
async def test_sign_keypress_opens_modal(tmp_path: Path):
    runs_dir = tmp_path / ".tine_runs"
    runs_dir.mkdir()
    _completed_run("ui").save(runs_dir / "ui.tine")
    app = OpentineTUI(runs_dir=runs_dir)

    async with app.run_test() as pilot:
        await pilot.pause()
        app.select_run(next(r.key for r in app._records if r.run_id == "ui"))
        await pilot.press("s")
        assert await _settle(app, pilot, SignModal)


# --- regression coverage for the adversarial review findings -----------------


def test_set_tags_preserves_signature_and_integrity(tmp_path: Path):
    """Tagging a signed run must NOT drop its signature (tags are outside both
    the digest and the signed view)."""
    repo = RunRepository(tmp_path)
    service = RunActionService(repo)
    path = tmp_path / "signed.tine"
    _completed_run("a").save(path, sign_key=HMAC_KEY, key_id="k1")
    assert repo.inspect_path(path).has_signature

    result = service.set_tags(repo.inspect_path(path), ["important"], [])

    assert result.ok
    assert Run.verify_signature(path, hmac_key=HMAC_KEY).ok  # still verifies
    assert Run.verify_integrity(path).ok
    assert "important" in Run.load(path).tags


def test_set_tags_on_v1_does_not_migrate(tmp_path: Path):
    """A tag edit must not silently perform the one-way v1->v2 upgrade."""
    repo = RunRepository(tmp_path)
    service = RunActionService(repo)
    dest = _write_v1(tmp_path / "v1.tine")
    assert repo.inspect_path(dest).on_disk_version == 1

    result = service.set_tags(repo.inspect_path(dest), ["x"], [])

    assert result.ok
    assert repo.inspect_path(dest).on_disk_version == 1  # still v1 on disk
    assert "x" in Run.load(dest).tags


def test_future_version_is_not_corrupt(tmp_path: Path):
    repo = RunRepository(tmp_path)
    path = tmp_path / "future.tine"
    _completed_run("a").save(path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["format_version"] = 99
    path.write_text(json.dumps(raw), encoding="utf-8")

    record = repo.inspect_path(path)
    assert record.is_future
    assert not record.is_corrupt
    assert record.run is None  # a newer-format file is not loadable


def test_migrate_refuses_future_version(tmp_path: Path):
    repo = RunRepository(tmp_path)
    service = RunActionService(repo)
    path = tmp_path / "future.tine"
    _completed_run("a").save(path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["format_version"] = 99
    path.write_text(json.dumps(raw), encoding="utf-8")
    result = service.migrate(repo.inspect_path(path))
    assert not result.ok
    assert "newer opentine" in result.message


@pytest.mark.asyncio
async def test_search_query_with_bracket_does_not_crash(tmp_path: Path):
    """A free-text query containing '[' must not raise MarkupError in the panel
    title (which would crash the app and re-crash on every 5s refresh)."""
    runs_dir = tmp_path / ".tine_runs"
    runs_dir.mkdir()
    _completed_run("a").save(runs_dir / "a.tine")
    app = OpentineTUI(runs_dir=runs_dir)

    async with app.run_test() as pilot:
        await pilot.pause()
        # an invalid query surfaces a bracketed DSL error into the panel title
        app._query = "cost:[notanumber]"
        app._refresh()  # raised MarkupError before the fix
        await pilot.pause()
        app._refresh()  # the 5s auto-refresh would re-run this; must also survive
        await pilot.pause()
        assert app.query_one(RunList).row_count == 1  # bad query -> records unfiltered


@pytest.mark.asyncio
async def test_search_cancel_keeps_active_filter(tmp_path: Path):
    from opentine_tui.dialogs import TextInputModal

    runs_dir = tmp_path / ".tine_runs"
    runs_dir.mkdir()
    _completed_run("a", tags=["bug"]).save(runs_dir / "a.tine")
    _completed_run("b").save(runs_dir / "b.tine")
    app = OpentineTUI(runs_dir=runs_dir)

    async with app.run_test() as pilot:
        await pilot.pause()
        app._query = "tag:bug"
        app._refresh()
        assert app.query_one(RunList).row_count == 1
        await pilot.press("slash")
        assert await _settle(app, pilot, TextInputModal)
        await pilot.press("escape")  # cancel, not clear
        await pilot.pause()
        assert app._query == "tag:bug"
        assert app.query_one(RunList).row_count == 1
