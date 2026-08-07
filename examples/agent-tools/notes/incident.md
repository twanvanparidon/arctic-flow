# Incident 2026-07-14: checkout latency

At 09:12 UTC the checkout service began returning 504s. The rate climbed from
0.2% to 31% of requests over eleven minutes.

The cause was a schema migration that added an index on `orders.customer_id`
while holding an exclusive lock. Connection pool saturation followed within
four minutes.

Mitigation was to cancel the migration at 09:26. Latency recovered by 09:31.

Follow-ups: run migrations through the online-DDL path, and alert on pool
saturation rather than on error rate alone.
