"""Run one LLM turn through the Claude Code CLI.

The CLI is the runtime, so this spawns a process, but no longer a shell to build the
arguments. `jq` became `json` and took a class of bug with it: jq's `//` treats `false` as
absent, so an explicit `isolate: false` was read as `true`.

Two decisions that look like defaults but are not:

  **The engine's tools, not the CLI's.** `--tools` is always `""`, disabling the CLI's own
  built-in set. An agent's tools are the engine's, served over MCP by `atf mcp-serve`, so
  one agent spec means the same thing under a different adapter. The two are independent:
  with `--tools ""` an MCP tool is still callable.

  **Isolated config.** `isolate` defaults true, so the host's CLAUDE.md, skills, plugins
  and hooks do not silently join the turn and the same request behaves the same on another
  machine. How that is spelled depends on whether the turn has tools; see `build_args`.
  Authentication is unaffected either way.

Flags were verified against CLI 2.1.224 and move between releases: 2.1.222 has no
`--max-turns`, and `speed: fast` was removed from Opus 4.7. Check `claude --help` before
adding a parameter, and move VERIFIED_CLI_VERSION when you do.

Always set `model`. The CLI's configured default is the per-machine dependency `isolate`
exists to remove, and a latency risk: on one machine it stalled ~100s then failed, where
`--model sonnet` answered in 5s.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from typing import Any

from adapters.errors import AdapterProtocolError, AdapterRunFailed, AdapterUnavailable
from paths.resolver import flat_name

NAME = "claude_code"
DESCRIPTION = "Run one LLM turn through the Claude Code CLI and return a normalised envelope."
VERIFIED_CLI_VERSION = "2.1.224"

BINARY = "claude"
TIMEOUT_SECONDS = 600

# What the CLI is told to call the engine's tool server. It prefixes this onto every tool
# the server offers, so the same string has to be rebuilt to allow one. `cli.mcp_server`
# reports the same name in its handshake; they are two ends of one convention.
MCP_SERVER_NAME = "atf"

# Validated by the engine before run() is called, exactly as a tool's schema is.
INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "prompt": {
            "type": "string",
            "minLength": 1,
            "description": "The user turn to send. Passed to the CLI on stdin, so it is not "
            "subject to an argument-length limit.",
        },
        "system": {
            "type": "string",
            "description": "Replaces the default system prompt (--system-prompt).",
        },
        "append_system": {
            "type": "string",
            "description": "Appends to the default system prompt instead of replacing it. "
            "Ignored by the CLI when 'system' is also set.",
        },
        "model": {
            "type": "string",
            "description": "Alias ('opus', 'sonnet', 'haiku', 'fable') or full id "
            "('claude-opus-5'). Omitting it falls back to the CLI's configured default, "
            "which is a per-machine dependency. Set it.",
        },
        "effort": {
            "type": "string",
            "enum": ["low", "medium", "high", "xhigh", "max"],
            "description": "Reasoning effort for this turn (--effort).",
        },
        "json_schema": {
            "type": "object",
            "description": "Schema the response must validate against (--json-schema). When "
            "set, 'text' in the envelope is a JSON document; parse it rather than reading it "
            "as prose.",
        },
        "tools": {
            "type": "array",
            "items": {"type": "string"},
            "default": [],
            "description": "Engine tool names to expose for this turn, served over MCP. Not "
            "the CLI's built-in tools, which stay disabled: what a flow declares has to mean "
            "the same thing under another adapter. Empty keeps this a pure text completion.",
        },
        "tool_server": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
            "description": "argv the adapter spawns to serve 'tools'. Supplied by the engine, "
            "which knows where it is installed. Required whenever 'tools' is non-empty.",
        },
        "timeout_seconds": {
            "type": "number",
            "exclusiveMinimum": 0,
            "description": f"How long this turn may take (default {TIMEOUT_SECONDS}). A turn "
            "with tools takes many model turns inside it, so the default is short for one.",
        },
        "resume": {
            "type": "string",
            "description": "A session_id from a previous envelope, to continue that "
            "conversation instead of starting a new one.",
        },
        "max_budget_usd": {
            "type": "number",
            "exclusiveMinimum": 0,
            "description": "Hard spend ceiling for this turn. Worth setting on any "
            "autonomous loop.",
        },
        "isolate": {
            "type": "boolean",
            "default": True,
            "description": "Run with --safe-mode, keeping the host's ambient configuration "
            "out of the turn. Set false only when you deliberately want it.",
        },
    },
    # `model` is required rather than defaulted. The CLI's own default is a per-machine
    # dependency, which is what `isolate` exists to remove, so an agent that names none
    # would behave differently on another machine. The engine's probe makes `atf lint`
    # refuse such a spec without running anything.
    "required": ["prompt", "model"],
    "additionalProperties": False,
}

# Request field to CLI flag, for the plain pass-through options.
_FLAGS = {
    "system": "--system-prompt",
    "append_system": "--append-system-prompt",
    "model": "--model",
    "effort": "--effort",
    "resume": "--resume",
    "max_budget_usd": "--max-budget-usd",
}


def cli_version() -> str:
    """The runtime's version, or a reason it cannot be used."""
    if shutil.which(BINARY) is None:
        raise AdapterUnavailable(
            f"the {BINARY} binary is not on PATH: install Claude Code, or use another adapter"
        )
    try:
        result = subprocess.run(
            [BINARY, "--version"], capture_output=True, text=True, timeout=30, check=True
        )
    except (subprocess.SubprocessError, OSError) as exc:
        raise AdapterUnavailable(
            f"{BINARY} is present but `{BINARY} --version` failed: {exc}"
        ) from exc
    return result.stdout.split()[0] if result.stdout.split() else "unknown"


