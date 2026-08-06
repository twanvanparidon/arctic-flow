# Writing style

Applies to everything written for this project: docs, code comments, commit messages,
and replies in chat.

## Never use em dashes

No `—`. Do not substitute ` - ` or ` – ` for one either. Use a full stop, a comma, a colon,
or brackets. Two short sentences are better than one sentence joined by a dash.

This is about prose. Hyphens in code, flags, and compound words are fine: `--workspace`,
`push-based`, `linux-x86_64`.

## Keep it short

Say it once. Cut any sentence that restates the one before it in different words. If a
sentence and a code block say the same thing, keep the code block.

Length should match the point. A one line answer stays one line. Do not add a summary of
what you just wrote, and do not add a preamble before getting to it.

## Simple sentences

One clause where one clause works. Subject, verb, object. Split a long sentence instead of
adding a subordinate clause.

Avoid stacked constructions like "which is why", "not because X but because Y", and
sentences that carry three ideas through two semicolons.

## Plain words, exact terms

Use everyday vocabulary for the ordinary parts of a sentence. Do not reach for a longer
word when a common one fits: "use" not "utilise", "so" not "consequently", "before" not
"prior to".

Keep technical terms exact. Never soften or paraphrase a developer term to sound more
accessible. `stdout`, adapter, subprocess, reverse edge, idempotent, thread pool, and
`ThreadPoolExecutor` all stay as they are.

```
write:  The engine derives the reverse edges, then runs whatever is ready.
not:    The engine works out which connections point the other way, and then it goes
        ahead and starts up anything that happens to be good to go.
```

The rule is plain language around precise terms, not plain language instead of them.
