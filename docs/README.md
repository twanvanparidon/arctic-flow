# Arctic Flow docs

A flow is a graph of steps: some run a tool, some run a model. Each step declares where it
hands its result next.

Install it, then write one. This costs nothing and needs no key:

```sh
curl -fsSL https://raw.githubusercontent.com/twanvanparidon/arctic-flow/main/packaging/install.sh | bash

mkdir myproject && cd myproject
atf create flow hello                        # writes flows/hello.yaml
atf lint hello                               # checks it without running it
atf run hello --input path=flows/hello.yaml  # reads the flow back to you
```

Then open `flows/hello.yaml`. It is commented, it runs as it is, and the agent step is
waiting for you, commented out.

| Page | Covers |
| --- | --- |
| [Writing flows](flows.md) | the YAML: templates, branching, checks, loops, secrets |
| [Components](components.md) | what ships, writing a tool or an agent, granting tools, cost |
| [The CLI](cli.md) | every command and flag |
| [Projects and names](projects.md) | what a project is, and which definition of a name wins |
| [Setting up](setup.md) | install options, and the three settings in `config.yaml` |
| [Reference](reference.md) | every refusal, every variable, the glossary |
| [Design](design/README.md) | how the engine works, and why |

The first three are the ones to read in order. The next two answer "why did it not resolve"
and "how do I configure this", which are questions you get later.

The [six examples](../examples/README.md) run as they are. Copy the nearest one.

Ask the engine before you read anything: `atf lint` says what is wrong, `atf inspect flow`
draws the graph, `atf list` says which definition of a name won. None of them calls a model.

Before you pay for a run, [do it for free](components.md#cost-and-running-for-free): one
setting turns every agent step into an offline echo, so the graph, its branches and its loops
run with no model and no network.
