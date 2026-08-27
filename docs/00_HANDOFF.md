# 00 — HANDOFF

## Where things stand, 27 August 2026

Research: **done. Stop doing it.**
Production code: **none exists.** That is the entire problem.
Deadline: **5 September 2026.** Nine days.

Four simulation harnesses have been written. The current one (`sim/`) is sound
and tested. The old one (`legacy/`) is defective and frozen.

## Decided — do not relitigate

| Decision | Why |
|---|---|
| Track 3, mandate retry sequencing | Razorpay lists it as an example direction |
| Belief over balance **and** payday | Payday posterior is where the moat lives |
| `solo_shared_pd` is the policy | Best measured; +10.2 pts from pooling |
| **No** coordinated budgeting | Measured −6 pts twice. Cut. |
| No LLM on the debit-timing path | ADR-005. Deliberate, defensible. |
| **Yes** LLM on diagnosis / intervention choice / audit narrative | Needed for the track, and honest |
| Cycle-based metric, no LTV constant | Death priced automatically |
| `payday_wait` is a permanent baseline row | It is what a good rival builds in an afternoon |

## Open — genuinely unresolved

1. **How accurately can payday be estimated?** Decides whether the sophisticated
   version is worth building at all. Resolution: make the agent learn it online
   and expose its own uncertainty. Do not chase the number externally.
2. **S1 belief calibration fails.** Overconfident in the top decile.
3. **Is cross-merchant pooling legal?** See `01_FACTS.md`. Unread: Razorpay's
   privacy policy, their merchant terms, RBI PA/PG directions.
4. **Peak-hour rule: hard reject or time-shift?** Sources disagree. Stage 0
   assumes hard reject, which is the conservative choice.
5. **Does exhausting attempts cancel a mandate, or halt it?** One news report
   says cancel; Razorpay's own docs suggest halt-and-manually-chargeable.

## The three-way split — keep this true in the code

- **LLM** decides *what* to do and explains *why*
- **Bandit policy** decides *when*
- **Constraint layer** decides *whether it is allowed*

## What "done" looks like on 5 September

- [ ] Public repo, commits visible across the whole nine days
- [ ] Agent runs end to end over a batch of synthetic merchants
- [ ] One number: money recovered, with `payday_wait` printed beside it
- [ ] Audit log: every money action, with reason, constraint check, outcome
- [ ] Stopping rules explicit and demonstrable
- [ ] One failure handled gracefully, on camera
- [ ] Architecture doc, one page
- [ ] 5-minute pitch video, opening with the six errors
- [ ] `NOTES.md` full of real mess
