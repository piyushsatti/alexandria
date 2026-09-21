# Getting started

This guide assumes the Knowledge repository already exists on GitHub and that
you want to serve one exact revision through Alexandria.

## 1. Prepare the two checkouts

The Knowledge checkout contains the committed Markdown/text corpus. The
Alexandria checkout contains the runtime and build tools. Only the trusted build
machine needs access to the private Knowledge repository.

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
  --data "$ACTIVE"
```

If you already have an index, you can skip this step only when its active
manifest names the same Knowledge revision and embedding model.

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
  --output "$RELEASE"
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
