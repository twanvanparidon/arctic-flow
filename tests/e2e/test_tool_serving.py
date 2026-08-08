"""The binary re-invoking itself to serve an agent's tools.

An agent's tools reach its runtime over MCP, and the server is this program again:
`tool_server_command` builds the argv, the adapter hands it to the runtime to spawn, and
`atf mcp-serve` on the far end parses it. Building that argv is the one place the engine
has to know how it was installed, and it branches on being frozen:

    [sys.executable]                   frozen: the binary *is* the interpreter
    [sys.executable, <entry point>]    a checkout or an installed wheel

Only the first branch is reachable here. Every other suite runs unfrozen, so a binary that
named the wrong launcher would pass all of them and then fail at the first agent granted a
tool, reported by the model as the tool not working rather than as a broken command.

The turn is `adapters.echo`, which answers with the request instead of calling a model, so
the command the engine built can be read out of the flow's own output and then run. That is
the whole trick: no runtime is involved, and what gets executed is the argv the engine would
really have handed one.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from support import components as make
from support.mcp import HANDSHAKE, LIST, answered, call, frames, parsed
from support.outcome import Runner

from .conftest import reported_version

SERVED = "served from a frozen build"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """An agent granted one of two tools, and a flow that reports how it was called."""
    root = tmp_path / "project"
    root.mkdir()
    make.write_tool(root, "reader", script=make.prints(SERVED))
    make.write_tool(root, "ungranted", script=make.prints("never offered"))
    make.write_agent(root, "writer", tools=["reader"])
    make.write_flow(
        root,
        "probe",
        {
            "flow": "probe",
            "start": "ask",
            "steps": [{"id": "ask", "agent": "writer", "prompt": "!invocation"}],
            "output": {"template": "{{ steps.ask.text }}"},
        },
    )
    return root


def payload_of(atf: Runner, project: Path, flow: str = "probe") -> dict:
    """What the adapter was handed, out of an `!invocation` turn's own output."""
    result = atf("--workspace", str(project), "run", flow)
    assert result.code == 0, result.err
    return json.loads(result.out)["payload"]


@pytest.fixture
def server_argv(atf: Runner, project: Path, tmp_path: Path) -> list[str]:
    """The command the engine built, pointed at an events file that still exists.

    The engine's own lives in a temporary directory removed when the turn ends, so only
    the path is substituted. Everything else is what would have been spawned.
    """
    argv = payload_of(atf, project)["tool_server"]
    argv[argv.index("--events") + 1] = str(tmp_path / "calls.ndjson")
    return argv


def serve(argv: list[str], *messages: dict, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        argv,
        input=frames(*messages),
        capture_output=True,
        text=True,
        timeout=120,
        cwd=None if cwd is None else str(cwd),
    )


class TestTheCommandTheEngineBuilds:
    def test_the_launcher_is_the_binary_itself(
        self, atf: Runner, project: Path, binary: Path
    ) -> None:
        """The frozen branch, and the only place it is taken. Unfrozen there would be an
        entry point between these two, which is what makes the second assertion the test."""
        argv = payload_of(atf, project)["tool_server"]
        assert argv[0] == str(binary)
        assert argv[1] == "--workspace"

    def test_it_names_the_workspace_rather_than_trusting_a_cwd(
        self, atf: Runner, project: Path
    ) -> None:
        """The runtime starts the server from a directory of its own choosing, so the
        lookup and the directory tools run in have to be passed, not inherited."""
        argv = payload_of(atf, project)["tool_server"]
        assert argv[argv.index("--workspace") + 1] == str(project)

    def test_it_carries_only_the_tools_the_agent_was_granted(
        self, atf: Runner, project: Path
    ) -> None:
        argv = payload_of(atf, project)["tool_server"]
        granted = [argv[i + 1] for i, word in enumerate(argv) if word == "--tool"]
        assert granted == ["reader"]

    def test_a_turn_without_tools_is_sent_no_server_at_all(
        self, atf: Runner, project: Path
    ) -> None:
        """Granting nothing starts neither a file nor a thread, so an ordinary turn pays
        nothing for any of this."""
        make.write_agent(project, "plain")
        make.write_flow(
            project,
            "plain",
            {
                "flow": "plain",
                "start": "ask",
                "steps": [{"id": "ask", "agent": "plain", "prompt": "!invocation"}],
                "output": {"template": "{{ steps.ask.text }}"},
            },
        )
        assert "tool_server" not in payload_of(atf, project, "plain")


class TestServingFromTheFrozenBinary:
    def test_the_command_the_engine_built_offers_the_granted_tool(
        self, server_argv: list[str]
    ) -> None:
        """Run verbatim. Two processes agreeing on an argv one built and the other parses
        is the claim, and a frozen build is where it has never been checked."""
        served = serve(server_argv, HANDSHAKE, LIST)
        assert served.returncode == 0, served.stderr
        listing = answered(parsed(served.stdout), 2)
        assert [tool["name"] for tool in listing["result"]["tools"]] == ["reader"]

    def test_a_tool_that_was_not_granted_is_not_offered(self, server_argv: list[str]) -> None:
        """`ungranted` is resolvable in the same workspace, so this is the grant filtering
        rather than the lookup coming up empty."""
        listing = answered(parsed(serve(server_argv, HANDSHAKE, LIST).stdout), 2)
        assert "ungranted" not in [tool["name"] for tool in listing["result"]["tools"]]

    def test_calling_one_really_runs_it(self, server_argv: list[str]) -> None:
        """A frozen process, spawned by nothing it controls, spawning a tool of its own."""
        reply = answered(parsed(serve(server_argv, HANDSHAKE, call("reader")).stdout), 3)
        assert reply["result"]["isError"] is False
        assert reply["result"]["content"][0]["text"] == SERVED

    def test_it_resolves_tools_where_it_was_told_and_not_where_it_started(
        self, server_argv: list[str], tmp_path: Path
    ) -> None:
        """Started from an unrelated directory, the way a runtime would start it."""
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        reply = answered(
            parsed(serve(server_argv, HANDSHAKE, call("reader"), cwd=elsewhere).stdout), 3
        )
        assert reply["result"]["content"][0]["text"] == SERVED

    def test_stdout_carries_the_protocol_and_nothing_else(self, server_argv: list[str]) -> None:
        """One stray print anywhere below the server corrupts the framing, and a bundle can
        print things a checkout does not."""
        served = serve(server_argv, HANDSHAKE, LIST, call("reader"))
        for line in served.stdout.splitlines():
            json.loads(line)

    def test_the_server_is_this_build(self, server_argv: list[str], atf: Runner) -> None:
        """It reports its own version in the handshake, so a stale binary answering would
        not be mistaken for the one under test."""
        [greeting] = parsed(serve(server_argv, HANDSHAKE).stdout)
        assert greeting["result"]["serverInfo"]["version"] == reported_version(atf("--version").out)

    def test_a_call_is_reported_back_to_the_engine(
        self, server_argv: list[str], tmp_path: Path
    ) -> None:
        """A file rather than a pipe, because the server is two processes away and not the
        engine's child. Without it a turn that read nine files reports as one silent row."""
        serve(server_argv, HANDSHAKE, call("reader"))
        reported = parsed((tmp_path / "calls.ndjson").read_text())
        assert [(entry["tool"], entry["ok"]) for entry in reported] == [("reader", True)]
