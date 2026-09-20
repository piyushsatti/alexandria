# Deployment

Alexandria is currently packaged as a code-only image plus one versioned data
block. The existing Saturn release is retained as rollback material while the
product split is tested.

Read [data-block.md](data-block.md) for the mount and replacement contract.
The copied Kubernetes files are historical references from the first manual
deployment and are not applied automatically. A live candidate manifest must
be generated only after the exact cluster, namespace, volume, image-loading,
gateway, and identity identifiers have been verified.

```mermaid
flowchart TD
    K[Knowledge checkout] --> P[prepare_data_block.py]
    P --> D[Release data receipt]
    A[Alexandria checkout] --> I[Code-only image]
    D --> C[Candidate container]
    I --> C
    C --> V[Parity and restart checks]
    V -->|pass| R[Route switch]
    V -->|fail| B[Retain current Saturn release]
```
