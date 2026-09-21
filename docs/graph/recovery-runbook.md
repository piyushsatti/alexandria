# Graph package recovery

Recovery is a local, explicit operation over a selected graph store. It does
not search for a “best” package or ignore a withdrawn source.

```mermaid
flowchart TD
    B[Verified package backup] --> C[Verify manifest and hashes]
    C --> D{Current withdrawal policy}
    D -->|compatible| R[Restore to a new store]
    D -->|withdrawn or inconsistent| X[Refuse without publishing]
    R --> S[Recheck selected and previous pointers]
```

Use the implementation and tests in `recovery.py`, `generation.py`, and
`tests/test_recovery.py`. Restore into a fresh destination; the current store
is never overwritten by a failed attempt.
