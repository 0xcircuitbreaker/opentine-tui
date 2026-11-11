"""Modal dialogs used by the Textual application."""

from __future__ import annotations

import shlex
from collections.abc import Callable, Sequence
from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.validation import Integer, Number
from textual.widgets import (
    Button,
    Input,
    Label,
    OptionList,
    Select,
    Static,
    Switch,
    TextArea,
)
from textual.widgets.option_list import Option

from opentine_tui.actions import (
    HARNESS_FACTORIES,
    BudgetOptions,
    HarnessOptions,
    SignOptions,
    VerifyKeyOptions,
)
from opentine_tui.formatting import escape_markup
from opentine_tui.interop import (
    DEFAULT_REF,
    IMPORT_FORMATS,
    ExportOptions,
    TraceImportOptions,
)
from opentine_tui.repo_actions import EvaluateOptions, ImportOptions, PromoteOptions

try:
    from opentine.signing import HAS_ED25519
except Exception:  # pragma: no cover - defensive
    HAS_ED25519 = False


def _parse_optional_float(text: str) -> float | None:
    text = text.strip()
    return float(text) if text else None


def _parse_optional_int(text: str) -> int | None:
    text = text.strip()
    return int(text) if text else None


def _split_csv(text: str) -> list[str]:
    return [item.strip() for item in text.split(",") if item.strip()]


_DIALOG_CSS = """
%(name)s {
    align: center middle;
}
#dialog {
    width: 84;
    max-width: 95%%;
    height: auto;
    max-height: 90%%;
    border: thick #FF6900;
    background: $surface;
    padding: 0 2;
}
#dialog Input {
    margin-bottom: 0;
}
#dialog .field-label {
    color: $text-muted;
}
#dialog-error {
    color: red;
    min-height: 1;
}
#dialog-actions {
    height: auto;
    align-horizontal: right;
    margin-top: 1;
}
#dialog .toggle {
    height: auto;
}
#dialog .toggle Label {
    padding: 1 1 0 1;
}
"""


class _Dialog(ModalScreen):
    """Shared plumbing for modals: Enter / Ctrl+S submit, Escape cancels.

    Submitting presses the ``#ok`` button, so a disabled OK (invalid fields)
    also blocks the keyboard paths. Inputs with validators are re-checked on
    every change: the first failure goes to ``#dialog-error`` and ``#ok`` is
    disabled until all fields are valid (blank always counts as valid).
    """

    BINDINGS = [
        Binding("ctrl+s", "submit", "Submit", priority=True),
        Binding("escape", "cancel", "Cancel"),
    ]
    ERROR_ID = "dialog-error"

    def _value(self, selector: str) -> str:
        return self.query_one(selector, Input).value.strip()

    def _switch(self, selector: str) -> bool:
        return self.query_one(selector, Switch).value

    def _error(self, message: str) -> None:
        for widget in self.query(f"#{self.ERROR_ID}"):
            if isinstance(widget, Static):
                widget.update(escape_markup(message))

    def action_submit(self) -> None:
        buttons = self.query("#ok")
        if not buttons:
            return
        ok = buttons.first(Button)
        if not ok.disabled:
            ok.press()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.action_submit()

    def on_input_changed(self, event: Input.Changed) -> None:
        self._revalidate()

    def _validation_failures(self) -> list[str]:
        failures: list[str] = []
        for field in self.query(Input):
            if not field.validators:
                continue
            value = field.value.strip()
            if not value:
                field.remove_class("-invalid")
                continue
            result = field.validate(value)
            if result is not None and not result.is_valid:
                name = getattr(field, "field_label", None) or field.name or field.id or "field"
                reason = "; ".join(result.failure_descriptions) or "invalid value"
                failures.append(f"{name}: {reason}")
        return failures

    def _revalidate(self) -> bool:
        if not any(field.validators for field in self.query(Input)):
            return True
        failures = self._validation_failures()
        self._error(failures[0] if failures else "")
        for ok in self.query("#ok"):
            ok.disabled = bool(failures)
        return not failures


def _toggle(label: str, switch_id: str, value: bool = False) -> ComposeResult:
    with Horizontal(classes="toggle"):
        yield Switch(value=value, id=switch_id)
        yield Label(escape_markup(label))


def _field(label: str, field: Input) -> ComposeResult:
    """A label above its input, so a prefilled value never hides its meaning."""
    field.field_label = label  # type: ignore[attr-defined]
    yield Label(escape_markup(label), classes="field-label")
    yield field


