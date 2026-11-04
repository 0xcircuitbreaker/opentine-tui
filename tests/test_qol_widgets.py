"""QOL widget behaviour: detail truncation, run sorting, empty states, step navigation."""

from __future__ import annotations

from pathlib import Path

import pytest
from opentine.core import Run, RunStatus, StepKind

from opentine_tui.app import OpentineTUI
from opentine_tui.widgets.run_list import RunList
from opentine_tui.widgets.step_detail import TRUNCATION_MARKER, StepDetail
from opentine_tui.widgets.step_tree import NO_STEPS, StepTree


def _app(tmp_path: Path) -> OpentineTUI:
    runs = tmp_path / ".tine_runs"
    runs.mkdir(exist_ok=True)
    return OpentineTUI(runs_dir=runs)


@pytest.mark.asyncio
async def test_long_output_truncated_and_x_expands(tmp_path: Path):
    run = Run(id="big", model_info="m", user_prompt="p")
    out = {"text": "\n".join(f"line {i} [red]x[/]" for i in range(400))}
    step = run.add_step(StepKind.think, {"text": "t"}, outputs=out)
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        detail = app.query_one(StepDetail)
        detail.set_step(run, step)
        assert TRUNCATION_MARKER in detail.last_rendered
        assert "▸ Outputs" in detail.last_rendered
        assert "Inputs" in detail.last_rendered
        detail.focus()
        await pilot.press("z")
        await pilot.pause()
        assert TRUNCATION_MARKER not in detail.last_rendered
        assert "\\[red]" in detail.last_rendered  # still escaped


@pytest.mark.asyncio
async def test_error_section_comes_first(tmp_path: Path):
    run = Run(id="err", model_info="m", user_prompt="p")
    step = run.add_step(StepKind.error, {"text": "boom"}, error={"message": "bad"})
    app = _app(tmp_path)
    async with app.run_test():
        detail = app.query_one(StepDetail)
        detail.set_step(run, step)
        text = detail.last_rendered
        assert "▾ Error" in text
        assert text.index("Error") < text.index("Inputs")


@pytest.mark.asyncio
async def test_empty_states(tmp_path: Path):
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        tree = app.query_one(StepTree)
        assert "Select a run" in str(tree.root.label)
        tree.load_run(Run(id="empty", model_info="m", user_prompt="p"))
        await pilot.pause()
        assert any(NO_STEPS in str(line.node.label) for line in tree._tree_lines)
    detail = StepDetail()
    assert "Select a step to see its input" in detail.last_rendered


def _write(runs: Path, run_id: str, cost: float) -> None:
    run = Run(id=run_id, model_info="m", user_prompt="p")
    run.add_step(StepKind.model, {"text": "x"}, cost=cost)
    run.status = RunStatus.completed
    run.save(runs / f"{run_id}.tine")


@pytest.mark.asyncio
async def test_cycle_sort_by_cost_keeps_highlight(tmp_path: Path):
    runs = tmp_path / ".tine_runs"
    runs.mkdir()
    for run_id, cost in (("a1", 1.0), ("c3", 3.0), ("b2", 2.0)):
        _write(runs, run_id, cost)
    app = OpentineTUI(runs_dir=runs)
    async with app.run_test() as pilot:
        table = app.query_one(RunList)
        key = next(r.key for r in table._records if r.run_id == "b2")
        table.highlight_key(key)
        label = table.cycle_sort()
        await pilot.pause()
        assert label == "cost ▼"
        assert table._records[0].run_id == "c3"
        assert table.highlighted_key == key


def _big_run(count: int = 600) -> Run:
    run = Run(id="big", model_info="m", user_prompt="p")
    for i in range(count):
        if i == 300:
            run.add_step(StepKind.error, {"text": "failure here"})
        elif i % 100 == 7:
            run.add_step(StepKind.tool, {"name": "grep_tool", "arguments": {"q": str(i)}})
        else:
            run.add_step(StepKind.think, {"text": f"t{i}"})
    return run


@pytest.mark.asyncio
async def test_lazy_expansion_error_jump_and_search(tmp_path: Path):
    run = _big_run()
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        tree = app.query_one(StepTree)
        tree.load_run(run)
        await pilot.pause()
        assert len(list(tree._tree_lines)) < 600
        tree.focus()
        await pilot.press("exclamation_mark")
        await pilot.pause()
        assert tree.cursor_node.data.step_id == run.steps[300].id
        first = tree.set_search("grep_tool")
        assert first.data.step_id == run.steps[7].id
        await pilot.press("n")
        await pilot.pause()
        assert tree.cursor_node.data.step_id == run.steps[107].id
        await pilot.press("N")
        await pilot.pause()
        assert tree.cursor_node.data.step_id == run.steps[7].id


@pytest.mark.asyncio
async def test_long_args_truncated_and_metrics_aligned(tmp_path: Path):
    run = Run(id="al", model_info="m", user_prompt="p")
    run.add_step(StepKind.tool, {"name": "t", "arguments": {"q": "z" * 300}}, cost=0.5)
    run.add_step(StepKind.think, {"text": "short"}, parent_ids=[], cost=0.5)
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        tree = app.query_one(StepTree)
        tree.load_run(run)
        await pilot.pause()
        nodes = list(tree._order)
        plains = [n.label.plain for n in nodes]
        assert "z" * 60 not in plains[0]
        assert "…" in plains[0]
        offsets = {p.index("$0.5000") for p in plains}
        assert len(offsets) == 1
