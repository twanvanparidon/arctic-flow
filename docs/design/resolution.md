# Resolution

`paths/`. Components are named, not pathed, and `resolver.py` decides which definition of a
name wins.

```
$ATF_PATH  →  ./.arctic  →  the workspace root  →  ~/.arctic  →  sources  →  enabled packs
           →  src/builtin
```

First match wins, per name and totally. There is no merging and no partial override, because a
component that inherited half of another would make `atf inspect` a lie.

Where a component is **found** never changes where it **runs**: a tool executes with its
working directory set to the workspace root. Otherwise a tool inherited from `~/.arctic` would
act on a different tree than the same tool copied into the project, and moving one would
change what it did.

## Namespaces need nothing declared

A directory holding a `spec.json` is a component. Any other directory is a namespace. That one
rule gives arbitrary depth with no registry, no manifest and nothing to keep in step.

The name is the whole path, so `arctic/read_file` and a bare `read_file` are two tools that
neither override nor fall back to each other. `spec.json` carries only the leaf, because the
namespace is where the directory sits and the spec has no way of knowing that.

`check_name` refuses a name whose segments would leave the root: `..`, an absolute path, an
empty segment. That check sits in the resolver rather than in `lint`, so one place covers
`run`, a grant and `mcp-serve` alike. A flow can arrive by clone.

The first segment is a **vendor** segment in the `vendor/package` sense.

## A flow has a second spelling

`flows/review/review.yaml` is the flow `review`, a **bundle**, and the directory is not part of
the name. `_bundle_file` decides it. Inside a namespace the file carries the leaf, so
`release/sign` is `flows/release/sign/sign.yaml`.

A bundle is **also** still a namespace, so `flows/review/helper.yaml` remains `review/helper`
and nothing that resolved before stops resolving.

Written both ways at once the flat spelling wins, and `find_all` reports the other as
shadowing. `_collect` reads files before directories so `list` agrees with `find` by
construction rather than by luck.

The bundle exists so `prompt_file` has somewhere to read from. That is its whole reason.

## `arctic/` is refused, not preferred

`ENGINE_NAMESPACE` is the engine's, and nothing outside `builtin_root()` may define a name
inside it.

The important word is **refused**. Quietly preferring the built-in would leave someone editing
a directory that does nothing, and reporting "not found" would be a lie. So `find` raises, and
`list` drops the contested name and reports it under `refused`.

The reservation covers the **whole namespace**, not only the names that ship. A near miss like
`arctic/read_files` cannot read as shipped, and a new built-in can never collide with a name
somebody already had.

`create` refuses the name too, before a directory exists, so the failure arrives at the moment
someone chose the name rather than the moment a flow ran.

This is a security property, not tidiness. `tool: arctic/read_file` in a flow has to mean the
contained, no-network tool that ships. If any higher root could define it, reading a flow
would tell you nothing about what runs, and a cloned repository is a higher root.

`find_all` deliberately does **not** raise, because `commands.inventory` calls it for every
listed name and a listing has to survive the thing it exists to report. `intruders` and
`all_intruders` are what `find` and `list` read instead.

## Packs sit inside the built-in root

A pack is an ordinary root, `tools/`/`agents/`/`flows/` with a `pack.json` beside them,
spliced in when `config.yaml` names it.

It lives **inside** `builtin_root()`, and that is the whole design. `arctic/` resolves inside
the built-in root or nowhere, so a pack may define `arctic/git/commit` where a source never
can, and the reservation, the intruder refusal and the listing rules all cover it with no
exception added.

The opt-in is **consent rather than installation**: the code is already in the binary. A pack
holds tools that write, and an install nobody configured has none of them.

An unknown pack name is refused in `Paths._check_packs`, because a typo would otherwise build
a root that does not exist, which is silently dropped. `find` names the pack and the line to
add when a tool is only in one that is off, so `lint` and `run` both say what to do rather
than "unknown tool".

## Sources sit below your home layer

`sources` are extra roots named by `config.yaml`. They sit below `~/.arctic` and above the
built-ins, so a shared library never replaces something you or the project defined, and cannot
define anything under `arctic/` at all.

A source that is not there is skipped rather than refused, because a repository you have not
cloned yet should not break every command. A relative path **is** refused, since it would mean
something different from each directory you ran in.

`Paths` loads `config.yaml` eagerly, so a broken config stops every command rather than the
one that happened to need the key.

## Granted tools flatten

A granted tool reaches a turn under `flat_name`, where the separator is `__`, because
`mcp__atf__<tool>` cannot carry a slash. `cli.mcp_server` decides it and
`adapters.claude_code` writes the same string into `--allowedTools`. Drift between those two
and every tool in the turn is unpermitted, which reaches a user as a model saying they do not
work.

Never undo it by string surgery: `git__commit` is a legal directory name, so the server keeps
the mapping, and `validate` refuses a grant where two names flatten onto one.
