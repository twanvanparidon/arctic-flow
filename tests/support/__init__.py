"""Helpers the tests share. Nothing here fakes a unit under test.

`components` writes real tool, agent and flow definitions to disk. `terminal` opens a real
pseudo-terminal, so code branching on `isatty()` gets a true answer instead of an object
pretending to be a stream. `adapter_echo` is a working adapter, registered the way any
adapter is, because the only shipped one needs a CLI, an account and a network.
"""

from __future__ import annotations
