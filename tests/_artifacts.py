"""Read and tamper with ``.tine`` files the way they are stored now.

opentine compresses artifacts by default (``TINEZ1`` + zlib/zstd), so a test that
edits an artifact goes through these helpers rather than ``read_text``: they
unwrap with opentine's own reader and write back in the same stored form.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from opentine._artifact_io import read_artifact_bytes
from opentine._codec import is_wrapped
from opentine._codec_io import write_artifact


def is_compressed(path: Path) -> bool:
    with Path(path).open("rb") as handle:
        return is_wrapped(handle.read(16))


def read_text(path: Path) -> str:
    return read_artifact_bytes(path).decode("utf-8")


def write_text(path: Path, text: str, *, compressed: bool | None = None) -> None:
    """Write ``text`` back compressed when the file was (or when asked)."""
    path = Path(path)
    if compressed is None:
        compressed = path.exists() and is_compressed(path)
    if compressed:
        write_artifact(path, text)
    else:
        path.write_text(text, encoding="utf-8")


def read_doc(path: Path) -> dict[str, Any]:
    return json.loads(read_text(path))


def write_doc(path: Path, data: Any) -> None:
    write_text(path, json.dumps(data))


def edit_text(path: Path, edit: Callable[[str], str]) -> None:
    write_text(path, edit(read_text(path)))
