"""QOL dialog behaviour: keyboard submit, confirmation, validation, pickers."""

from __future__ import annotations

from typing import Any

import pytest
from textual.app import App
from textual.widgets import Button, Input, OptionList, Select, Static

from opentine_tui import dialogs
from opentine_tui.dialogs import (
    BudgetModal,
    ConfirmationModal,
    CopyMenuModal,
    GotoRunModal,
    SearchModal,
    SignModal,
    TagEditorModal,
    TextInputModal,
    VerifyKeyModal,
)

_MISSING = object()


class Host(App):
    def __init__(self) -> None:
        super().__init__()
        self.result: Any = _MISSING

    def open(self, screen) -> None:
        self.push_screen(screen, callback=self._done)

    def _done(self, value: Any) -> None:
        self.result = value


async def _open(pilot, screen) -> None:
    pilot.app.open(screen)
    await pilot.pause()


def _text(screen, selector: str) -> str:
    return str(screen.query_one(selector, Static).render())


@pytest.mark.asyncio
async def test_enter_in_an_input_submits_the_dialog():
    app = Host()
    async with app.run_test() as pilot:
        modal = TagEditorModal([])
        await _open(pilot, modal)
        modal.query_one("#add", Input).focus()
        await pilot.press("s", "m", "o", "k", "e", "enter")
        await pilot.pause()
        assert app.result == {"add": ["smoke"], "remove": []}


@pytest.mark.asyncio
async def test_ctrl_s_submits_and_escape_cancels():
    app = Host()
    async with app.run_test() as pilot:
        modal = VerifyKeyModal()
        await _open(pilot, modal)
        modal.query_one("#key_env", Input).value = "KEY"
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert app.result.key_env == "KEY"
        assert app.result.trust_embedded is False

        app.result = _MISSING
        await _open(pilot, BudgetModal())
        await pilot.press("escape")
        await pilot.pause()
        assert app.result is None


@pytest.mark.asyncio
async def test_text_input_modal_still_submits_on_enter():
    app = Host()
    async with app.run_test() as pilot:
        await _open(pilot, TextInputModal("t", value=" x "))
        await pilot.press("enter")
        await pilot.pause()
        assert app.result == "x"


@pytest.mark.asyncio
async def test_verify_trust_is_a_switch():
    app = Host()
    async with app.run_test() as pilot:
        modal = VerifyKeyModal()
        await _open(pilot, modal)
        modal.query_one("#trust").value = True
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert app.result.trust_embedded is True


@pytest.mark.asyncio
@pytest.mark.parametrize("key,expected", [("y", True), ("n", False), ("escape", False)])
async def test_confirmation_keys_and_safe_focus(key, expected):
    app = Host()
    async with app.run_test() as pilot:
        modal = ConfirmationModal("Overwrite?", "[bold]x", confirm_label="Overwrite [key]")
        await _open(pilot, modal)
        assert app.focused is not None and app.focused.id == "cancel"
        assert "Overwrite [key]" in str(modal.query_one("#confirm", Button).label)
        await pilot.press(key)
        await pilot.pause()
        assert app.result is expected


@pytest.mark.asyncio
async def test_confirmation_enter_on_default_focus_cancels():
    app = Host()
    async with app.run_test() as pilot:
        await _open(pilot, ConfirmationModal("t", "m"))
        await pilot.press("enter")
        await pilot.pause()
        assert app.result is False


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["abc", "-1"])
async def test_budget_inline_validation_disables_ok(bad):
    app = Host()
    async with app.run_test() as pilot:
        modal = BudgetModal()
        await _open(pilot, modal)
        field = modal.query_one("#max_cost", Input)
        field.value = bad
        await pilot.pause()
        assert modal.query_one("#ok", Button).disabled
        assert "Max cost" in _text(modal, "#dialog-error")
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert app.result is _MISSING  # still open

        field.value = "2.5"
        await pilot.pause()
        assert not modal.query_one("#ok", Button).disabled
        assert _text(modal, "#dialog-error") == ""
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert app.result.max_cost == 2.5


@pytest.mark.asyncio
async def test_budget_integer_fields_reject_fractions():
    app = Host()
    async with app.run_test() as pilot:
        modal = BudgetModal()
        await _open(pilot, modal)
        modal.query_one("#max_steps", Input).value = "1.5"
        await pilot.pause()
        assert modal.query_one("#ok", Button).disabled


@pytest.mark.asyncio
async def test_sign_modal_hides_fields_for_the_other_algorithm(monkeypatch):
    monkeypatch.setattr(dialogs, "HAS_ED25519", True)
    app = Host()
    async with app.run_test() as pilot:
        modal = SignModal()
        await _open(pilot, modal)
        assert modal.query_one("#key_env").display
        assert not modal.query_one("#ed_key_file").display
        modal.query_one("#algorithm", Select).value = "ed25519"
        await pilot.pause()
        assert not modal.query_one("#key_env").display
        assert modal.query_one("#ed_key_file").display
        modal.query_one("#key_env", Input).value = "HIDDEN"
        modal.query_one("#ed_key_file", Input).value = "k.pem"
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert app.result.algorithm == "ed25519"
        assert app.result.ed25519_key_file == "k.pem"
        assert app.result.key_env is None


