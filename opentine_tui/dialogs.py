"""Modal dialogs used by the Textual application."""

from __future__ import annotations

import shlex
from typing import Any

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Static

from opentine_tui.actions import BudgetOptions, HarnessOptions, SignOptions, VerifyKeyOptions

try:
    from opentine.signing import HAS_ED25519
except Exception:  # pragma: no cover - defensive
    HAS_ED25519 = False


_TRUTHY = {"1", "true", "yes", "y", "on"}


def _parse_optional_float(text: str) -> float | None:
    text = text.strip()
    return float(text) if text else None


def _parse_optional_int(text: str) -> int | None:
    text = text.strip()
    return int(text) if text else None


def _split_csv(text: str) -> list[str]:
    return [item.strip() for item in text.split(",") if item.strip()]


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

    # Distinguish "submitted" (a string, possibly empty -> clears a filter) from
    # "cancelled" (None -> no-op). Callers that reject empties use `if not value`.
    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip())

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "ok":
            self.dismiss(self.query_one("#value", Input).value.strip())
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


_DIALOG_CSS = """
%(name)s {
    align: center middle;
}
#dialog {
    width: 84;
    max-width: 95%%;
    height: auto;
    border: thick #FF6900;
    background: $surface;
    padding: 1 2;
}
#dialog Input {
    margin-bottom: 1;
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
"""


class TagEditorModal(ModalScreen[dict | None]):
    DEFAULT_CSS = _DIALOG_CSS % {"name": "TagEditorModal"}

    def __init__(self, current_tags: list[str]) -> None:
        super().__init__()
        self.current_tags = list(current_tags)

    def compose(self) -> ComposeResult:
        current = ", ".join(self.current_tags) or "(none)"
        with Vertical(id="dialog"):
            yield Label("[bold #FF6900]Edit tags[/]")
            yield Static(f"Current: {current}")
            yield Static("[dim]Tags are lower-cased, de-duped, and live outside the digest.[/]")
            yield Input(placeholder="Add (comma separated)", id="add")
            yield Input(placeholder="Remove (comma separated)", id="remove")
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

    def key_escape(self) -> None:
        self.dismiss(None)


