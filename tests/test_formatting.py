"""Coverage for the shared display helpers."""

from __future__ import annotations

from opentine_tui.formatting import (
    billing_status,
    cost_str,
    duration_str,
    relative_time,
    token_parts,
    token_summary,
    token_total,
)


class _Step:
    def __init__(self, billing: dict | None = None) -> None:
        self.billing = billing or {}


def test_token_total_counts_every_billed_dimension():
    usage = {
        "input": 1200,
        "output": 340,
        "cache_read": 800,
        "cache_write_5m": 500,
        "cache_write_1h": 100,
        "reasoning": 220,
    }
    # The pre-0.3 dashboard counted input+output only and under-reported by 1620.
    assert token_total(usage) == 3160


def test_token_total_prefers_a_larger_provider_total():
    # A provider total above the dimensions we can name means there is a dimension
    # we do not know how to spell; trusting the sum would silently lose it.
    assert token_total({"total": 5000, "input": 10, "output": 5}) == 5000
    assert token_total({"total": 5, "input": 100}) == 100


def test_token_total_is_defensive_about_junk():
    assert token_total(None) == 0
    assert token_total({}) == 0
    assert token_total({"input": True}) == 0
    assert token_total({"input": "many"}) == 0
    assert token_total({"input": -5}) == 0


def test_token_parts_labels_dimensions_and_passes_extras_through():
    parts = token_parts({"input": 10, "reasoning": 3, "audio_seconds": 2.5, "output": 0})
    assert parts == [("in", 10), ("reasoning", 3), ("audio_seconds", 2.5)]


def test_token_summary_renders_total_and_detail():
    assert token_summary({"input": 1200, "output": 340}) == "1540 (in 1200 / out 340)"
    assert token_summary({}) == ""
    # An extra keeps its own units rather than being truncated to an int.
    assert "audio_seconds 2.5" in token_summary({"input": 1, "audio_seconds": 2.5})


def test_billing_status_distinguishes_unpriced_from_free():
    assert billing_status(_Step({"status": "unknown"})) == ("unknown", "unpriced", "red")
    assert billing_status(_Step({"status": "complete"})) == ("complete", "priced", "green")
    assert billing_status(_Step({"status": "unmetered"})) == ("unmetered", "unmetered", "blue")
    assert billing_status(_Step({"status": "partial"}))[1] == "partially priced"
    assert billing_status(_Step()) is None
    assert billing_status(_Step({"status": "weird"})) == ("weird", "weird", "white")


def test_cost_and_duration_formats():
    assert cost_str(0) == "$0.0000"
    assert cost_str(0.00012) == "$0.0001"
    assert cost_str(1.2345) == "$1.234"
    assert duration_str(9.44) == "9.4s"
    assert duration_str(75.2) == "1m15.2s"
    assert duration_str(4000) == "1h06m"


def test_relative_time_buckets():
    assert relative_time(-1) == "now"
    assert relative_time(5) == "5s"
    assert relative_time(400) == "6m"
    assert relative_time(9000) == "2h"
    assert relative_time(400000) == "4d"
