"""Repository helpers for loading and saving .tine run artifacts."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from opentine.core import Run

# --- opentine 0.2.0 surface (degrade gracefully on an older/odd install) ------

try:  # canonical format constants live in opentine._canon, not the package root
    from opentine._canon import FORMAT_VERSION, SUPPORTED_VERSIONS
except Exception:  # pragma: no cover - defensive
    FORMAT_VERSION = 2
    SUPPORTED_VERSIONS = (1, 2)

try:
    from opentine.migrations import LEGACY_VERSION, MigrationError, detect_version, is_legacy_linear
except Exception:  # pragma: no cover - defensive

    class MigrationError(Exception): ...

    LEGACY_VERSION = 0
    detect_version = None
    is_legacy_linear = None

try:
    from opentine.index import (
        QueryError,
        atomic_write_text,
        entry_from_run,
        match_entry,
        parse_query,
    )
except Exception:  # pragma: no cover - defensive

    class QueryError(Exception): ...

    entry_from_run = None
    match_entry = None
    parse_query = None
    atomic_write_text = None

DEFAULT_RUNS_DIR = ".tine_runs"

_UNSAFE_NAME = re.compile(r"[^A-Za-z0-9._-]")


def safe_filename(value: str, fallback: str = "run") -> str:
    """Reduce an untrusted id to a single, harmless path component."""
    cleaned = _UNSAFE_NAME.sub("_", value or "").strip("._") or fallback
    return cleaned[:120]


def default_runs_dir() -> Path:
    """Return the configured runs directory."""
    return Path(os.environ.get("OPENTINE_RUNS_DIR", DEFAULT_RUNS_DIR)).expanduser()


@dataclass(slots=True)
class IntegrityCheck:
    ok: bool
    algorithm: str | None
    expected: str | None
    actual: str | None
    reason: str
    draft: bool = False


def verify_integrity(path: str | Path) -> Any:
    verifier = getattr(Run, "verify_integrity", None)
    if verifier is not None:
        return verifier(path)

    try:
        Run.load(path)
    except FileNotFoundError:
        return IntegrityCheck(False, None, None, None, "file not found")
    except Exception as exc:
        return IntegrityCheck(False, None, None, None, f"{type(exc).__name__}: {exc}")
    return IntegrityCheck(
        False,
        None,
        None,
        None,
        "installed opentine does not expose integrity verification",
    )


def _read_raw(path: Path) -> dict[str, Any] | None:
    """Parse the raw .tine JSON (pre-migration, on-disk view), or None if unreadable."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _detect_on_disk(raw: dict[str, Any] | None) -> tuple[int | None, str]:
    """Classify a file by its ON-DISK format version.

    Critical: the loaded ``Run`` and the run index both report the migrated
    in-memory version (always ``FORMAT_VERSION``), so neither can tell us whether
    a file still needs migration. We must classify from the raw JSON.
    """
    if raw is None:
        return None, "unreadable"
    if detect_version is None:  # pragma: no cover - defensive
        version = raw.get("format_version")
        if isinstance(version, int) and not isinstance(version, bool):
            return version, f"v{version}"
        if is_legacy_linear is None and raw.get("type") == "Run" and "graph" not in raw:
            return LEGACY_VERSION, "legacy 0.1.0"
        return None, "unknown"
    try:
        version = detect_version(raw)
    except MigrationError:
        return None, "unsupported"
    if version == LEGACY_VERSION:
        return version, "legacy 0.1.0"
    if version in SUPPORTED_VERSIONS:
        return version, f"v{version}"
    return version, "unsupported"


def _signature_status(raw: dict[str, Any] | None) -> Any | None:
    """Signature presence/header WITHOUT a key (state: unsigned/no-key/...)."""
    if raw is None:
        return None
    verifier = getattr(Run, "verify_signature", None)
    if verifier is None:
        return None
    try:
        return verifier(raw)
    except Exception:  # pragma: no cover - defensive
        return None


