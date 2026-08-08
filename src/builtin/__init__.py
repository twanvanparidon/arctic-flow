"""Components that ship with the engine, and the scaffolds `create` writes.

Not code. `tools/` holds ordinary component directories, and the resolver treats this as
the lowest-precedence search root. They live inside the package so that they survive being
installed as a wheel or frozen into a binary, neither of which preserves a directory that
merely sat next to the source.

`scaffolds/` is here for the same reason and is not a search layer: the resolver only ever
looks under `tools/`, `agents/` and `flows/`, so nothing in there resolves as a component.
It is what `commands/scaffold.py` copies out of.
"""
