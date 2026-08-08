"""Write real components to disk.

The engine finds a tool by looking for a directory with a `spec.json` in it and runs it by
spawning the file `run.command` names. There is no seam to substitute, and none is wanted:
a test that writes the directory and lets the engine spawn the process is testing the thing
that ships.

So the scripts here are real scripts. `prints`, `fails` and the rest build the smallest
program that has the behaviour a test needs, and `write_tool` puts it beside a spec that
declares it. Anything a test wants to observe (the payload that arrived, the environment
the process ran in) it observes by having the script print it.
"""

from __future__ import annotations

import json
import os
import shlex
import stat
import time
from pathlib import Path
from typing import Any

import yaml

SH = "#!/bin/sh\nset -eu\n"

# python3 rather than jq, so the test suite needs nothing installed that the interpreter
# running it does not already have.
PY = "#!/usr/bin/env python3\nimport json, os, sys\npayload = json.load(sys.stdin)\n"


def sh(body: str) -> str:
    return SH + body


def python(body: str) -> str:
    """A tool whose stdin is already parsed into `payload`."""
    return PY + body


# The payload straight back out, so a test can see exactly what the engine sent.
ECHO_STDIN = sh("cat\n")


def prints(text: str) -> str:
    """Ignores its input, writes `text` to stdout, exits 0."""
    return sh(f"cat >/dev/null\nprintf %s {shlex.quote(text)}\n")


def fails(code: int, message: str = "", stdout: str = "") -> str:
    """Exits `code`, having written `stdout` to stdout and `message` to stderr."""
    return sh(
        f"cat >/dev/null\n"
        f"printf %s {shlex.quote(stdout)}\n"
        f"printf %s {shlex.quote(message)} >&2\n"
        f"exit {code}\n"
    )


def echoes_input(key: str) -> str:
    """Prints one value from the payload, so a step's result is a value the test chose."""
    return python(f"sys.stdout.write(str(payload[{key!r}]))\n")


def grows(step: str, empty: str) -> str:
    """Appends `step` to the payload's `previous`, counting `empty` as nothing yet.

    A loop needs a step whose result differs each pass, or the switch that leaves the loop
    never sees a value it has not already seen. `empty` is what the first pass reads: a
    loop's steps are mutually upstream, so one of them resolves before it has run.
    """
    return python(
        f"previous = payload['previous']\n"
        f"sys.stdout.write(('' if previous == {empty!r} else previous) + {step!r})\n"
    )


def echoes_env(name: str) -> str:
    """Prints one environment variable, or nothing when it is unset.

    How a test sees which secrets a step was actually granted: the engine passes them
    through the environment and nowhere else.
    """
    return sh(f'cat >/dev/null\nprintf %s "${{{name}-}}"\n')


def echoes_environment() -> str:
    """Prints the whole environment as a JSON object."""
    return python("json.dump(dict(os.environ), sys.stdout)\n")


def sleeps(seconds: float) -> str:
    """Outlives a timeout. Reads stdin first, or the engine's write breaks on a closed pipe."""
    return sh(f"cat >/dev/null\nsleep {seconds}\nprintf slept\n")


def finishes_later(started: Path, finished: Path, seconds: float = 3.0) -> str:
    """Signals `started`, then leaves `finished` behind `seconds` later.

    For proving a component was really stopped rather than merely unanswered. Those two
    look identical from the caller's side, and the only difference is whether the process
    ever reached its last line, so `finished` is the discriminator: a component that was
    not stopped writes it, one that was does not.

    The wait has to outlast whatever is being tested, and nothing must release it early. An
    earlier version waited on a file the test wrote *after* the run, which meant a survivor
    hit its own deadline and skipped the marker anyway, and the test passed either way.

    The marker is written by a backgrounded child, so what has to be stopped is a tree.
    Signalling the shell alone leaves the child, and the marker still appears.
    """
    waiter = (
        "import pathlib, time\n"
        f"time.sleep({seconds})\n"
        f"pathlib.Path({str(finished)!r}).write_text('done')\n"
    )
    return sh(
        f"cat >/dev/null\n"
        f"python3 -c {shlex.quote(waiter)} &\n"
        f"child=$!\n"
        f"printf %s $child > {shlex.quote(str(started))}\n"
        f"wait $child\n"
        f"printf released\n"
    )


def rendezvous(mine: Path, theirs: Path, timeout: float = 20.0) -> str:
    """Signals through `mine`, then waits for `theirs` to appear.

    Two steps running this only both finish if they were running at the same time, which
    is the claim being tested. The wait has its own deadline and a non-zero exit, so a
    regression fails the test instead of hanging the suite.
    """
    return python(
        "import pathlib, time\n"
        f"mine = pathlib.Path({str(mine)!r})\n"
        f"theirs = pathlib.Path({str(theirs)!r})\n"
        "mine.write_text('here')\n"
        f"deadline = time.monotonic() + {timeout}\n"
        "while not theirs.exists():\n"
        "    if time.monotonic() > deadline:\n"
        "        sys.stderr.write('the other step never started\\n')\n"
        "        sys.exit(9)\n"
        "    time.sleep(0.01)\n"
        "sys.stdout.write('met')\n"
    )


