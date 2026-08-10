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
  tool: arctic/read_file
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
deliver. A case naming a step that already ran sends the work back to it, which is how a
reviewer declines a draft, and `max_loops` says how many times it may.

**Why it is built this way.** Workflows are code. They live in files you can diff, review
and override, not in a UI. A flow names a graph and nothing else. Which model, which
effort and which prompt belong to the agent, in its own directory, so changing a prompt is
not a change to the workflow. A flow can also be read without running it.
`atf inspect flow` draws the graph, `atf lint` checks every reference and every component
spec, and neither calls a model.

---

## Install

One binary, Linux x86-64. No Python and no running service. The script takes the latest
release, checks its `sha256`, and installs it under `~/.local`:

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
| `arctic/read_file` | `bash`, `jq`, `awk`, `realpath` |
| `arctic/write_file` | `bash`, `jq`, `realpath`, `wc` |
| `arctic/edit_file` | `bash`, `jq`, `realpath`, `mktemp`, `cmp` |
| `arctic/grep` | `bash`, `jq`, `find`, `grep`, `sed` |
| `arctic/glob` | `bash`, `jq`, `find`, `sed`, `sort` |
| `arctic/fetch_url` | `bash`, `jq`, `curl`, and a network |
| Any **agent** step | a supported adapter's provider, installed and authenticated |
| Tool-only flows | nothing else: no key, no network |

An agent names its adapter in its `spec.json`, and the adapter is what needs something on
the machine: a CLI to spawn, or a credential in the environment. `atf list` prints the
adapters this build carries and what each one runs, so install what the agents you use ask
for.

```sh
atf list             # every name that resolves, and where each was found:
                     #   ./x, $HOME/.arctic/x, $ATF_ROOT/x
```

### The tools that ship

Six, all under the `arctic/` namespace, all contained to the workspace:

| Tool | Does |
| --- | --- |
| `arctic/read_file` | Returns one file verbatim, or several with a header each. |
| `arctic/write_file` | Writes a file. Refuses to clobber unless told to. |
| `arctic/edit_file` | Replaces an exact string in a file. Refuses an ambiguous match. |
| `arctic/glob` | Lists the paths matching a shell pattern. |
| `arctic/grep` | Finds a pattern across the tree, as `path:line:text`. |
| `arctic/fetch_url` | Fetches an `http(s)` URL and returns the body undecorated. |

`glob` finds files, `grep` finds text in them, `read_file` returns them: that is the
usual order, and doing it in that order is much cheaper than reading a tree to find
one thing.

`write_file` takes whole contents and `edit_file` takes the old text and the new, so
a one line change to a large file costs one line rather than all of it.

The first five cannot reach outside the workspace root: a path is canonicalised
before it is used, so `..` and a symlink pointing out are both refused. `fetch_url`
is the one that touches the network, and it touches nothing else: its
`permissions.filesystem` is `none`.

Each has a `tool.md` beside its `spec.json` saying when to use it and when not to. That
file is what a model is given, so it is worth reading before granting one.

One of them needs nothing. Point an agent at `"adapter": "echo"` and it answers from the
request instead of from a model, so a flow's graph, its branches, its gates and every
template in it run offline and for free. Useful while you are still writing the flow; the
prompt can say `!fail` to see what a refusal does to the graph, or `!json {"verdict": …}`
to send a `switch` down the branch you want to look at.

### Packs: more tools, switched off

A pack is a set of first-party tools that ships in the binary and does nothing until you
say so. Three ship today:

```yaml
# ~/.arctic/config.yaml
packs:
  - git
  - github        # or bitbucket
```

| Pack | Holds | Needs |
| --- | --- | --- |
| `git` | the repository a flow runs in: status, log, diff, show, branch, add, commit, checkout | `git`, `jq` |
| `github` | pull requests: open, status, comment. Also GitHub Enterprise | `curl`, `jq`, `git` |
| `bitbucket` | the same three, for Bitbucket **Cloud** | `curl`, `jq`, `git` |

| Tool | | Does |
| --- | --- | --- |
| `arctic/git/status` | read | Branch, staged, unstaged, untracked. `clean` when there is nothing. |
| `arctic/git/log` | read | Recent commits, by ref or by path. |
| `arctic/git/diff` | read | The worktree, the index, or the difference from a ref. |
| `arctic/git/show` | read | One commit: its message and what it changed. |
| `arctic/git/branch` | read | Which branches exist, newest first. |
| `arctic/git/add` | write | Stages named paths. |
| `arctic/git/commit` | write | Records what is staged. |
| `arctic/git/checkout` | write | Switches branches, or creates one. |

`atf list` shows every pack and whether it is on. A flow naming a tool from a pack that is
off fails with the line to add, at `lint` time as well as at `run` time.

**Why switch it off at all, when it is already in the binary?** Because three of these
write. A pack is consent rather than installation: an engine nobody configured cannot be
talked into a commit by a flow that was merely run. Granting one of the three to an agent
needs `unattended: true` on top, which is the engine's ordinary gate for a tool that writes.

