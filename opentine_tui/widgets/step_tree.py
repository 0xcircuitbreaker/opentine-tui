"""Step tree widget — Tree view of a run's step DAG."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from opentine.core import Run, StepKind
from rich.markup import escape
from textual.message import Message
from textual.widgets import Tree

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
                    f"{getattr(step, 'short_id', step.id[:12])}"
                )

            cost_str = f"  [dim]${step.cost:.4f}[/]" if step.cost > 0 else ""
            dur_str = f"  [dim]{step.duration:.1f}s[/]" if step.duration > 0 else ""
            usage = getattr(step, "usage", {}) or {}
            tokens = int(usage.get("input", 0) or 0) + int(usage.get("output", 0) or 0)
            tok_str = f"  [dim]{tokens}tok[/]" if tokens else ""

            data = StepNodeData(run.id, step.id)
            parent_id = getattr(step, "parent_id", None)
            annotated = label + cost_str + tok_str + dur_str
            if parent_id and parent_id in nodes:
                node = nodes[parent_id].add(annotated, data=data)
            else:
                node = self.root.add(annotated, data=data)
            nodes[step.id] = node

        self.root.expand()

    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        data = event.node.data
        if isinstance(data, StepNodeData):
            event.stop()
            self.post_message(StepSelected(data.run_id, data.step_id))
