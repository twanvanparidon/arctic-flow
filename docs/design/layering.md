# Layering

```
src/main.py              development entry point; puts src/ on the path
src/commands/            what the engine can be asked to do, with no front end attached
src/cli/                 the terminal front end: arguments, help, output, progress
src/engine/              executor.py runs a flow, specs.py checks one first
src/paths/               layered component lookup, and ~/.arctic/config.yaml
src/vault/               the encrypted secrets file
src/adapters/            model runtimes, as Python modules
src/builtin/             components that ship with the engine, packs/, and create's scaffolds
src/util/                ways of looking at a flow without running it
```

Four invariants hold this together, and each is easy to break by accident.

## 1. Nothing in `commands/` prints, prompts, or reads a stream

`commands/` is one function per command (`run`, `lint`, `graph`, `vault set`). Each takes
ordinary arguments, returns a dataclass from `commands/results.py`, and raises on failure. No
`argparse.Namespace` crosses into it either: arguments are real types.

```
cli/app.py         the shape of the interface: commands, flags, help text, exit codes
cli/dispatch.py    arguments in, command called, result printed, exit code out
cli/render.py      a result in, a string out: pure, no stream, no colour
```

**The point is a second front end.** A TUI reimplements `dispatch.py`, reuses whichever of
`render.py`'s strings still read well in a pane, and calls the same commands the CLI calls. No
command reimplemented, no behaviour to keep in sync.

The two interactive parts are injected rather than assumed: a vault password arrives as a
string or a callable (`commands.Password`), and live progress as an observer
(`commands.EventObserver`), which is the event stream the engine already emitted.

Two habits keep it usable from both:

- **A `print` in `commands/` breaks it.** A command that writes to a stream has decided
  something that is not its to decide. Return the fact and let the front end place it.
- **Human wording lives in the front end.** `--help` prose documents flags and pipes, which is
  a command line's vocabulary. A menu label is a TUI's. Neither belongs beside the command.

`run` is deliberately two calls, `prepare` then `run`. A front end wants every early failure
**before** it paints a progress display: an unknown flow, a bad input, a locked vault. Folded
into one call, a mistyped input arrives under a spinner with a "failed after 0ms" over the top
of it.

`prepare` is also the only place both input sources exist, which is why precedence between
`--input` and `$ATF_VAR_` lives there. `inputs_from_environment` reads `paths.env` rather than
`os.environ`, so one environment decides both the search roots and the inputs, and a caller
isolating one isolates both.

## 2. stdout carries the flow's output and nothing else

Progress, the output frame, warnings and traces all go to stderr, so `run … > file` produces
the result byte for byte.

That is also why the frame has no left edge. A marker on those lines would mean editing bytes
the flow produced, and the frame is drawn only when both streams are terminals so a pipe never
sees it at all.

## 3. `engine/` decides, `cli/` renders

The engine emits event dicts through an `on_event` observer and formats nothing. That is how
the progress display was added without touching the engine.

Events arrive from worker threads, so an observer must be concurrency-safe. See
[execution](execution.md#concurrency).

## 4. `util/` is only for things the core could not import

The graph listing and the Mermaid diagram live there, they are imported lazily by the commands
that need them, and `run` never touches the package at all. **The engine works with the whole
directory deleted.**

Validation deliberately does **not** live there, even though `atf lint` looks like a sibling of
`atf inspect flow`. Its checks are the ones `run` performs before executing anything, so they
sit in `engine/specs.py` next to the code that depends on them.

The test: could the core import it? If yes, it is not a util.

### What the diagram derives

`util/mermaid.py` computes three things the YAML never states: **waves** (a step's wave is one
past its deepest inbound step), **guaranteed** (a switch guarantees a step only when *every*
case eventually reaches it, so the property is transitive rather than per-edge), and **joins**.
All on the loop-opened graph.

Two details there were bugs first. Node ids are positional (`n0`, `n1`, …) rather than derived
from step names, so `read-target` and `read_target` do not collapse onto one node. Classes are
emitted one per line, because `class a,b c;` in Mermaid means two *node ids* and one class,
not one node and two classes.

`util/graph.py` prints `(terminal)` for a step with no outbound edge, so "this step ends the
flow" and "I forgot to draw the rest" do not look the same.

## On the flat `src/`

There is no wrapping package: `cli`, `commands`, `engine`, `paths`, `vault`, `adapters`,
`builtin` and `util` are top-level, both here and in `site-packages` once installed. Imports
stay short, and an import that works from a checkout works identically when installed.

The cost is real and worth knowing before adding a directory. Those names are generic, so
`pip install arctic-flow` claims all eight, `commands` most of all. Another distribution
shipping its own top-level `engine/` or `util/` would collide, and `import util` in an
unrelated project can pick ours up. The binary and from-source paths are unaffected; only the
wheel shares a namespace.

If that becomes a problem, the fix is nesting everything under one distinct package
(`arcticflow/`), which is a mechanical rename of the import prefix plus four lines of
`pyproject.toml`.

`[tool.setuptools.packages.find]` lists the packages explicitly rather than globbing, so a new
directory under `src/` ships only when someone adds it on purpose. `builtin` needs its
`package-data` entry or the built-in search layer comes up empty in a wheel.