**What is deliberately not in it** is as much the point as what is. Nothing that leaves the
machine, so no `push`, `pull` or `fetch`, and `permissions.network` is `false` for the whole
pack. Nothing that destroys work, so no `reset`, `rebase`, `clean` or `--force`, and
`checkout` moves between branches without ever restoring a file. No `add -A`, because paths
being named is how an unrelated file stays out of a commit nobody reviewed. And no
`--no-verify`: a repository's hooks are the checks its owner decided a commit must pass.

Every tool acts on the repository whose root **is** the workspace. A flow run in
`myrepo/subproject` is refused rather than quietly reporting on the whole of `myrepo`. To
work on the outer repository, point the engine at it: `atf --workspace myrepo run …`.

A pack is not a `source`. A source is a directory you cloned, so it sits below `~/.arctic`
and may not define anything under `arctic/`. A pack ships with the engine, so it can, and
`arctic/git/commit` in a flow means the tool that shipped under that name.

### The forge packs

`github` and `bitbucket` open pull requests, read their state and comment on them.

```yaml
- id: pr
  tool: arctic/github/pr/status
  secrets: [GITHUB_TOKEN]
  switch: "{{ this.json.checks.failure }}"
  cases:
    "0": [approve_note]
  default: [failing_note]

- id: failing_note
  tool: arctic/github/pr/comment
  secrets: [GITHUB_TOKEN]
  input:
    body: "These are red: {{ steps.pr.json.checks.failing }}"
```

They answer in **JSON with the same field names**, so swapping `arctic/github/pr/status`
for `arctic/bitbucket/pr/status` changes the tool name and nothing else. Where a forge
genuinely cannot answer it says `null` rather than inventing one: Bitbucket reports no
`mergeable` without a dry-run merge, so it returns `null` there and you gate on `checks`
and `reviews` instead.

The token lives in the vault and reaches exactly the step that declares it:

```sh
atf vault set GITHUB_TOKEN
```

**No agent can be granted these tools.** Every one declares `secrets`, and the engine
refuses to grant a tool that expects a credential, because nothing scopes one to a single
in-turn call. So opening a pull request or commenting is always a step the flow decided
on, never something a model does mid-turn. The token never reaches `argv` either: `curl`
reads the header from a private config file, because `-H` would show it to `ps`.

Neither pack approves, requests changes, merges, closes or pushes. Approving is a verdict
that counts in a branch protection rule, and a flow that could cast one could approve its
own work.

---

## Examples

Five projects that run as they are. Read them forwards, the way the engine does:

- **[`examples/sign-release`](examples/sign-release)** is tools and secrets. Two steps and
  one key from an encrypted vault. Deterministic, no key, no network.
- **[`examples/file-review`](examples/file-review)** is agents, a branch and a join. Triage
  picks one path, the other is skipped, and the report waits for neither. A few cents to run.
- **[`examples/gated-summary`](examples/gated-summary)** is a gate. A tool has to accept the
  agent's answer before it goes anywhere, and says what was wrong with it when it does not.
- **[`examples/draft-review`](examples/draft-review)** is a loop. A reviewer sends the draft
  back to the writer until it passes or runs out of passes, and the writer is handed what
  the reviewer said. Read it against `gated-summary` for when to use which.
- **[`examples/agent-tools`](examples/agent-tools)** grants an agent `arctic/read_file`
  and `arctic/write_file`, so it decides for itself when to read and when to write. One step
  where the same job as three would also work, and the flow header says when to prefer which.

```sh
atf --workspace examples/file-review inspect flow review_file

ATF_VAULT_PASSWORD=demo atf --workspace examples/sign-release \
    run sign_release --input path=release-notes.md

atf --workspace examples/gated-summary run summarize --input path=incident.md

atf --workspace examples/draft-review run draft_review --input path=brief.md

atf --workspace examples/agent-tools run annotate \
    --input path=notes/incident.md --input out=out/incident.md
```

A project is a directory with `flows/` in it. There is nothing to initialise. `atf --help`
lists the rest, and only `run` calls a model. `lint` and `inspect` read without executing
anything: `list` says which definition of a name wins, `inspect` says what is in it.

```sh
atf --workspace examples/file-review lint                       # every flow in the project
atf --workspace examples/file-review inspect flow review_file -o md > review.md
atf --workspace examples/file-review inspect agent summarizer   # its system prompt
atf inspect tool arctic/read_file                               # what it may touch
```

`lint` with no flow checks every flow in scope and reports all of them before exiting
non-zero, which is the shape a pipeline wants: one run, every answer.

### Starting your own

`create` writes one component from the scaffold that ships with the engine:

```sh
atf create flow review          # flows/review.yaml
atf create agent reviewer       # agents/reviewer/: spec.json, agent.md
atf create tool deploy/notify   # tools/deploy/notify/: spec.json, tool.md, run.sh
```

It lands where the lookup reads first: `./.arctic` when the project keeps one, the project
root otherwise. What it writes runs as it is. A scaffolded flow reads a file through a
built-in tool, so `lint` passes and `run` works before any agent exists to name, and the
agent step is commented out beside it, waiting for one. Nothing is overwritten.

### Prompts in their own files

