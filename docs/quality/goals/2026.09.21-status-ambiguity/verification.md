---
version: 2026.09.21
status: pass
---

# Verification contract

This file is the evidence ledger for the goal. A checkbox without a receipt is
not completion.

## Gate states

| State | Meaning | Next action |
|---|---|---|
| `pass` | Required checks are complete and no blocking finding remains | Advance to the next task |
| `hold` | Evidence is incomplete or meaning is unresolved | Preserve artifacts and clarify; do not advance |
| `fail` | The candidate violates a required invariant | Revert or narrow the change; retain the failed receipt |
| `inconclusive` | The test could not distinguish competing explanations | Add a better case or independent review |

## Preflight checks

- [x] Goal and phase are identified.
- [x] Source paths, revisions, hashes, and privacy scope are recorded.
- [x] Expected statuses and evidence were frozen before model input.
- [x] Code, prompt, dependency, and configuration identities are recorded.
- [x] Output directory is fresh and outside the active serving generation.
- [x] No credential, home directory, Docker socket, or working checkout is mounted
      into an untrusted model worker.

## Required receipt fields

```yaml
goal_id: quality-status-ambiguity-2026.09.21
phase: step-04 or step-05
status: pass | hold | fail | inconclusive
source_revision: <exact revision>
source_manifest_sha256: <hash>
code_revision: <exact revision>
configuration_identity: <hash or declared identity>
model_provider: <actual provider or local>
model_identity: <actual model and revision>
expectation_revision: <hash>
output_manifest_sha256: <hash>
checks:
  structural: pass | fail
  source_coverage: pass | fail | hold
  status_accuracy: pass | fail | hold
  ambiguity_handling: pass | fail | hold
  evidence_mapping: pass | fail | hold
  regression: pass | fail
findings: []
active_index_changed: false
next_action: <single bounded action>
```

If a provider or model identity cannot be verified, record that fact and mark the
receipt `hold`; do not substitute an alias for an identity.

## Phase gates

```mermaid
flowchart TD
    P[Preflight] --> Q4[Step 4 receipt]
    Q4 -->|all required fields and bounded findings| Q5[Step 5 receipt]
    Q4 -->|missing or unclear| H1[Hold Step 4]
    Q5 -->|all exit checks pass| F[Final goal decision]
    Q5 -->|meaning unresolved| H2[Hold Step 5]
    Q5 -->|invariant violated| X[Fail and retain artifacts]
```

## Final decision rule

The goal is complete only when Step 5 has a `pass` or an explicit owner-approved
`hold` that records the remaining limitation. A held result is not an accepted
graph and cannot replace the active index. Every failed or held attempt remains
available for comparison and recovery.

## Step 4 receipt recorded

The Step 4 receipt uses `status: hold` for the candidate and
`phase_status: pass-to-targeted-fix` for the bounded experiment. This keeps the
phase decision separate from candidate acceptance. The source-linked receipt and
full synthetic run are in the Knowledge repository at:

`Alexandria/graph/goals/2026.09.21-status-ambiguity/step-04-baseline/`

The receipt records source and expectation hashes, code revision, model route
identity limits, output manifest hash, mechanical coverage, two blocking
findings, and `active_index_changed: false`. Step 5 may implement only the
recorded targeted safeguards.

## Step 5 receipt recorded

The Step 5 receipt has `status: pass` and `phase_status: complete`. The
deterministic comparison passed all nine frozen cases, the stable mapping check
passed, and the quality gate reported zero blocking findings. Genuine ambiguity
is represented with `review_state: held`; the unfamiliar review rule remains a
requirement rather than accepted knowledge. The GraphReader exposes the review
receipt while keeping `accepted: false`.

The candidate remains `held-pending-owner-review`. No production index,
deployment, or model strategy changed. Failed provider/runtime attempts are
retained in the receipt, and the successful run records the configured route as
`chatgpt/gpt-5.6-sol` through the local LiteLLM gateway.

Receipt:

`Alexandria/graph/goals/2026.09.21-status-ambiguity/step-05-targeted-fix/comparison.json`

## Final gate result

Step 4 and Step 5 are complete for this bounded quality goal. The result is
eligible for later quality research, but it is not authorization to publish or
replace the active graph. Keep the candidate and both receipts available for
owner review and rollback comparison.
