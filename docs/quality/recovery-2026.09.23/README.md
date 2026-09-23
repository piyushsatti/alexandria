---
title: Alexandria quality recovery
version: 2026.09.23
status: recovered-and-verified
---

# Alexandria recovery — 2026.09.23

## Committed recovery

The recovered implementation is committed as `4b80fca2cabbeea9679658ff8ddcbc0cfae32a09` on
`alexandria-manual-checkpoint`. It replaces the missing local changes with a
new commit; it does not recreate their historical commit identities.
The original working checkout was preserved. No push, merge, deployment or
corpus modification was performed.

## What was recovered

The original remote history survives in `remote-baseline.bundle`. The two unpushed changes historically identified as `248cfd4` and `4c1b1ca` were reconstructed from 98 recorded file-change events and the previously used Ruff 0.16.4 formatting. The original commit objects were not recovered; do not label this checkout with those commit IDs.

The resulting diff matches the recorded combined scope: 37 files, 3,239 insertions and 106 deletions. Fresh verification: 144 Python tests and 9 Node tests passed; Ruff lint, formatting and whitespace checks passed. Reapplying the saved patch to the baseline produced the same Git tree. Every source archive file was hash-verified against the recovered checkout.

## Durable copies

The bundle, source archive, patch and raw file events remain in the local recovery directory outside this repository. Only the portable receipt and file hashes are included here.

- `recovered-source.tar.gz`: all 116 source files; excludes environments, credentials, corpus, indexes and model caches.
- `recovered-changes.patch`: binary-safe patch against the remote baseline.
- `remote-baseline.bundle`: baseline Git history.
- [recovery-receipt.json](recovery-receipt.json): verification results, limitations and artifact checksums.
- [recovered-file-sha256.json](recovered-file-sha256.json): hashes of all 37 changed files.
- `file-events.json`: source events used for reconstruction.

These copies are on the same computer, so they protect against temporary-directory cleanup but do not constitute an off-host backup. The archive was captured before committing. The recovery commit adds the same verified source tree; no push, deployment or corpus change was performed.

## Remaining work

The combined semantic/authority/provenance review still returns HOLD, matching the saved review result. Owner decisions, replayable provider evidence and candidate release verification remain pending. Recovery does not approve those items or restore a candidate index.
