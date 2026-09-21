# Changelog

## 2026.09.20

- Separated the product runtime from the Knowledge corpus.
- Adopted the `pi.alexandria` PEP 420 namespace and `src/` package layout.
- Moved Docker build definitions into `docker/` and product operations docs into
  `docs/`.
- Moved deployment manifests and historical curation/trial helpers into the
  product checkout; Knowledge now retains their documents, receipts, and
  evidence without executable Alexandria code.
- Added Foundry-compatible Ruff bugbear and naming checks, plus a checked-in
  `uv.lock` and repository-layout guide.
