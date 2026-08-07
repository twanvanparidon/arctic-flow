"""Serving the engine's tools to an agent's turn, over MCP on stdio.

An adapter cannot hand its runtime a Python function, so a tool an agent uses mid-turn has
to arrive over a protocol. The adapter spawns this; `engine.executor.invoke` still does the
running, so an in-turn call is checked and executed exactly as a step's would be.

**stdout carries the protocol and nothing else.** The usual rule with a sharper edge: one
stray `print` below this module corrupts the framing, and the symptom is a model reporting
that a tool does not work. Diagnostics go to stderr.

A front end rather than a command, because it reads a stream and writes one. It is spawned,
not typed: `atf mcp-serve` exists so an adapter has something to launch.

Four methods, which is all a tool server needs. Two behaviours here are not obvious and
both were confirmed against a live client:

  A notification carries no `id` and must never be answered, not even to refuse it.
  `notifications/initialized` is the one that arrives, and replying to it wedges the
  handshake.

  A bad tool call is a *result* with `isError`, not a JSON-RPC error. The model chose the
  arguments, so it is the party that can fix them; a protocol error tells it nothing.

**Only `tools/call` leaves the read loop.** A model asks for several things at once, and
running them in turn makes a turn take the sum rather than the longest. Everything else is
answered where it is read, `ping` above all: the spec says a receiver answers one promptly
and has no exemption for a busy server, so a queued ping would be the exact stall the pool
exists to remove. The cost is that a `tools/list` on a stalled filesystem blocks the loop.

That makes two things load-bearing. Writes are serialised, or two replies interleave and
the framing is gone. And a worker carries its own guard, because an exception inside a
future is captured by the future: nothing would be sent, and the model would wait for a
reply that is never coming.

**A namespaced tool loses its slash here.** `common/read_file` is offered as
`common__read_file`, because a client builds `mcp__atf__<tool>` out of the name and a slash
is not legal in one. `serve` keeps the mapping and looks a call up in it rather than undoing
the spelling, since `git__commit` is a name a tool directory can have of its own.

**A cancelled call is stopped, not just dropped.** `notifications/cancelled` is the client
withdrawing a request: its tool's process tree is signalled and no reply is sent at all,
which is what the spec asks of a receiver. It is handled on the read loop rather than
submitted, because a pooled cancel would queue behind the very call it cancels. One entry
per call in flight settles the race between the worker about to answer and the cancel:
whichever claims it acts, so a call is never answered twice and a withdrawn one is never
answered at all.

No secret is in reach: `validate()` refuses a step that both declares `secrets` and runs an
agent granted tools, so the environment this inherits carries none.
"""

from __future__ import annotations

import json
import sys
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any

import commands
from cli import branding
from paths.resolver import Paths, flat_name

# What this speaks when a client states no preference. A client's own revision is echoed
# back instead of negotiated down: these three methods are unchanged across every revision
# that exists, so refusing one we would have satisfied anyway buys nothing.
PROTOCOL_VERSION = "2025-06-18"

# The name the tools are exposed under. A client prefixes it onto every tool, so a model
# sees `mcp__atf__read_file`, and an adapter has to build the same string to allow it.
SERVER_NAME = "atf"

PARSE_ERROR = -32700
METHOD_NOT_FOUND = -32601
INTERNAL_ERROR = -32603

# How many tool calls run at once. A model can ask for a dozen in one block, and each is a
# process tree: read_file is bash running jq, awk and realpath. This bounds those trees, not
# requests. Past it a call waits for a worker, and its own timeout clock does not start
# until it does, so nothing times out for having queued.
#
# A fixed number rather than one derived from the machine, so a laptop and CI hold the same
# many open processes and a test means the same thing on both.
MAX_CONCURRENT_CALLS = 8

# One reply per line, and a reply larger than the stdout buffer is flushed in pieces, so two
# workers' bytes interleave without this. `sys.stdout` is a TextIOWrapper, which the io docs
# state is not thread-safe; only the binary layer beneath it takes a lock of its own.
_writing = threading.Lock()

