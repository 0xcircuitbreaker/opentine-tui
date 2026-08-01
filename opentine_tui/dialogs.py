"""Modal dialogs used by the Textual application."""

from __future__ import annotations

import shlex
from typing import Any

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Select, Static, Switch

from opentine_tui.actions import (
    HARNESS_FACTORIES,
    BudgetOptions,
    HarnessOptions,
    SignOptions,
    VerifyKeyOptions,
)
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
            # The harness list comes from the installed opentine, so a preset added
            # there (0.3.0 added grok and gemini) shows up without editing the TUI.
            choices = sorted(HARNESS_FACTORIES) or ["generic"]
            yield Select(
                [(name, name) for name in choices],
                value="codex" if "codex" in choices else choices[0],
                allow_blank=False,
                id="harness",
            )
            yield Input(value=self.default_prompt, placeholder="Prompt", id="prompt")
            yield Input(value=self.default_cwd, placeholder="cwd", id="cwd")
            yield Input(placeholder='Command override, e.g. "codex exec"', id="command")
            yield Input(placeholder="Extra args, shell-quoted", id="extra")
            yield Input(placeholder="Env allowlist, comma separated", id="env")
            with Horizontal(classes="toggle"):
                yield Switch(value=False, id="login")
                yield Label("Pass the login environment through to the harness")
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

        login_env = self.query_one("#login", Switch).value
        env_allowlist = tuple(
            item.strip() for item in self._input_value("#env").split(",") if item.strip()
        )
        data: dict[str, Any] = {
            "harness": str(self.query_one("#harness", Select).value or "codex"),
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
#dialog .toggle {
    height: auto;
    margin-bottom: 1;
}
#dialog .toggle Label {
    padding: 1 1 0 1;
}
"""


class _Dialog(ModalScreen):
    """Shared plumbing for the option modals."""

    def _value(self, selector: str) -> str:
        return self.query_one(selector, Input).value.strip()

    def _switch(self, selector: str) -> bool:
        return self.query_one(selector, Switch).value

    def _error(self, message: str) -> None:
        self.query_one("#dialog-error", Static).update(message)

    def key_escape(self) -> None:
        self.dismiss(None)


def _toggle(label: str, switch_id: str, value: bool = False) -> ComposeResult:
    with Horizontal(classes="toggle"):
        yield Switch(value=value, id=switch_id)
        yield Label(label)


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
                on_breach=str(self.query_one("#on_breach", Select).value or "stop"),
                strict_cost=self.query_one("#strict_cost", Switch).value,
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
            yield Select(
                [("HMAC-SHA256", "hmac-sha256"), ("Ed25519", "ed25519")],
                value="hmac-sha256",
                allow_blank=False,
                id="algorithm",
            )
            yield Input(placeholder="HMAC key from env var (name)", id="key_env")
            yield Input(placeholder="HMAC key from file (path)", id="key_file")
            yield Input(placeholder="ed25519 private key file (path)", id="ed_key_file")
            yield Input(placeholder="key id (optional)", id="key_id")
            yield Input(placeholder="signer label (optional, display only)", id="signer")
            yield Input(placeholder="save as (optional; default in place)", id="save_path")
            # Two separate decisions, deliberately: "sign anyway" must never be a
            # side effect of "replace that file".
            yield from _toggle("Force — sign despite a failed integrity check", "force")
            yield from _toggle("Overwrite an existing save-as destination", "overwrite")
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
            algorithm=str(self.query_one("#algorithm", Select).value or "hmac-sha256"),
            key_env=self._value("#key_env") or None,
            key_file=self._value("#key_file") or None,
            ed25519_key_file=self._value("#ed_key_file") or None,
            key_id=self._value("#key_id") or None,
            signer=self._value("#signer") or None,
            save_path=self._value("#save_path") or None,
            force=self.query_one("#force", Switch).value,
            overwrite=self.query_one("#overwrite", Switch).value,
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
            yield Input(placeholder="save as (blank: rewrite in place)", id="save_path")
            if not self.integrity_ok and not self.is_legacy:
                yield Static("[red]Integrity check failed.[/] Enable Force to migrate.")
            yield from _toggle("Force — migrate despite a failed check / replace a file", "force")
            with Horizontal(id="dialog-actions"):
                yield Button("Cancel", id="cancel")
                yield Button("Migrate", id="ok", variant="error")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != "ok":
            self.dismiss(None)
            return
        self.dismiss(
            {
                "force": self.query_one("#force", Switch).value,
                "save_path": self.query_one("#save_path", Input).value.strip() or None,
            }
        )

    def key_escape(self) -> None:
        self.dismiss(None)


class PromoteModal(_Dialog):
    """Point `promotions/<name>` at the selected run object."""

    DEFAULT_CSS = _DIALOG_CSS % {"name": "PromoteModal"}

    def __init__(self, run_label: str, existing: str | None = None) -> None:
        super().__init__()
        self.run_label = run_label
        self.existing = existing

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label("[bold #FF6900]Promote run[/]")
            yield Static(f"Promoting {self.run_label}.")
            yield Static("[dim]Ref names are lower-case and may use a-z 0-9 . _ - / only.[/]")
            yield Input(placeholder="promotion name, e.g. production", id="name")
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
        with Vertical(id="dialog"):
            yield Label("[bold #FF6900]Evaluate run[/]")
            yield Static(
                "[dim]Writes an attestation object; the run itself is untouched.\n"
                "Signer is a label, not a verified identity.[/]"
            )
            yield Input(placeholder="scores, e.g. quality=0.9, speed=0.7", id="scores")
            yield Input(placeholder="signer (optional)", id="signer")
            yield Input(placeholder="note (optional)", id="note")
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
        with Vertical(id="dialog"):
            yield Label("[bold #FF6900]Import .tine into the repository[/]")
            yield Static(
                "[dim]Recomputes v3 identities, keeps the original bytes as a legacy\n"
                "blob, and scopes the legacy signature to that blob.[/]"
            )
            yield Input(value=self.default_source, placeholder="source .tine path", id="source")
            yield Input(value="heads/main", placeholder="ref to update", id="ref")
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
        with Vertical(id="dialog"):
            yield Label("[bold #FF6900]Export as OpenTelemetry[/]")
            yield Static(
                "[dim]Renders the run as GenAI spans in a complete OTLP/JSON document.\n"
                "Import and export are inverses, so `tine import --format otel-json`\n"
                "reads the result back.[/]"
            )
            yield Input(
                value=self.default_destination, placeholder="destination .json path", id="dest"
            )
            yield Input(value="opentine", placeholder="service.name", id="service")
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
        with Vertical(id="dialog"):
            yield Label("[bold #FF6900]Import a trace[/]")
            yield Static(
                "[dim]OpenTelemetry GenAI, OpenTine JSONL, or a framework's logs.\n"
                "At least one destination is required. Capture stays off: the\n"
                "provenance belongs to the machine that produced the trace.[/]"
            )
            yield Input(placeholder="source trace file", id="source")
            yield Select(
                [(name, name) for name in IMPORT_FORMATS],
                value="otel-json",
                allow_blank=False,
                id="format",
            )
            yield Input(placeholder="save as .tine (optional)", id="save")
            if self.has_repository:
                yield from _toggle("Also record into the v3 repository", "into_repo")
                yield Input(value="heads/main", placeholder="ref to advance", id="ref")
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
