"""Pure display helpers shared by the widgets and the action layer.

Nothing here touches the filesystem or mutates a run — it turns opentine values
into strings the dashboard can print.
"""

from __future__ import annotations

import re
from typing import Any

#: A v3 object id: ``event:sha256:<64 hex>``. Steps materialized out of a
#: repository carry one as their step id, so truncating to 12 characters the way a
#: v2 content hash allows would render every step as the useless ``event:sha256``.
_OID = re.compile(r"^[a-z]+:[a-z0-9]+:([0-9a-f]{8,})$")
#: A bare v2 step id / digest.
_DIGEST = re.compile(r"^[0-9a-f]{32,}$")

# Token dimensions opentine normalizes into ``step.usage``. Mirrors the set
# ``Run.total_tokens`` sums (opentine/_graph_run.py) — a step that only reports
# ``input``/``output`` is the simple case, not the whole shape: cache reads,
# 5m/1h cache writes and reasoning tokens are billed and must be shown.
TOKEN_DIMENSIONS: tuple[str, ...] = (
    "input",
    "output",
    "cache_read",
    "cache_write_5m",
    "cache_write_1h",
    "reasoning",
)

TOKEN_LABELS: dict[str, str] = {
    "input": "in",
    "output": "out",
    "cache_read": "cache-r",
    "cache_write_5m": "cache-w5m",
    "cache_write_1h": "cache-w1h",
    "reasoning": "reasoning",
}

#: ``billing.status`` -> (label, color). ``unknown`` is the important one: it is
#: why a step can read ``$0.0000`` while having cost real money.
BILLING_STATES: dict[str, tuple[str, str]] = {
    "complete": ("priced", "green"),
    "partial": ("partially priced", "yellow"),
    "unknown": ("unpriced", "red"),
    "unmetered": ("unmetered", "blue"),
}


def escape_markup(text: Any) -> str:
    """Make arbitrary text safe to interpolate into Textual console markup.

    Neither ``rich.markup.escape`` nor ``textual.markup.escape`` is correct here:
    both only escape a ``[`` that begins something they recognize as a tag, and
    Textual 8's ``Content`` parser then reads the leftovers as real markup. Recorded
    model output containing ``[bold red]…[/]`` therefore leaves a tag open and
    raises ``MarkupError`` several lines later, taking the dashboard down with it.

    Escaping *every* opening bracket is both sufficient and lossless: Textual reads
    ``\\[`` as a literal ``[`` and leaves every other character — backslashes
    included — alone, so the text renders exactly as recorded.
    """
    return str(text).replace("[", r"\[")


def short_ref(value: str | None, width: int = 12) -> str:
    """Abbreviate a content hash or v3 object id to something a human can compare.

    Only digests are shortened. A name someone chose — a run id, a ref like
    ``heads/experiment`` — is returned whole, because clipping it to 12 characters
    destroys the very thing that made it readable.
    """
    if not value:
        return ""
    match = _OID.match(value)
    if match:
        return match.group(1)[:width]
    return value[:width] if _DIGEST.match(value) else value


def _count(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0
    return int(value) if value > 0 else 0


def token_total(usage: Any) -> int:
    """Total tokens for one step, matching ``Run.total_tokens`` accounting.

    A provider-reported ``total`` wins when it exceeds the dimensions we know
    how to name, so an unmapped dimension is never silently dropped.
    """
    if not isinstance(usage, dict):
        return 0
    return max(
        _count(usage.get("total")),
        sum(_count(usage.get(name)) for name in TOKEN_DIMENSIONS),
    )


def token_parts(usage: Any) -> list[tuple[str, int | float]]:
    """Non-zero named token dimensions, in display order, then typed extras.

    Extras keep their own units (opentine allows non-integer dimensions such as
    audio seconds), so their value is passed through rather than counted.
    """
    if not isinstance(usage, dict):
        return []
    parts: list[tuple[str, int | float]] = [
        (TOKEN_LABELS[name], count)
        for name in TOKEN_DIMENSIONS
        if (count := _count(usage.get(name)))
    ]
    known = {*TOKEN_DIMENSIONS, "total"}
    parts.extend(
        (str(name), value)
        for name, value in sorted(usage.items())
        if name not in known
        and not isinstance(value, bool)
        and isinstance(value, int | float)
        and value > 0
    )
    return parts


def _amount(value: int | float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:g}"


def token_summary(usage: Any) -> str:
    """``1540 (in 1200 / out 340)`` — empty when a step reports no usage."""
    total = token_total(usage)
    if not total:
        return ""
    parts = token_parts(usage)
    if not parts:
        return str(total)
    detail = " / ".join(f"{label} {_amount(count)}" for label, count in parts)
    return f"{total} ({detail})"


def step_cost(step: Any) -> float:
    """The cost opentine itself attributes to a step.

    ``Run.total_cost`` sums ``billing.known_subtotal_usd`` and only falls back to
    the plain ``cost`` field when that is absent or unusable, so reading ``cost``
    alone shows ``$0.0000`` steps inside a run whose total is not zero.
    """
    billing = getattr(step, "billing", None)
    raw = billing.get("known_subtotal_usd") if isinstance(billing, dict) else None
    if raw is not None:
        try:
            amount = float(raw)
        except (TypeError, ValueError):
            amount = None
        if amount is not None and amount >= 0 and amount == amount and amount != float("inf"):
            return amount
    try:
        return float(getattr(step, "cost", 0.0) or 0.0)
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return 0.0


def billing_status(step: Any) -> tuple[str, str, str] | None:
    """``(status, label, color)`` from a step's billing record, if it has one."""
    billing = getattr(step, "billing", None)
    if not isinstance(billing, dict) or not billing:
        return None
    status = str(billing.get("status") or "")
    label, color = BILLING_STATES.get(status, (status or "unrecorded", "white"))
    return status, label, color


def cost_str(value: float) -> str:
    """Cost with enough precision to stay non-zero for cheap steps."""
    if not value:
        return "$0.0000"
    return f"${value:.4f}" if value < 0.01 else f"${value:.3f}"


def duration_str(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, rest = divmod(seconds, 60)
    if minutes < 60:
        return f"{int(minutes)}m{rest:04.1f}s"
    hours, minutes = divmod(int(minutes), 60)
    return f"{hours}h{minutes:02d}m"


def relative_time(seconds_ago: float) -> str:
    """Compact age: ``12s`` / ``4m`` / ``3h`` / ``6d``."""
    if seconds_ago < 0:
        return "now"
    if seconds_ago < 60:
        return f"{int(seconds_ago)}s"
    if seconds_ago < 3600:
        return f"{int(seconds_ago // 60)}m"
    if seconds_ago < 86400:
        return f"{int(seconds_ago // 3600)}h"
    return f"{int(seconds_ago // 86400)}d"
