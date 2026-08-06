# Git

## Never push

Asked to commit means commit. Do not push. Not the branch, not a tag, not with `--force`.
Push only when the operator asks for a push in those words.

The same holds for anything else that leaves the machine. Opening a pull request, uploading
a release, and triggering a pipeline are not part of committing.

## Never commit on main

Check the branch before you commit. If it is `main`, stop. Report that main is not
committable and ask which branch to use. Do not create one yourself, and do not commit and
sort it out afterwards.

## Commit message format

```
<type>(<scope>): <title>

- what changed
- what changed
```

Types are `fix`, `feat`, `refactor`, `chore`, `docs`. Use `docs` for prose and comments:
README, CONTRIBUTING, the rule files, and changes that only touch docstrings or comments.

Include a scope only when it tells the reader something: `engine`, `cli`, `commands`,
`paths`, `vault`, `adapters`, `builtin`, `util`, `packaging`.

The title is imperative, lowercase, no full stop, under 72 characters.

The body stays small. Two to five bullets, one line each. Say what changed, and why when
the why is not obvious. Drop the body when the title already covers it. Never walk through
the diff.

```
feat(vault): read the password from a file

- adds --vault-password-file, so scripts stop putting it in argv
- $ATF_VAULT_PASSWORD still wins when both are set
```

## Carry the issue tag from the branch

When the branch name carries an issue tag, put it in a `Refs:` trailer. The title stays
clean and the tag stays machine readable.

```
branch: feature/<issue-tag>-skip-propagation

fix(engine): stop skip propagation stalling on a join

- a join now runs once every inbound edge delivered or skipped
- unreachable branches no longer hold the pool open

Refs: <issue-tag>
```

One tag per commit. No tag in the branch name means no `Refs:` line, so do not invent one
and do not go hunting for an issue that was never mentioned.

## One change per commit

A commit holds one logical change. Do not fold a fix, a refactor, and a doc update into
one. Work that split into unrelated parts splits into separate commits.

## Trailers

Trailers come last, in one block, in this order:

```
Refs: <issue-tag>
Co-Authored-By: ...
```

`Refs:` appears only when the branch carries a tag. The `Co-Authored-By:` line names the
model that wrote the commit. Leave it in place.
