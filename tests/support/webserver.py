"""A real HTTP server on a loopback port, for testing a tool that fetches one.

A fake in the sense TESTING.md uses: a working implementation, simplified. It really binds
a socket, really parses the request, and really writes a response, so a tool that builds a
wrong request or mishandles a status fails against it. A stub returning a canned body could
not have failed for either reason.

Loopback and port 0, so the suite needs no network and two tests can never collide on a
port. Routes are supplied per test rather than fixed here, because what each one is asking
about is a different response.
"""

from __future__ import annotations

import http.server
import json
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

# What a route answers with: a status, the body, and any headers worth setting.
Response = tuple[int, bytes, dict[str, str]]


def ok(body: bytes, **headers: str) -> Response:
    return 200, body, headers


def json_ok(document: Any, code: int = 200) -> Response:
    """A JSON response, which is what an API double answers with almost every time."""
    return code, json.dumps(document).encode(), {"Content-Type": "application/json"}


def status(code: int, body: bytes = b"") -> Response:
    return code, body, {}


def redirect(to: str) -> Response:
    return 302, b"", {"Location": to}


@dataclass(frozen=True)
class Request:
    """One request as it arrived, for the assertions only the wire can settle.

    Which method, which path, whether the credential was sent and in what form, and what
    was in the body. A tool that built the right URL and forgot the Authorization header
    is the failure this exists to catch, and nothing below the socket can see it.
    """

    method: str
    path: str
    headers: dict[str, str]
    body: bytes

    @property
    def json(self) -> Any:
        return json.loads(self.body) if self.body else None

    def header(self, name: str) -> str | None:
        """Case-insensitively, because a header name on the wire is not case sensitive."""
        return next(
            (value for key, value in self.headers.items() if key.lower() == name.lower()), None
        )


@dataclass
class Server:
    """A running server. `url(path)` is what to hand the tool under test."""

    port: int
    seen: list[str] = field(default_factory=list)
    accepts: list[str | None] = field(default_factory=list)
    requests: list[Request] = field(default_factory=list)

    def url(self, path: str = "/") -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def sent(self, method: str, path: str) -> Request | None:
        """The first request that matched, or None. For asserting on what went out."""
        return next(
            (one for one in self.requests if one.method == method and one.path == path), None
        )


@contextmanager
def serving(routes: dict[str, Callable[[], Response] | Response]) -> Iterator[Server]:
    """Serve `routes` until the block exits. A path not in it answers 404.

    A route key is either a path (`/thing`), which answers any method, or a method and a
    path (`POST /thing`), which answers only that one. The second form is what an API
    double needs: reading a pull request and commenting on it are the same path, and a
    tool that sent the wrong verb should fail rather than get the other answer.

    A callable route is called per request, for the cases that have to differ between two
    requests to the same path.
    """
    running = Server(port=0)

    class Handler(http.server.BaseHTTPRequestHandler):
        def _answer(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""

            running.seen.append(self.path)
            running.accepts.append(self.headers.get("Accept"))
            running.requests.append(
                Request(
                    method=self.command,
                    path=self.path,
                    headers=dict(self.headers.items()),
                    body=body,
                )
            )

            # The qualified key first, so `POST /x` beats a bare `/x` that would otherwise
            # answer both verbs.
            route = routes.get(f"{self.command} {self.path}", routes.get(self.path))
            code, answer, headers = (
                status(404, b"no such route")
                if route is None
                else (route() if callable(route) else route)
            )
            self.send_response(code)
            for name, value in headers.items():
                self.send_header(name, value)
            # Set explicitly, so the tool is reading a length rather than a closed socket.
            self.send_header("Content-Length", str(len(answer)))
            self.end_headers()
            self.wfile.write(answer)

        def do_GET(self) -> None:  # noqa: N802 (the name is BaseHTTPRequestHandler's)
            self._answer()

        def do_POST(self) -> None:  # noqa: N802 (the name is BaseHTTPRequestHandler's)
            self._answer()

        def log_message(self, *_args: object) -> None:
            """Silence. The default writes every request to stderr, which pytest captures
            and prints beside a failure that has nothing to do with it."""

    httpd = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    running.port = httpd.server_port
    thread = threading.Thread(target=httpd.serve_forever, daemon=True, name="atf-test-http")
    thread.start()
    try:
        yield running
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
