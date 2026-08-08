# Arctic Flow

```
   *  .  *
    \ | /       A R C T I C   F L O W
  .-- * --.     atf: push-based agentic workflows
    / | \
   *  .  *
```

A code-first engine for agentic workflows. A **flow** is a graph of steps: some run a
tool, some run a model. Each step declares **where it hands its result next**, so a flow
reads forwards, in the order it happens.

```yaml
- id: read
  tool: read_file
  input:
    path: "{{ inputs.path }}"
  push: [explain]        # <- where this result goes

- id: explain
  agent: explainer
  prompt: |
    Explain this file.
    ---
    {{ steps.read.text }}
```

That is the whole idea, and everything else follows from it. Nothing declares what it
waits for. The engine derives the reverse edges, runs whatever is ready, and delivers
results onward. Two steps pushed from one place run concurrently. A step named by two
places runs once both have arrived. A branch that is not taken is skipped, and the skip
travels downstream, so a join is reached without waiting for a path that will never
deliver.

**Why it is built this way.** Workflows are code. They live in files you can diff, review
and override, not in a UI. A flow names a graph and nothing else. Which model, which
effort and which prompt belong to the agent, in its own directory, so changing a prompt is
not a change to the workflow. A flow can also be read without running it.
`atf inspect flow` draws the graph, `atf lint` checks every reference and every component
spec, and neither calls a model.

---

## Install

One binary, Linux x86-64. No Python, no running service, no config file. The script takes
the latest release, checks its `sha256`, and installs it under `~/.local`:

```sh
curl -fsSL https://raw.githubusercontent.com/twanvanparidon/arctic-flow/main/packaging/install.sh | bash
atf --version
```

Save it and read it first if you would rather not pipe a script into a shell. It takes
`--prefix <dir>` to install elsewhere and `--version <tag>` to pin a release, or
`$ATF_PREFIX` and `$ATF_VERSION` for the piped form. Uninstalling is deleting
`~/.local/lib/atf` and the link at `~/.local/bin/atf`.

### Tab completion

```sh
eval "$(atf completion bash)"        # in ~/.bashrc, once
```

Completes the commands, their flags, and flow names, which it resolves the way `run` does:
through the lookup, from the `--workspace` on the line. bash for now.

### What else you need

| For | You need |
| --- | --- |
| The built-in `read_file` tool | `bash`, `jq`, `awk`, `realpath` |
| The built-in `write_file` tool | `bash`, `jq`, `realpath`, `wc` |
| Any **agent** step | a supported adapter's provider, installed and authenticated |
| Tool-only flows | nothing else: no key, no network |

An agent names its adapter in its `spec.json`, and the adapter is what needs something on
the machine: a CLI to spawn, or a credential in the environment. `atf list` prints the
adapters this build carries and what each one runs, so install what the agents you use ask
for.

```sh
atf list             # adapters, and every component name the engine can see
```

One of them needs nothing. Point an agent at `"adapter": "echo"` and it answers from the
request instead of from a model, so a flow's graph, its branches, its gates and every
template in it run offline and for free. Useful while you are still writing the flow; the
prompt can say `!fail` to see what a refusal does to the graph, or `!json {"verdict": …}`
to send a `switch` down the branch you want to look at.

---

## Examples

Four projects that run as they are. Read them forwards, the way the engine does:

- **[`examples/sign-release`](examples/sign-release)** is tools and secrets. Two steps and
  one key from an encrypted vault. Deterministic, no key, no network.
- **[`examples/file-review`](examples/file-review)** is agents, a branch and a join. Triage
  picks one path, the other is skipped, and the report waits for neither. A few cents to run.
- **[`examples/gated-summary`](examples/gated-summary)** is a gate. A tool has to accept the
  agent's answer before it goes anywhere, and says what was wrong with it when it does not.
- **[`examples/agent-tools`](examples/agent-tools)** grants an agent `read_file` and
  `write_file`, so it decides for itself when to read and when to write. One step where the
  same job as three would also work, and the flow header says when to prefer which.

```sh
atf --workspace examples/file-review inspect flow review_file

ATF_VAULT_PASSWORD=demo atf --workspace examples/sign-release \
    run sign_release --input path=release-notes.md

atf --workspace examples/gated-summary run summarize --input path=incident.md

atf --workspace examples/agent-tools run annotate \
    --input path=notes/incident.md --input out=out/incident.md
```

A project is a directory with `flows/` in it. There is nothing to initialise. `atf --help`
lists the rest, and only `run` calls a model: `lint` and `inspect` read a flow without
executing it.

```sh
atf --workspace examples/file-review inspect flow review_file -o md > review.md
```

### Grouping components

Put a component in a subdirectory and its name says so. There is nothing to declare and no
depth limit: a directory holding a `spec.json` is a component, and any other directory is a
namespace.

```txt
tools/
   common/
      read_file/        ->  tool: common/read_file
      write_file/
   git/
      commit/           ->  tool: git/commit
      worktree/add/     ->  tool: git/worktree/add
```

`agents/` and `flows/` group the same way, so `atf run release/sign_release` runs
`flows/release/sign_release.yaml`. The name is the whole path: `common/read_file` and
`read_file` are two tools, and overriding one does not touch the other. `atf list` prints
every name qualified.

---

## More

[CONTRIBUTING.md](CONTRIBUTING.md) has the design, the layout, how to add a component, and
the limits to know before you build on it.

## Licence

Arctic Flow is free software under the GNU General Public License, version 3 or later.
The full text is in [LICENSE](LICENSE).

    Copyright (C) 2026 Twan van Paridon

    This program is free software: you can redistribute it and/or modify it under the
    terms of the GNU General Public License as published by the Free Software Foundation,
    either version 3 of the License, or (at your option) any later version.

    This program is distributed in the hope that it will be useful, but WITHOUT ANY
    WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
    PARTICULAR PURPOSE. See the GNU General Public License for more details.

    You should have received a copy of the GNU General Public License along with this
    program. If not, see <https://www.gnu.org/licenses/>.

Use it, change it and run it however you like. Distribute a modified version and the
source goes with it, under the same licence.