# Not for the append, which O_APPEND already makes atomic for a line this short. It is for
# what reads the file: `ToolCallReporter` rewinds on a line it cannot parse and waits for
# the rest, so two writes that interleave produce a line that never becomes valid and every
# later call in that turn stops being reported. Silently, which is the part worth locking.
_reporting = threading.Lock()


def _send(message: dict[str, Any]) -> None:
    # Serialised outside the lock: it is the slow part, it needs no protection, and a large
    # tool result must not hold up a ping.
    line = json.dumps(message) + "\n"
    with _writing:
        sys.stdout.write(line)
        sys.stdout.flush()


def _result(message_id: Any, result: dict[str, Any]) -> None:
    _send({"jsonrpc": "2.0", "id": message_id, "result": result})


def _error(message_id: Any, code: int, message: str) -> None:
    _send({"jsonrpc": "2.0", "id": message_id, "error": {"code": code, "message": message}})


def _content(text: str, *, is_error: bool) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def _initialize(params: dict[str, Any]) -> dict[str, Any]:
    requested = params.get("protocolVersion")
    return {
        "protocolVersion": requested if isinstance(requested, str) else PROTOCOL_VERSION,
        "capabilities": {"tools": {}},
        "serverInfo": {"name": SERVER_NAME, "version": branding.__version__},
    }


def _tools_list(exposed: dict[str, str], paths: Paths) -> dict[str, Any]:
    """The granted tools, each under the name a model may call it by.

    `ToolDescription.name` is the name the tool was looked up by, so flattening it here
    lands on a key of `exposed` by construction. Pairing the two lists by position would
    have said the same thing while depending on an order neither one promises.
    """
    return {
        "tools": [
            {
                "name": flat_name(tool.name),
                "description": tool.description,
                "inputSchema": tool.input_schema,
            }
            for tool in commands.describe_tools(list(exposed.values()), paths)
        ]
    }


def _report(events: Path | None, call: commands.ToolCall) -> None:
    """Tell the engine a tool ran, so a call inside a turn is not invisible to it.

    Appended rather than written, because the engine is reading the same file while this
    goes on. A failure to report is swallowed: the turn is what the caller asked for, and
    losing a progress line is not worth ending it.
    """
    if events is None:
        return
    entry: dict[str, Any] = {"tool": call.name, "ok": call.ok, "ms": call.ms}
    if call.cancelled:
        # Only where it means something, the way `attempts` is. Without it a call the
        # model abandoned reads on screen as a tool that broke.
        entry["cancelled"] = True
    line = json.dumps(entry) + "\n"
    try:
        with _reporting, events.open("a") as stream:
            stream.write(line)
    except OSError:
        pass


def _tools_call(
    params: dict[str, Any],
    exposed: dict[str, str],
    paths: Paths,
    events: Path | None,
    cancel: threading.Event,
) -> dict[str, Any]:
    requested = params.get("name")
    # Through the mapping, never by unflattening the string: `common__read_file` is what a
    # namespaced grant is called here, and it is also what a tool directory of that literal
    # name would be called.
    name = exposed.get(requested) if isinstance(requested, str) else None
    if name is None:
        # Reported to the model rather than raised: it picked the name, and picking another
        # is a recovery. Naming what it may call is what makes that possible.
        return _content(
            f"unknown tool '{requested}'. Available: {', '.join(exposed) or 'none'}", is_error=True
        )

    call = commands.call_tool(name, params.get("arguments") or {}, paths, cancel=cancel)
    _report(events, call)
    if call.ok:
        return _content(call.text, is_error=False)
    return _content(call.error or f"{call.name} failed without saying why", is_error=True)


def _answer_here(
    message_id: Any,
    method: str | None,
    params: dict[str, Any],
    exposed: dict[str, str],
    paths: Paths,
) -> None:
    """The methods answered on the read loop, because none of them waits on anything."""
    if method == "initialize":
        _result(message_id, _initialize(params))
    elif method == "tools/list":
        _result(message_id, _tools_list(exposed, paths))
    elif method == "ping":
        # An empty result is the whole answer. It is also the one thing that proves the
        # loop is still reading while tools run, which is why it is not in the pool.
        _result(message_id, {})
    else:
        _error(message_id, METHOD_NOT_FOUND, f"unsupported method '{method}'")


