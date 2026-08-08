# Brief: "Rolling back a deploy"

Write the section a new on-call engineer reads at 3am when a deploy has gone wrong.

Audience: someone who joined this month. Assume they can use a terminal and have never
rolled anything back here before.

It has to cover, in this order:

1. How to tell a bad deploy from an unrelated incident. Two signals, no more.
2. The rollback command, and what it does not undo. Database migrations are not reverted.
3. Who to tell, and when: before the rollback if the service is degraded, after if it is
   down.

Constraints:

- Between 120 and 180 words. Not 119, not 181.
- No links. This gets read on a phone with no signal.
- Every instruction in the imperative. "Run", not "you should run".
- Say the migration caveat explicitly. Leaving it implied is the failure this section
  exists to prevent.