A prompt is the long part of a flow, and inlining it buries the graph. Give the flow a
directory of its own name and the prompts sit beside it:

```txt
flows/
   review/
      review.yaml           ->  atf run review
      prompts/
         triage.md
         report.md
```

`flows/review/review.yaml` **is** the flow `review`: the directory is not part of the name.
A step then names a file instead of carrying the text.

```yaml
- id: report
  agent: reporter
  prompt_file: report       # reads prompts/report.md
```

The rule is `prompts/` beside the flow file, so a flat `flows/review.yaml` still works and
reads from `flows/prompts/`. Naming both `prompt` and `prompt_file` on one step is refused,
and a file that is not there fails `atf lint` rather than the run.

### Prompts that leave out what did not run

A skipped branch and a loop's first pass are the same problem: the template reads a step that
has no result. Guard the section and it is left out.

```yaml
prompt: |
  Summary:
  {{ steps.summarize.text }}

  {% if steps.risk_scan %}
  Risk findings:
  {{ steps.risk_scan.text }}
  {% else %}
  No risk review was run.
  {% endif %}
```

**A step that did not run is false**, so that is the whole test. `{% if not … %}` inverts it,
`{% else %}` is optional, and they nest. Null, false, `0`, an empty string, an empty list and
an empty object are false too.

The branch that is not taken is never rendered, which is what makes a deeper reference safe:
`{{ steps.risk_scan.json.severity }}` has nothing to reach into until the step has run. A tag
alone on its line takes the line with it, so the prompt has no holes where the tags were.

Without a guard a skipped step still resolves, as the literal `(not run)`. That is still
there, but it means telling the model what the placeholder means. `examples/file-review`
carries the conditional version, and `examples/draft-review` uses one for a loop's first pass.

### Components you keep across projects

`atf init` creates `~/.arctic`, which is a search layer under every project:

```sh
atf init             # ~/.arctic/: tools/, agents/, flows/, config.yaml
```

A tool you put in `~/.arctic/tools/` resolves from any directory, and a project still
overrides it by defining the same name. Run `init` again after an upgrade and it adds
whatever is missing, leaving what is there alone.

`config.yaml` beside them holds the three things neither a flow nor a spec should:

```yaml
run:
  max_minutes: 240          # a ceiling on any one run

sources:                    # more directories to search, laid out the same way
  - ~/work/arctic-components

packs:                      # shipped tool packs to switch on
  - git
```

`max_minutes` is a safeguard rather than a tuning knob, so no flow can raise it. When it
fires the run fails and its tools are stopped; an agent turn already in flight is bounded
by its own `timeout_seconds` instead, so the real stop can be one turn later than this.

`sources` sit below `~/.arctic` and above the built-ins, so a shared library can replace a
shipped tool but never one you or the project defined.

`packs` names what is already in the binary, so nothing is downloaded and there is no
version to keep in step. A name that is not a pack is refused, and so is an unknown key.

### Grouping components

Put a component in a subdirectory and its name says so. There is nothing to declare and no
depth limit: a directory holding a `spec.json` is a component, and any other directory is a
namespace.

```txt
tools/
   arctic/             <- the engine's own. Yours may not go here
      read_file/        ->  tool: arctic/read_file
      grep/
   git/
      notify/           ->  tool: deploy/notify
      release/tag/      ->  tool: deploy/release/tag
```

`agents/` and `flows/` group the same way, so `atf run release/sign_release` runs
`flows/release/sign_release.yaml`. The name is the whole path, so `arctic/read_file` and a
`read_file` of your own are two tools and overriding one does not touch the other.
`atf list` prints every name qualified.

The first segment says who a component came from, the way `vendor/package` does in
Composer. **`arctic/` is the engine's, and nothing else may define a name inside it:**

```sh
atf create tool arctic/read_file
# engine: 'arctic/' belongs to the engine, so 'arctic/read_file' is not a name to
#         create. Put yours in a namespace of your own: 'tool <yours>/read_file'
```

That is a security property rather than tidiness. `tool: arctic/read_file` in a flow has to
mean the contained, no-network tool that ships, and if any higher root could define that
name then reading the flow would tell you nothing about what runs. A repository you cloned
is a higher root. So a directory in that namespace is refused rather than used, by name and
wherever it came from, including `$ATF_PATH` and a source; `atf list` reports it under
`refused` so it is not merely missing. Want different behaviour from a shipped tool? Copy it
under a name of your own and change the flows that name it, which is the same edit, said out
loud.

### Writing them with Claude Code

Two skills, installed as a plugin:

```
/plugin marketplace add twanvanparidon/claude
/plugin install atf@twanvanparidon
```

`/atf:create` scaffolds a flow, an agent or a tool and fills it in, and does not report the
work done until `atf lint` passes. `/atf:help` diagnoses one that will not lint or will not
run: it asks the engine before it reads the YAML, and carries references for the parts no
command prints.

Both drive the `atf` you already have, so neither is a second copy of the engine. The plugin
is [.claude-plugins](.claude-plugins) here; the marketplace serving it is
[twanvanparidon/claude](https://github.com/twanvanparidon/claude).

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
