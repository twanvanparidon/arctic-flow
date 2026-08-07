"""Adapters: how the engine talks to a model runtime.

One turn in, the same normalised envelope out, whatever the runtime. Nothing downstream
reads a runtime's own field names, so a second runtime is a new module here and no change
anywhere else.

Adapters are Python modules, not component directories like tools, because a tool is
user-extensible in any language and earns a subprocess where an adapter would pay for one
and get nothing. `INPUT_SCHEMA` still gives the same validation guarantee.

So there is no `~/.arctic/adapters/`: adding one means a module here plus an entry in
ADAPTERS. Loading them from a path would be a plugin mechanism and should be built as one.
The registry is static imports, because a frozen build misses anything resolved by name.
"""

from __future__ import annotations

from types import ModuleType

from adapters import claude_code, echo
from adapters.errors import (
    AdapterError,
    AdapterProtocolError,
    AdapterRunFailed,
    AdapterUnavailable,
)

ADAPTERS: dict[str, ModuleType] = {
    claude_code.NAME: claude_code,
    echo.NAME: echo,
}


def get(name: str) -> ModuleType:
    try:
        return ADAPTERS[name]
    except KeyError:
        raise AdapterError(
            f"unknown adapter '{name}'. Available: {', '.join(sorted(ADAPTERS)) or 'none'}"
        ) from None


def names() -> list[str]:
    return sorted(ADAPTERS)


def describe() -> dict[str, str]:
    """Name to one-line description, for `atf list`."""
    return {name: getattr(module, "DESCRIPTION", "") for name, module in sorted(ADAPTERS.items())}


__all__ = [
    "ADAPTERS",
    "AdapterError",
    "AdapterProtocolError",
    "AdapterRunFailed",
    "AdapterUnavailable",
    "describe",
    "get",
    "names",
]
