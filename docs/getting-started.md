# Getting started

This guide assumes the Knowledge repository already exists on GitHub and that
you want to serve one exact revision through Alexandria.

## 1. Prepare the two checkouts

The Knowledge checkout contains the committed Markdown/text corpus. The
Alexandria checkout contains the runtime and build tools. Only the trusted build
machine needs access to the private Knowledge repository. The committed corpus
manifest is the release allowlist: Alexandria never falls back to indexing
every Markdown or text file in the checkout. Records marked held or excluded
are preserved for review but cannot enter a committed data block.

Every record with `source_status: present` must also name a repository-relative
`source` path and its exact `source_sha256`. The runtime reads that blob from
the selected Git revision and verifies the hash before it uses the record. A
missing path, unsafe path, missing hash, or mismatch fails the build closed;
the snapshot copy is not treated as proof of provenance by itself.

```bash
cd /path/to/alexandria
uv sync

KNOWLEDGE=/path/to/Knowledge
ACTIVE=/tmp/alexandria-index
RELEASE=/path/to/alexandria-data-2026.09.20
MODEL=BAAI/bge-small-en-v1.5
```

Check out the desired Knowledge commit before continuing. Alexandria indexes
only committed `.md` and `.txt` files; uncommitted working-tree changes are not
served.

```bash
REV=$(git -C "$KNOWLEDGE" rev-parse HEAD)
```

## 2. Build the index

The first build may download the selected embedding model. It creates a new
LanceDB generation and advances `active.json` only after the build succeeds.

```bash
uv run python -m pi.alexandria.runtime.app index \
  --source "$KNOWLEDGE" \
  --model "$MODEL" \
  --data "$ACTIVE" \
  --manifest Alexandria/corpus/corpus-manifest.json
```

If you already have an index, you can skip this step only when its active
manifest names the same Knowledge revision, embedding model, and corpus
manifest hash. An older all-files index must be rebuilt before it can be
packaged as a release data block.

## 3. Prepare the immutable data block

This step verifies that the index's source snapshot matches the exact GitHub
commit byte-for-byte. It creates the release receipt and an empty optional
inbound area.

```bash
uv run python -m pi.alexandria.runtime.prepare_data_block \
  --knowledge "$KNOWLEDGE" \
  --active-data "$ACTIVE" \
  --revision "$REV" \
  --model "$MODEL" \
  --output "$RELEASE" \
  --manifest Alexandria/corpus/corpus-manifest.json
```

The data block contains:

```text
/data/active.json
/data/builds/<generation>/source/...
/data/builds/<generation>/LanceDB tables
/data/models/...
/data/release-data-receipt.json
/data/.alexandria-inbound/...
```

The release receipt records the manifest hash, selected paths, held paths, and
excluded records. A source snapshot without verified provenance is held and is
not copied into the committed LanceDB generation. The source hash checks are
performed against the exact `REV`, so a later working-tree edit cannot silently
change the released bytes.

The committed portion is mounted read-only. The runtime never needs the GitHub
token or the original source checkout.

## 4. Build and run locally

Build the code-only image from the Alexandria repository:

```bash
docker build -t alexandria:2026.09.20 -f docker/Dockerfile .
```

Run it over stdio for a local MCP client:

```bash
docker run --rm -i \
  --network none \
  --read-only \
  --mount type=bind,src="$RELEASE",dst=/data,readonly \
  alexandria:2026.09.20 serve --data /data
```

For a local HTTP check, use Streamable HTTP and publish port 8000 only on a
trusted interface:

```bash
docker run --rm \
  --read-only \
  --mount type=bind,src="$RELEASE",dst=/data,readonly \
  -p 127.0.0.1:8000:8000 \
  alexandria:2026.09.20 serve --data /data \
    --transport streamable-http --host 0.0.0.0 --port 8000
```

## 5. Connect a client

- **Codex or another local client:** register the Docker command as a stdio
  MCP server. It can use the five read-only tools immediately.
- **ChatGPT or another remote client:** run Streamable HTTP behind an HTTPS
  gateway, configure OAuth discovery and token validation, then provide the
  public MCP resource URL to the client.
- **Inbound submissions:** add a writable `/inbound` mount and
  `--enable-inbound` only when you deliberately want `submit_inbound` enabled.

Alexandria is not a graphical editor. The MCP client is the viewer: use
`list_files` to discover the tree, `search` or `text_search` to locate material,
and `read_document` to retrieve the source text.

## 6. Update the service

When the GitHub Knowledge repository changes, repeat the index and data-block
steps for the new commit. Test the new image/data-block pair before replacing
the running service. Retain the previous pair so rollback does not require
rebuilding the old index.

## 7. Closed-loop transfer

When the target laptop has no registry or network access, export the image and
create an offline bundle. The bundle contains the image archive, complete data
block, release manifest, checksums, and a stdio configuration example.

```bash
docker save alexandria:<release> -o alexandria-image.tar
python tools/create_offline_bundle.py \
  --image-archive alexandria-image.tar \
  --data-block /path/to/alexandria-data \
  --output alexandria-<release>-offline.tar.gz \
  --product-revision <alexandria-revision> \
  --release <release> \
  --image-reference alexandria@sha256:<digest>
```

Transfer the archive privately, verify its checksum, extract it, load
`alexandria-image.tar`, and register the command from `stdio-config.json` with
the local MCP client. The generated manifest is the authority for the exact
product revision, indexed Knowledge revision, model, and image archive hash.
