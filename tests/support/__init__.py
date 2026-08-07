"""Helpers the tests share. Nothing here fakes a unit under test.

`components` writes real tool, agent and flow definitions to disk. `terminal` opens a real
pseudo-terminal, so code branching on `isatty()` gets a true answer instead of an object
pretending to be a stream. `console` goes further and gives a command a terminal it
*controls*, which is what `/dev/tty` needs and what a password prompt is read through.
`fake_claude` is a working program speaking the Claude Code CLI's protocol, so the adapter
really spawns a process without an account or a network.

There is no adapter here. `adapters.echo` ships and answers without a runtime, so a test
that needs an agent step uses the same module a user would.
"""

from __future__ import annotations
