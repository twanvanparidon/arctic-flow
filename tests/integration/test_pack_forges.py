"""The github and bitbucket packs, run the way a flow runs them.

One file for both, because the claim worth testing is the one neither pack can make
alone: **the same flow, pointed at either pack, gets the same answer back**. A test
parametrised over `FORGES` runs it against both and compares against one expectation,
which is the only way to check that rather than assert it in a doc.

The API is a real loopback server (`support/forge.py`), routed by method and path exactly
as the tools request them. So a tool that sent the wrong verb, dropped a filter or forgot
the Authorization header fails here. The alternative, a stub answering anything, would
have passed for all three.

Nothing here reaches a real forge, and nothing needs a token. `$*_API_URL` is what points
the tools at the double, and it is the same variable GitHub Actions already exports.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from paths.config import CONFIG_FILE
from support import forge as forges
from support import repository, webserver
from support.forge import BRANCH, REPO, TOKEN, Forge
from support.outcome import Outcome, Runner

from .conftest import requires


@pytest.fixture(autouse=True)
def needs_shell_tools() -> None:
    requires("git", "jq", "curl")


@pytest.fixture(params=forges.FORGES, ids=lambda one: one.name)
def forge(request: pytest.FixtureRequest) -> Forge:
    return request.param


@pytest.fixture
def enabled(home: Path, forge: Forge) -> None:
    (home / ".arctic").mkdir(parents=True, exist_ok=True)
    (home / ".arctic" / CONFIG_FILE).write_text(f"packs:\n  - {forge.name}\n")


@pytest.fixture
def repo(tmp_path: Path, forge: Forge, enabled: None) -> Path:
    """A workspace that is a repository, on a branch, with the forge as its origin."""
    root = repository.initialise(tmp_path / "widget")
    repository.git(root, "checkout", "-q", "-b", BRANCH)
    repository.git(root, "remote", "add", "origin", forge.remote)
    repository.commit(root, "first commit", **{"a.txt": "one\n", ".gitignore": "flows/\n"})
    return root


@pytest.fixture
def api(forge: Forge, monkeypatch: pytest.MonkeyPatch) -> Iterator[webserver.Server]:
    """The double, with the pack pointed at it and holding a token."""
    with webserver.serving(forge.routes) as server:
        monkeypatch.setenv(forge.api_env, server.url(forge.prefix))
        monkeypatch.setenv(forge.token_env, TOKEN)
        yield server


def call(atf: Runner, project: Path, tool: str, **input_values: Any) -> Outcome:
    """Run one pack tool as the only step of a flow, and return the whole outcome."""
    definition = {
        "flow": "call",
        "start": "act",
        "steps": [{"id": "act", "tool": tool, "input": input_values}],
        "output": {"template": "{{ steps.act.text }}"},
    }
    (project / "flows").mkdir(exist_ok=True)
    (project / "flows" / "call.yaml").write_text(json.dumps(definition))
    return atf("--workspace", str(project), "run", "call")


def answered(result: Outcome) -> Any:
    assert result.code == 0, result.err
    return json.loads(result.out)


class TestBothPacksAnswerTheSameShape:
    """The reason these are two packs and not two vocabularies.

    Every field here is asserted against one expectation for both forges, so a change to
    either mapping that broke the other's spelling fails.
    """

    def test_the_whole_answer_matches_but_for_what_the_forge_cannot_know(
        self, atf: Runner, repo: Path, forge: Forge, api: webserver.Server
    ) -> None:
        answer = answered(call(atf, repo, forge.tool("status"), number=42))
        assert {key: answer[key] for key in forge.expected} == forge.expected

    def test_mergeable_is_the_one_field_that_differs(
        self, atf: Runner, repo: Path, forge: Forge, api: webserver.Server
    ) -> None:
        """Bitbucket does not report one without a dry-run merge, so it answers null
        rather than guessing. That is part of the contract, not a gap in it."""
        answer = answered(call(atf, repo, forge.tool("status"), number=42))
        assert answer["mergeable"] is forge.mergeable

    def test_a_review_is_counted_once_per_reviewer(
        self, atf: Runner, repo: Path, forge: Forge, api: webserver.Server
    ) -> None:
        """One person who requested a change and then approved is one approval, and a
        plain comment is not a verdict. Both doubles carry all three cases."""
        answer = answered(call(atf, repo, forge.tool("status"), number=42))
        assert answer["reviews"] == {"approved": 1, "changes_requested": 1}

    def test_a_check_that_stopped_is_a_failure_rather_than_a_pending(
        self, atf: Runner, repo: Path, forge: Forge, api: webserver.Server
    ) -> None:
        """A cancelled or timed-out build is not one still running. Calling it pending
        would make a flow wait for something that already stopped."""
        answer = answered(call(atf, repo, forge.tool("status"), number=42))
        assert answer["checks"]["pending"] == 1
        assert sorted(answer["checks"]["failing"]) == ["e2e", "test"]

    def test_the_answer_is_reachable_from_a_template(
        self, atf: Runner, repo: Path, forge: Forge, api: webserver.Server
    ) -> None:
        """JSON on stdout is what makes `{{ steps.pr.json.state }}` work, and that is why
        these tools return it rather than prose."""
        definition = {
            "flow": "gate",
            "start": "pr",
            "steps": [
                {
                    "id": "pr",
                    "tool": forge.tool("status"),
                    "input": {"number": 42},
                    "switch": "{{ this.json.checks.failure }}",
                    # `default`, not a `*` case: the engine matches a case whole and has
                    # no wildcard, so anything other than a clean run lands here.
                    "cases": {"0": ["green"]},
                    "default": ["red"],
                },
                {"id": "green", "tool": "arctic/glob", "input": {"pattern": "*.txt"}},
                {"id": "red", "tool": "arctic/glob", "input": {"pattern": "*.md"}},
            ],
            "output": {"template": "{{ steps.red.text }}"},
        }
        (repo / "flows").mkdir(exist_ok=True)
        (repo / "flows" / "gate.yaml").write_text(json.dumps(definition))
        result = atf("--workspace", str(repo), "run", "gate")
        assert result.code == 0, result.err


class TestFindingThePullRequest:
    def test_the_branch_identifies_it_when_no_number_is_given(
        self, atf: Runner, repo: Path, forge: Forge, api: webserver.Server
    ) -> None:
        """What makes "comment on the pull request for what I just pushed" one step."""
        assert answered(call(atf, repo, forge.tool("status")))["number"] == 42

    def test_a_number_that_does_not_exist_is_reported_as_not_found(
        self, atf: Runner, repo: Path, forge: Forge, api: webserver.Server
    ) -> None:
        result = call(atf, repo, forge.tool("status"), number=77)
        assert result.code != 0
        assert "not found" in result.err.lower()


class TestOpening:
    def test_it_returns_the_number_and_url_a_later_step_needs(
        self, atf: Runner, repo: Path, forge: Forge, api: webserver.Server
    ) -> None:
        answer = answered(call(atf, repo, forge.tool("open"), title="Add the git pack"))
        assert answer["number"] == 42
        assert answer["url"].startswith("https://")

    def test_the_target_is_read_from_the_repository_rather_than_assumed(
        self, atf: Runner, repo: Path, forge: Forge, api: webserver.Server
    ) -> None:
        """A repository whose default is `master` or `develop` is not unusual, and a pull
        request opened against a branch nobody reviews is a quiet failure."""
        call(atf, repo, forge.tool("open"), title="Add the git pack")
        kind = "repositories" if forge.name == "bitbucket" else "repos"
        asked = f"{forge.prefix}/{kind}/{REPO}"
        assert server_saw(api, "GET", asked)

    def test_the_source_defaults_to_the_branch_the_workspace_is_on(
        self, atf: Runner, repo: Path, forge: Forge, api: webserver.Server
    ) -> None:
        call(atf, repo, forge.tool("open"), title="Add the git pack")
        sent = posted_body(api)
        assert BRANCH in json.dumps(sent)

    def test_opening_onto_the_same_branch_is_refused(
        self, atf: Runner, repo: Path, forge: Forge, api: webserver.Server
    ) -> None:
        result = call(atf, repo, forge.tool("open"), title="x", source="main", target="main")
        assert result.code != 0
        assert "nothing to open" in result.err


class TestCommenting:
    def test_it_posts_the_body(
        self, atf: Runner, repo: Path, forge: Forge, api: webserver.Server
    ) -> None:
        answer = answered(call(atf, repo, forge.tool("comment"), body="looks good", number=42))
        assert answer["number"] == 42
        assert answer["id"] == 999

    def test_the_body_reaches_the_api(
        self, atf: Runner, repo: Path, forge: Forge, api: webserver.Server
    ) -> None:
        call(atf, repo, forge.tool("comment"), body="looks good", number=42)
        assert "looks good" in json.dumps(posted_body(api))

    def test_a_whitespace_only_body_is_refused_before_anything_is_posted(
        self, atf: Runner, repo: Path, forge: Forge, api: webserver.Server
    ) -> None:
        """Both forges would accept it and post an empty remark under someone's pull
        request, which is worse than a flow that failed."""
        result = call(atf, repo, forge.tool("comment"), body="   ", number=42)
        assert result.code != 0
        assert not [one for one in api.requests if one.method == "POST"]


class TestTheCredential:
    def test_the_token_is_sent_as_a_bearer_header(
        self, atf: Runner, repo: Path, forge: Forge, api: webserver.Server
    ) -> None:
        call(atf, repo, forge.tool("status"), number=42)
        assert api.requests[0].header("Authorization") == f"Bearer {TOKEN}"

    def test_a_missing_token_is_reported_before_the_request(
        self,
        atf: Runner,
        repo: Path,
        forge: Forge,
        api: webserver.Server,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv(forge.token_env)
        result = call(atf, repo, forge.tool("status"), number=42)
        assert result.code != 0
        assert forge.token_env in result.err
        assert api.requests == []

    def test_the_message_names_how_to_supply_it(
        self,
        atf: Runner,
        repo: Path,
        forge: Forge,
        api: webserver.Server,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv(forge.token_env)
        assert "secrets:" in call(atf, repo, forge.tool("status"), number=42).err

    @pytest.mark.parametrize("which", ["status", "open", "comment"])
    def test_no_tool_in_either_pack_can_be_granted_to_an_agent(
        self, atf: Runner, repo: Path, forge: Forge, which: str
    ) -> None:
        """The engine refuses to grant a tool that declares `secrets`, because nothing
        scopes a credential to one in-turn call. So opening a pull request or commenting
        is always a step the flow decided on, never something a model does mid-turn.

        Asserted here rather than trusted, because it is the whole security story these
        packs rest on and it lives in a spec field that is easy to drop."""
        agent = repo / "agents" / "reviewer"
        agent.mkdir(parents=True)
        (agent / "spec.json").write_text(
            json.dumps(
                {
                    "name": "reviewer",
                    "description": "reviews",
                    "adapter": "echo",
                    "unattended": True,
                    "tools": [forge.tool(which)],
                }
            )
        )
        (agent / "agent.md").write_text("review it\n")
        (repo / "flows").mkdir(exist_ok=True)
        (repo / "flows" / "granted.yaml").write_text(
            json.dumps(
                {
                    "flow": "granted",
                    "start": "go",
                    "steps": [{"id": "go", "agent": "reviewer", "prompt": "check"}],
                    "output": {"template": "{{ steps.go.text }}"},
                }
            )
        )
        result = atf("--workspace", str(repo), "lint", "granted")
        assert result.code != 0
        assert "secret" in (result.out + result.err)


class TestContainment:
    def test_a_repository_above_the_workspace_is_refused(
        self, atf: Runner, repo: Path, forge: Forge, api: webserver.Server
    ) -> None:
        """Otherwise a flow run in `myrepo/subproject` reads `myrepo`'s remote and
        comments on a pull request in the wrong repository."""
        inner = repo / "subproject"
        inner.mkdir()
        result = call(atf, inner, forge.tool("status"), number=42)
        assert result.code != 0
        assert "above the workspace" in result.err

    def test_naming_the_repository_skips_the_remote_entirely(
        self, atf: Runner, repo: Path, forge: Forge, api: webserver.Server
    ) -> None:
        """Which is the way out of the rule above: a flow that names `repo` needs no
        checkout at all."""
        inner = repo / "subproject"
        inner.mkdir()
        assert answered(call(atf, inner, forge.tool("status"), number=42, repo=REPO))["repo"] == (
            REPO
        )

    def test_the_other_forge_s_remote_is_refused(
        self, atf: Runner, repo: Path, forge: Forge, api: webserver.Server
    ) -> None:
        """A github tool that derived its repository from a bitbucket remote would build
        a plausible URL for the wrong service, and the 404 would explain nothing."""
        other = next(one for one in forges.FORGES if one.name != forge.name)
        repository.git(repo, "remote", "set-url", "origin", other.remote)
        result = call(atf, repo, forge.tool("status"), number=42)
        assert result.code != 0
        assert "remote points at" in result.err

    def test_a_bad_repo_parameter_is_refused(
        self, atf: Runner, repo: Path, forge: Forge, api: webserver.Server
    ) -> None:
        result = call(atf, repo, forge.tool("status"), number=42, repo="noslash")
        assert result.code != 0
        assert "repo" in result.err


class TestWhenTheApiWillNotAnswer:
    def test_a_host_that_cannot_be_reached_is_its_own_failure(
        self, atf: Runner, repo: Path, forge: Forge, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Distinct from an API that answered badly, because the fix is different: one is
        a network and the other is a request."""
        monkeypatch.setenv(forge.token_env, TOKEN)
        # Port 1 on loopback: nothing listens there, and it fails immediately rather than
        # waiting for a timeout the suite would have to sit through.
        monkeypatch.setenv(forge.api_env, "http://127.0.0.1:1")
        result = call(atf, repo, forge.tool("status"), number=42)
        assert result.code != 0
        assert "cannot reach" in result.err


def server_saw(server: webserver.Server, method: str, path: str) -> bool:
    return any(one.method == method and one.path == path for one in server.requests)


def posted_body(server: webserver.Server) -> Any:
    posted = next(one for one in server.requests if one.method == "POST")
    return posted.json
