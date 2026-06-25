"""Test configuration for the split opentine/opentine-tui workspace."""

from __future__ import annotations

import sys
from pathlib import Path

SIBLING_OPENTINE = Path(__file__).resolve().parents[2] / "opentine"
if SIBLING_OPENTINE.exists():
    sys.path.insert(0, str(SIBLING_OPENTINE))