@dataclass(slots=True)
class RunRecord:
    """A row in the run repository, including corrupt artifact state."""

    path: Path
    mtime: float
    run: Run | None = None
    load_error: str | None = None
    integrity: Any | None = None
    on_disk_version: int | None = None
    version_label: str = "?"
    tags: list[str] = field(default_factory=list)
    total_tokens: int = 0
    signature: Any | None = None
    # The run's search-index entry, built once with the record. Deriving it per
    # filter call re-walked every step of every run on the UI thread each time the
    # user typed, or the poll ticked.
    entry: Any | None = None

    @property
    def key(self) -> str:
        return str(self.path)

    @property
    def run_id(self) -> str:
        return self.run.id if self.run else self.path.stem

    @property
    def short_id(self) -> str:
        return self.run_id[:12]

    @property
    def is_legacy(self) -> bool:
        return self.on_disk_version == LEGACY_VERSION

    @property
    def needs_migration(self) -> bool:
        """True for readable legacy(0)/v1 files that a re-save would upgrade."""
        if self.load_error is not None or self.run is None:
            return False
        return self.on_disk_version in (LEGACY_VERSION, 1)

    @property
    def is_future(self) -> bool:
        return self.on_disk_version is not None and self.on_disk_version > FORMAT_VERSION

    @property
    def is_draft(self) -> bool:
        return bool(getattr(self.integrity, "draft", False))

    @property
    def is_corrupt(self) -> bool:
        # A future-version file is unreadable here but is not corruption — it was
        # written by a newer opentine. Surfaced separately (is_future / '!' flag).
        if self.is_future:
            return False
        if self.load_error is not None:
            return True
        # A legacy 0.1.0 file has no digest verifiable under current rules, so a
        # failed integrity check there is EXPECTED, not corruption.
        if self.integrity is not None and not self.integrity.ok:
            return self.on_disk_version in SUPPORTED_VERSIONS
        return False

    @property
    def has_signature(self) -> bool:
        state = getattr(self.signature, "state", "unsigned")
        return self.signature is not None and state != "unsigned"

    @property
    def sig_state(self) -> str:
        return getattr(self.signature, "state", "unsigned")

    @property
    def error_message(self) -> str:
        if self.load_error:
            return self.load_error
        if self.is_corrupt and self.integrity is not None:
            return self.integrity.reason
        return ""

    @property
    def status_value(self) -> str:
        if self.is_corrupt:
            return "corrupt"
        if self.run is None:
            return "unknown"
        return self.run.status.value

    @property
    def model_info(self) -> str:
        return self.run.model_info if self.run else ""

    @property
    def step_count(self) -> int:
        return len(self.run.steps) if self.run else 0

    @property
    def total_cost(self) -> float:
        return self.run.total_cost if self.run else 0.0


