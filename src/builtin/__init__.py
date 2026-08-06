"""Components that ship with the engine.

Not code. `tools/` holds ordinary component directories, and the resolver treats this as
the lowest-precedence search root. They live inside the package so that they survive being
installed as a wheel or frozen into a binary, neither of which preserves a directory that
merely sat next to the source.
"""
