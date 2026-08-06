"""Adapter failure kinds.

Their own module rather than the package's __init__, which would import the adapter
modules and be imported by them in turn. Putting the exceptions where both reach them
removes the cycle without depending on statement order in a file, which an import sorter
would undo at the first opportunity.

The distinctions are the ones a caller acts on differently, and are what the previous
shell adapter encoded as exit codes 5, 6 and 7.
"""

from __future__ import annotations


class AdapterError(RuntimeError):
    """An adapter could not complete a turn."""


class AdapterUnavailable(AdapterError):
    """The runtime this adapter wraps is missing or unusable.

    Separate from a failed turn because it is a host configuration problem. Retrying will
    not help, and the engine should stop rather than work through a flow calling something
    that cannot answer.
    """


class AdapterRunFailed(AdapterError):
    """The runtime ran and refused, errored, or was cut off."""


class AdapterProtocolError(AdapterError):
    """The runtime answered with something this adapter does not recognise.

    Distinct from a failed turn: the request may have succeeded, but the reply cannot be
    trusted, usually because the runtime's output format changed underneath us.
    """
