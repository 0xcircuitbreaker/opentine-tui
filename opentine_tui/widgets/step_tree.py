"""Step tree widget — Tree view of a run's step DAG."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from opentine.core import Run, StepKind
from rich.text import Text
from textual import events
from textual.binding import Binding
from textual.message import Message
from textual.widgets import Tree
from textual.widgets.tree import TreeNode

from opentine_tui.formatting import (
    billing_status,
    plain_text,
    short_ref,
    step_cost,
    token_total,
)
from opentine_tui.formatting import escape_markup as escape

STEP_ICONS = {
    StepKind.think: "*",
    StepKind.tool: ">",
    StepKind.model: "#",
    StepKind.done: "+",
    StepKind.error: "x",
}

STEP_COLORS = {
    StepKind.think: "bright_yellow",
    StepKind.tool: "#FF6900",
    StepKind.model: "cyan",
    StepKind.done: "green",
    StepKind.error: "red",
}


@dataclass(frozen=True, slots=True)
class StepNodeData:
    run_id: str
    step_id: str


class StepSelected(Message):
    def __init__(self, run_id: str, step_id: str) -> None:
        super().__init__()
        self.run_id = run_id
        self.step_id = step_id


def _display_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, sort_keys=True)
    except TypeError:
        return repr(value)


EMPTY_TREE = "Select a run on the left (↑/↓, enter)"
NO_STEPS = "(no steps recorded, the run may still be in progress; press r to refresh)"
LAZY_THRESHOLD = 500  # above this many steps only the top levels are expanded
LAZY_DEPTH = 2
ARGS_MAX = 50
TOK_W, COST_W, DUR_W = 9, 11, 8
METRICS_W = TOK_W + COST_W + DUR_W
GUIDE_W = 4  # cells of indentation Tree draws per depth level


def _truncate_args(args_str: str, limit: int = ARGS_MAX) -> str:
    """Cut raw (unescaped) argument text to ``limit`` chars with an ellipsis."""
    return args_str if len(args_str) <= limit else args_str[: limit - 1] + "…"


class StepTree(Tree):
    BINDINGS = [
        Binding("n", "next_match", "Next match", show=False),
        Binding("N", "prev_match", "Prev match", show=False),
        Binding("exclamation_mark", "next_error", "Next error", show=False),
        Binding("ctrl+slash", "search_steps", "Find step", show=False),
        # Many terminals send ctrl+/ as ctrl+_ (or not at all).
        Binding("ctrl+underscore", "search_steps", "Find step", show=False),
    ]

    def __init__(self) -> None:
        super().__init__(EMPTY_TREE)
        self._run: Run | None = None
        self._search = ""
        # node id -> (main Text, metrics Text, depth); kept to re-pad on resize.
        self._parts: dict[int, tuple[Text, Text, int]] = {}
        self._order: list[TreeNode] = []

    def clear_run(self, label: str = EMPTY_TREE) -> None:
        self._run = None
        self._parts = {}
        self._order = []
        self.clear()
        self.root.set_label(label)

    # -- building ----------------------------------------------------------

    def _main_label(self, step: Any) -> Text:
        """The step's label, built as styled spans — never parsed as markup.

        Recorded text (tool names, arguments, model output) is appended as plain
        text, so no bracket or backslash sequence in it can open a style or
        break the parser; control and bidi characters are stripped first.
        """
        icon = STEP_ICONS.get(step.kind, "o")
        color = STEP_COLORS.get(step.kind, "white")
        text = step.inputs.get("text", "")
        name = step.inputs.get("name", "")
        args = step.inputs.get("arguments", {})
        label = Text()
        label.append(icon, style=color)
        label.append(" ")
        if step.kind == StepKind.tool:
            if isinstance(args, dict):
                raw = ", ".join(
                    f'{k}="{v}"' if isinstance(v, str) else f"{k}={_display_value(v)}"
                    for k, v in args.items()
                )
            else:
                raw = _display_value(args)
            raw = _truncate_args(raw.replace("\n", " "))
            label.append("tool", style="bold")
            label.append(f"  {plain_text(_display_value(name))}({plain_text(raw)})")
            return label
        label.append(step.kind.value, style=color)
        if text:
            rendered_text = _display_value(text)
            preview = rendered_text[:80].replace("\n", " ")
            if len(rendered_text) > 80:
                preview += "..."
            label.append(f'  "{plain_text(preview)}"')
        else:
            label.append(f"  {plain_text(short_ref(step.id))}")
        return label

    @staticmethod
    def _metrics(step: Any, costliest: float) -> Text:
        tokens = token_total(getattr(step, "usage", {}))
        cost = step_cost(step)
        tok = Text(f"{tokens}tok" if tokens else "", style="dim")
        tok.align("right", TOK_W)
        cost_text = Text(f"${cost:.4f}" if cost > 0 else "", style="dim")
        billing = billing_status(step)
        if billing is not None and billing[0] in ("unknown", "partial"):
            # A blank cost column cannot distinguish "free" from "we could not
            # price this", and the second one is the one that costs money.
            cost_text.append("?", style=billing[2])
        if cost > 0 and cost == costliest:
            cost_text.stylize("bold")
        cost_text.align("right", COST_W)
        dur_style = "yellow" if step.duration >= 10 else "dim"
        dur = Text(f"{step.duration:.1f}s" if step.duration > 0 else "", style=dur_style)
        dur.align("right", DUR_W)
        return Text.assemble(tok, cost_text, dur)

    def _main_width(self, depth: int) -> int:
        width = self.size.width or 100
        return max(20, width - METRICS_W - 2 - GUIDE_W * depth)

    def _compose_label(self, main: Text, metrics: Text, depth: int) -> Text:
        width = self._main_width(depth)
        head = main.copy()
        head.truncate(width, overflow="ellipsis", pad=True)
        return Text.assemble(head, metrics)

    def load_run(self, run: Run) -> None:
        self._run = run
        self._parts = {}
        self._order = []
        self.clear()
        root = Text()
        root.append(plain_text(run.id), style="bold")
        root.append("  model=")
        root.append(plain_text(run.model_info), style="dim")
        self.root.set_label(root)

        if not run.steps:
            self.root.add(f"[dim]{escape(NO_STEPS)}[/]")
            self.root.expand()
            return

        costliest = max((step_cost(step) for step in run.steps), default=0.0)
        nodes: dict[str, TreeNode] = {}
        depths: dict[str, int] = {}
        for step in run.steps:
            main = self._main_label(step)
            # A step may have several parents. It is attached under the first one
            # already drawn, and the remaining edges are named in the label, so a
            # merge is visible instead of silently reduced to one ancestor.
            parents = [
                parent for parent in getattr(step, "parent_ids", None) or [] if parent in nodes
            ]
            if len(parents) > 1:
                merged = ", ".join(short_ref(parent) for parent in parents[1:])
                merge_note = Text(f"  ⋈ also from {plain_text(merged)}", style="#FF6900")
            else:
                merge_note = Text()
            depth = depths[parents[0]] + 1 if parents else 1
            # The merge note trails the metrics so truncation never hides it.
            metrics = self._metrics(step, costliest) + merge_note
            label = self._compose_label(main, metrics, depth)
            data = StepNodeData(run.id, step.id)
            parent_node = nodes[parents[0]] if parents else self.root
            node = parent_node.add(label, data=data)
            nodes[step.id] = node
            depths[step.id] = depth
            self._parts[node.id] = (main, metrics, depth)
            self._order.append(node)

        if len(run.steps) > LAZY_THRESHOLD:
            # expand_all on thousands of steps stalls the UI; show the top levels
            # and let the user (or n / ! navigation) open the rest on demand.
            self.root.expand()
            for node in self._order:
                if depths[node.data.step_id] < LAZY_DEPTH:
                    node.expand()
        else:
            # Every node is created collapsed, so expanding only the root showed a
            # run as a single step with the rest hidden one keypress deep, per level.
            self.root.expand_all()

    def on_resize(self, event: events.Resize) -> None:
        for node in self._order:
            parts = self._parts.get(node.id)
            if parts is not None:
                node.set_label(self._compose_label(*parts))

    # -- navigation --------------------------------------------------------

    def set_search(self, text: str) -> TreeNode | None:
        """Set the search text and jump to the first matching step (or None)."""
        self._search = text.strip().lower()
        if not self._search:
            return None
        return self._jump(self._matches_search, forward=True, from_start=True)

    def _matches_search(self, node: TreeNode) -> bool:
        parts = self._parts.get(node.id)
        return parts is not None and self._search in parts[0].plain.lower()

    def _is_error(self, node: TreeNode) -> bool:
        if self._run is None or not isinstance(node.data, StepNodeData):
            return False
        step = self._step_by_id().get(node.data.step_id)
        if step is None:
            return False
        return step.kind == StepKind.error or bool(getattr(step, "error", None))

    def _step_by_id(self) -> dict[str, Any]:
        return {step.id: step for step in self._run.steps} if self._run else {}

    def _current_index(self) -> int:
        node = self.cursor_node
        for index, candidate in enumerate(self._order):
            if candidate is node:
                return index
        return -1

    def _jump(self, predicate: Any, forward: bool, from_start: bool = False) -> TreeNode | None:
        if not self._order:
            return None
        count = len(self._order)
        start = -1 if from_start else self._current_index()
        if start < 0 and not forward:
            start = 0
        for offset in range(1, count + 1):
            index = (start + offset) % count if forward else (start - offset) % count
            node = self._order[index]
            if predicate(node):
                self.reveal(node)
                return node
        return None

    def reveal(self, node: TreeNode) -> None:
        """Expand ``node``'s ancestors, then move the cursor to it."""
        parent = node.parent
        while parent is not None:
            parent.expand()
            parent = parent.parent
        self._refresh_lines_now()
        self.move_cursor(node)
        self.scroll_to_node(node)

    def _refresh_lines_now(self) -> None:
        # Expanding invalidates the line cache; reading it rebuilds node lines so
        # move_cursor sees the node's real line number.
        _ = self._tree_lines

    def action_next_match(self) -> None:
        if self._search:
            self._jump(self._matches_search, forward=True)

    def action_prev_match(self) -> None:
        if self._search:
            self._jump(self._matches_search, forward=False)

    def action_next_error(self) -> None:
        self._jump(self._is_error, forward=True)

    def action_search_steps(self) -> None:
        from opentine_tui.dialogs import TextInputModal

        def done(value: str | None) -> None:
            if value is not None:
                self.set_search(value)

        self.app.push_screen(TextInputModal("Find step", "tool name or text", self._search), done)

    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        data = event.node.data
        if isinstance(data, StepNodeData):
            event.stop()
            self.post_message(StepSelected(data.run_id, data.step_id))