class BudgetModal(ModalScreen[BudgetOptions | None]):
    DEFAULT_CSS = _DIALOG_CSS % {"name": "BudgetModal"}

    def __init__(self, current: Any | None = None) -> None:
        super().__init__()
        self.current = current

    def _initial(self, attr: str) -> str:
        value = getattr(self.current, attr, None) if self.current is not None else None
        return "" if value is None else str(value)

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label("[bold #FF6900]Set budget[/]")
            yield Static(
                "[yellow]Writes manifest.budget (inside the digest) — rewrites the "
                "digest and invalidates any signature.[/]"
            )
            yield Input(value=self._initial("max_cost"), placeholder="max cost $", id="max_cost")
            yield Input(value=self._initial("max_usage"), placeholder="max tokens", id="max_usage")
            yield Input(value=self._initial("max_steps"), placeholder="max steps", id="max_steps")
            yield Input(
                value=self._initial("max_duration"), placeholder="max seconds", id="max_duration"
            )
            yield Input(
                value=getattr(self.current, "on_breach", "stop") if self.current else "stop",
                placeholder="on breach: stop / raise",
                id="on_breach",
            )
            yield Static("", id="dialog-error")
            with Horizontal(id="dialog-actions"):
                yield Button("Cancel", id="cancel")
                yield Button("Save", id="ok", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != "ok":
            self.dismiss(None)
            return
        try:
            options = BudgetOptions(
                max_cost=_parse_optional_float(self.query_one("#max_cost", Input).value),
                max_usage=_parse_optional_int(self.query_one("#max_usage", Input).value),
                max_steps=_parse_optional_int(self.query_one("#max_steps", Input).value),
                max_duration=_parse_optional_float(self.query_one("#max_duration", Input).value),
                on_breach=(self.query_one("#on_breach", Input).value.strip() or "stop"),
            )
        except ValueError as exc:
            self.query_one("#dialog-error", Static).update(f"Invalid number: {exc}")
            return
        self.dismiss(options)

    def key_escape(self) -> None:
        self.dismiss(None)


class SignModal(ModalScreen[SignOptions | None]):
    DEFAULT_CSS = _DIALOG_CSS % {"name": "SignModal"}

    def compose(self) -> ComposeResult:
        ed = "available" if HAS_ED25519 else "unavailable — pip install opentine[crypto]"
        with Vertical(id="dialog"):
            yield Label("[bold #FF6900]Sign artifact[/]")
            yield Static(f"[dim]ed25519: {ed}. HMAC keys must be >= 16 bytes.[/]")
            yield Input(value="hmac-sha256", placeholder="hmac-sha256 / ed25519", id="algorithm")
            yield Input(placeholder="HMAC key from env var (name)", id="key_env")
            yield Input(placeholder="HMAC key from file (path)", id="key_file")
            yield Input(placeholder="ed25519 private key file (path)", id="ed_key_file")
            yield Input(placeholder="key id (optional)", id="key_id")
            yield Input(placeholder="signer label (optional, display only)", id="signer")
            yield Input(placeholder="save as (optional; default in place)", id="save_path")
            yield Input(placeholder="force despite failed integrity: true/false", id="force")
            yield Static("", id="dialog-error")
            with Horizontal(id="dialog-actions"):
                yield Button("Cancel", id="cancel")
                yield Button("Sign", id="ok", variant="primary")

    def _value(self, selector: str) -> str:
        return self.query_one(selector, Input).value.strip()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != "ok":
            self.dismiss(None)
            return
        options = SignOptions(
            algorithm=self._value("#algorithm") or "hmac-sha256",
            key_env=self._value("#key_env") or None,
            key_file=self._value("#key_file") or None,
            ed25519_key_file=self._value("#ed_key_file") or None,
            key_id=self._value("#key_id") or None,
            signer=self._value("#signer") or None,
            save_path=self._value("#save_path") or None,
            force=self._value("#force").lower() in _TRUTHY,
        )
        self.dismiss(options)

    def key_escape(self) -> None:
        self.dismiss(None)


class VerifyKeyModal(ModalScreen[VerifyKeyOptions | None]):
    DEFAULT_CSS = _DIALOG_CSS % {"name": "VerifyKeyModal"}

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label("[bold #FF6900]Verify signature[/]")
            yield Static("[dim]A key implies fail-closed: unsigned runs won't pass.[/]")
            yield Input(placeholder="HMAC key from env var (name)", id="key_env")
            yield Input(placeholder="HMAC key from file (path)", id="key_file")
            yield Input(placeholder="ed25519 public key file (path)", id="pubkey")
            yield Input(placeholder="trust embedded ed25519 key (TOFU): true/false", id="trust")
            with Horizontal(id="dialog-actions"):
                yield Button("Cancel", id="cancel")
                yield Button("Verify", id="ok", variant="primary")

    def _value(self, selector: str) -> str:
        return self.query_one(selector, Input).value.strip()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != "ok":
            self.dismiss(None)
            return
        self.dismiss(
            VerifyKeyOptions(
                key_env=self._value("#key_env") or None,
                key_file=self._value("#key_file") or None,
                pubkey_file=self._value("#pubkey") or None,
                trust_embedded=self._value("#trust").lower() in _TRUTHY,
            )
        )

    def key_escape(self) -> None:
        self.dismiss(None)


class MigrateModal(ModalScreen[dict | None]):
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
        with Vertical(id="dialog"):
            yield Label("[bold #FF6900]Migrate to v2[/]")
            yield Static(f"{self.version_label} -> v2 (one-way; 0.1.x can no longer read it).")
            if self.is_legacy:
                yield Static("[yellow]Legacy import recomputes step ids (best-effort).[/]")
            if self.has_signature:
                yield Static("[yellow]Signature will be dropped — re-sign after migrating.[/]")
            if not self.integrity_ok and not self.is_legacy:
                yield Static("[red]Integrity check failed.[/] Enable Force to migrate.")
                yield Input(placeholder="force: true/false", id="force")
            with Horizontal(id="dialog-actions"):
                yield Button("Cancel", id="cancel")
                yield Button("Migrate", id="ok", variant="error")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != "ok":
            self.dismiss(None)
            return
        force = False
        try:
            force = self.query_one("#force", Input).value.strip().lower() in _TRUTHY
        except Exception:
            force = False
        self.dismiss({"force": force})

    def key_escape(self) -> None:
        self.dismiss(None)
