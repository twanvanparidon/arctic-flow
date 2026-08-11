<!-- Generated from docs/projects.md by packaging/sync_docs.py. Edit that file. -->

# Projects and names

Where your flows live, and how the engine decides what a name refers to.

## A project is a directory with `flows/` in it

There is nothing to initialise and no manifest.

```txt
myproject/
   flows/       one YAML file per flow, or a directory of its own name
   tools/       one directory per tool
   agents/      one directory per agent
   .arctic/     optional: the same three, searched first
```

Only `flows/` has to be there. A project with no `tools/` of its own uses the ones that ship.

`--workspace DIR` points the engine at a project. It **goes before the subcommand**, and it is
worth checking first when a name will not resolve:

```sh
atf --workspace examples/file-review lint review_file      # yes
atf lint review_file --workspace examples/file-review      # no
```

The workspace is also the directory components run in, and the boundary the shipped tools
refuse to reach outside.

## Where a name comes from

A flow says `tool: arctic/read_file` and the engine goes looking. Roots are searched in this
order, and the first match wins:

```
$ATF_PATH  →  ./.arctic  →  the workspace root  →  ~/.arctic  →  sources  →  enabled packs
           →  what ships with the engine
```

Under any root, components live in `tools/`, `agents/` and `flows/`. See
[setting up](setup.md) for `~/.arctic` and `sources`.

**Overriding is per name and total.** A project defining `deploy` replaces an inherited
`deploy` and inherits nothing from it. There is no merging and no partial override.

Where a component is *found* never changes where it *runs*: a tool executes with its working
directory set to the workspace root, wherever its directory came from.

## Namespaces

Put a component in a subdirectory and its name says so. Nothing is declared, and there is no
depth limit: a directory holding a `spec.json` is a component, and any other directory is a
namespace.

```txt
tools/
   deploy/
      notify/             ->  tool: deploy/notify
      release/tag/        ->  tool: deploy/release/tag
```

`agents/` and `flows/` group the same way, so `atf run release/sign` runs
`flows/release/sign.yaml`.

The name is the whole path. `arctic/read_file` and a bare `read_file` are **two tools**: they
do not override each other and neither falls back to the other. `spec.json` still carries only
the leaf, because the namespace is where the directory sits.

A name whose segments would leave the root is refused: `..`, an absolute path, an empty
segment.

## A flow has a second spelling

One YAML file, or a directory holding one of its own name (a **bundle**):

```txt
flows/review.yaml              ->  atf run review
flows/review/review.yaml       ->  atf run review, a bundle
```

The bundle is what gives [prompt files](flows.md#prompt-files) somewhere to live: `prompts/`
sits beside the flow. Inside a namespace the file carries the leaf, so
`flows/release/sign/sign.yaml` is `release/sign`. A bundle is still a namespace, so
`review/helper` keeps working.

Written both ways at once, the flat spelling wins and `atf list` reports the other as
shadowing.

## `arctic/` belongs to the engine

Nothing outside the engine may define a name inside it.

```sh
atf create tool arctic/read_file
# engine: 'arctic/' belongs to the engine, so 'arctic/read_file' is not a name to
#         create. Put yours in a namespace of your own: 'tool <yours>/read_file'
```

That is a security property rather than tidiness. `tool: arctic/read_file` in a flow has to
mean the contained, no-network tool that ships. If any higher root could define that name,
reading the flow would tell you nothing about what runs, and a repository you cloned is a
higher root.

So a directory in that namespace is **refused rather than used**, wherever it came from, and
`atf list` reports it under `refused` rather than leaving it merely missing. The whole
namespace is reserved, not only the names that ship, so a near miss like `arctic/read_files`
cannot read as shipped.

A [pack](components.md#what-ships) is the exception, because it ships inside the engine.

Want different behaviour from a shipped tool? Copy it under a name of your own and change the
flows that name it. That is no more work than editing the original would have been, and it
leaves the difference visible: the flow names your tool, so a reader can see it is not the
shipped one.

## Seeing what resolved

```sh
atf list
```

Every flow, tool, agent and adapter that resolves, beside the definition that won, plus every
pack and whether it is on.

Each path is written as the layer it came from, so the column stays short enough to read:

| Reads as | Came from |
| --- | --- |
| `./x` | this project |
| `$HOME/.arctic/x` | your own layer, across every project |
| `$ATF_ROOT/x` | shipped with the engine, packs included |

`$ATF_ROOT` is a label, not a variable. There is nothing to set: where the engine keeps its own
components is the engine's business, and `$ATF_PATH` is the way to add a root of your own.

- **Shadowing is marked.** A second definition higher in the search order is why an edit can
  appear to do nothing.
- A name that does not resolve reports every path it was looked for. Read that list rather than
  guessing which directory was meant.
- A tool that is only in a pack that is off says so, with the line to add.
