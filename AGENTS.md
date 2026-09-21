# Alexandria repository instructions

Read and follow the canonical instructions in
`~/.agents/AGENTS.md` before working in this repository. This
file records only Alexandria-specific conventions.

## Repository boundary

- This repository owns the Alexandria runtime, graph implementation, tests,
  packaging, deployment manifests, product documentation, and operator tools.
- The Knowledge repository owns corpus documents, source snapshots, research,
  provenance, receipts, and evidence. Do not copy private corpus text,
  generated indexes, model caches, or credentials into tracked product files.
- The ignored `.local/` directory is workstation state. It is not part of the
  product image or repository history.

## Code and packaging

- Keep Python packages under `src/pi/alexandria/` and tests under
  `tests/pi/alexandria/`.
- Preserve the PEP 420 `pi.alexandria` namespace and use absolute imports.
- Keep the verified Python 3.12 baseline and pinned runtime dependencies unless
  a compatibility change is explicitly scoped.
- Use `uv.lock` for the development environment and run Ruff plus the locked
  pytest suite before handing off code changes.
- Keep `tools/` historical or operator-facing. Tools are not runtime imports,
  console entry points, or Docker image inputs unless explicitly promoted.

## Runtime and release safety

- The runtime image is code-only; committed data blocks are attached separately
  and mounted read-only.
- Never place credentials in source, fixtures, images, logs, or receipts.
- Preserve the current data block and rollback material while changing runtime
  or deployment behavior.
- Internal versions use CalVer `YYYY.MM.DD`; use SemVer only for an explicitly
  public release.
- Keep unrelated working-tree edits intact. Do not commit, push, merge, or
  change registered deployment identifiers without explicit authorization.
