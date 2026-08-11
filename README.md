# Arctic Flow

```
   *  .  *
    \ | /       A R C T I C   F L O W
  .-- * --.     atf: push-based agentic workflows
    / | \
   *  .  *
```

A code-first engine for agentic workflows. A **flow** is a graph of steps: some run a tool,
some run a model. Each step declares **where it hands its result next**, so a flow reads
forwards, in the order it happens.

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

That is the whole idea. Nothing declares what it waits for. The engine derives the reverse
edges, runs whatever is ready, and delivers results onward. Two steps pushed from one place
run concurrently. A branch that is not taken is skipped, and the skip travels downstream. A
case naming a step that already ran sends the work back to it, which is how a reviewer
declines a draft.

Workflows are code: files you can diff, review and override, not a UI. A flow names the
graph and nothing else, and it can be read without being run.

## Install

One binary, Linux x86-64. No Python and no running service.

```sh
curl -fsSL https://raw.githubusercontent.com/twanvanparidon/arctic-flow/main/packaging/install.sh | bash
atf --version
```

[Install](docs/setup.md) has the flags, the checkout route, and what else the machine
needs.

## Examples

Five projects that run as they are, in [examples/](examples/README.md). This one signs a
release with a key from an encrypted vault, and needs no model, no network and no key.

```sh
ATF_VAULT_PASSWORD=demo atf --workspace examples/sign-release \
    run sign_release --input path=release-notes.md
```

## Docs

[The quickstart](docs/README.md) writes a flow from nothing in about two minutes, and the
documentation behind it covers writing flows, writing tools and agents, the CLI, setting up,
every refusal, and the design.

Writing flows with Claude Code: `/plugin marketplace add twanvanparidon/claude` then
`/plugin install atf@twanvanparidon`. `/atf:create` scaffolds a component and fills it in,
`/atf:help` diagnoses one that will not lint or run. Both drive the `atf` you already have.

## Contributing

[CONTRIBUTING.md](CONTRIBUTING.md) has the build, the tests and the release.

## Licence

Free software under the GNU General Public License, version 3 or later, in
[LICENSE](LICENSE). Use it, change it and run it however you like. Distribute a modified
version and the source goes with it, under the same licence.