@pytest.mark.asyncio
async def test_sign_modal_without_ed25519_offers_only_hmac(monkeypatch):
    monkeypatch.setattr(dialogs, "HAS_ED25519", False)
    app = Host()
    async with app.run_test() as pilot:
        modal = SignModal()
        await _open(pilot, modal)
        values = [value for _, value in modal.query_one("#algorithm", Select)._options]
        assert "ed25519" not in values


@pytest.mark.asyncio
async def test_sign_modal_rejects_two_or_zero_hmac_sources():
    app = Host()
    async with app.run_test() as pilot:
        modal = SignModal()
        await _open(pilot, modal)
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert app.result is _MISSING
        assert "needs a key" in _text(modal, "#dialog-error")
        modal.query_one("#key_env", Input).value = "A"
        modal.query_one("#key_file", Input).value = "b"
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert app.result is _MISSING
        assert "not both" in _text(modal, "#dialog-error")
        modal.query_one("#key_file", Input).value = ""
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert app.result.key_env == "A"


def _counter(query: str) -> int | str:
    if query.startswith("cost:") and not query[5:].lstrip("<>").replace(".", "").isdigit():
        return "bad cost [filter]"
    return len(query)


@pytest.mark.asyncio
async def test_search_shows_live_count_and_errors():
    app = Host()
    async with app.run_test() as pilot:
        modal = SearchModal(count_fn=_counter, total=9)
        modal.DEBOUNCE = 0.01
        await _open(pilot, modal)
        assert "tag:" in _text(modal, "#search-help")
        modal.query_one("#query", Input).value = "abc"
        await pilot.pause(0.1)
        assert _text(modal, "#search-count") == "3 of 9 runs match"
        modal.query_one("#query", Input).value = "cost:zz"
        await pilot.pause(0.1)
        assert "bad cost [filter]" in _text(modal, "#dialog-error")
        await pilot.press("enter")
        await pilot.pause()
        assert app.result is _MISSING  # stays open on an error
        modal.query_one("#query", Input).value = "cost:>1"
        await pilot.press("enter")
        await pilot.pause()
        assert app.result == "cost:>1"


@pytest.mark.asyncio
async def test_search_history_recall():
    app = Host()
    async with app.run_test() as pilot:
        modal = SearchModal(value="", history=["newest", "older", "newest"], count_fn=_counter)
        await _open(pilot, modal)
        field = modal.query_one("#query", Input)
        await pilot.press("up")
        assert field.value == "newest"
        await pilot.press("up")
        assert field.value == "older"
        await pilot.press("up")
        assert field.value == "older"
        await pilot.press("down", "down")
        assert field.value == ""


@pytest.mark.asyncio
async def test_goto_run_prefers_prefix_matches():
    candidates = [("k1", "zzabcd run-one"), ("k2", "abcd1234 run-two"), ("k3", "ffff [x]")]
    modal = GotoRunModal(candidates)
    assert [k for k, _ in modal.matches("abcd")] == ["k2", "k1"]
    app = Host()
    async with app.run_test() as pilot:
        await _open(pilot, modal)
        modal.query_one("#query", Input).value = "abcd"
        await pilot.pause()
        assert modal.query_one(OptionList).option_count == 2
        await pilot.press("enter")
        await pilot.pause()
        assert app.result == "k2"


@pytest.mark.asyncio
async def test_goto_run_arrow_then_enter_and_no_match():
    candidates = [("k1", "aaa1"), ("k2", "aaa2")]
    app = Host()
    async with app.run_test() as pilot:
        modal = GotoRunModal(candidates)
        await _open(pilot, modal)
        modal.query_one("#query", Input).value = "nomatch"
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert app.result is _MISSING
        assert "matches" in _text(modal, "#dialog-error")
        modal.query_one("#query", Input).value = "aaa"
        await pilot.pause()
        await pilot.press("down", "enter")
        await pilot.pause()
        assert app.result == "k2"


@pytest.mark.asyncio
async def test_copy_menu_returns_the_value():
    app = Host()
    async with app.run_test() as pilot:
        modal = CopyMenuModal([("run id", "run-[1]"), ("path", "/tmp/x.tine")])
        await _open(pilot, modal)
        await pilot.press("down", "enter")
        await pilot.pause()
        assert app.result == "/tmp/x.tine"
        app.result = _MISSING
        await _open(pilot, CopyMenuModal([("run id", "run-[1]")]))
        await pilot.press("enter")
        await pilot.pause()
        assert app.result == "run-[1]"
        await _open(pilot, CopyMenuModal([("run id", "r")]))
        await pilot.press("escape")
        await pilot.pause()
        assert app.result is None
