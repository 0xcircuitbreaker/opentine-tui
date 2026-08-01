"""OpenTelemetry export and foreign-trace import (opentine 0.5.0).

0.5.0 made OpenTine a *source* of OpenTelemetry GenAI provenance as well as a
sink, and brought the importers to the shell as ``tine import``. This module is
the dashboard's side of both, and deliberately mirrors ``tine import``'s
semantics rather than inventing its own:

* at least one destination is required — a portable ``.tine`` artifact, a v3
  repository ref, or both;
* with only an artifact requested the run is built in a throwaway repository, so
  importing never leaves a repository behind nobody asked for;
* capture is off, because the provenance of an imported trace belongs to the
  machine that produced it, not to the one running the dashboard.
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from opentine_tui.actions import ActionResult

try:
    from opentine import to_otel_genai_document
except Exception:  # pragma: no cover - opentine < 0.5
    to_otel_genai_document = None

try:
    from opentine.trace.importers import framework_events, jsonl_events, otel_genai_events
    from opentine.trace.recorder import Recorder
except Exception:  # pragma: no cover - opentine without the trace subsystem
    framework_events = jsonl_events = otel_genai_events = None
    Recorder = None

try:
    from opentine.repo import Repo
except Exception:  # pragma: no cover - defensive
    Repo = None

#: Framework log formats the importers recognize by name.
FRAMEWORK_FORMATS = ("langchain", "llamaindex", "autogen", "crewai", "openai-agents")
#: Every ``--format`` ``tine import`` accepts. A drift test pins this to the CLI's
#: own list, so a format added there fails the suite rather than going unnoticed.
IMPORT_FORMATS = ("otel-json", "otel-spans", "jsonl", *FRAMEWORK_FORMATS)
DEFAULT_REF = "heads/main"

#: Import and export both raise these for "this payload cannot be read/written".
#: KernelError subclasses ValueError, so a repository refusal lands here too.
INTEROP_ERRORS = (OSError, RecursionError, TypeError, ValueError)


@dataclass(slots=True)
class ExportOptions:
    destination: str = ""
    service_name: str = "opentine"
    overwrite: bool = False


@dataclass(slots=True)
class TraceImportOptions:
    source: str = ""
    source_format: str = "otel-json"
    save_path: str = ""
    into_repository: bool = False
    ref: str = DEFAULT_REF
    overwrite: bool = False


def _fail(title: str, message: str) -> ActionResult:
    return ActionResult(False, title, message)


def _reason(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"


def _records(text: str) -> list[Any]:
    """A JSON array, a single object, or one object per line.

    Whole-document parsing is tried first so a pretty-printed array spanning many
    lines is not mistaken for JSONL — the same order ``tine import`` uses.
    """
    try:
        decoded = json.loads(text)
    except ValueError:
        pass
    else:
        return decoded if isinstance(decoded, list) else [decoded]
    records = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except ValueError as exc:
            # The decoder counts lines inside the fragment it was handed, so on
            # its own it says "line 1" for every bad record in the file.
            raise ValueError(f"line {number} is not valid JSON: {exc}") from exc
    return records


def read_events(source: str | Path, source_format: str) -> list[Any]:
    """Route a trace file to the importer named by ``source_format``."""
    if otel_genai_events is None:  # pragma: no cover - opentine without importers
        raise RuntimeError("installed opentine has no trace importers")
    if source_format == "jsonl":
        # The JSONL importer does its own bounded, streaming read of a path.
        return jsonl_events(str(source))
    text = Path(source).read_text(encoding="utf-8", errors="replace")
    if source_format == "otel-json":
        return otel_genai_events(json.loads(text))
    if source_format == "otel-spans":
        return otel_genai_events(_records(text))
    if source_format in FRAMEWORK_FORMATS:
        return framework_events(_records(text), source_format)
    raise ValueError(f"unsupported import format: {source_format}")


class InteropService:
    """Export runs as OTLP/JSON, and import foreign traces as runs."""

    def __init__(self, repository: Any, v3: Any | None = None) -> None:
        self.repository = repository
        self.v3 = v3

    # -- export ------------------------------------------------------------

    def export_otel(self, run: Any, options: ExportOptions) -> ActionResult:
        """Write a run as a complete OTLP/JSON document.

        Accepts either source unchanged: the exporter takes anything with
        ``.steps``, which is both a v2 ``Run`` and a v3 ``repo.load_run()``.
        """
        if to_otel_genai_document is None:
            return _fail(
                "Export unavailable",
                "OpenTelemetry export needs opentine>=0.5.",
            )
        destination = (options.destination or "").strip()
        if not destination:
            return _fail("Export failed", "A destination path is required.")
        out = Path(destination).expanduser()
        if out.is_dir():
            return _fail("Export failed", f"{out} is a directory; give a .json file path.")
        if out.exists() and not options.overwrite:
            return _fail(
                "Export blocked",
                f"{out} already exists; enable Overwrite to replace it.",
            )
        try:
            document = to_otel_genai_document(run, service_name=options.service_name or "opentine")
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(document, indent=2), encoding="utf-8")
        except INTEROP_ERRORS as exc:
            return _fail("Export failed", _reason(exc))
        spans = _span_count(document)
        return ActionResult(
            True,
            "Exported",
            (
                f"{spans} GenAI span(s) -> {out}\n"
                "Import and export are inverses, so `tine import --format otel-json` "
                "reads this back."
            ),
            path=out,
        )

    # -- import ------------------------------------------------------------

    def import_trace(self, options: TraceImportOptions) -> ActionResult:
        """Import a foreign trace as a run (`tine import`)."""
        if Recorder is None or Repo is None:
            return _fail("Import unavailable", "Trace import needs opentine>=0.5.")
        source = Path((options.source or "").strip()).expanduser()
        if not source.is_file():
            return _fail("Import failed", f"Not a file: {source}")
        save_path = (options.save_path or "").strip()
        if not save_path and not options.into_repository:
            return _fail(
                "Import failed",
                "Nothing to write: give a .tine destination, record into the repository, or both.",
            )
        if options.into_repository and self.v3 is None:
            return _fail("Import failed", "There is no v3 repository to record into.")

        out = Path(save_path).expanduser() if save_path else None
        if out is not None and out.exists() and not options.overwrite:
            return _fail("Import blocked", f"{out} already exists; enable Overwrite to replace it.")

        try:
            events = read_events(source, options.source_format)
        except INTEROP_ERRORS as exc:
            return _fail("Import failed", _reason(exc))
        if not events:
            # Distinct from a read failure: the source was read fine and held
            # nothing this importer recognizes — almost always the wrong format.
            return _fail(
                "Nothing to import",
                f"{source.name} held no trace events as '{options.source_format}'.\n"
                "That usually means the format does not match the file.",
            )

        ref = (options.ref or DEFAULT_REF).strip() or DEFAULT_REF
        try:
            if options.into_repository:
                run_id = self._persist(self.v3.repo, events, ref, out)
                where = f"repository ref {ref}"
            else:
                # No repository was asked for, so the one the recorder needs is
                # temporary and goes away with the import.
                with tempfile.TemporaryDirectory(prefix="tine-import-") as scratch:
                    run_id = self._persist(Repo.init(Path(scratch) / "import"), events, ref, out)
                where = "a throwaway repository (discarded)"
        except INTEROP_ERRORS as exc:
            return _fail("Import failed", _reason(exc))

        lines = [f"{len(events)} event(s) from {source.name}", f"Run: {run_id}"]
        if out is not None:
            lines.append(f"Saved: {out}")
        lines.append(f"Recorded into {where}.")
        lines.append("[dim]Capture is off — the provenance belongs to the source machine.[/]")
        return ActionResult(True, "Imported", "\n".join(lines), path=out, refresh=True)

    @staticmethod
    def _persist(repo: Any, events: list[Any], ref: str, output: Path | None) -> str:
        recorder = Recorder.start(repo, ref=ref, capture=False)
        recorder.import_events(events)
        run_id = recorder.finalize()
        if output is not None:
            repo.load_run(run_id).save(output)
        return run_id


def _span_count(document: dict[str, Any]) -> int:
    try:
        return sum(
            len(scope.get("spans") or [])
            for resource in document.get("resourceSpans") or []
            for scope in resource.get("scopeSpans") or []
        )
    except (AttributeError, TypeError):  # pragma: no cover - defensive
        return 0
