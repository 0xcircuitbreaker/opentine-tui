"""Step tree widget — Tree view of a run's step DAG."""

from __future__ import annotations

from textual.widgets import Tree

from opentine.core import Run, StepKind

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


class StepTree(Tree):
    def __init__(self) -> None:
        super().__init__("Select a run")

    def load_run(self, run: Run) -> None:
        self.clear()
        self.root.set_label(f"[bold]{run.id}[/]  model=[dim]{run.model_info}[/]")

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
                args_str = ", ".join(
                    f'{k}="{v}"' if isinstance(v, str) else f"{k}={v}" for k, v in args.items()
                )
                label = f"[{color}]{icon}[/] [bold]tool[/]  {name}({args_str})"
            elif text:
                preview = text[:80].replace("\n", " ")
                if len(text) > 80:
                    preview += "..."
                label = f'[{color}]{icon}[/] [{color}]{step.kind.value}[/]  "{preview}"'
            else:
                label = f"[{color}]{icon}[/] [{color}]{step.kind.value}[/]  {step.id}"

            cost_str = f"  [dim]${step.cost:.4f}[/]" if step.cost > 0 else ""
            dur_str = f"  [dim]{step.duration:.1f}s[/]" if step.duration > 0 else ""

            if step.parent_id and step.parent_id in nodes:
                node = nodes[step.parent_id].add(label + cost_str + dur_str)
            else:
                node = self.root.add(label + cost_str + dur_str)
            nodes[step.id] = node

        self.root.expand()
