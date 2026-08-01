"""Access to an opentine v3 (`.tine/`) repository.

opentine 0.3.0 keeps portable `*.tine` files at format v2 and adds a git-shaped
object repository beside them: content-addressed `run:`/`event:`/`blob:`/
`attestation:`/`annotation:` objects, refs with reflogs, deterministic packs, and
remotes. This module is the dashboard's read model for that repository — it turns
the object database into rows and reports, and never hides a refusal from the
library, because a refusal there is usually the interesting news (a tampered
object, a shallow boundary, an over-cap search).

Deliberately thin: the v3 objects are verified on read by opentine itself, so
there is nothing here resembling the integrity/signature bookkeeping the v2 file
list needs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from opentine.core import Run

try:
    from opentine.repo import Repo
except Exception:  # pragma: no cover - defensive
    Repo = None  # type: ignore[assignment]

try:
    from opentine.kernel import KernelError
except Exception:  # pragma: no cover - defensive

    class KernelError(ValueError): ...


#: opentine caps `search(limit=...)` at 1000 and defaults it to 20; the dashboard
#: wants a whole (bounded) directory, so it asks for the maximum.
SEARCH_LIMIT = 1000

# Errors the repository layer raises for "this repository, object or query cannot
# be served" — all of which the dashboard reports rather than crashes on.
REPO_ERRORS = (KernelError, KeyError, OSError, ValueError, UnicodeError)


def short_oid(oid: str) -> str:
    """``run:sha256:216c7cb…`` -> ``216c7cb7f219``."""
    digest = oid.rsplit(":", 1)[-1]
    return digest[:12]


@dataclass(slots=True)
class RepoRunRecord:
    """A run object in a v3 repository, as a dashboard row."""

    oid: str
    status: str = "unknown"
    model: str = ""
    created_at: float = 0.0
    cost: float = 0.0
    latency: float = 0.0
    score: float | None = None
    event_count: int = 0
    refs: tuple[str, ...] = ()
    source_run_id: str | None = None
    forked_from: str | None = None
    shallow: bool = False
    load_error: str | None = None

    @property
    def key(self) -> str:
        return self.oid

    @property
    def short_id(self) -> str:
        return short_oid(self.oid)

    @property
    def ref_label(self) -> str:
        """The most meaningful ref name pointing at this run, if any.

        A promotion outranks a branch head: it is the claim someone made about the
        run, not merely where work continued.
        """
        promotions = [name for name in self.refs if name.startswith("promotions/")]
        heads = [name for name in self.refs if name.startswith("heads/")]
        chosen = promotions or heads or list(self.refs)
        if not chosen:
            return ""
        name = sorted(chosen)[0]
        return name.split("/", 1)[1] if "/" in name else name

    @property
    def is_fork(self) -> bool:
        return self.forked_from is not None

    @property
    def display_name(self) -> str:
        return self.source_run_id or self.short_id


@dataclass(slots=True)
class RepoStatus:
    """What the dashboard shows about the repository itself."""

    path: Path
    worktree: Path
    refs: dict[str, str] = field(default_factory=dict)
    object_count: int = 0
    shallow: bool = False
    error: str | None = None


class V3Repository:
    """Dashboard-facing wrapper over ``opentine.repo.Repo``."""

    def __init__(self, repo: Any) -> None:
        self.repo = repo
        self._runs: list[RepoRunRecord] = []
        # v3 objects are content-addressed and immutable, so a payload keyed by oid
        # never goes stale. Only the ref map and the set of objects change.
        self._payloads: dict[str, dict[str, Any]] = {}
        self._fingerprint: tuple[Any, ...] | None = None

    def invalidate(self) -> None:
        self._fingerprint = None

    # -- discovery ---------------------------------------------------------

    @classmethod
    def available(cls) -> bool:
        return Repo is not None

    @classmethod
    def discover(cls, start: str | Path | None = None) -> V3Repository | None:
        """Open the repository containing ``start``, or None when there is none.

        ``Repo.open`` already walks up to the first ``.tine/config.json``, which is
        how ``tine`` itself resolves ``--repo``.
        """
        if Repo is None:  # pragma: no cover - defensive
            return None
        try:
            return cls(Repo.open(Path(start).expanduser() if start else Path.cwd()))
        except (FileNotFoundError, *REPO_ERRORS):
            return None

    @classmethod
    def init(cls, path: str | Path) -> V3Repository:
        if Repo is None:  # pragma: no cover - defensive
            raise RuntimeError("installed opentine has no v3 repository support")
        return cls(Repo.init(path))

    # -- identity ----------------------------------------------------------

    @property
    def path(self) -> Path:
        return Path(self.repo.path)

    @property
    def worktree(self) -> Path:
        return Path(self.repo.worktree)

    def status(self) -> RepoStatus:
        status = RepoStatus(path=self.path, worktree=self.worktree)
        try:
            status.refs = dict(self.repo.list_refs())
        except REPO_ERRORS as exc:
            status.error = _reason(exc)
        try:
            status.object_count = len(self.repo.iter_oids())
            status.shallow = bool(self.repo.shallow_oids())
        except REPO_ERRORS as exc:
            status.error = status.error or _reason(exc)
        return status

    # -- reads -------------------------------------------------------------

    def refs_by_oid(self) -> dict[str, list[str]]:
        try:
            refs = self.repo.list_refs()
        except REPO_ERRORS:
            return {}
        grouped: dict[str, list[str]] = {}
        for name, oid in refs.items():
            grouped.setdefault(oid, []).append(name)
        return grouped

    def list_runs(self) -> list[RepoRunRecord]:
        """Every run object in the repository, newest first.

        Cost/latency/evaluation score come from ``repo.search`` (which walks the
        events); identity and ancestry come from the run payload. Either source may
        refuse — a repository over a search cap still lists its runs.
        """
        grouped = self.refs_by_oid()
        oids = self._all_oids()
        run_oids = self._run_oids(oids, grouped)
        fingerprint = (len(oids), tuple(sorted(grouped.items())))
        if self._fingerprint == fingerprint and self._runs:
            # Nothing was added and no ref moved: walking every event again would
            # cost seconds per poll to reproduce the list already on screen.
            return self._runs
        shallow = self._shallow_oids()
        scored = self._search_index()

        records: list[RepoRunRecord] = []
        for oid in run_oids:
            record = RepoRunRecord(oid=oid, refs=tuple(sorted(grouped.get(oid, ()))))
            try:
                payload = self._payload(oid)
            except REPO_ERRORS as exc:
                record.load_error = _reason(exc)
                records.append(record)
                continue
            events = payload.get("events") or []
            record.status = str(payload.get("status") or "unknown")
            record.model = str(payload.get("model") or "")
            record.created_at = _number(payload.get("created_at"))
            record.event_count = len(events) if isinstance(events, list) else 0
            record.source_run_id = _text(payload.get("source_run_id"))
            record.forked_from = _text(payload.get("forked_from"))
            record.shallow = bool(shallow) and any(
                isinstance(event, str) and event in shallow for event in events
            )
            hit = scored.get(oid)
            if hit is not None:
                record.cost = hit.cost
                record.latency = hit.latency
                record.score = hit.score
                if hit.models:
                    record.model = ", ".join(hit.models)
            records.append(record)

        records.sort(key=lambda item: (item.created_at, item.oid), reverse=True)
        self._runs = records
        self._fingerprint = fingerprint
        return records

    def load_run(self, oid_or_ref: str) -> Run:
        """Materialize a v3 run as the same ``Run`` the file list renders."""
        return self.repo.load_run(oid_or_ref)

    def log(self, ref: str = "heads/main", limit: int | None = None) -> list[Any]:
        return list(self.repo.log(ref, limit=limit))

    def context_slice(self, event_oid: str, depth: int = 8) -> list[Any]:
        return list(self.repo.context_slice(event_oid, depth=depth))

    def inspect(self, oid: str, resolve_blobs: bool = False) -> dict[str, Any]:
        return self.repo.inspect(oid, resolve_blobs=resolve_blobs)

    def search(self, query: str = "", *, successful_only: bool = False) -> list[Any]:
        return list(self.repo.search(query, successful_only=successful_only, limit=SEARCH_LIMIT))

    def fsck(self, deep: bool = True) -> Any:
        return self.repo.fsck(deep=deep)

    def diff(self, left: str, right: str) -> Any:
        return self.repo.diff(left, right)

    def find(self, ref: str) -> RepoRunRecord | None:
        """Resolve a ref name, full oid, or short oid prefix to a listed run."""
        # Always go through list_runs: it is cached on an unchanged repository, and
        # reading `_runs` directly returned the pre-promotion view after a write.
        candidates = self.list_runs()
        for record in candidates:
            if record.oid == ref or ref in record.refs:
                return record
        try:
            resolved = self.repo.read_ref(ref)
        except REPO_ERRORS:
            resolved = None
        if resolved:
            return next((record for record in candidates if record.oid == resolved), None)
        matches = [
            record
            for record in candidates
            if record.short_id.startswith(ref) or record.oid.endswith(ref)
        ]
        return matches[0] if len(matches) == 1 else None

    # -- internals ---------------------------------------------------------

    def _payload(self, oid: str) -> dict[str, Any]:
        cached = self._payloads.get(oid)
        if cached is None:
            cached = self.repo.get(oid).payload()
            self._payloads[oid] = cached
        return cached

    def all_oids(self) -> list[str]:
        """Every object id in the repository, or an empty list if unreadable."""
        try:
            return self.repo.iter_oids()
        except REPO_ERRORS:
            return []

    _all_oids = all_oids

    def _run_oids(self, oids: list[str], grouped: dict[str, list[str]]) -> list[str]:
        found = {oid for oid in oids if oid.startswith("run:")}
        # A shallow clone can hold a ref whose run object is present but whose
        # objects directory was not fully enumerated; refs are authoritative.
        found.update(oid for oid in grouped if oid.startswith("run:"))
        return sorted(found)

    def _shallow_oids(self) -> set[str]:
        try:
            return set(self.repo.shallow_oids())
        except REPO_ERRORS:  # pragma: no cover - defensive
            return set()

    def _search_index(self) -> dict[str, Any]:
        try:
            hits = self.repo.search("", successful_only=False, limit=SEARCH_LIMIT)
        except REPO_ERRORS:
            return {}
        return {hit.run_id: hit for hit in hits}


def _reason(exc: BaseException) -> str:
    message = exc.args[0] if isinstance(exc, KeyError) and exc.args else str(exc)
    return f"{type(exc).__name__}: {message}"


def _number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0.0
    return float(value)


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None