class RunRepository:
    """File-backed repository for .tine v2 (and migratable v1/legacy) artifacts."""

    def __init__(self, runs_dir: str | Path | None = None) -> None:
        self.runs_dir = Path(runs_dir).expanduser() if runs_dir is not None else default_runs_dir()
        # path -> ((mtime, size), record). Inspecting one artifact reads, verifies,
        # signature-checks and loads it; at ~13ms each a 200-run directory costs
        # ~2.6s, which the dashboard would otherwise pay on every poll.
        self._cache: dict[Path, tuple[tuple[float, int], RunRecord]] = {}

    def invalidate(self, path: str | Path | None = None) -> None:
        """Drop cached inspection state (all of it, or one artifact)."""
        if path is None:
            self._cache.clear()
        else:
            self._cache.pop(Path(path), None)

    def list_records(self) -> list[RunRecord]:
        if not self.runs_dir.exists():
            self._cache.clear()
            return []
        stats: list[tuple[Path, os.stat_result]] = []
        for path in self.runs_dir.glob("*.tine"):
            try:
                stats.append((path, path.stat()))
            except OSError:  # vanished mid-scan
                continue
        stats.sort(key=lambda item: item[1].st_mtime, reverse=True)

        records: list[RunRecord] = []
        fresh: dict[Path, tuple[tuple[float, int], RunRecord]] = {}
        for path, stat_result in stats:
            fingerprint = (stat_result.st_mtime, stat_result.st_size)
            cached = self._cache.get(path)
            record = cached[1] if cached is not None and cached[0] == fingerprint else None
            if record is None:
                record = self.inspect_path(path)
            fresh[path] = (fingerprint, record)
            records.append(record)
        self._cache = fresh  # drops entries for deleted files
        return records

    def inspect_path(self, path: str | Path) -> RunRecord:
        run_path = Path(path)
        try:
            mtime = run_path.stat().st_mtime
        except OSError:
            mtime = 0.0

        raw = _read_raw(run_path)
        on_disk_version, version_label = _detect_on_disk(raw)
        integrity = verify_integrity(run_path)
        signature = _signature_status(raw)

        try:
            run = Run.load(run_path)
        except Exception as exc:
            return RunRecord(
                path=run_path,
                mtime=mtime,
                run=None,
                load_error=f"{type(exc).__name__}: {exc}",
                integrity=integrity,
                on_disk_version=on_disk_version,
                version_label=version_label,
                signature=signature,
            )

        entry = None
        if entry_from_run is not None:
            try:
                entry = entry_from_run(run, run_path.name, mtime)
            except Exception:  # pragma: no cover - an unindexable run still lists
                entry = None

        return RunRecord(
            path=run_path,
            mtime=mtime,
            run=run,
            integrity=integrity,
            on_disk_version=on_disk_version,
            version_label=version_label,
            tags=list(getattr(run, "tags", []) or []),
            total_tokens=int(getattr(run, "total_tokens", 0) or 0),
            signature=signature,
            entry=entry,
        )

    def find_path(self, ref: str | Path) -> Path:
        query_path = Path(ref).expanduser()
        if query_path.exists():
            return query_path

        ref_text = str(ref)
        if not self.runs_dir.exists():
            raise FileNotFoundError(ref_text)

        matches: list[Path] = [
            path for path in self.runs_dir.glob("*.tine") if path.stem.startswith(ref_text)
        ]

        for record in self.list_records():
            if record.run and record.run.id.startswith(ref_text) and record.path not in matches:
                matches.append(record.path)

        if not matches:
            raise FileNotFoundError(ref_text)
        if len(matches) > 1:
            names = ", ".join(path.name for path in matches[:5])
            raise ValueError(f"Ambiguous run reference {ref_text!r}: {names}")
        return matches[0]

    def load(self, ref: str | Path) -> Run:
        return Run.load(self.find_path(ref))

    def path_for_run(self, run: Run) -> Path:
        """Where a run is stored by default.

        A run id comes out of an artifact, which is data someone else may have
        written; ``../../.ssh/authorized_keys`` is a legal string. The filename is
        therefore built from a sanitized id, never from the raw one.
        """
        return self.runs_dir / f"{safe_filename(run.id)}.tine"

    def save(self, run: Run, path: str | Path | None = None, *, overwrite: bool = True) -> Path:
        """Persist ``run``. ``overwrite=False`` refuses to replace an existing file.

        In-place edits (tag, budget, sign, migrate) want the default; anything that
        creates a *new* artifact passes ``overwrite=False``, matching the CLI's
        refusal to clobber a run without an explicit flag.
        """
        out = Path(path).expanduser() if path is not None else self.path_for_run(run)
        if out.is_dir():
            # `Run.save` treats a directory holding config.json as a v3 repository
            # target and writes objects into it. Every save from here means a
            # portable file, so a directory is a mistake, not a mode switch.
            raise IsADirectoryError(
                f"{out} is a directory; give a .tine file path "
                "(use 'i' to import into a repository)"
            )
        if not overwrite and out.exists():
            raise FileExistsError(str(out))
        out.parent.mkdir(parents=True, exist_ok=True)
        return run.save(out)

    def write_tags(self, path: str | Path, tags: list[str]) -> Path:
        """Persist tags by editing ``metadata.tags`` in the raw JSON.

        Tags live outside both the integrity digest and the signature's signed
        view, so this preserves an existing digest **and** signature and never
        triggers a v1->v2 upgrade — unlike re-saving through ``Run.save``, which
        recomputes the digest and drops the signature block. Mirrors the
        canonical re-tag path exercised by opentine's own signing tests.
        """
        out = Path(path)
        raw = _read_raw(out)
        if raw is None:
            raise ValueError("cannot edit tags: file is not readable JSON")
        meta = raw.setdefault("metadata", {})
        if not isinstance(meta, dict):  # pragma: no cover - defensive
            raise ValueError("cannot edit tags: metadata is not an object")
        if tags:
            meta["tags"] = list(tags)
        else:
            meta.pop("tags", None)
        text = json.dumps(raw, indent=2, sort_keys=True)
        if atomic_write_text is not None:
            atomic_write_text(out, text)
        else:  # pragma: no cover - defensive fallback
            tmp = out.with_name(out.name + ".tmp")
            tmp.write_text(text, encoding="utf-8")
            tmp.replace(out)
        return out


def filter_records(records: list[RunRecord], query: str) -> tuple[list[RunRecord], str | None]:
    """Filter records by the opentine search DSL.

    Returns ``(filtered, error)``. Corrupt rows are always kept visible so a
    broken file never silently disappears behind a filter. A bad query is
    reported via ``error`` and leaves the records unfiltered.
    """
    query = (query or "").strip()
    if not query:
        return records, None
    if parse_query is None or match_entry is None or entry_from_run is None:
        return records, "search requires opentine>=0.2"
    try:
        parsed = parse_query(query)
    except QueryError as exc:
        return records, str(exc)
    except Exception as exc:  # pragma: no cover - defensive
        return records, f"{type(exc).__name__}: {exc}"

    kept: list[RunRecord] = []
    for record in records:
        if record.run is None:
            kept.append(record)  # keep corrupt rows visible while filtering
            continue
        entry = record.entry
        if entry is None:
            # No index entry means the run could not be described, not that it
            # matched: hiding it would be worse, so it stays visible and labelled.
            kept.append(record)
            continue
        try:
            if match_entry(entry, parsed):
                kept.append(record)
        except Exception as exc:  # pragma: no cover - defensive
            return records, f"{type(exc).__name__}: {exc}"
    return kept, None
