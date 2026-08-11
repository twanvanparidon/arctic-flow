# Execution

`engine/executor.py`. Push, not pull: a step declares `push: [ids]` or `switch:`/`cases:`, and
nothing declares dependencies. `build_graph` inverts the forward edges into an inbound map,
and `execute` runs a step once every inbound edge is settled, on a thread pool.

## The state machine

Two maps, and everything follows from how they move.

| Map | Values |
| --- | --- |
| `state[step_id]` | `waiting` → `running` → `done`, or `skipped` |
| `edge[(source, target)]` | `pending` → `delivered`, or `skipped` |

A step becomes ready when it is `waiting` and every inbound edge is `delivered` or `skipped`.
The entry edge `(START, flow["start"])` is seeded `delivered`, which is what opens the graph.

Back-edges start `skipped` rather than `pending`. Otherwise the loop head would wait forever
on an edge from a step downstream of itself, and nothing would ever become ready.

## Skip propagation

When a `switch` picks a case, the edges to every other case are marked `skipped`. Then:

> a step whose inbound edges are **all** `skipped` is itself `skipped`, and its own outbound
> edges are marked `skipped` too.

That cascade is the whole mechanism. It is what lets a join downstream of a branch run on both
paths instead of waiting forever for one that will never deliver. Without it, `report` in
`examples/file-review` would hang whenever triage said `clean`.

Note the `all`: one delivered edge is enough to run a join. That is deliberate, and it is why
a join does not need to know which branch was taken.

A skipped step still resolves in templates, as `SKIPPED_RESULT`
(`{"skipped": True, "text": "(not run)", "json": None}`). Two consequences worth knowing:

- `truthy` checks the `skipped` marker **before** emptiness, because the result is a non-empty
  mapping and would otherwise read as true. That one ordering is what makes
  `{% if steps.risk_scan %}` the whole test.
- `.text` resolves, `.json` does not. A prompt reading `{{ steps.x.json.field }}` on a skipped
  step fails, which is why a guard is the answer rather than a fallback value.

## Loops

A `switch` case naming a step already upstream is a loop, the only cycle a flow may have.
`back_edges` finds them by a depth-first walk from `start`, so **declaration order decides
which edge closes a cycle**. `max_loops` on the source is then required.

When a back-edge fires, the body is `descendants_of(head) & ancestors_of(source)`. Every step
in it goes back to `waiting`, and the edges inside it go back to `pending`.

Four rules about that reset, each of which was a bug first.

**`results` is deliberately not cleared.** The next pass reads the last one, which is how a
writer sees the review that sent its work back. A body step that has not run yet is seeded
with `SKIPPED_RESULT`, so a first-pass template gets `(not run)` rather than a `KeyError`.

**A step that took a back-edge leaves its other edges `pending`, not `skipped`.** This looks
wrong: the exit branch was not taken, so surely it is skipped? Mark it skipped and the cascade
above skips everything after the loop, so the run finishes with no output at all. The edge has
to stay pending, because on some later pass it will be taken.

**A back-edge inside the body being reset goes back to `skipped`**, not `pending`, for the
same reason back-edges start that way. Left pending, the inner head waits on a step downstream
of itself, nothing becomes ready, `execute` returns, and the run exits **0 with the previous
pass's output**. Silent rather than loud, which is the one thing nesting costs.

**`loops` is not reset either.** A bound is per step over the whole run, so two nested threes
are six passes and not sixteen. A flow's worst case is something a reader can add up. The
price is that an inner loop which spent its bound early fails on a later outer pass rather
than starting fresh.

### Why only nesting

Two loops may share a body only where one **contains** the other, which is the tool check
inside an agent review. Partial overlap is refused: a step one loop re-runs and the other does
not belongs to neither pass, so which count a reset should touch is undefined.

Containment also keeps a pass safe to start. A loop's body is bounded by what leads back to
the closing step, so every member has finished by the time the work goes back, and none is
still running when its state returns to `waiting`.

