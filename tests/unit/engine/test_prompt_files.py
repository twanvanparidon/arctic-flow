"""`prompt_file`: a step's prompt read from `prompts/` beside the flow.

Resolved by `load_flow`, so by the time anything else looks at a step there is one kind of
prompt and a missing file has already failed. That is what these pin: the text arrives
where an inline prompt would have been, and every way of naming a file that is not a file
beside the flow is refused before a step could run.

Real files under `tmp_path`, because what is being tested is reading one.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import support.components as make
from engine.executor import FlowError, load_flow

PROMPT = "Review {{ inputs.path }}.\n"


def flow_yaml(step: str) -> str:
    return (
        "flow: demo\n"
        "start: report\n"
        "inputs:\n"
        "  path: {type: string, required: true}\n"
        "steps:\n"
        f"{step}"
    )


AGENT_STEP = "  - id: report\n    agent: reporter\n"


class TestReadingAPromptFile:
    def test_the_file_becomes_the_step_s_prompt(self, tmp_path: Path) -> None:
        flow = make.write_text_flow(
            tmp_path, "demo", flow_yaml(AGENT_STEP + "    prompt_file: report\n"), bundle=True
        )
        make.write_prompt_file(flow, "report", PROMPT)
        assert load_flow(flow)["steps"][0]["prompt"] == PROMPT

    def test_a_bundle_reads_from_its_own_directory(self, tmp_path: Path) -> None:
        """Which is what the bundle layout is for: the prompts belong to this flow, not to
        every flow in `flows/`."""
        flow = make.write_text_flow(
            tmp_path, "demo", flow_yaml(AGENT_STEP + "    prompt_file: report\n"), bundle=True
        )
        make.write_prompt_file(flow, "report", PROMPT)
        assert (tmp_path / "flows" / "demo" / "prompts" / "report.md").is_file()

    def test_a_flat_flow_reads_from_prompts_beside_it(self, tmp_path: Path) -> None:
        """One rule, `prompts/` next to the flow file, so the older layout is not left out."""
        flow = make.write_text_flow(
            tmp_path, "demo", flow_yaml(AGENT_STEP + "    prompt_file: report\n")
        )
        make.write_prompt_file(flow, "report", PROMPT)
        assert load_flow(flow)["steps"][0]["prompt"] == PROMPT

    def test_a_namespaced_prompt_reads_from_a_subdirectory(self, tmp_path: Path) -> None:
        flow = make.write_text_flow(
            tmp_path,
            "demo",
            flow_yaml(AGENT_STEP + "    prompt_file: shared/report\n"),
            bundle=True,
        )
        make.write_prompt_file(flow, "shared/report", PROMPT)
        assert load_flow(flow)["steps"][0]["prompt"] == PROMPT

    def test_the_file_is_read_verbatim(self, tmp_path: Path) -> None:
        """Templates are rendered when the step runs, not when the flow is loaded, so what
        is stored is still a template."""
        flow = make.write_text_flow(
            tmp_path, "demo", flow_yaml(AGENT_STEP + "    prompt_file: report\n"), bundle=True
        )
        make.write_prompt_file(flow, "report", "  leading space\n\n{{ inputs.path }}\n")
        assert load_flow(flow)["steps"][0]["prompt"] == "  leading space\n\n{{ inputs.path }}\n"

    def test_a_step_without_one_is_untouched(self, tmp_path: Path) -> None:
        flow = make.write_text_flow(
            tmp_path, "demo", flow_yaml(AGENT_STEP + "    prompt: inline\n")
        )
        assert load_flow(flow)["steps"][0]["prompt"] == "inline"


class TestRefusals:
    def refuse(self, tmp_path: Path, step: str, match: str, *, bundle: bool = True) -> None:
        flow = make.write_text_flow(tmp_path, "demo", flow_yaml(step), bundle=bundle)
        with pytest.raises(FlowError, match=match):
            load_flow(flow)

    def test_a_file_that_is_not_there_is_refused(self, tmp_path: Path) -> None:
        """Named as the flow spells it. The absolute path is inside whatever directory the
        flow resolved out of, which is not what anyone would open."""
        self.refuse(tmp_path, AGENT_STEP + "    prompt_file: absent\n", "prompts/absent.md")

    def test_both_spellings_at_once_is_refused(self, tmp_path: Path) -> None:
        """One of them would be the prompt and the other would be dead text in the repo."""
        self.refuse(
            tmp_path,
            AGENT_STEP + "    prompt: inline\n    prompt_file: report\n",
            "both 'prompt' and 'prompt_file'",
        )

    @pytest.mark.parametrize(
        "reference", ["../secret", "shared/../../secret", "/etc/passwd", "./report"]
    )
    def test_a_name_that_leaves_the_prompts_directory_is_refused(
        self, tmp_path: Path, reference: str
    ) -> None:
        """Same rule as a component name, and for the same reason: joining it on resolves,
        and a flow can arrive by clone."""
        self.refuse(tmp_path, AGENT_STEP + f"    prompt_file: {reference}\n", "is not a name")

    @pytest.mark.parametrize("reference", ["3", "true", '""', "[]", "{}"])
    def test_something_that_is_not_a_name_is_refused(self, tmp_path: Path, reference: str) -> None:
        self.refuse(tmp_path, AGENT_STEP + f"    prompt_file: {reference}\n", "must be a name")

    def test_the_step_is_named_even_before_the_flow_is_validated(self, tmp_path: Path) -> None:
        """`load_flow` runs first, so its message is the only one that can point at the step."""
        self.refuse(tmp_path, AGENT_STEP + "    prompt_file: absent\n", "step 'report'")
