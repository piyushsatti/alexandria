---
version: 2026.09.21
status: active
---

# Goal decisions

This is the decision log for the status/ambiguity goal. New entries are appended;
older entries are not rewritten to make a later result look cleaner.

| ID | Decision | State | Reason |
|---|---|---|---|
| D-01 | Use a small synthetic contrast set plus one unfamiliar local document | accepted for this goal | It tests known failure classes without expanding private-document scope |
| D-02 | Do not make external provider calls in Step 4 by default | accepted for this goal | The first gate measures the workflow and preserves a local privacy boundary |
| D-03 | Do not refresh the active index or change deployment | accepted for this goal | Quality candidates must remain separate from the serving generation |
| D-04 | Treat unsupported status classification as held rather than silently assigning `accepted` or `proposed` | proposed default | Prevents false certainty; revisit only if the contrast set proves a separate `unclassified` label is necessary |
| D-05 | Preserve exact source quotes and source revisions as the authority | accepted for this goal | Generated prose and graph edges are derivatives, not evidence |
| D-06 | Keep model, embedding, hybrid retrieval, and reranking changes out of Step 5 | accepted for this goal | Fix the demonstrated preservation defects before changing retrieval variables |
| D-07 | Use independent human-reviewed expectations written before extraction | accepted for this goal | Assistant-authored expectations alone cannot establish semantic accuracy |
| D-08 | Enforce claim-level holds and status-inflation findings with a deterministic quality gate | accepted for this gate | The Step 4 defects are narrow enough to fix without changing model, retrieval, or deployment variables |
| D-09 | Keep the passing candidate held pending owner review | accepted for this gate | A passing comparison proves the guard behavior, not acceptance of a new graph |

## Open decisions

- Whether plain factual statements without an explicit source status need a new
  `asserted`/`unclassified` label, or whether `held` is sufficient.
- Whether the unfamiliar document should be a local architecture document or a
  fully synthetic document with a separate author.
- What minimum number of cases is enough to treat the status distinction as
  stable rather than coincidental.

Open decisions do not authorize implementation changes. Record the answer and
its evidence here before changing the task contract.