def leaf(name: str) -> str:
    """What a namespaced component calls itself in its own spec.

    `tools/common/greet/spec.json` says "greet": the namespace is where the directory sits,
    which the spec has no way of knowing and no reason to repeat. Written into the builders
    so a namespaced test component is the shape a real one has, which is what makes it worth
    testing that the *lookup* name is the one the engine hands around.
    """
    return name.rsplit("/", 1)[-1]


def tool_spec(name: str, **overrides: Any) -> dict[str, Any]:
    """A minimal spec that `check_tool_spec` accepts and `invoke` can run."""
    spec: dict[str, Any] = {
        "name": leaf(name),
        "description": f"test tool {name}",
        # Well under the engine's 60s default: a test that hangs should fail quickly.
        "run": {"command": ["./run.sh"], "timeout_seconds": 20},
        "input_schema": {"type": "object"},
        # Required by TOOL_SPEC_SCHEMA. "none" keeps the default tool grantable to an
        # agent without `unattended`; a test about that gate overrides it with "write".
        "permissions": {"filesystem": "none"},
    }
    spec.update(overrides)
    return spec


def agent_spec(name: str, **overrides: Any) -> dict[str, Any]:
    spec: dict[str, Any] = {
        "name": leaf(name),
        "description": f"test agent {name}",
        "adapter": "echo",
    }
    spec.update(overrides)
    return spec


def write_tool(
    root: Path,
    name: str,
    *,
    script: str = ECHO_STDIN,
    spec: dict[str, Any] | None = None,
    executable: bool = True,
    **overrides: Any,
) -> Path:
    """A tool directory: spec.json, tool.md and an executable run.sh.

    `spec` replaces the default outright, for testing what happens to a spec missing a
    field. `**overrides` amends it, for the ordinary case of changing one key.
    """
    base = root / "tools" / name
    base.mkdir(parents=True, exist_ok=True)
    built = tool_spec(name, **overrides) if spec is None else {**spec, **overrides}
    (base / "spec.json").write_text(json.dumps(built, indent=2))
    (base / "tool.md").write_text(f"# {name}\n")

    command = built.get("run", {}).get("command") or ["./run.sh"]
    # A malformed command is a thing under test, so it is written to the spec and not run.
    # An absolute one names something already installed, and joining it onto the component
    # directory would resolve straight back out of tmp_path and write over what is there.
    if isinstance(command, str) or not isinstance(command[0], str):
        return base
    if not Path(command[0]).is_absolute():
        runner = base / command[0]
        runner.parent.mkdir(parents=True, exist_ok=True)
        runner.write_text(script)
        if executable:
            make_executable(runner)
    return base


def write_agent(
    root: Path,
    name: str,
    *,
    prompt: str = "Answer in one line.",
    spec: dict[str, Any] | None = None,
    write_prompt: bool = True,
    **overrides: Any,
) -> Path:
    """An agent directory: spec.json plus the markdown file that *is* its system prompt."""
    base = root / "agents" / name
    base.mkdir(parents=True, exist_ok=True)
    built = agent_spec(name, **overrides) if spec is None else {**spec, **overrides}
    (base / "spec.json").write_text(json.dumps(built, indent=2))
    if write_prompt:
        (base / built.get("system_prompt", "agent.md")).write_text(prompt)
    return base


def write_flow(root: Path, name: str, definition: dict[str, Any]) -> Path:
    path = root / "flows" / f"{name}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(definition, sort_keys=False))
    return path


def write_text_flow(root: Path, name: str, text: str, suffix: str = ".yaml") -> Path:
    """A flow written as literal YAML, for cases a mapping cannot express."""
    path = root / "flows" / f"{name}{suffix}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def make_executable(path: Path) -> None:
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def make_unexecutable(path: Path) -> None:
    path.chmod(path.stat().st_mode & ~(stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))


def one_step_flow(step: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    """The smallest runnable flow, wrapped around one step."""
    flow: dict[str, Any] = {
        "flow": "test_flow",
        "start": step["id"],
        "steps": [step],
    }
    flow.update(overrides)
    return flow


def is_executable(path: Path) -> bool:
    return os.access(path, os.X_OK)


def wait_for(path: Path, timeout: float = 20.0) -> None:
    """Block until `path` appears. The Python half of `rendezvous`.

    How a test knows a component has really started before it does something to it. Its own
    deadline and a failure that says which file, so a regression fails the test rather than
    hanging the suite.
    """
    deadline = time.monotonic() + timeout
    while not path.exists():
        if time.monotonic() > deadline:
            raise AssertionError(f"{path} never appeared")
        time.sleep(0.01)
