"""The dataclasses a command hands back.

They are a contract, but a contract made of stored values, and asserting that a frozen
dataclass is frozen tests Python rather than this repo. So only the two that compute
something are here. `cost_usd` is summed in one place because a tool-only flow reports
`None` per step, and the `or 0` that handles it was being forgotten in one front end out of
every few.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from commands.results import FlowPlan, RunResult
from paths.resolver import Paths


def run_result(trace: list[dict[str, object]]) -> RunResult:
    return RunResult(flow="f", path=Path("f.yaml"), display="./f.yaml", output="", trace=trace)


class TestRunResultCost:
    def test_adds_up_what_the_steps_reported(self) -> None:
        assert run_result([{"cost_usd": 0.01}, {"cost_usd": 0.02}]).cost_usd == pytest.approx(0.03)

    def test_a_step_that_cost_nothing_counts_as_nothing(self) -> None:
        """Every step of a tool-only flow reports None, and that is not an error."""
        assert run_result([{"cost_usd": None}, {"cost_usd": 0.01}]).cost_usd == pytest.approx(0.01)

    def test_a_step_that_did_not_mention_cost_counts_as_nothing(self) -> None:
        assert run_result([{"step": "a"}]).cost_usd == 0

    def test_a_run_with_no_trace_cost_nothing(self) -> None:
        assert run_result([]).cost_usd == 0


class TestFlowPlan:
    def test_the_name_is_the_flows_own_rather_than_its_filename(self, paths: Paths) -> None:
        plan = FlowPlan(
            paths=paths,
            definition={"flow": "sign_release"},
            path=Path("flows/signing.yaml"),
            display="./flows/signing.yaml",
        )
        assert plan.name == "sign_release"
