"""Modal dialogs used by the Textual application."""

from __future__ import annotations

import shlex
from typing import Any

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Static

from opentine_tui.actions import HarnessOptions


class ConfirmationModal(ModalScreen[bool]):
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

    def __init__(self, title: str, message: str) -> None:
        super().__init__()
        self.title_text = title
        self.message = message

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-dialog"):
            yield Label(f"[bold #FF6900]{self.title_text}[/]")
            yield Static(self.message)
            with Horizontal(id="confirm-actions"):
                yield Button("Cancel", id="cancel")
                yield Button("Confirm", id="confirm", variant="error")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirm")

    def key_escape(self) -> None:
        self.dismiss(False)


class TextInputModal(ModalScreen[str | None]):
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
            yield Label(f"[bold #FF6900]{self.title_text}[/]")
            yield Input(value=self.value, placeholder=self.placeholder, id="value")
            with Horizontal(id="input-actions"):
                yield Button("Cancel", id="cancel")
                yield Button("OK", id="ok", variant="primary")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip() or None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "ok":
            self.dismiss(self.query_one("#value", Input).value.strip() or None)
        else:
            self.dismiss(None)

    def key_escape(self) -> None:
        self.dismiss(None)


class HarnessOptionsModal(ModalScreen[HarnessOptions | None]):
    DEFAULT_CSS = """
    HarnessOptionsModal {
        align: center middle;
    }
    #harness-dialog {
        width: 92;
        max-width: 95%;
        height: auto;
        border: thick #FF6900;
        background: $surface;
        padding: 1 2;
    }
    #harness-dialog Input {
        margin-bottom: 1;
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

    def __init__(
        self,
        *,
        title: str = "Harness options",
        default_prompt: str = "",
        default_cwd: str = "",
    ) -> None:
        super().__init__()
        self.title_text = title
        self.default_prompt = default_prompt
        self.default_cwd = default_cwd

    def compose(self) -> ComposeResult:
        with Vertical(id="harness-dialog"):
            yield Label(f"[bold #FF6900]{self.title_text}[/]")
            yield Input(value="codex", placeholder="Harness", id="harness")
            yield Input(value=self.default_prompt, placeholder="Prompt", id="prompt")
            yield Input(value=self.default_cwd, placeholder="cwd", id="cwd")
            yield Input(placeholder='Command override, e.g. "codex exec"', id="command")
            yield Input(placeholder="Extra args, shell-quoted", id="extra")
            yield Input(placeholder="Login env: true/false", id="login")
            yield Input(placeholder="Env allowlist, comma separated", id="env")
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

    def key_escape(self) -> None:
        self.dismiss(None)

    def _input_value(self, selector: str) -> str:
        return self.query_one(selector, Input).value.strip()

    def _parse_options(self) -> HarnessOptions | None:
        try:
            extra_args = tuple(shlex.split(self._input_value("#extra")))
        except ValueError as exc:
            self.query_one("#harness-error", Static).update(str(exc))
            return None

        login_text = self._input_value("#login").lower()
        login_env = login_text in {"1", "true", "yes", "y", "on"}
        env_allowlist = tuple(
            item.strip() for item in self._input_value("#env").split(",") if item.strip()
        )
        data: dict[str, Any] = {
            "harness": self._input_value("#harness") or "codex",
            "prompt": self._input_value("#prompt"),
            "cwd": self._input_value("#cwd") or None,
            "command_override": self._input_value("#command") or None,
            "extra_args": extra_args,
            "login_env": login_env,
            "env_allowlist": env_allowlist,
        }
        return HarnessOptions(**data)
