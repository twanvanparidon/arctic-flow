"""What the engine can be asked to do, independent of who is asking.

One function per command, each returning a dataclass from `commands.results`. Import this
package and you have the whole product with no terminal attached:

    plan = commands.prepare("sign_release", paths, {"path": "release-notes.md"})
    result = commands.run(plan, on_event=my_observer)

Four rules keep it usable from more than one front end, and a new command keeps all four:

- **No stream, ever.** Nothing prints, reads stdin, or prompts. `atf run > file` has to
  produce the flow's output alone, and a stray `print` is what breaks it.
- **No argparse.** Threading a `Namespace` through is what made this layer CLI-only.
- **Failures raise**, always one of `EXPECTED_ERRORS`. No exit codes, no None meaning
  "something went wrong": a front end catches once, at its edge.
- **Interactive parts are injected.** A vault password and live progress arrive as
  callables, so a terminal supplies `getpass` and a spinner and a script supplies neither.

Human-facing wording belongs to the front end, written where it is read.
"""

from __future__ import annotations

from commands.flows import (
    EventObserver,
    diagram,
    graph,
    lint,
    prepare,
    resolve_flow,
    run,
)
from commands.inventory import inventory, search_paths
from commands.results import (
    ComponentEntry,
    DiagramResult,
    FlowPlan,
    GraphResult,
    Inventory,
    KindListing,
    LintResult,
    PathsReport,
    RootReport,
    RunResult,
    SecretListing,
    SecretSet,
    ToolCall,
    ToolDescription,
    VaultContents,
    VaultCreated,
)
from commands.secrets import (
    Password,
    PasswordProvider,
    create_vault,
    open_vault,
    secret_names,
    set_secret,
    unlock,
    vault_contents,
)
from commands.tools import call_tool, describe_tools
from engine.executor import FlowError
from paths.resolver import LookupError_
from vault.vault import VaultError

# What a front end catches, in one place so every front end catches the same set. OSError
# is in it because a missing file or an unreadable directory is an ordinary failure of
# these commands, not a bug in them. Anything else is a bug and should keep its traceback.
EXPECTED_ERRORS: tuple[type[BaseException], ...] = (
    FlowError,
    LookupError_,
    VaultError,
    OSError,
)

__all__ = [
    # flows
    "prepare",
    "run",
    "lint",
    "graph",
    "diagram",
    "resolve_flow",
    # tools, outside a flow: what an agent's turn needs
    "describe_tools",
    "call_tool",
    # the installation
    "inventory",
    "search_paths",
    # vault
    "open_vault",
    "create_vault",
    "set_secret",
    "secret_names",
    "vault_contents",
    # injected behaviour
    "EventObserver",
    "Password",
    "PasswordProvider",
    "unlock",
    # failure
    "EXPECTED_ERRORS",
    "FlowError",
    "LookupError_",
    "VaultError",
    # results
    "ComponentEntry",
    "DiagramResult",
    "FlowPlan",
    "GraphResult",
    "Inventory",
    "KindListing",
    "LintResult",
    "PathsReport",
    "RootReport",
    "RunResult",
    "SecretListing",
    "SecretSet",
    "ToolCall",
    "ToolDescription",
    "VaultContents",
    "VaultCreated",
]
