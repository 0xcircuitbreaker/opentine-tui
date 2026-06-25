"""Repository helpers for loading and saving .tine run artifacts."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from opentine.core import Run

DEFAULT_RUNS_DIR = ".tine_runs"


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


@dataclass(slots=True)
class RunRecord:
    """A row in the run repository, including corrupt artifact state."""

    path: Path
    mtime: float
    run: Run | None = None
    load_error: str | None = None
    integrity: Any | None = None

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
    def is_corrupt(self) -> bool:
        return self.load_error is not None or (self.integrity is not None and not self.integrity.ok)

    @property
    def error_message(self) -> str:
        if self.load_error:
            return self.load_error
        if self.integrity and not self.integrity.ok:
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
    """File-backed repository for current .tine v1 run artifacts."""

    def __init__(self, runs_dir: str | Path | None = None) -> None:
        self.runs_dir = Path(runs_dir).expanduser() if runs_dir is not None else default_runs_dir()

    def list_records(self) -> list[RunRecord]:
        if not self.runs_dir.exists():
            return []
        files = sorted(
            self.runs_dir.glob("*.tine"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        return [self.inspect_path(path) for path in files]

    def inspect_path(self, path: str | Path) -> RunRecord:
        run_path = Path(path)
        try:
            mtime = run_path.stat().st_mtime
        except OSError:
            mtime = 0.0

        integrity = verify_integrity(run_path)
        try:
            run = Run.load(run_path)
        except Exception as exc:
            return RunRecord(
                path=run_path,
                mtime=mtime,
                run=None,
                load_error=f"{type(exc).__name__}: {exc}",
                integrity=integrity,
            )

        return RunRecord(path=run_path, mtime=mtime, run=run, integrity=integrity)

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
        return self.runs_dir / f"{run.id}.tine"

    def save(self, run: Run, path: str | Path | None = None) -> Path:
        out = Path(path).expanduser() if path is not None else self.path_for_run(run)
        out.parent.mkdir(parents=True, exist_ok=True)
        return run.save(out)
