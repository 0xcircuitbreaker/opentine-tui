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

_source = os.environ.get("OPENTINE_SRC")
if _source:
    _resolved = Path(_source).expanduser().resolve()
    if not (_resolved / "opentine" / "__init__.py").is_file():
        raise RuntimeError(f"OPENTINE_SRC={_source} does not contain an opentine package")
    sys.path.insert(0, str(_resolved))
