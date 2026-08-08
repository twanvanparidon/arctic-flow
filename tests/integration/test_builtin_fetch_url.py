"""The shipped `fetch_url`, run the way a flow runs it, against a real local server.

`support.webserver` binds a loopback port rather than reaching the internet, so these are
deterministic and need no network. It is a fake and not a stub on purpose: it parses the
request, so a tool sending a wrong header or mishandling a redirect fails here.

The claim that matters most is that the body arrives undecorated. A flow templates this
result into a prompt or a file, and anything the tool added would have to be stripped again
before a JSON response could be parsed. Truncation is the single exception, and it says so
because it breaks exactly that property.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from support import webserver

from .conftest import Runner, requires


@pytest.fixture(autouse=True)
def needs_shell_tools() -> None:
    requires("jq", "curl")


def fetch(atf: Runner, project: Path, **input_values: Any) -> Any:
    definition = {
        "flow": "get",
        "start": "fetch",
        "steps": [{"id": "fetch", "tool": "common/fetch_url", "input": input_values}],
        "output": {"template": "{{ steps.fetch.text }}"},
    }
    (project / "flows").mkdir(exist_ok=True)
    (project / "flows" / "get.yaml").write_text(json.dumps(definition))
    return atf("--workspace", str(project), "run", "get")


def body_of(result: Any) -> str:
    """The body, without the newline the printer adds.

    `run_flow` strips its rendered output and `cli/dispatch.py` terminates the line, so a
    body with no trailing newline of its own still arrives with one. That belongs to the
    front end rather than to the tool.
    """
    return result.out.rstrip("\n")


class TestFetching:
    def test_the_body_comes_back(self, atf: Runner, project: Path) -> None:
        with webserver.serving({"/": webserver.ok(b"hello\n")}) as server:
            assert body_of(fetch(atf, project, url=server.url())) == "hello"

    def test_a_json_response_is_still_parseable(self, atf: Runner, project: Path) -> None:
        """Nothing is prefixed and no status line is added, so the result is the document."""
        body = b'{"version":"2.1.224"}'
        with webserver.serving({"/v": webserver.ok(body)}) as server:
            out = fetch(atf, project, url=server.url("/v")).out
        assert json.loads(out) == {"version": "2.1.224"}

    def test_the_accept_header_is_sent_as_given(self, atf: Runner, project: Path) -> None:
        with webserver.serving({"/": webserver.ok(b"x")}) as server:
            fetch(atf, project, url=server.url(), accept="application/json")
            assert server.accepts == ["application/json"]

    def test_a_redirect_is_followed(self, atf: Runner, project: Path) -> None:
        routes = {"/old": webserver.redirect("/new"), "/new": webserver.ok(b"arrived")}
        with webserver.serving(routes) as server:
            assert body_of(fetch(atf, project, url=server.url("/old"))) == "arrived"


class TestTruncation:
    def test_a_long_body_is_cut_at_max_bytes(self, atf: Runner, project: Path) -> None:
        with webserver.serving({"/": webserver.ok(b"x" * 500)}) as server:
            out = fetch(atf, project, url=server.url(), max_bytes=20).out
        # The first line only. The notice below it mentions max_bytes, whose own "x" would
        # be counted by a tally over the whole result.
        assert out.splitlines()[0] == "x" * 20

    def test_it_says_it_truncated(self, atf: Runner, project: Path) -> None:
        """Silence here would hand a model a JSON document that stopped mid-object with
        nothing to say it was incomplete."""
        with webserver.serving({"/": webserver.ok(b"x" * 500)}) as server:
            out = fetch(atf, project, url=server.url(), max_bytes=20).out
        assert "showing 20 of 500 bytes" in out

    def test_a_body_that_fits_gets_no_notice(self, atf: Runner, project: Path) -> None:
        with webserver.serving({"/": webserver.ok(b"short")}) as server:
            assert body_of(fetch(atf, project, url=server.url(), max_bytes=500)) == "short"


class TestFailures:
    def test_an_http_error_fails_the_step(self, atf: Runner, project: Path) -> None:
        with webserver.serving({"/gone": webserver.status(404, b"not here")}) as server:
            result = fetch(atf, project, url=server.url("/gone"))
        assert result.code != 0
        assert "404" in result.err

    def test_the_error_body_is_in_the_message(self, atf: Runner, project: Path) -> None:
        """A bare status leaves a model with nothing to act on. The server usually says why."""
        with webserver.serving(
            {"/gone": webserver.status(422, b"field 'name' is required")}
        ) as srv:
            result = fetch(atf, project, url=srv.url("/gone"))
        assert "field 'name' is required" in result.err

    def test_a_host_that_does_not_resolve_carries_curls_own_words(
        self, atf: Runner, project: Path
    ) -> None:
        """`.invalid` is reserved by RFC 2606 and never resolves, so this needs no network."""
        result = fetch(atf, project, url="http://nothing.invalid/", timeout_seconds=5)
        assert result.code != 0
        assert "resolve" in result.err


class TestSchemesItRefuses:
    @pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.com/x", "example.com"])
    def test_anything_but_http_is_refused(self, atf: Runner, project: Path, url: str) -> None:
        """Refused by `input_schema`'s pattern before the tool runs, and again by the script,
        because a scheme is how a fetch would turn into a local file read."""
        assert fetch(atf, project, url=url).code != 0

    def test_a_redirect_to_another_scheme_is_not_followed(self, atf: Runner, project: Path) -> None:
        """--proto-redir. Without it the check above is only a check on the first hop."""
        with webserver.serving({"/out": webserver.redirect("file:///etc/passwd")}) as server:
            result = fetch(atf, project, url=server.url("/out"))
        assert result.code != 0
        assert "root:" not in result.out
