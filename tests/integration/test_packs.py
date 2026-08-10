"""The pack switch, through the CLI.

Not what a pack's tools do, which is `test_pack_git.py`. This is the mechanism: a set of
components that ships with the engine and resolves only once `~/.arctic/config.yaml` names
it, and the three places a person meets that fact.

The switch is the whole feature, so the tests that matter are the ones about being off. A
pack that is on is an ordinary tool lookup and has nothing new to prove.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from paths.config import CONFIG_FILE
from support.outcome import Runner

# One tool out of the pack that ships, named once so a rename is one edit.
A_PACK_TOOL = "arctic/git/log"

# What `configure` hands back: write this config, get the file it went into.
Configure = Callable[[str], Path]


@pytest.fixture
def configure(home: Path) -> Configure:
    def write(text: str) -> Path:
        (home / ".arctic").mkdir(parents=True, exist_ok=True)
        path = home / ".arctic" / CONFIG_FILE
        path.write_text(text)
        return path

    return write


@pytest.fixture
def flow_using_the_pack(project: Path) -> Path:
    definition = {
        "flow": "history",
        "start": "look",
        "steps": [{"id": "look", "tool": A_PACK_TOOL, "input": {"max_commits": 1}}],
        "output": {"template": "{{ steps.look.text }}"},
    }
    (project / "flows").mkdir(exist_ok=True)
    (project / "flows" / "history.yaml").write_text(json.dumps(definition))
    return project


class TestListing:
    def test_every_pack_is_listed_whether_it_is_on_or_not(self, atf: Runner) -> None:
        """An off pack's components are absent from everything above this section, so
        without the line they are indistinguishable from components that do not exist."""
        assert "git" in atf("list").out

    def test_an_off_pack_says_off(self, atf: Runner) -> None:
        listing = atf("list").out
        packs = listing[listing.index("packs:") :]
        assert "off" in packs

    def test_an_on_pack_says_on(self, atf: Runner, configure: Configure) -> None:
        configure("packs:\n  - git\n")
        listing = atf("list").out
        packs = listing[listing.index("packs:") :]
        assert "off" not in packs

    def test_a_pack_tool_is_absent_while_it_is_off(self, atf: Runner) -> None:
        assert A_PACK_TOOL not in atf("list").out

    def test_a_pack_tool_appears_once_it_is_on(self, atf: Runner, configure: Configure) -> None:
        configure("packs:\n  - git\n")
        assert A_PACK_TOOL in atf("list").out


class TestAFlowNamingAPackThatIsOff:
    def test_lint_refuses_it(self, atf: Runner, flow_using_the_pack: Path) -> None:
        """At lint time and not only at run time, so the failure arrives before a flow has
        spent anything on the steps ahead of it."""
        result = atf("--workspace", str(flow_using_the_pack), "lint")
        assert result.code != 0

    def test_lint_names_the_pack(self, atf: Runner, flow_using_the_pack: Path) -> None:
        result = atf("--workspace", str(flow_using_the_pack), "lint")
        assert "'git' pack" in result.out + result.err

    def test_running_it_names_the_line_to_add(self, atf: Runner, flow_using_the_pack: Path) -> None:
        result = atf("--workspace", str(flow_using_the_pack), "run", "history")
        assert result.code != 0
        assert "packs: [git]" in result.err

    def test_the_message_names_the_file_to_add_it_to(
        self, atf: Runner, flow_using_the_pack: Path
    ) -> None:
        result = atf("--workspace", str(flow_using_the_pack), "run", "history")
        assert CONFIG_FILE in result.err


class TestAnUnknownPack:
    def test_it_stops_the_command(self, atf: Runner, configure: Configure) -> None:
        """Every command, not the one that would have needed the pack: a config that
        cannot be honoured is the same kind of failure as one that cannot be parsed."""
        configure("packs:\n  - gti\n")
        assert atf("list").code != 0

    def test_it_names_what_ships(self, atf: Runner, configure: Configure) -> None:
        configure("packs:\n  - gti\n")
        assert "git" in atf("list").err


class TestInit:
    def test_the_written_config_documents_the_key(self, atf: Runner, home: Path) -> None:
        """The scaffold is the documentation for this file, so a key it does not mention
        is one nobody discovers."""
        atf("init")
        assert "packs" in (home / ".arctic" / CONFIG_FILE).read_text()

    def test_the_written_config_is_accepted_by_the_reader_that_reads_it(self, atf: Runner) -> None:
        """`init` writes it and every later command parses it, so a scaffold with a key
        the schema refuses would break the next command a new user ran."""
        atf("init")
        assert atf("list").code == 0