def serve(names: list[str], paths: Paths, events: Path | None = None) -> int:
    """Answer frames until stdin closes."""
    # What each granted tool is called over MCP, mapped back to the name the engine resolves
    # it by. A namespaced tool cannot keep its slash: a client builds `mcp__atf__<tool>` out
    # of this, and a slash is not legal in a tool name there.
    #
    # Built once and kept, rather than undone per call. `engine.executor.validate` refuses a
    # grant where two names flatten onto one, so this stays one-to-one.
    exposed = {flat_name(name): name for name in names}

    # One entry per call in flight. Two threads race for every one: the worker about to
    # answer it, and the loop handling a cancel for it. Whichever pops the entry acts and
    # the other does nothing, so a call is never answered twice and a cancelled one is
    # never answered at all.
    #
    # A cancel arriving after the worker popped finds nothing and is dropped, so a client
    # can still get a reply to a request it withdrew. That is the race the spec says both
    # sides have to handle, and the client's half of it is to ignore the reply.
    inflight: dict[Any, threading.Event] = {}
    settled = threading.Lock()

    def answer(message_id: Any, params: dict[str, Any], cancel: threading.Event) -> None:
        """Run one call and reply, unless a cancel got here first.

        Its own guard, because the loop's cannot see this far: an exception inside a future
        is kept by the future rather than raised, so without this nothing would be sent and
        the model would wait for a reply that never comes.
        """
        try:
            reply: dict[str, Any] = {"result": _tools_call(params, exposed, paths, events, cancel)}
        except Exception as exc:
            reply = {"error": {"code": INTERNAL_ERROR, "message": f"{type(exc).__name__}: {exc}"}}
        with settled:
            claimed = inflight.pop(message_id, None) is not None
        if claimed:
            _send({"jsonrpc": "2.0", "id": message_id, **reply})

    submitted: list[Future[None]] = []
    with ThreadPoolExecutor(
        max_workers=MAX_CONCURRENT_CALLS, thread_name_prefix="atf-call"
    ) as pool:
        for raw in sys.stdin:
            line = raw.strip()
            if not line:
                continue

            try:
                message = json.loads(line)
            except json.JSONDecodeError as exc:
                _error(None, PARSE_ERROR, f"not JSON: {exc}")
                continue

            message_id = message.get("id")
            method = message.get("method")
            params = message.get("params") or {}

            if message_id is None:
                # The method is read before the exit because one notification is not inert.
                # `notifications/cancelled` is the client withdrawing a request, and acting
                # on it is still not answering it, which is what the exit protects.
                #
                # Handled here rather than submitted: a pooled cancel would queue behind the
                # very call it cancels, and on a full pool that never resolves.
                if method == "notifications/cancelled":
                    with settled:
                        stop = inflight.pop(params.get("requestId"), None)
                    if stop is not None:
                        stop.set()
                continue

            try:
                if method == "tools/call":
                    # Registered before submit, so a cancel for a call still waiting for a
                    # worker is caught by spawn's check and never starts a process.
                    stop = threading.Event()
                    with settled:
                        inflight[message_id] = stop
                    submitted.append(pool.submit(answer, message_id, params, stop))
                else:
                    _answer_here(message_id, method, params, exposed, paths)
            except Exception as exc:
                # One bad frame must not end the session. Letting this escape would close
                # stdout mid-turn, and the client would report a transport failure that says
                # nothing about the cause.
                _error(message_id, INTERNAL_ERROR, f"{type(exc).__name__}: {exc}")

    # Leaving the pool waits for what is still running, so a client that closed stdin still
    # gets the answer to a call it had already sent. Every worker is bounded by its tool's
    # own timeout, so this cannot wait forever.
    for future in submitted:
        # A worker answers its own exceptions; what reaches here is a stream that broke
        # under it. Raised rather than dropped, so a server whose stdout is gone still
        # exits non-zero instead of reporting success.
        future.result()
    return 0
