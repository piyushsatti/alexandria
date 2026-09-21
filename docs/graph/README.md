# Graph layer

The graph layer is an inspection-oriented derivative of committed source. It
keeps source quotes, local status, qualifications, ambiguity markers, and
provenance so a graph result cannot silently become accepted knowledge.

```mermaid
flowchart LR
    S[Committed source snapshot] --> E[Bounded extraction]
    E --> V[Contract and meaning checks]
    V --> H[Held candidate]
    H --> P[Owner selection]
    P --> R[Read-only graph package]
    R --> M[Optional graph=true MCP reads]
```

The product repository contains the pure implementation, offline fixtures, and
tests. Corpus documents, provider responses, receipts, and graph run outputs
remain in the Knowledge repository and are never copied into the product image.

## Safety boundary

- Source scope is explicit and committed; a working checkout is not mounted.
- The model receives data as text and has no shell, filesystem, network, or MCP
  tools.
- A candidate remains held until the owner selects it.
- Source hashes and graph receipts are checked before a package can be read.
- Withdrawn source hashes block selection, restore, and backup.
- The graph reader accepts only its fixed package contract and rejects symlinks,
  traversal, tampered SQLite/JSONL, and unknown files.

## Local checks

```text
python3 -m unittest discover -s tests/pi/alexandria/graph -p 'test_*.py'
node --test tests/pi/alexandria/graph/test_preservation*.mjs
```

The packaged `src/pi/alexandria/graph/assets/pi-trial` scripts are the narrow
preservation contract used by the offline tests. They are not part of the
runtime image and do not choose a provider, read credentials, or publish a
graph automatically. An installed package resolves these assets from its own
package data; a checkout may override them with `ALEXANDRIA_GRAPH_ASSET_ROOT`.
The pinned Node dependencies remain operator-managed rather than vendored;
install them with `npm ci --prefix src/pi/alexandria/graph/assets/pi-trial`, or
point `ALEXANDRIA_GRAPH_NODE_MODULES` at an equivalent dependency directory.

See [WORKFLOW.md](WORKFLOW.md) for the operator boundary,
[preservation-workflow.md](preservation-workflow.md) for the held-candidate
contract, [editorial-workflow.md](editorial-workflow.md) for Humanizer and
ambiguity handling, and [recovery-runbook.md](recovery-runbook.md) for local
package recovery.
