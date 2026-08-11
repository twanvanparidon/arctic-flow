<!-- Generated from docs/setup.md by packaging/sync_docs.py. Edit that file. -->

# Setting up

Getting `atf` onto the machine and configured. Nothing here is per project: for that, see
[projects](projects.md).

## Install

One binary, Linux x86-64. No Python, no running service.

```sh
curl -fsSL https://raw.githubusercontent.com/twanvanparidon/arctic-flow/main/packaging/install.sh | bash
atf --version
```

It installs under `~/.local`. Use `--prefix <dir>` or `--version <tag>` to change that, or
`$ATF_PREFIX` and `$ATF_VERSION` for the piped form. Uninstalling is deleting
`~/.local/lib/atf` and the link at `~/.local/bin/atf`.

Prereleases are tagged `vX.Y.Z-rc.N` and stay off the default, so the one-liner never installs
a candidate. Name one with `--version` to get it.

From a checkout, `python3 src/main.py …` is the same program. It reports `0.0.0.dev0`, because
the version is stamped on from the tag at build time.

Tab completion is one line in `~/.bashrc`, and covers commands, flags and flow names. See
[the CLI](cli.md#completion).

```sh
eval "$(atf completion bash)"
```

## What else you need

Nothing for a tool-only flow beyond the ordinary POSIX utilities. The shipped tools want
`bash` and `jq`, plus `awk`, `realpath`, `find`, `grep`, `sed`, `sort`, `wc`, `mktemp`, `cmp`
and `curl` between them. Each tool declares its own list under `requires`, and
`atf inspect tool <name>` prints it.

An agent step needs its adapter's provider. `claude_code` spawns the `claude` CLI, so that has
to be installed and authenticated. `echo` needs nothing and answers from the request, which is
how you [run a flow offline and for free](components.md#cost-and-running-for-free).

## ~/.arctic

Optional. `atf` runs with no configuration at all. Create it when you want components across
projects, or want to change one of the three settings below.

```sh
atf init        # ~/.arctic/: tools/, agents/, flows/, config.yaml
```

Idempotent, and it never overwrites. Run it again after an upgrade and it adds whatever is
missing. `--workspace` does not apply: `init` always writes your real home directory.

Anything you put in `~/.arctic/tools/`, `agents/` or `flows/` resolves from every project. See
[projects](projects.md#where-a-name-comes-from).

## config.yaml

Three keys, all optional. An unknown key is refused rather than ignored, and a broken file stops
every command rather than one.

```yaml
run:
  max_minutes: 240          # a ceiling on any one run

sources:                    # more roots to search, laid out like ~/.arctic
  - ~/work/arctic-components

packs:                      # shipped tool packs to switch on
  - git
```

### run.max_minutes

A ceiling on the whole of one run, in minutes. A safeguard rather than a tuning knob, so **no
flow can raise it**. No value means no ceiling.

When it fires the run fails and its tools are stopped. An agent turn already started cannot be
reached, so it runs to its own `timeout_seconds` and the real stop can be one turn later.

### sources

Extra roots to search, in the order listed. Each is a directory laid out the way `~/.arctic`
is. Absolute or `~/` only, and one that is not there is skipped rather than refused.

They sit below `~/.arctic` and above the built-ins, and may not define anything under
`arctic/`. [Projects](projects.md#where-a-name-comes-from) has the full order.

### packs

Which of the tool sets that shipped in the binary are switched on. Nothing is downloaded and
there is no version to keep in step. A name that is not a pack is refused, and the refusal
lists the ones there are.

They ship switched off because some of them write: this line is consent, not installation.
[Components](components.md#what-ships) has what each one holds.

```sh
atf list        # every pack, and whether it is on
```
