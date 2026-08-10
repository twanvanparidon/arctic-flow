# glob

List paths in the workspace whose name or path matches a shell pattern.

## Purpose

Find out what exists before reading or searching it. Answers "which files are
there" in one call, where guessing a path costs a failed read and tells you
nothing about what you should have asked for.

**This tool is about names. `grep` is about contents.** "Which files are named
`*Controller.php`" is this one. "Which of them call `die(`" is `grep`. Neither
answers the other's question, and the usual order is this one first.

## When to use it

- You need the shape of a tree: every test file, every migration, every YAML.
- You half know a filename and want the rest of it.
- You are about to search and want to bound it: glob to see what is there, then
  `grep` over it.

## When not to use it

- You are looking for a *string* rather than a filename. That is `grep`, and it
  answers in lines rather than in paths.
- You already have the path. Read it.
- You want the whole tree listed. A pattern that matches everything returns a
  wall of paths, most of a truncation notice, and no answer.

## Parameters

`spec.json` → `input_schema` is the authoritative contract.

| Parameter     | Type    | Required | Default  | Notes                                             |
| ------------- | ------- | -------- | -------- | ------------------------------------------------- |
| `pattern`     | string  | yes      | none     | `*`, `?` and `[...]`. See below for the slash.    |
| `path`        | string  | no       | `.`      | Directory to search. Must be inside the workspace.|
| `type`        | enum    | no       | `file`   | `file`, `dir` or `any`.                           |
| `max_results` | integer | no       | `200`    | Stop after this many paths and say so.            |

## A slash changes what is matched

**No slash matches the file name**, at any depth:

```sh
echo '{"pattern":"*.py"}' | src/builtin/tools/arctic/glob/run.sh
```

```
src/app.py
src/db/models.py
tests/test_app.py
```

**A slash matches the whole path**, and `*` spans directories there, so one
pattern reaches any depth:

```sh
echo '{"pattern":"src/*_test.py"}' | src/builtin/tools/arctic/glob/run.sh
```

```
src/db/models_test.py
src/handlers/http_test.py
```

There is no separate `**`. `*` already crosses `/` in a path pattern, which is
the behaviour `**` exists to provide elsewhere.

## The order is sorted, not by modification time

Two runs over an unchanged tree return the same bytes, which is what makes a
result safe to diff or cache. It also means truncation is stable: the same 200
paths survive every time, rather than whichever ones the filesystem happened to
hand over first.

If you want the most recently changed file, this is not the tool. Read the ones
the pattern found.

## Nothing is excluded for you

No `.git`, no `node_modules`, no build output. A bare `*` over a real repository
is mostly noise. Bound it with `path`, or make the pattern specific:

```
[glob] output truncated at 200 paths. Narrow the pattern or the path, or raise max_results.
```

## Matching nothing is an answer

It exits `0` and says so, so a flow can ask a question whose honest answer is
"none":

```
[glob] no matches for *.rs
```

Take it at face value, but check the tree you searched before concluding the
files do not exist: a `path` or a `type` you set may be excluding them.

## Errors

Failures write one line to stderr and set an exit code from `spec.json`'s
`exit_codes`: `2` invalid input, `3` the search path does not exist, `4` not
permitted.

```
$ echo '{"pattern":"*","path":"/etc"}' | src/builtin/tools/arctic/glob/run.sh
glob: path resolves outside the workspace root: /etc
$ echo $?
4
```

The path is fully canonicalised first, so `..` segments and symlinks leaving the
workspace are rejected too, not just absolute paths.

## Portability

`find` with `-name` and `-path`, which POSIX specifies, and nothing else. `-regex`
and `-printf` are not portable, and `-mindepth` is not either, which is why the
search root is dropped with `! -path` instead. A pattern therefore returns the
same list wherever the engine runs.
