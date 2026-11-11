"""opentine 0.35-0.50: v5 files, encrypted keys, recorded-prompt trust, assurance."""

from __future__ import annotations

import stat
import sys
from pathlib import Path

import pytest
from opentine.core import Run, RunStatus, StepKind

from opentine_tui.actions import (
    HarnessOptions,
    RunActionService,
    SignOptions,
    verify_report_text,
)
from opentine_tui.cli_bridge import cli_error
from opentine_tui.repository import RunRepository

PASS = "correct horse battery"


def _run(run_id: str = "r50") -> Run:
    run = Run(id=run_id, model_info="m", user_prompt="recorded prompt from the file")
    first = run.add_step(StepKind.think, {"text": "a"})
    run.add_step(StepKind.done, {"text": "z"}, parent_id=first.id)
    run.status = RunStatus.completed
    return run


@pytest.fixture
def service(tmp_path: Path) -> RunActionService:
    runs = tmp_path / "runs"
    runs.mkdir()
    return RunActionService(RunRepository(runs))


@pytest.fixture(autouse=True)
def _no_passphrase_env(monkeypatch):
    monkeypatch.delenv("OPENTINE_KEY_PASSPHRASE", raising=False)


# -- .tine v5 --------------------------------------------------------------


def test_a_v5_file_is_labelled_v5_and_counts_as_current(service):
    path = service.repository.save(_run())
    record = service.repository.inspect_path(path)
    assert record.on_disk_version == 5 and record.version_label == "v5"
    assert record.is_v4 and record.is_v5
    assert not record.can_upgrade and not record.is_corrupt
    assert record.integrity.ok


def test_the_legacy_writer_still_reads_as_v4(service, monkeypatch):
    monkeypatch.setenv("OPENTINE_FORMAT", "4")
    record = service.repository.inspect_path(service.repository.save(_run()))
    assert record.on_disk_version == 4 and not record.is_v5


def test_tagging_keeps_a_v5_file_v5(service):
    path = service.repository.save(_run())
    result = service.set_tags(service.repository.inspect_path(path), ["prod"], [])
    assert result.ok, result.message
    assert service.repository.inspect_path(path).on_disk_version == 5


# -- encrypted keys ----------------------------------------------------------


def test_keygen_writes_an_encrypted_key_and_signing_needs_its_passphrase(service, tmp_path):
    key = tmp_path / "ed"
    made = service.keygen(key, passphrase=PASS)
    assert made.ok, made.message
    assert key.read_bytes().startswith(b"-----BEGIN ENCRYPTED PRIVATE KEY-----")
    if sys.platform != "win32":
        assert stat.S_IMODE(key.stat().st_mode) == 0o600

    record = service.repository.inspect_path(service.repository.save(_run()))
    no_pass = service.sign(record, SignOptions(algorithm="ed25519", ed25519_key_file=str(key)))
    assert not no_pass.ok and "passphrase" in no_pass.message
    wrong = service.sign(
        record,
        SignOptions(algorithm="ed25519", ed25519_key_file=str(key), passphrase="nope nope"),
    )
    assert not wrong.ok and "wrong passphrase" in wrong.message
    right = service.sign(
        record, SignOptions(algorithm="ed25519", ed25519_key_file=str(key), passphrase=PASS)
    )
    assert right.ok, right.message
    assert "plaintext" not in right.message


def test_signing_never_prompts_on_the_terminal(service, tmp_path, monkeypatch):
    """opentine falls back to getpass on a tty; inside the TUI that would hang."""
    import getpass

    monkeypatch.setattr(getpass, "getpass", lambda *a, **k: pytest.fail("prompted on the terminal"))
    key = tmp_path / "ed"
    assert service.keygen(key, passphrase=PASS).ok
    record = service.repository.inspect_path(service.repository.save(_run()))
    result = service.sign(record, SignOptions(algorithm="ed25519", ed25519_key_file=str(key)))
    assert not result.ok


def test_keygen_refuses_without_a_passphrase_unless_plaintext_is_asked_for(service, tmp_path):
    assert not service.keygen(tmp_path / "a").ok
    assert not service.keygen(tmp_path / "b", passphrase="short").ok
    plain = service.keygen(tmp_path / "c", unencrypted=True)
    assert plain.ok and "PLAINTEXT" in plain.message


def test_a_plaintext_key_signs_with_a_warning_and_can_be_encrypted(service, tmp_path):
    key = tmp_path / "old"
    assert service.keygen(key, unencrypted=True).ok
    record = service.repository.inspect_path(service.repository.save(_run()))
    signed = service.sign(record, SignOptions(algorithm="ed25519", ed25519_key_file=str(key)))
    assert signed.ok, signed.message
    assert "plaintext" in signed.message

    encrypted = service.encrypt_key(key, PASS)
    assert encrypted.ok, encrypted.message
    assert key.read_bytes().startswith(b"-----BEGIN ENCRYPTED PRIVATE KEY-----")
    assert not service.encrypt_key(key, PASS).ok  # already encrypted


# -- recorded prompt trust (0.35) ---------------------------------------------


def test_replay_never_sends_the_recorded_prompt_unasked(service, monkeypatch):
    from opentine_tui import actions

    sent: list[str] = []

    class Wrapped:
        run = None

        def __init__(self, harness, run=None):
            pass

        def run_sync(self, prompt, context=None, save_path=None):
            sent.append(prompt)
            return _run("replayed")

    monkeypatch.setattr(actions, "OpentineHarness", Wrapped)
    monkeypatch.setattr(service, "_harness_from_options", lambda options: object())
    run = _run()

    refused = service.replay_harness(run, None, HarnessOptions(harness="codex"))
    assert not refused.ok and sent == []

    trusted = service.replay_harness(
        run, None, HarnessOptions(harness="codex", trust_recorded_prompt=True)
    )
    assert trusted.ok, trusted.message
    assert sent == ["recorded prompt from the file"]


# -- report and error rendering ------------------------------------------------


def test_the_verify_report_shows_the_assurance_tier(service):
    record = service.repository.inspect_path(service.repository.save(_run()))
    result = service.verify_report(record)
    assert result.ok, result.message
    assert "assurance: T0 verified" in result.message
    text = verify_report_text(
        {"verdict": "pass", "assurance": {"verified": "T2", "claimed": "T3"}, "checks": []}
    )
    assert "T2 verified (claims T3)" in text and "proxy" in text


def test_cli_error_envelopes_are_read():
    assert cli_error({"error": {"code": "x", "message": "boom\x1b[2J"}}) == "boom[2J"
    assert cli_error({"command": "verify", "ok": True}) is None
    assert cli_error(None) is None


@pytest.mark.asyncio
async def test_keygen_dialog_masks_and_checks_the_passphrase(tmp_path):
    from textual.app import App
    from textual.widgets import Input

    from opentine_tui.dialogs import KeygenModal

    results: list = []

    class Host(App):
        def on_mount(self) -> None:
            self.push_screen(KeygenModal(default_path=str(tmp_path / "k")), results.append)

    async with Host().run_test() as pilot:
        await pilot.pause()
        screen = pilot.app.screen
        assert screen.query_one("#passphrase", Input).password
        screen.query_one("#passphrase", Input).value = PASS
        screen.query_one("#repeat", Input).value = PASS + "x"
        screen.query_one("#ok").press()
        await pilot.pause()
        assert results == []  # mismatch keeps the dialog open
        screen.query_one("#repeat", Input).value = PASS
        screen.query_one("#ok").press()
        await pilot.pause()
    assert results and results[0]["passphrase"] == PASS and not results[0]["unencrypted"]
