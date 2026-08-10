# grep

Report lines in the workspace matching a pattern, as `path:line:text`.

## Purpose

Find where a piece of text appears. A search over a tree costs one call and a few
hundred bytes; reading five files to find one function costs five calls and most
of the context you were saving.

**This tool is about contents. `glob` is about names.** "Where is `die(` called"
is this one. "Which files are named `*Controller.php`" is `glob`. Reach for
`glob` first when you do not yet know what exists, then search what it found.

## When to use it

- You need the place something you can name lives: a function, a class, a config
  key, an error string.
- You want every place a symbol is used, not just where it is defined.
- You are about to guess at a filename. Search instead.

## When not to use it

- You already know the path. Read it.
- You are looking for files by *name*. That is `glob`, and it answers in paths
  rather than in lines.
- The question is about what a file means rather than where a string is. Reading
  beats grepping for that.
- You are searching a tree with build output, `node_modules` or a `.git` in it.
  Nothing is excluded for you, so bound the search with `path` or `glob` or the
  answer arrives buried.

## Parameters

`spec.json` → `input_schema` is the authoritative contract.

| Parameter     | Type    | Required | Default | Notes                                                          |
| ------------- | ------- | -------- | ------- | -------------------------------------------------------------- |
| `pattern`     | string  | yes      | none    | POSIX extended regex, or literal text with `fixed`.            |
| `path`        | string  | no       | `.`     | Directory or one file. Must resolve inside the workspace root. |
| `glob`        | string  | no       | none    | Matches the file *name* only, so `src/*.py` matches nothing.   |
| `fixed`       | boolean | no       | `false` | Treat `pattern` as literal text.                               |
| `ignore_case` | boolean | no       | `false` | Match regardless of case.                                      |
| `max_matches` | integer | no       | `200`   | Stop after this many lines and say so.                         |

## The pattern is a POSIX extended regex

Not PCRE. `\d`, `\w`, `\b`, `\s` and non-greedy `*?` are **not available** and
will not do what you expect. Use `[0-9]`, `[[:alnum:]_]` and `[[:space:]]`.

Anything containing `. * [ ] ( ) | + ? ^ $ \` and meant literally wants
`fixed: true`. Searching for `foo.bar()` as a regex quietly matches `fooXbarZZ`
too.

## Example

```sh
echo '{"pattern":"def [a-z_]+_file","glob":"*.py"}' \
  | src/builtin/tools/arctic/grep/run.sh
```

```
src/paths/resolver.py:112:def _candidates_for_file(self):
src/commands/tools.py:31:def describe_file(names, paths):
```

Bounded to the files a name pattern picks out, which is the common shape:

```sh
echo '{"pattern":"die\\(","glob":"*Controller.php"}' \
  | src/builtin/tools/arctic/grep/run.sh
```

`glob` here filters the file *name* only, and it is a convenience, not a
replacement for the `glob` tool: it cannot match a path, and it tells you nothing
about files that exist but do not contain the pattern.

Every result is a `path` `read_file` accepts, so the way through a codebase is
`glob` to see what exists, `grep` to find the line, `read_file` to read around it.

## Finding nothing is an answer

An empty search is not a failure. It exits `0` and says so, so a flow can ask a
question whose honest answer is "nowhere":

```
[grep] no matches for handle_retry
```

Take that at face value. It means the string is not in the tree you searched, so
check the tree before concluding the thing does not exist: a `path` or a `glob`
you set may be excluding it.

## Errors

Failures write one line to stderr and set an exit code from `spec.json`'s
`exit_codes`: `2` invalid input, `3` the search path does not exist, `4` not
permitted.

```
$ echo '{"pattern":"root","path":"/etc"}' | src/builtin/tools/arctic/grep/run.sh
grep: path resolves outside the workspace root: /etc
$ echo $?
4
```

The path is fully canonicalised first, so `..` segments and symlinks leaving the
workspace are rejected too, not just absolute paths.

## Portability

Recursion is `find`, not `grep -r`, and the options used are only the ones POSIX
specifies: `-E -F -i -n -e`. GNU, BSD, macOS and busybox grep disagree past that
line, and `--include`, `-P` and `-o` are not portable at all. A search therefore
returns the same thing wherever the engine runs.