### The two graphs

Anything derived from an ordering reads `without_back_edges`: waves, the "always runs"
guarantee, the cycle check, and the upstream test in `validate`. The cyclic graph is only used
to run.

That split is why `{{ steps.write.text }}` inside `write` is legal in a loop and refused
outside one. A loop makes a step its own ancestor in the real graph.

## Concurrency

Steps run on a `ThreadPoolExecutor`. Two steps pushed from one place run concurrently; a step
named by two places runs once. Nothing declares either.

Events reach `on_event` from worker threads, so **an observer must be concurrency-safe**. The
CLI's progress display locks around its writes for exactly this reason.

Granted tool calls are pooled separately, capped at `MAX_CONCURRENT_CALLS`. Only `tools/call`
leaves the MCP read loop; `initialize`, `tools/list` and `ping` are answered where they are
read, because a queued `ping` is the stall the pool exists to remove. Two things follow and
both are load-bearing: writes are locked, or two replies interleave and the framing is gone;
and a worker carries its own error guard, because an exception inside a future is kept by the
future rather than raised. Replies arrive as calls finish, so anything reading them keys by
request id and never by position.

## The run ceiling

`run.max_minutes` from `~/.arctic/config.yaml` bounds the whole of `execute`, and is the one
limit a flow cannot raise, because it is a safeguard rather than a setting.

It is the timeout on the pool's `wait`, so nothing blocks past it, and firing sets a run-wide
cancel event that reaches every tool subprocess.

**It cannot reach an agent turn.** `adapter.run` is a synchronous call with no way in, so a
turn already started runs to its own `timeout_seconds` and the pool's shutdown waits for it.
The ceiling is therefore a ceiling plus at most one turn. `run_agent` checks the event before
each turn, so the gap costs time and never a second paid turn. Closing it means putting
cancellation into the adapter contract, which is [deferred](deferred.md).

No ceiling configured means no event is created at all, so a run without a config takes the
path it always did.

## Stopping a subprocess

`spawn` takes `cancel` and `grouped` as **separate** arguments, and conflating them is a bug.

- **`cancel`** is whether the work can be stopped. Both callers pass one: an in-turn call from
  its client, a step from the run ceiling.
- **`grouped`** is whether the call has a terminal to answer to. An in-turn call has none, so
  it gets `start_new_session` and the whole process tree is signalled. A step stays in the
  caller's process group, so Ctrl-C on `atf run` still reaches its tool.

The price of the second is that only the direct child is signalled, so a step's tool that
backgrounded something can leave it behind.

A cancelled in-turn call is stopped, not merely unanswered: `notifications/cancelled` sets the
call's event, `spawn` signals TERM then KILL, and no reply is sent. The cancel is handled on
the read loop, because pooled it would queue behind the call it cancels. TERM comes first
because a tool that writes can be interrupted mid-write, and `write_file` truncates in place.

## child_environment

Anything spawning a subprocess builds its environment with `child_environment()`. It undoes
PyInstaller's `LD_LIBRARY_PATH` rewrite, without which a spawned system binary loads the
bundle's OpenSSL and fails.

This breaks in frozen builds only, so no unit or integration test can see it. That is what
`tests/e2e` is for, and `sign_release` spawning `openssl` is the test that says so.

## Checks, and why there is no gate key

A tool step switches exactly as an agent step does, so a check needs no key of its own: the
tool answers a verdict, exits 0 because answering is its job, and one case sends the work back.

The verdict goes on stdout as JSON because `run_step` parses it into `.json`, and because
`.verdict` and `.reason` are wanted in two different places: the switch, and the next pass's
prompt. One line of prose cannot serve both.

The retry this replaced used to live inside `run_agent`. Moving it into the graph is what
gives the judge a row in `inspect flow`, a line per pass in the progress output, and its own
`--trace` entry. Nothing is appended to a prompt behind the flow's back.