def build_args(payload: dict) -> list[str]:
    """The CLI invocation for a request, without the prompt.

    The prompt goes on stdin: it keeps long prompts clear of ARG_MAX, and clear of
    `--tools`, which is variadic and would swallow a trailing positional.
    """
    args = ["--print", "--output-format", "json"]

    # "" is the CLI's disable-all for its own built-in set, and it stays off
    # unconditionally: the engine's tools arrive over MCP, and the two are independent.
    tools = payload.get("tools") or []
    args += ["--tools", ""]

    if tools:
        server = payload.get("tool_server")
        if not server:
            raise AdapterRunFailed(
                f"tools were requested ({', '.join(tools)}) with no tool_server to serve them"
            )
        config: dict[str, Any] = {
            "mcpServers": {MCP_SERVER_NAME: {"command": server[0], "args": list(server[1:])}}
        }
        # $ATF_PATH is the highest-precedence search root, so a server that does not get it
        # can resolve the same tool name to a different directory than `lint` checked.
        # Passed explicitly rather than trusted to inherit: what a client hands a stdio
        # server is the client's business, and being wrong here is silent.
        if os.environ.get("ATF_PATH"):
            config["mcpServers"][MCP_SERVER_NAME]["env"] = {"ATF_PATH": os.environ["ATF_PATH"]}
        # --mcp-config takes the JSON itself, so there is no temporary file to place, clean
        # up, or leak. --strict-mcp-config keeps every other MCP source out of the turn.
        args += ["--mcp-config", json.dumps(config), "--strict-mcp-config"]
        # Naming an MCP tool in --tools does *not* permit it: the server connects, its tools
        # are listed, and none is ever called. --allowedTools is what grants them.
        #
        # `flat_name` because a namespaced tool is not offered under its slash: the server
        # calls `arctic/read_file` `arctic__read_file`, and allowing the slashed spelling
        # would match no tool the server listed.
        args += [
            "--allowedTools",
            ",".join(f"mcp__{MCP_SERVER_NAME}__{flat_name(name)}" for name in tools),
        ]

    # A plain dict lookup, so an explicit False stays False.
    if payload.get("isolate", True):
        if tools:
            # --safe-mode cannot be used here: its own help lists MCP servers among what it
            # disables, so the tool server never starts. The turn then *succeeds*, having
            # cost money, with the model saying it has no such tool.
            #
            # These two are the closest equivalent that leaves --mcp-config working, and
            # they are narrower than the flag they replace: --safe-mode names nine
            # categories, and settings files and skills are the two covered here. Plugins,
            # CLAUDE.md, custom agents and output styles are not, and unlike --safe-mode
            # failing, that gap is silent. Re-check it when moving VERIFIED_CLI_VERSION.
            args += ["--setting-sources", "", "--disable-slash-commands"]
        else:
            args.append("--safe-mode")

    for field, flag in _FLAGS.items():
        if payload.get(field) is not None:
            args += [flag, str(payload[field])]

    if payload.get("json_schema"):
        args += ["--json-schema", json.dumps(payload["json_schema"])]

    return args


