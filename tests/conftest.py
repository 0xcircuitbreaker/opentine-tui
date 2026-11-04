"""Test configuration.

Tests run against the **installed** ``opentine`` — the one this package declares a
dependency on — so a green suite says something about what users actually get.
Set ``OPENTINE_SRC`` to a source checkout (a worktree at a release tag, say) to
test against that instead:

    OPENTINE_SRC=/path/to/opentine pytest

This file used to prepend a sibling checkout unconditionally, which shadowed
whatever CI had installed and made the version pin untestable.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_source = os.environ.get("OPENTINE_SRC")
if _source:
    _resolved = Path(_source).expanduser().resolve()
    if not (_resolved / "opentine" / "__init__.py").is_file():
        raise RuntimeError(f"OPENTINE_SRC={_source} does not contain an opentine package")
    sys.path.insert(0, str(_resolved))


@pytest.fixture(autouse=True)
def _plain_artifacts(monkeypatch):
    """Write uncompressed artifacts so tests can read and tamper with the JSON.

    opentine compresses ``.tine`` files by default since 0.11; compression is a
    storage detail outside every digest, and the dashboard's own reads go through
    opentine's decompressing reader either way (``test_v034`` covers that path).
    """
    monkeypatch.setenv("OPENTINE_COMPRESS", "0")


@pytest.fixture
def legacy_v2(monkeypatch):
    """Pin the deprecated v2 writer, for tests about v2 file semantics."""
    monkeypatch.setenv("OPENTINE_FORMAT", "2")
