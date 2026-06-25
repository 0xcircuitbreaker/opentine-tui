"""Import smoke coverage."""

from __future__ import annotations


def test_import_package():
    import opentine_tui

    assert opentine_tui.BRAND == "#FF6900"
