# One-off analysis scripts

These three produced transcripts that other documents cite, but they had no home
and no reference: they were sitting loose in `logs/` next to their own output,
and each began with an absolute path to one developer's checkout, so nobody
else could have run them. Moved here on 1 September 2026 and the path made
repo-relative. Nothing else about them changed.

They are **not** tests and are not gate-protected. Run with `py -3.12`.

| script | what it answers | transcript |
|---|---|---|
| `horizon.py` | Is V1 a property of the world rather than of the run length? Sweeps the horizon at three spend levels. | (printed to stdout; the finding is quoted in `NOTES.md` W11) |
| `w11_ceiling_script.py` | The **constrained oracle** — the policy-free ceiling for V5 and V7 at each cell. This is where "V5's ceiling is 100%" comes from, which is the evidence behind X2's reclassification. | `logs/w11_constrained_oracle.txt` |
| `w11_coupling_script.py` | What the due-date/payday coupling fix does, measured policy-free. | `logs/w11_canon_out.txt` and neighbours |

**All three are policy-free by construction** — they measure the world and its
ceilings, not the agent. That is why the W24 belief change (`prior_w` 9 → 5,
`prior_floor` 0.5 → 0.1, `cycle_value` 0 → 0.6, `sim/w3.py` at
2026-09-01 18:08) does not make their output stale, and why they were not
re-run in the 1 September re-measurement pass. A constrained oracle that moved
when the belief moved would be a defect in the oracle.
