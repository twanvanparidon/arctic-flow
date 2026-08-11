# Incident 2043: checkout latency, 14 March

Sample input for the `summarize` flow. Nothing here is real.

## What happened

Between 09:12 and 10:47 UTC, the checkout service answered between 4 and 30 seconds
per request, against a normal p99 of 380ms. Roughly 18,000 checkout attempts were
affected. About 2,900 of them were abandoned by the customer before the page
returned. No orders were lost, double-charged, or written twice.

## How it was found

Not by the alerts. The latency alert is wired to the mean rather than a percentile,
and the mean stayed inside its threshold for the first 40 minutes because health
checks and cached responses outnumbered real checkouts. The first report came from
a support agent at 09:51, and the on-call engineer was paged by hand at 09:58.

## Cause

A configuration change the previous evening lowered the connection pool for the
pricing database from 64 to 8. The change was meant for a batch worker that shares
the same config file, where 8 is correct and 64 had been exhausting the database's
connection limit during nightly runs. Nothing in the file separates the two
consumers, and nothing in review caught that the smaller pool would also apply to
the request path.

Under load, every checkout request queued for a connection. The queue was unbounded,
so requests waited instead of failing, and the service kept reporting itself healthy
throughout.

## Mitigation

The pool was raised back to 64 at 10:41 and latency recovered within six minutes.
The nightly batch problem the original change was meant to solve is still open.

## Follow-ups

1. Split the pricing database config so the request path and the batch worker set
   their pool sizes separately.
2. Bound the connection queue and fail fast when it is full. A checkout that fails
   in 200ms is better than one that succeeds in 30 seconds.
3. Alert on p99 rather than the mean, and exclude health checks from the metric.
4. Add the pool size to the pre-deploy diff summary, which currently shows only the
   files that changed and not the values inside them.
