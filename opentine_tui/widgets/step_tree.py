"""Step tree widget — Tree view of a run's step DAG."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from opentine.core import Run, StepKind
from textual.message import Message
from textual.widgets import Tree

from opentine_tui.formatting import billing_status, short_ref, step_cost, token_total
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


class StepTree(Tree):
    def __init__(self) -> None:
        super().__init__("Select a run")
        self._run: Run | None = None

    def clear_run(self, label: str = "Select a run") -> None:
        self._run = None
        self.clear()
        self.root.set_label(label)

    def load_run(self, run: Run) -> None:
        self._run = run
        self.clear()
        self.root.set_label(f"[bold]{escape(run.id)}[/]  model=[dim]{escape(run.model_info)}[/]")

        if not run.steps:
            self.root.add("[dim](no steps)[/]")
            return

        nodes = {}
        for step in run.steps:
            icon = STEP_ICONS.get(step.kind, "o")
            color = STEP_COLORS.get(step.kind, "white")

            text = step.inputs.get("text", "")
            name = step.inputs.get("name", "")
            args = step.inputs.get("arguments", {})

            if step.kind == StepKind.tool:
                if isinstance(args, dict):
                    args_str = ", ".join(
                        f'{escape(str(k))}="{escape(v)}"'
                        if isinstance(v, str)
                        else f"{escape(str(k))}={escape(_display_value(v))}"
                        for k, v in args.items()
                    )
                else:
                    args_str = escape(_display_value(args))
                label = (
                    f"[{color}]{icon}[/] [bold]tool[/]  {escape(_display_value(name))}({args_str})"
                )
            elif text:
                rendered_text = _display_value(text)
                preview = rendered_text[:80].replace("\n", " ")
                if len(rendered_text) > 80:
                    preview += "..."
                label = f'[{color}]{icon}[/] [{color}]{step.kind.value}[/]  "{escape(preview)}"'
            else:
                label = (
                    f"[{color}]{icon}[/] [{color}]{step.kind.value}[/]  "
                    f"{escape(short_ref(step.id))}"
                )

            cost = step_cost(step)
            cost_str = f"  [dim]${cost:.4f}[/]" if cost > 0 else ""
            billing = billing_status(step)
            if billing is not None and billing[0] in ("unknown", "partial"):
                # A blank cost column cannot distinguish "free" from "we could not
                # price this", and the second one is the one that costs money.
                cost_str += f"  [{billing[2]}]?[/]"
            dur_str = f"  [dim]{step.duration:.1f}s[/]" if step.duration > 0 else ""
            tokens = token_total(getattr(step, "usage", {}))
            tok_str = f"  [dim]{tokens}tok[/]" if tokens else ""

            data = StepNodeData(run.id, step.id)
            annotated = label + cost_str + tok_str + dur_str
            # A step may have several parents. It is attached under the first one
            # already drawn, and the remaining edges are named in the label, so a
            # merge is visible instead of silently reduced to one ancestor.
            parents = [
                parent for parent in getattr(step, "parent_ids", None) or [] if parent in nodes
            ]
            if len(parents) > 1:
                merged = ", ".join(short_ref(parent) for parent in parents[1:])
                annotated += f"  [#FF6900]⋈ also from {escape(merged)}[/]"
            if parents:
                node = nodes[parents[0]].add(annotated, data=data)
            else:
                node = self.root.add(annotated, data=data)
            nodes[step.id] = node

        # Every node is created collapsed, so expanding only the root showed a run
        # as a single step with the rest hidden one keypress deep, per level.
        self.root.expand_all()

    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        data = event.node.data
        if isinstance(data, StepNodeData):
            event.stop()
            self.post_message(StepSelected(data.run_id, data.step_id))