class ConfirmationModal(ModalScreen[bool]):
    """Yes/no prompt. Focus starts on Cancel; ``y`` confirms, ``n``/Escape cancel."""

    DEFAULT_CSS = """
    ConfirmationModal {
        align: center middle;
    }
    #confirm-dialog {
        width: 72;
        max-width: 90%;
        height: auto;
        border: thick #FF6900;
        background: $surface;
        padding: 1 2;
    }
    #confirm-actions {
        height: auto;
        align-horizontal: right;
        margin-top: 1;
    }
    """
    AUTO_FOCUS = "#cancel"
    BINDINGS = [
        Binding("y", "confirm", "Confirm"),
        Binding("n", "cancel", "Cancel"),
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(self, title: str, message: str, confirm_label: str = "Confirm") -> None:
        super().__init__()
        self.title_text = title
        self.message = message
        self.confirm_label = confirm_label or "Confirm"

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-dialog"):
            yield Label(f"[bold #FF6900]{escape_markup(self.title_text)}[/]")
            yield Static(escape_markup(self.message))
            yield Static("[dim]y confirm · n / Esc cancel[/]", id="confirm-hint")
            with Horizontal(id="confirm-actions"):
                yield Button("Cancel", id="cancel")
                yield Button(escape_markup(self.confirm_label), id="confirm", variant="error")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirm")

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


class TextInputModal(_Dialog):
    DEFAULT_CSS = """
    TextInputModal {
        align: center middle;
    }
    #input-dialog {
        width: 78;
        max-width: 90%;
        height: auto;
        border: thick #FF6900;
        background: $surface;
        padding: 1 2;
    }
    #input-actions {
        height: auto;
        align-horizontal: right;
        margin-top: 1;
    }
    """

    def __init__(self, title: str, placeholder: str = "", value: str = "") -> None:
        super().__init__()
        self.title_text = title
        self.placeholder = placeholder
        self.value = value

    def compose(self) -> ComposeResult:
        with Vertical(id="input-dialog"):
            yield Label(f"[bold #FF6900]{escape_markup(self.title_text)}[/]")
            yield Input(value=self.value, placeholder=self.placeholder, id="value")
            with Horizontal(id="input-actions"):
                yield Button("Cancel", id="cancel")
                yield Button("OK", id="ok", variant="primary")

    # Distinguish "submitted" (a string, possibly empty -> clears a filter) from
    # "cancelled" (None -> no-op). Callers that reject empties use `if not value`.
    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "ok":
            self.dismiss(self.query_one("#value", Input).value.strip())
        else:
            self.dismiss(None)


SEARCH_HELP = (
    "tag:NAME  model:NAME  status:STATE  cost:>N  cost:<N  "
    "after:YYYY-MM-DD  before:YYYY-MM-DD  free text"
)


class SearchModal(_Dialog):
    """Live run search: shows the match count (or a parse error) as you type.

    ``count_fn(query)`` returns the number of matches, or an error string.
    ``history`` is most-recent-first; Up/Down walk it. Dismisses the query
    (possibly empty, meaning "clear the filter") or None on cancel. A query
    that ``count_fn`` rejects keeps the modal open.
    """

    DEFAULT_CSS = (
        _DIALOG_CSS % {"name": "SearchModal"}
        + """
    #search-count { min-height: 1; }
    """
    )
    BINDINGS = [
        Binding("up", "history(1)", "Older", show=False, priority=True),
        Binding("down", "history(-1)", "Newer", show=False, priority=True),
    ]
    DEBOUNCE = 0.15

    def __init__(
        self,
        value: str = "",
        history: Sequence[str] = (),
        count_fn: Callable[[str], int | str] | None = None,
        total: int | None = None,
    ) -> None:
        super().__init__()
        self.initial = value
        self.history = list(dict.fromkeys(h for h in history if h))[:20]
        self.count_fn = count_fn
        self.total = total
        self._history_index = -1
        self._timer: Any = None
        self._last_error: str | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label("[bold #FF6900]Search runs[/]")
            yield Input(value=self.initial, placeholder="query (blank clears)", id="query")
            yield Static(f"[dim]{escape_markup(SEARCH_HELP)}[/]", id="search-help")
            yield Static("", id="search-count")
            yield Static("", id="dialog-error")
            with Horizontal(id="dialog-actions"):
                yield Button("Cancel", id="cancel")
                yield Button("Search", id="ok", variant="primary")

    def on_mount(self) -> None:
        self.refresh_count()

    def on_input_changed(self, event: Input.Changed) -> None:
        if self._timer is not None:
            self._timer.stop()
        self._timer = self.set_timer(self.DEBOUNCE, self.refresh_count)

    def refresh_count(self) -> None:
        self._timer = None
        self._last_error = None
        if self.count_fn is None:
            return
        query = self.query_one("#query", Input).value.strip()
        try:
            result = self.count_fn(query)
        except Exception as exc:
            result = str(exc) or type(exc).__name__
        count = self.query_one("#search-count", Static)
        if isinstance(result, str):
            self._last_error = result
            count.update("")
            self._error(result)
        else:
            suffix = f" of {self.total}" if self.total is not None else ""
            noun = "run matches" if result == 1 and not suffix else "runs match"
            count.update(f"{int(result)}{suffix} {noun}")
            self._error("")

    def action_history(self, step: int) -> None:
        if not self.history:
            return
        index = max(-1, min(len(self.history) - 1, self._history_index + step))
        self._history_index = index
        field = self.query_one("#query", Input)
        field.value = self.initial if index < 0 else self.history[index]
        field.cursor_position = len(field.value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != "ok":
            self.dismiss(None)
            return
        self.refresh_count()
        if self._last_error is not None:
            return
        self.dismiss(self._value("#query"))


class _PickerModal(_Dialog):
    DEFAULT_CSS = """
    #dialog OptionList { height: auto; max-height: 14; }
    """

    def _pick(self) -> None:
        options = self.query_one(OptionList)
        index = options.highlighted
        if index is None and options.option_count:
            index = 0
        if index is None:
            self._error("Nothing matches.")
            return
        self._choose(options.get_option_at_index(index).id)

    def _choose(self, option_id: str | None) -> None:
        raise NotImplementedError

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        self._choose(event.option.id)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "ok":
            self._pick()
        else:
            self.dismiss(None)


class GotoRunModal(_PickerModal):
    """Jump to a run by id. ``candidates`` are ``(key, label)`` pairs; the
    label holds the ids to match. Prefix matches come before substring matches,
    at most ``LIMIT`` shown. Dismisses the chosen key or None."""

    DEFAULT_CSS = _DIALOG_CSS % {"name": "GotoRunModal"} + _PickerModal.DEFAULT_CSS
    BINDINGS = [
        Binding("down", "move(1)", "Next", show=False, priority=True),
        Binding("up", "move(-1)", "Previous", show=False, priority=True),
    ]
    LIMIT = 10

    def __init__(self, candidates: Sequence[tuple[str, str]], value: str = "") -> None:
        super().__init__()
        self.candidates = [(str(k), str(lbl)) for k, lbl in candidates]
        self.initial = value
        self._shown: list[str] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label("[bold #FF6900]Go to run[/]")
            yield Input(value=self.initial, placeholder="run id or prefix", id="query")
            yield OptionList(id="matches")
            yield Static("", id="dialog-error")
            with Horizontal(id="dialog-actions"):
                yield Button("Cancel", id="cancel")
                yield Button("Go", id="ok", variant="primary")

    def on_mount(self) -> None:
        self._refilter()

    def matches(self, query: str) -> list[tuple[str, str]]:
        query = query.strip().lower()
        if not query:
            return self.candidates[: self.LIMIT]
        prefix: list[tuple[str, str]] = []
        inner: list[tuple[str, str]] = []
        for key, label in self.candidates:
            words = [key.lower(), *label.lower().split()]
            if any(word.startswith(query) for word in words):
                prefix.append((key, label))
            elif query in key.lower() or query in label.lower():
                inner.append((key, label))
        return (prefix + inner)[: self.LIMIT]

    def _refilter(self) -> None:
        options = self.query_one("#matches", OptionList)
        options.clear_options()
        found = self.matches(self.query_one("#query", Input).value)
        self._shown = [key for key, _ in found]
        options.add_options(
            [Option(escape_markup(label), id=str(i)) for i, (_, label) in enumerate(found)]
        )
        if found:
            options.highlighted = 0
            self._error("")
        else:
            self._error("No run matches.")

    def on_input_changed(self, event: Input.Changed) -> None:
        self._refilter()

    def action_move(self, step: int) -> None:
        options = self.query_one("#matches", OptionList)
        if not options.option_count:
            return
        current = options.highlighted if options.highlighted is not None else -step
        options.highlighted = max(0, min(options.option_count - 1, current + step))

    def _choose(self, option_id: str | None) -> None:
        if option_id is None:
            return
        self.dismiss(self._shown[int(option_id)])


class CopyMenuModal(_PickerModal):
    """Choose what to copy. ``choices`` are ``(label, value)``; dismisses the
    value or None."""

    DEFAULT_CSS = _DIALOG_CSS % {"name": "CopyMenuModal"} + _PickerModal.DEFAULT_CSS
    AUTO_FOCUS = "#choices"

    def __init__(self, choices: Sequence[tuple[str, str]], title: str = "Copy") -> None:
        super().__init__()
        self.choices = [(str(lbl), str(v)) for lbl, v in choices]
        self.title_text = title

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(f"[bold #FF6900]{escape_markup(self.title_text)}[/]")
            yield OptionList(
                *[
                    Option(f"{escape_markup(label)}  [dim]{escape_markup(value)}[/]", id=str(i))
                    for i, (label, value) in enumerate(self.choices)
                ],
                id="choices",
            )
            yield Static("", id="dialog-error")
            with Horizontal(id="dialog-actions"):
                yield Button("Cancel", id="cancel")
                yield Button("Copy", id="ok", variant="primary")

    def on_mount(self) -> None:
        if self.choices:
            self.query_one("#choices", OptionList).highlighted = 0

    def _choose(self, option_id: str | None) -> None:
        if option_id is None:
            return
        self.dismiss(self.choices[int(option_id)][1])


class HarnessOptionsModal(_Dialog):
    DEFAULT_CSS = """
    HarnessOptionsModal {
        align: center middle;
    }
    #harness-dialog {
        width: 92;
        max-width: 95%;
        height: auto;
        max-height: 90%;
        border: thick #FF6900;
        background: $surface;
        padding: 1 2;
    }
    #harness-dialog Input {
        margin-bottom: 1;
    }
    #harness-dialog .field-label {
        color: $text-muted;
    }
    #harness-error {
        color: red;
        min-height: 1;
    }
    #harness-actions {
        height: auto;
        align-horizontal: right;
        margin-top: 1;
    }
    """
    ERROR_ID = "harness-error"

    def __init__(
        self,
        *,
        title: str = "Harness options",
        default_prompt: str = "",
        default_cwd: str = "",
        recorded_prompt: str = "",
    ) -> None:
        super().__init__()
        self.title_text = title
        self.default_prompt = default_prompt
        self.default_cwd = default_cwd
        # The prompt stored in the artifact being replayed. Since opentine 0.35 a
        # replay never sends it on its own: it came from the file, not the user.
        self.recorded_prompt = recorded_prompt

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="harness-dialog"):
            yield Label(f"[bold #FF6900]{escape_markup(self.title_text)}[/]")
            # The harness list comes from the installed opentine, so a preset added
            # there (0.3.0 added grok and gemini) shows up without editing the TUI.
            choices = sorted(HARNESS_FACTORIES) or ["generic"]
            yield Select(
                [(name, name) for name in choices],
                value="codex" if "codex" in choices else choices[0],
                allow_blank=False,
                id="harness",
            )
            yield from _field(
                "Prompt", Input(value=self.default_prompt, placeholder="Prompt", id="prompt")
            )
            yield from _field(
                "Working directory", Input(value=self.default_cwd, placeholder="cwd", id="cwd")
            )
            yield from _field(
                "Command override",
                Input(placeholder='Command override, e.g. "codex exec"', id="command"),
            )
            yield from _field(
                "Extra args", Input(placeholder="Extra args, shell-quoted", id="extra")
            )
            yield from _field(
                "Env allowlist", Input(placeholder="Env allowlist, comma separated", id="env")
            )
            yield from _field(
                "Timeout (seconds)",
                Input(
                    placeholder="Timeout seconds (blank: opentine default)",
                    id="timeout",
                    validators=[Number(minimum=0, failure_description="must be a number >= 0")],
                    valid_empty=True,
                ),
            )
            with Horizontal(classes="toggle"):
                yield Switch(value=False, id="login")
                yield Label("Pass the login environment through to the harness")
            if self.recorded_prompt:
                preview = self.recorded_prompt.replace("\n", " ")
                if len(preview) > 160:
                    preview = preview[:159] + "…"
                yield Static(f"[dim]Recorded prompt:[/] {escape_markup(preview)}")
                yield from _toggle(
                    "Send the recorded prompt — I vouch for it (it came from the file)",
                    "trust_recorded",
                )
            yield Static("", id="harness-error")
            with Horizontal(id="harness-actions"):
                yield Button("Cancel", id="cancel")
                yield Button("OK", id="ok", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
            return
        options = self._parse_options()
        if options is not None:
            self.dismiss(options)

    def _input_value(self, selector: str) -> str:
        return self.query_one(selector, Input).value.strip()

    def _parse_options(self) -> HarnessOptions | None:
        try:
            extra_args = tuple(shlex.split(self._input_value("#extra")))
        except ValueError as exc:
            self.query_one("#harness-error", Static).update(escape_markup(str(exc)))
            return None

        login_env = self.query_one("#login", Switch).value
        env_allowlist = tuple(
            item.strip() for item in self._input_value("#env").split(",") if item.strip()
        )
        timeout_text = self._input_value("#timeout")
        try:
            timeout = float(timeout_text) if timeout_text else None
        except ValueError:
            self.query_one("#harness-error", Static).update("Timeout must be a number of seconds.")
            return None
        if timeout is not None and not timeout > 0:
            self.query_one("#harness-error", Static).update("Timeout must be positive.")
            return None
        trust = bool(self.recorded_prompt) and self.query_one("#trust_recorded", Switch).value
        if trust and self._input_value("#prompt"):
            self.query_one("#harness-error", Static).update(
                "Type a prompt or send the recorded one, not both."
            )
            return None
        data: dict[str, Any] = {
            "trust_recorded_prompt": trust,
            "timeout_seconds": timeout,
            "harness": str(self.query_one("#harness", Select).value or "codex"),
            "prompt": self._input_value("#prompt"),
            "cwd": self._input_value("#cwd") or None,
            "command_override": self._input_value("#command") or None,
            "extra_args": extra_args,
            "login_env": login_env,
            "env_allowlist": env_allowlist,
        }
        return HarnessOptions(**data)


class TagEditorModal(_Dialog):
    DEFAULT_CSS = _DIALOG_CSS % {"name": "TagEditorModal"}

    def __init__(self, current_tags: list[str]) -> None:
        super().__init__()
        self.current_tags = list(current_tags)

    def compose(self) -> ComposeResult:
        current = ", ".join(self.current_tags) or "(none)"
        with VerticalScroll(id="dialog"):
            yield Label("[bold #FF6900]Edit tags[/]")
            yield Static(f"Current: {escape_markup(current)}")
            yield Static("[dim]Tags are lower-cased, de-duped, and live outside the digest.[/]")
            yield from _field("Add", Input(placeholder="Add (comma separated)", id="add"))
            yield from _field("Remove", Input(placeholder="Remove (comma separated)", id="remove"))
            with Horizontal(id="dialog-actions"):
                yield Button("Cancel", id="cancel")
                yield Button("Apply", id="ok", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != "ok":
            self.dismiss(None)
            return
        add = _split_csv(self.query_one("#add", Input).value)
        remove = _split_csv(self.query_one("#remove", Input).value)
        if not add and not remove:
            self.dismiss(None)
            return
        self.dismiss({"add": add, "remove": remove})


def _number(field_id: str, value: str, placeholder: str, *, integer: bool = False) -> Input:
    validator = (
        Integer(minimum=1, failure_description="must be a whole number >= 1")
        if integer
        else Number(minimum=0, failure_description="must be a number >= 0")
    )
    return Input(
        value=value, placeholder=placeholder, id=field_id, validators=[validator], valid_empty=True
    )


class BudgetModal(_Dialog):
    DEFAULT_CSS = _DIALOG_CSS % {"name": "BudgetModal"}

    def __init__(self, current: Any | None = None) -> None:
        super().__init__()
        self.current = current

    def _initial(self, attr: str) -> str:
        value = getattr(self.current, attr, None) if self.current is not None else None
        return "" if value is None else str(value)

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="dialog"):
            yield Label("[bold #FF6900]Set budget[/]")
            yield Static(
                "[yellow]Writes manifest.budget (inside the digest) — rewrites the "
                "digest and invalidates any signature.[/]"
            )
            yield from _field(
                "Max cost ($)", _number("max_cost", self._initial("max_cost"), "max cost $")
            )
            yield from _field(
                "Max tokens",
                _number("max_usage", self._initial("max_usage"), "max tokens", integer=True),
            )
            yield from _field(
                "Max steps",
                _number("max_steps", self._initial("max_steps"), "max steps", integer=True),
            )
            yield from _field(
                "Max duration (s)",
                _number("max_duration", self._initial("max_duration"), "max seconds"),
            )
            yield Label("On breach", classes="field-label")
            yield Select(
                [("stop the run", "stop"), ("raise BudgetExceeded", "raise")],
                value=(getattr(self.current, "on_breach", "stop") if self.current else "stop"),
                allow_blank=False,
                id="on_breach",
            )
            yield from _toggle(
                "Strict cost — an unpriced step counts as a breach",
                "strict_cost",
                bool(getattr(self.current, "strict_cost", False)),
            )
            yield Static("", id="dialog-error")
            with Horizontal(id="dialog-actions"):
                yield Button("Cancel", id="cancel")
                yield Button("Save", id="ok", variant="primary")

    def on_mount(self) -> None:
        self._revalidate()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != "ok":
            self.dismiss(None)
            return
        if not self._revalidate():
            return
        try:
            options = BudgetOptions(
                max_cost=_parse_optional_float(self.query_one("#max_cost", Input).value),
                max_usage=_parse_optional_int(self.query_one("#max_usage", Input).value),
                max_steps=_parse_optional_int(self.query_one("#max_steps", Input).value),
                max_duration=_parse_optional_float(self.query_one("#max_duration", Input).value),
                on_breach=str(self.query_one("#on_breach", Select).value or "stop"),
                strict_cost=self.query_one("#strict_cost", Switch).value,
            )
        except ValueError as exc:
            self._error(f"Invalid number: {exc}")
            return
        self.dismiss(options)


_HMAC_FIELDS = ("#key_env", "#key_file")
_ED25519_FIELDS = ("#ed_key_file", "#passphrase")


class SignModal(_Dialog):
    DEFAULT_CSS = _DIALOG_CSS % {"name": "SignModal"}

    def compose(self) -> ComposeResult:
        ed = "available" if HAS_ED25519 else "unavailable — pip install opentine[crypto]"
        algorithms = [("HMAC-SHA256", "hmac-sha256")]
        if HAS_ED25519:
            algorithms.append(("Ed25519", "ed25519"))
        with VerticalScroll(id="dialog"):
            yield Label("[bold #FF6900]Sign artifact[/]")
            yield Static(f"[dim]ed25519: {ed}. HMAC keys must be >= 16 bytes.[/]")
            yield Select(algorithms, value="hmac-sha256", allow_blank=False, id="algorithm")
            yield Label("HMAC key from env var", classes="field-label hmac")
            yield Input(placeholder="env var name", id="key_env", name="HMAC key env var")
            yield Label("HMAC key from file", classes="field-label hmac")
            yield Input(placeholder="path", id="key_file", name="HMAC key file")
            yield Label("Ed25519 private key file", classes="field-label ed25519")
            yield Input(placeholder="path", id="ed_key_file", name="Ed25519 key file")
            yield Label("Key passphrase", classes="field-label ed25519")
            yield Input(
                placeholder="for an encrypted key (blank: $OPENTINE_KEY_PASSPHRASE)",
                password=True,
                id="passphrase",
                name="Key passphrase",
            )
            yield from _field("Key id", Input(placeholder="optional", id="key_id"))
            yield from _field(
                "Signer label", Input(placeholder="optional, display only", id="signer")
            )
            yield from _field(
                "Save as", Input(placeholder="optional; default in place", id="save_path")
            )
            # Two separate decisions, deliberately: "sign anyway" must never be a
            # side effect of "replace that file".
            yield from _toggle("Force — sign despite a failed integrity check", "force")
            yield from _toggle("Overwrite an existing save-as destination", "overwrite")
            yield Static("", id="dialog-error")
            with Horizontal(id="dialog-actions"):
                yield Button("Cancel", id="cancel")
                yield Button("Sign", id="ok", variant="primary")

    def on_mount(self) -> None:
        self._sync_algorithm()

    def _algorithm(self) -> str:
        return str(self.query_one("#algorithm", Select).value or "hmac-sha256")

    def _sync_algorithm(self) -> None:
        ed = self._algorithm() == "ed25519"
        for selector in (*_HMAC_FIELDS, ".hmac"):
            for widget in self.query(selector):
                widget.display = not ed
        for selector in (*_ED25519_FIELDS, ".ed25519"):
            for widget in self.query(selector):
                widget.display = ed

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "algorithm":
            self._sync_algorithm()
            self._error("")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != "ok":
            self.dismiss(None)
            return
        algorithm = self._algorithm()
        key_env = self._value("#key_env") or None
        key_file = self._value("#key_file") or None
        ed_file = self._value("#ed_key_file") or None
        if algorithm == "ed25519":
            if not ed_file:
                self._error("Ed25519 signing needs a private key file.")
                return
            key_env = key_file = None  # hidden fields never leak into the request
            passphrase = self.query_one("#passphrase", Input).value or None
        else:
            if key_env and key_file:
                self._error("Give the HMAC key as an env var or a file, not both.")
                return
            if not key_env and not key_file:
                self._error("HMAC signing needs a key: an env var or a file.")
                return
            ed_file = None
            passphrase = None
        self.dismiss(
            SignOptions(
                algorithm=algorithm,
                key_env=key_env,
                key_file=key_file,
                ed25519_key_file=ed_file,
                key_id=self._value("#key_id") or None,
                signer=self._value("#signer") or None,
                save_path=self._value("#save_path") or None,
                force=self._switch("#force"),
                overwrite=self._switch("#overwrite"),
                passphrase=passphrase,
            )
        )


class KeygenModal(_Dialog):
    """``tine keygen``: an encrypted Ed25519 key by default (opentine 0.41)."""

    DEFAULT_CSS = _DIALOG_CSS % {"name": "KeygenModal"}

    def __init__(self, default_path: str = "opentine_ed25519.key", encrypt_only: bool = False):
        super().__init__()
        self.default_path = default_path
        # Reused for "encrypt an existing plaintext key": same fields, no toggles.
        self.encrypt_only = encrypt_only

    def compose(self) -> ComposeResult:
        title = "Encrypt a plaintext key" if self.encrypt_only else "Generate an Ed25519 keypair"
        with VerticalScroll(id="dialog"):
            yield Label(f"[bold #FF6900]{title}[/]")
            yield Static(
                "[dim]Encrypted PKCS#8 (AES-256). The passphrase is used here and "
                "never stored or shown; blank uses $OPENTINE_KEY_PASSPHRASE.[/]"
            )
            yield from _field(
                "Private key file" if not self.encrypt_only else "Key file to encrypt",
                Input(value=self.default_path, id="path"),
            )
            yield from _field("Passphrase", Input(password=True, id="passphrase"))
            yield from _field("Repeat passphrase", Input(password=True, id="repeat"))
            if not self.encrypt_only:
                yield from _toggle(
                    "Unencrypted — legacy plaintext hex (deprecated, not recommended)",
                    "unencrypted",
                )
                yield from _toggle("Replace an existing key file", "force")
            yield Static("", id="dialog-error")
            with Horizontal(id="dialog-actions"):
                yield Button("Cancel", id="cancel")
                yield Button(
                    "Encrypt" if self.encrypt_only else "Generate", id="ok", variant="primary"
                )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != "ok":
            self.dismiss(None)
            return
        path = self._value("#path")
        if not path:
            self._error("A key file path is required.")
            return
        passphrase = self.query_one("#passphrase", Input).value
        repeat = self.query_one("#repeat", Input).value
        unencrypted = False if self.encrypt_only else self._switch("#unencrypted")
        if passphrase != repeat:
            self._error("The passphrases do not match.")
            return
        if unencrypted and passphrase:
            self._error("An unencrypted key takes no passphrase; clear one or the other.")
            return
        self.dismiss(
            {
                "path": path,
                "passphrase": passphrase or None,
                "unencrypted": unencrypted,
                "force": False if self.encrypt_only else self._switch("#force"),
            }
        )


class VerifyKeyModal(_Dialog):
    DEFAULT_CSS = _DIALOG_CSS % {"name": "VerifyKeyModal"}

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="dialog"):
            yield Label("[bold #FF6900]Verify signature[/]")
            yield Static("[dim]A key implies fail-closed: unsigned runs won't pass.[/]")
            yield from _field("HMAC key from env var", Input(placeholder="name", id="key_env"))
            yield from _field("HMAC key from file", Input(placeholder="path", id="key_file"))
            yield from _field("Ed25519 public key file", Input(placeholder="path", id="pubkey"))
            yield from _toggle("Trust the embedded ed25519 key (TOFU)", "trust")
            yield Static("", id="dialog-error")
            with Horizontal(id="dialog-actions"):
                yield Button("Cancel", id="cancel")
                yield Button("Verify", id="ok", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != "ok":
            self.dismiss(None)
            return
        self.dismiss(
            VerifyKeyOptions(
                key_env=self._value("#key_env") or None,
                key_file=self._value("#key_file") or None,
                pubkey_file=self._value("#pubkey") or None,
                trust_embedded=self._switch("#trust"),
            )
        )


class MigrateModal(_Dialog):
    """``tine migrate --to v4``: writes NAME.v4.tine beside the original."""

    DEFAULT_CSS = _DIALOG_CSS % {"name": "MigrateModal"}

    def __init__(
        self,
        *,
        version_label: str,
        has_signature: bool,
        integrity_ok: bool,
        is_legacy: bool,
    ) -> None:
        super().__init__()
        self.version_label = version_label
        self.has_signature = has_signature
        self.integrity_ok = integrity_ok
        self.is_legacy = is_legacy

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="dialog"):
            yield Label("[bold #FF6900]Migrate to format v4[/]")
            yield Static(
                f"{escape_markup(self.version_label)} -> v4. The original is left untouched; the "
                "upgrade is written as NAME.v4.tine unless you name a path. "
                "Readers need opentine >= 0.26."
            )
            if self.is_legacy:
                yield Static("[yellow]Legacy import recomputes step ids (best-effort).[/]")
            if self.has_signature:
                yield Static(
                    "[yellow]The source signature is recorded as a claim; "
                    "re-sign the v4 file afterwards.[/]"
                )
            yield Input(placeholder="save as (blank: NAME.v4.tine)", id="save_path")
            yield from _toggle("In place (keeps NAME.tine.v2.bak)", "in_place")
            if not self.integrity_ok and not self.is_legacy:
                yield Static("[red]Integrity check failed.[/] Enable Force to migrate.")
            yield from _toggle("Force — migrate despite a failed check / replace a file", "force")
            yield Static("", id="dialog-error")
            with Horizontal(id="dialog-actions"):
                yield Button("Cancel", id="cancel")
                yield Button("Migrate", id="ok", variant="error")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != "ok":
            self.dismiss(None)
            return
        save_path = self.query_one("#save_path", Input).value.strip() or None
        in_place = self.query_one("#in_place", Switch).value
        if save_path and in_place:
            self._error("Choose a save path or in place.")
            return
        self.dismiss(
            {
                "force": self.query_one("#force", Switch).value,
                "save_path": save_path,
                "in_place": in_place,
            }
        )


class ForkEditModal(_Dialog):
    """``tine fork --set/--unset``: fork with the fork-point step edited."""

    DEFAULT_CSS = (
        _DIALOG_CSS % {"name": "ForkEditModal"}
        + """
    #edits { height: 8; margin-bottom: 1; }
    """
    )

    def __init__(self, step_label: str) -> None:
        super().__init__()
        self.step_label = step_label

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="dialog"):
            yield Label("[bold #FF6900]Fork with an edited step[/]")
            yield Static(f"Fork point: {escape_markup(self.step_label)}")
            yield Static(
                "[dim]One edit per line. PATH=VALUE sets (VALUE is JSON, else a string),\n"
                "PATH[+]=VALUE appends, -PATH deletes. Paths: inputs.KEY..., model,\n"
                "tool_info.KEY. The original run is never modified.[/]"
            )
            yield TextArea("", id="edits")
            yield from _field("Save as", Input(placeholder="optional", id="save_path"))
            yield Static("", id="dialog-error")
            with Horizontal(id="dialog-actions"):
                yield Button("Cancel", id="cancel")
                yield Button("Fork", id="ok", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != "ok":
            self.dismiss(None)
            return
        sets: list[str] = []
        unsets: list[str] = []
        for line in self.query_one("#edits", TextArea).text.splitlines():
            line = line.strip()
            if not line:
                continue
            if line.startswith("-"):
                unsets.append(line[1:].strip())
            elif "=" in line:
                sets.append(line)
            else:
                self._error(f"Not an edit: {line[:60]!r} (PATH=VALUE or -PATH)")
                return
        if not sets and not unsets:
            self._error("Give at least one edit.")
            return
        self.dismiss(
            {"sets": sets, "unsets": unsets, "save_path": self._value("#save_path") or None}
        )


class PromoteModal(_Dialog):
    """Point `promotions/<name>` at the selected run object."""

    DEFAULT_CSS = _DIALOG_CSS % {"name": "PromoteModal"}

    def __init__(self, run_label: str, existing: str | None = None) -> None:
        super().__init__()
        self.run_label = run_label
        self.existing = existing

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="dialog"):
            yield Label("[bold #FF6900]Promote run[/]")
            yield Static(f"Promoting {escape_markup(self.run_label)}.")
            yield Static("[dim]Ref names are lower-case and may use a-z 0-9 . _ - / only.[/]")
            yield from _field("Promotion name", Input(placeholder="e.g. production", id="name"))
            yield from _toggle("Move an existing promotion (compare-and-swap)", "overwrite")
            yield Static("", id="dialog-error")
            with Horizontal(id="dialog-actions"):
                yield Button("Cancel", id="cancel")
                yield Button("Promote", id="ok", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != "ok":
            self.dismiss(None)
            return
        name = self._value("#name")
        if not name:
            self._error("A promotion name is required.")
            return
        self.dismiss(PromoteOptions(name=name, overwrite=self._switch("#overwrite")))


class EvaluateModal(_Dialog):
    """Record an evaluation attestation — the scores repository search ranks by."""

    DEFAULT_CSS = _DIALOG_CSS % {"name": "EvaluateModal"}

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="dialog"):
            yield Label("[bold #FF6900]Evaluate run[/]")
            yield Static(
                "[dim]Writes an attestation object; the run itself is untouched.\n"
                "Signer is a label, not a verified identity.[/]"
            )
            yield from _field(
                "Scores", Input(placeholder="e.g. quality=0.9, speed=0.7", id="scores")
            )
            yield from _field("Signer", Input(placeholder="optional", id="signer"))
            yield from _field("Note", Input(placeholder="optional", id="note"))
            yield Static("", id="dialog-error")
            with Horizontal(id="dialog-actions"):
                yield Button("Cancel", id="cancel")
                yield Button("Record", id="ok", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != "ok":
            self.dismiss(None)
            return
        try:
            scores = parse_scores(self._value("#scores"))
        except ValueError as exc:
            self._error(str(exc))
            return
        if not scores:
            self._error("At least one score is required.")
            return
        self.dismiss(
            EvaluateOptions(
                scores=scores,
                signer=self._value("#signer") or "dashboard",
                note=self._value("#note"),
            )
        )


class ImportModal(_Dialog):
    """Import a portable v2 artifact into the repository (`tine migrate-v3`)."""

    DEFAULT_CSS = _DIALOG_CSS % {"name": "ImportModal"}

    def __init__(self, default_source: str = "") -> None:
        super().__init__()
        self.default_source = default_source

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="dialog"):
            yield Label("[bold #FF6900]Import .tine into the repository[/]")
            yield Static(
                "[dim]Recomputes v3 identities, keeps the original bytes as a legacy\n"
                "blob, and scopes the legacy signature to that blob.[/]"
            )
            yield from _field(
                "Source .tine", Input(value=self.default_source, placeholder="path", id="source")
            )
            yield from _field("Ref to update", Input(value="heads/main", id="ref"))
            yield from _toggle("Import even if the source fails verification", "allow_unverified")
            yield Static("", id="dialog-error")
            with Horizontal(id="dialog-actions"):
                yield Button("Cancel", id="cancel")
                yield Button("Import", id="ok", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != "ok":
            self.dismiss(None)
            return
        source = self._value("#source")
        if not source:
            self._error("A source path is required.")
            return
        self.dismiss(
            ImportOptions(
                source=source,
                ref=self._value("#ref"),
                allow_unverified=self._switch("#allow_unverified"),
            )
        )


def parse_scores(text: str) -> dict[str, float]:
    """``quality=0.9, speed=0.7`` -> ``{"quality": 0.9, "speed": 0.7}``."""
    scores: dict[str, float] = {}
    for item in text.replace(";", ",").split(","):
        item = item.strip()
        if not item:
            continue
        name, separator, value = item.partition("=")
        if not separator:
            raise ValueError(f"expected name=value, got {item!r}")
        name = name.strip()
        if not name:
            raise ValueError(f"missing score name in {item!r}")
        try:
            scores[name] = float(value.strip())
        except ValueError:
            raise ValueError(f"{value.strip()!r} is not a number") from None
    return scores


class ExportModal(_Dialog):
    """Write the selected run as an OpenTelemetry GenAI (OTLP/JSON) document."""

    DEFAULT_CSS = _DIALOG_CSS % {"name": "ExportModal"}

    def __init__(self, default_destination: str = "") -> None:
        super().__init__()
        self.default_destination = default_destination

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="dialog"):
            yield Label("[bold #FF6900]Export as OpenTelemetry[/]")
            yield Static(
                "[dim]Renders the run as GenAI spans in a complete OTLP/JSON document.\n"
                "Import and export are inverses, so `tine import --format otel-json`\n"
                "reads the result back.[/]"
            )
            yield from _field(
                "Destination .json",
                Input(value=self.default_destination, placeholder="path", id="dest"),
            )
            yield from _field("service.name", Input(value="opentine", id="service"))
            yield from _toggle("Overwrite an existing file", "overwrite")
            yield Static("", id="dialog-error")
            with Horizontal(id="dialog-actions"):
                yield Button("Cancel", id="cancel")
                yield Button("Export", id="ok", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != "ok":
            self.dismiss(None)
            return
        destination = self._value("#dest")
        if not destination:
            self._error("A destination path is required.")
            return
        self.dismiss(
            ExportOptions(
                destination=destination,
                service_name=self._value("#service") or "opentine",
                overwrite=self._switch("#overwrite"),
            )
        )


class TraceImportModal(_Dialog):
    """Import a foreign agent trace as a run (`tine import`)."""

    DEFAULT_CSS = _DIALOG_CSS % {"name": "TraceImportModal"}

    def __init__(self, has_repository: bool = False) -> None:
        super().__init__()
        self.has_repository = has_repository

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="dialog"):
            yield Label("[bold #FF6900]Import a trace[/]")
            yield Static(
                "[dim]OpenTelemetry GenAI, OpenTine JSONL, or a framework's logs.\n"
                "At least one destination is required. Capture stays off: the\n"
                "provenance belongs to the machine that produced the trace.[/]"
            )
            yield from _field("Source trace file", Input(placeholder="path", id="source"))
            yield Select(
                [(name, name) for name in IMPORT_FORMATS],
                value="otel-json",
                allow_blank=False,
                id="format",
            )
            yield from _field("Save as .tine", Input(placeholder="optional", id="save"))
            if self.has_repository:
                yield from _toggle("Also record into the v3 repository", "into_repo")
                yield from _field("Ref to advance", Input(value="heads/main", id="ref"))
            yield from _toggle("Overwrite an existing .tine destination", "overwrite")
            yield Static("", id="dialog-error")
            with Horizontal(id="dialog-actions"):
                yield Button("Cancel", id="cancel")
                yield Button("Import", id="ok", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != "ok":
            self.dismiss(None)
            return
        source = self._value("#source")
        if not source:
            self._error("A source trace file is required.")
            return
        into_repo = self.has_repository and self._switch("#into_repo")
        save = self._value("#save")
        if not save and not into_repo:
            self._error("Give a .tine destination, record into the repository, or both.")
            return
        self.dismiss(
            TraceImportOptions(
                source=source,
                source_format=str(self.query_one("#format", Select).value or "otel-json"),
                save_path=save,
                into_repository=into_repo,
                ref=(self._value("#ref") if self.has_repository else "") or DEFAULT_REF,
                overwrite=self._switch("#overwrite"),
            )
        )


class ChoiceModal(_Dialog):
    """Pick one of a few named options (e.g. a compliance profile)."""

    DEFAULT_CSS = _DIALOG_CSS % {"name": "ChoiceModal"}

    def __init__(self, title: str, choices: list[tuple[str, str]], note: str = "") -> None:
        super().__init__()
        self.title_text = title
        self.choices = list(choices)
        self.note = note

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="dialog"):
            yield Label(f"[bold #FF6900]{escape_markup(self.title_text)}[/]")
            if self.note:
                yield Static(f"[dim]{escape_markup(self.note)}[/]")
            yield Select(
                [(escape_markup(f"{name} — {title}"), name) for name, title in self.choices],
                value=self.choices[0][0],
                allow_blank=False,
                id="choice",
            )
            yield Static("", id="dialog-error")
            with Horizontal(id="dialog-actions"):
                yield Button("Cancel", id="cancel")
                yield Button("OK", id="ok", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != "ok":
            self.dismiss(None)
            return
        self.dismiss(str(self.query_one("#choice", Select).value))