def _failure_detail(result: dict) -> str:
    """What the CLI says went wrong, in one line.

    `subtype` is dropped when it reads "success". A failed turn can carry it, which
    produced messages like "success: 529: API Error ..." saying the opposite of what
    happened.
    """
    parts = [result.get("subtype"), result.get("api_error_status"), result.get("result")]
    return ": ".join(str(p) for p in parts if p not in (None, "", "success"))[:400]


def run(payload: dict, env: dict[str, str]) -> dict:
    """One turn. Returns the normalised envelope; raises AdapterError on failure.

    `env` is prepared by the engine: the caller's environment, the step's granted secrets,
    and the frozen-build library-path correction.
    """
    version = cli_version()
    timeout = payload.get("timeout_seconds") or TIMEOUT_SECONDS

    try:
        completed = subprocess.run(
            [BINARY, *build_args(payload)],
            input=payload["prompt"],
            capture_output=True,
            text=True,
            env=env,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise AdapterRunFailed(f"{BINARY} exceeded {timeout}s") from exc
    except OSError as exc:
        raise AdapterUnavailable(f"could not run {BINARY}: {exc}") from exc

    try:
        result = json.loads(completed.stdout)
        if not isinstance(result, dict) or result.get("type") != "result":
            raise ValueError("not a result object")
    except (json.JSONDecodeError, ValueError) as exc:
        # A failed turn is reported two ways at once, a non-zero exit *and* a result object
        # carrying the detail, so stdout is parsed first. Only when there is no result to
        # read does the exit code become the whole story.
        if completed.returncode != 0:
            detail = " ".join(completed.stderr.split())[:400] or "no stderr output"
            raise AdapterRunFailed(f"{BINARY} exited {completed.returncode}: {detail}") from exc
        raise AdapterProtocolError(
            f"expected a JSON result object from --output-format json, got "
            f"{completed.stdout[:200]!r}"
        ) from exc

    if result.get("is_error"):
        raise AdapterRunFailed(_failure_detail(result) or "reported an error without describing it")
    if completed.returncode != 0:
        # Contradictory: a result claiming success from a process that failed. Trust the
        # exit code rather than passing off a partial turn as a good one.
        raise AdapterRunFailed(
            f"{BINARY} exited {completed.returncode} but its result claims success, "
            f"so treating it as failed ({_failure_detail(result)})"
        )

    usage = result.get("usage") or {}
    return {
        "ok": True,
        "text": result.get("result") or "",
        "stop_reason": result.get("stop_reason"),
        "session_id": result.get("session_id"),
        # What was asked for. Not necessarily the only model that ran: the CLI may make
        # auxiliary calls, and cost_usd covers all of them. model_usage is the ground truth.
        "requested_model": payload.get("model"),
        "num_turns": result.get("num_turns"),
        "usage": {
            "input_tokens": usage.get("input_tokens", 0),
            "output_tokens": usage.get("output_tokens", 0),
            "cache_read_input_tokens": usage.get("cache_read_input_tokens", 0),
            "cache_creation_input_tokens": usage.get("cache_creation_input_tokens", 0),
        },
        "cost_usd": result.get("total_cost_usd", 0),
        "duration_ms": result.get("duration_ms"),
        "model_usage": result.get("modelUsage") or {},
        "adapter": {"name": NAME, "cli_version": version},
    }
