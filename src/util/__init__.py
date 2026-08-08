"""Ways of looking at a flow without running it.

Nothing here is on the execution path. `run` never imports this package and the engine
works with the whole directory deleted, minus the ability to see what a flow will do
before it does it. These modules read a validated flow and render it. They decide nothing.

  graph     the push edges as text, the shape people actually check
  mermaid   a diagram, plus a static report of how the flow resolves

Both are `atf inspect flow`, which picks between them on `-o`.

Validation is deliberately *not* here, even though `atf lint` looks like a sibling of it.
Its checks are what `run` performs before executing anything, so they live in
`engine/specs.py` beside the code that depends on them. A util the core imported would not
be a util.
"""
