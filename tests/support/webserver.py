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
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

# What a route answers with: a status, the body, and any headers worth setting.
Response = tuple[int, bytes, dict[str, str]]


def ok(body: bytes, **headers: str) -> Response:
    return 200, body, headers


def status(code: int, body: bytes = b"") -> Response:
    return code, body, {}


def redirect(to: str) -> Response:
    return 302, b"", {"Location": to}


@dataclass
class Server:
    """A running server. `url(path)` is what to hand the tool under test."""

    port: int
    seen: list[str] = field(default_factory=list)
    accepts: list[str | None] = field(default_factory=list)

    def url(self, path: str = "/") -> str:
        return f"http://127.0.0.1:{self.port}{path}"


@contextmanager
def serving(routes: dict[str, Callable[[], Response] | Response]) -> Iterator[Server]:
    """Serve `routes` until the block exits. A path not in it answers 404.

    A callable route is called per request, for the cases that have to differ between two
    requests to the same path.
    """
    running = Server(port=0)

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 (the name is BaseHTTPRequestHandler's)
            running.seen.append(self.path)
            running.accepts.append(self.headers.get("Accept"))
            route = routes.get(self.path)
            code, body, headers = (
                status(404, b"no such route")
                if route is None
                else (route() if callable(route) else route)
            )
            self.send_response(code)
            for name, value in headers.items():
                self.send_header(name, value)
            # Set explicitly, so the tool is reading a length rather than a closed socket.
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

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
