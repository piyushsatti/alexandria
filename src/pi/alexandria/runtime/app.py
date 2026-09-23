"""Alexandria indexing and an attached-data-block MCP over stdio or HTTP."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import selectors
import subprocess
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

try:
    from mcp.server.fastmcp import Context
except ImportError:  # pragma: no cover - runtime image always includes MCP
    Context = object

from pi.alexandria.runtime.corpus import (
    DEFAULT_MANIFEST,
    git_revision,
    load_selection,
    selection_summary,
)
from pi.alexandria.runtime.listing import (
    committed_tree,
    inbound_tree,
    validate_relative_path,
)


def git(root, *args):
    return subprocess.check_output(
        ["git", "-c", f"safe.directory={root}", "-C", str(root), *args]
    )


def committed_documents(root, corpus_manifest=DEFAULT_MANIFEST):
    """Read only the manifest-selected committed corpus blobs."""
    selection = load_selection(Path(root), manifest_path=corpus_manifest)
    revision = git_revision(Path(root))
    for item in selection["selected"]:
        try:
            text = item["bytes"].decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValueError(f"Corpus file is not UTF-8: {item['path']}") from error
        yield revision, item["path"], text


def passages(text, maximum=800):
    """Small contiguous character windows; hierarchy-aware chunking is deferred."""
    for start in range(0, len(text), maximum):
        piece = text[start : start + maximum]
        if piece.strip():
            yield start, piece


def embedder(model, data):
    from fastembed import TextEmbedding

    return TextEmbedding(model_name=model, cache_dir=str(data / "models"), threads=2)


def token_counter(engine):
    from tokenizers import Tokenizer

    tokenizer = Tokenizer.from_str(engine.model.tokenizer.to_str())
    tokenizer.no_truncation()
    tokenizer.no_padding()
    return lambda text: len(tokenizer.encode(text).ids)


def bounded_passages(text, count_tokens, maximum_tokens=480):
    """Keep exact character offsets while avoiding the selected model's truncation."""
    pending = list(reversed(list(passages(text))))
    while pending:
        offset, piece = pending.pop()
        if count_tokens(piece) <= maximum_tokens:
            yield offset, piece
        else:
            middle = len(piece) // 2
            if not middle:
                raise ValueError("Cannot fit passage in embedding token budget")
            pending.extend(
                [(offset + middle, piece[middle:]), (offset, piece[:middle])]
            )


def build(root, data, model, corpus_manifest=DEFAULT_MANIFEST):
    import lancedb
    import pyarrow as pa

    data.mkdir(parents=True, exist_ok=True)
    selection = load_selection(Path(root), manifest_path=corpus_manifest)
    revision = git_revision(Path(root))
    # A failed manual build leaves the active pointer unchanged.
    generation = data / "builds" / uuid.uuid4().hex
    snapshot = generation / "source"
    snapshot.mkdir(parents=True)
    engine = embedder(model, data)
    count_tokens = token_counter(engine)
    db = lancedb.connect(str(generation))
    documents = db.create_table(
        "documents",
        schema=pa.schema(
            [
                ("path", pa.string()),
                ("revision", pa.string()),
                ("characters", pa.int64()),
                ("sha256", pa.string()),
            ]
        ),
    )
    table = None
    count = 0
    document_count = 0
    maximum_tokens = 0
    split_windows = 0
    for item in selection["selected"]:
        path = item["path"]
        text = item["bytes"].decode("utf-8")
        document_hash = hashlib.sha256(text.encode()).hexdigest()
        relative = Path(path)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Unsafe source path")
        destination = snapshot / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(text.encode("utf-8"))
        documents.add(
            [
                {
                    "path": path,
                    "revision": revision,
                    "characters": len(text),
                    "sha256": document_hash,
                }
            ]
        )
        document_count += 1
        chunks = list(bounded_passages(text, count_tokens))
        split_windows += len(chunks) - len(list(passages(text)))
        if not chunks:
            continue
        vectors = list(
            engine.passage_embed([part for _, part in chunks], batch_size=32)
        )
        maximum_tokens = max(
            maximum_tokens, *(count_tokens(part) for _, part in chunks)
        )
        rows = [
            {
                "path": path,
                "revision": revision,
                "offset": offset,
                "text": part,
                "sha256": document_hash,
                "vector": vector.tolist(),
            }
            for (offset, part), vector in zip(chunks, vectors, strict=True)
        ]
        if table is None:
            schema = pa.schema(
                [
                    ("path", pa.string()),
                    ("revision", pa.string()),
                    ("offset", pa.int64()),
                    ("text", pa.string()),
                    ("sha256", pa.string()),
                    ("vector", pa.list_(pa.float32(), len(rows[0]["vector"]))),
                ]
            )
            table = db.create_table("passages", rows, schema=schema)
        else:
            table.add(rows)
        count += len(rows)
        if document_count % 100 == 0:
            print(
                f"Indexed {document_count} documents / {count} passages",
                file=sys.stderr,
            )
    if table is None:
        raise ValueError("No non-empty committed Markdown or text files found")
    manifest = {
        "database": str(generation.relative_to(data)),
        "revision": revision,
        "embedding_model": model,
        "passages": count,
        "documents": document_count,
        "maximum_passage_tokens": maximum_tokens,
        "additional_windows_to_avoid_truncation": split_windows,
        "formats": [".md", ".txt"],
        "corpus": selection_summary(selection),
    }
    temporary = data / "active.next.json"
    temporary.write_text(json.dumps(manifest, indent=2))
    os.replace(temporary, data / "active.json")
    print(json.dumps(manifest))


def _clean_result(row, layer):
    row = dict(row)
    row.pop("vector", None)
    row["layer"] = layer
    if "_distance" in row:
        row["score"] = 1 - row["_distance"]
    if "facets" in row and isinstance(row["facets"], str):
        try:
            row["facets"] = json.loads(row["facets"])
        except json.JSONDecodeError:
            pass
    return row


def _merge_search(committed, inbound, limit):
    rows = [*committed, *inbound]
    rows.sort(
        key=lambda row: (
            -(row.get("score", float("-inf"))),
            row.get("path", ""),
            row.get("offset", 0),
            row.get("layer", ""),
        )
    )
    return rows[:limit]


def serve(
    data,
    graph_candidate=None,
    graph_store=None,
    *,
    transport="stdio",
    host="127.0.0.1",
    port=8000,
    issuer_url=None,
    resource_url=None,
    jwks_url=None,
    required_scope=None,
    allowed_subjects=(),
    inbound=None,
    enable_inbound=False,
    enable_graph_inspection=False,
):
    import lancedb
    from mcp.server.fastmcp import FastMCP

    data = Path(data)
    if data.is_symlink() or not data.is_dir():
        raise ValueError("Committed data mount must be a real directory")
    manifest_path = data / "active.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValueError("Committed data mount has no regular active.json")
    manifest = json.loads(manifest_path.read_text())
    database_input = data / manifest["database"]
    if database_input.is_symlink():
        raise ValueError("Active database may not be a symlink")
    database_path = database_input.resolve()
    if not database_path.is_relative_to(data.resolve()):
        raise ValueError("Active database must remain inside the committed data mount")
    database = lancedb.connect(str(database_path))
    table = database.open_table("passages")
    documents = database.open_table("documents")
    source_input = database_path / "source"
    if source_input.is_symlink():
        raise ValueError("Committed source snapshot may not be a symlink")
    snapshot = source_input.resolve()
    if not snapshot.is_relative_to(data.resolve()) or not snapshot.is_dir():
        raise ValueError("Committed source snapshot must remain inside the data mount")
    engine = embedder(manifest["embedding_model"], data)
    count_tokens = token_counter(engine)
    from pi.alexandria.runtime.auth import LogtoJwtVerifier
    from pi.alexandria.runtime.graph_access import OptionalGraph
    from pi.alexandria.runtime.inbound import InboundStore, authenticated_identity

    if (
        graph_candidate is not None or graph_store is not None
    ) and not enable_graph_inspection:
        raise ValueError(
            "Held graph access requires the explicit graph inspection flag"
        )
    graph_access = (
        OptionalGraph(graph_candidate, store=graph_store)
        if enable_graph_inspection
        else OptionalGraph()
    )
    inbound_store = None
    if enable_inbound:
        if inbound is None:
            raise ValueError("Inbound mode requires an explicit writable inbound mount")
        inbound_store = InboundStore(Path(inbound).resolve())
    server_options = {}
    if transport == "streamable-http":
        from mcp.server.auth.settings import AuthSettings
        from mcp.server.transport_security import TransportSecuritySettings

        required = {
            "issuer URL": issuer_url,
            "resource URL": resource_url,
            "JWKS URL": jwks_url,
            "required scope": required_scope,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise ValueError("Streamable HTTP requires " + ", ".join(missing))
        if not allowed_subjects:
            raise ValueError(
                "Streamable HTTP requires at least one explicitly allowed Logto subject"
            )
        resource = resource_url.rstrip("/")
        public = urlsplit(resource)
        if public.scheme != "https" or not public.netloc:
            raise ValueError("The public resource URL must be an absolute HTTPS URL")
        verifier = LogtoJwtVerifier(
            issuer=issuer_url,
            resource=resource,
            jwks_url=jwks_url,
            required_scopes=[required_scope],
            allowed_subjects=allowed_subjects,
        )
        server_options = {
            "host": host,
            "port": port,
            "token_verifier": verifier,
            "auth": AuthSettings(
                issuer_url=issuer_url,
                resource_server_url=resource,
                required_scopes=[required_scope],
                validate_token_resource=True,
            ),
            "transport_security": TransportSecuritySettings(
                enable_dns_rebinding_protection=True,
                allowed_hosts=[public.netloc],
                allowed_origins=[f"{public.scheme}://{public.netloc}"],
            ),
            "max_request_body_size": 1048576,
            "session_idle_timeout": 300,
            "max_sessions": 128,
        }
    server = FastMCP("Alexandria", **server_options)

    @server.tool()
    def search(
        query: str,
        limit: int = 5,
        graph: bool = False,
        offset: int = 0,
        layer: str = "committed",
    ) -> list[dict] | dict:
        """Retrieve committed, inbound, or combined passages."""
        if layer not in ("committed", "inbound", "all"):
            raise ValueError("Layer must be committed, inbound, or all")
        if graph:
            if layer != "committed":
                raise ValueError(
                    "Graph search is available only for committed knowledge"
                )
            return graph_access.search(query, limit, offset)
        if offset != 0:
            raise ValueError("Offset is supported only in graph mode")
        if not query.strip() or len(query) > 2000:
            raise ValueError("Query must contain 1 to 2000 characters")
        if not 1 <= limit <= 20:
            raise ValueError("Limit must be between 1 and 20")
        if count_tokens(query) > 480:
            raise ValueError("Query exceeds the 480-token embedding budget")
        vector = next(engine.query_embed(query)).tolist()
        committed = []
        if layer in ("committed", "all"):
            rows = table.search(vector).distance_type("cosine").limit(limit).to_list()
            for row in rows:
                item = _clean_result(row, "committed")
                item["source"] = {
                    "repository": os.environ.get(
                        "ALEXANDRIA_SOURCE_REPOSITORY", "configured-source"
                    ),
                    "revision": row["revision"],
                    "path": row["path"],
                    "character_offset": row["offset"],
                }
                committed.append(item)
        inbound_results = []
        if layer in ("inbound", "all"):
            if inbound_store is None:
                raise ValueError("Inbound layer is disabled")
            inbound_results = inbound_store.search(vector, limit)
            for item in inbound_results:
                item["source"] = {
                    "repository": "alexandria-inbound",
                    "revision": item.get("revision"),
                    "path": item["path"],
                    "character_offset": item["offset"],
                    "submission_id": item.get("submission_id"),
                }
        return (
            committed
            if layer == "committed"
            else inbound_results
            if layer == "inbound"
            else _merge_search(committed, inbound_results, limit)
        )

    @server.tool()
    def read_document(
        path: str,
        start: int = 0,
        max_chars: int = 12000,
        graph: bool = False,
        frontmatter_only: bool = False,
    ) -> dict:
        """Read a committed or server-issued inbound document.

        Graph reads use the candidate own source snapshot, keep held status, and
        return source.next_start for full-text pagination. No graph filesystem paths.
        """
        if graph:
            if frontmatter_only:
                raise ValueError("Frontmatter mode is not available for graph records")
            return graph_access.read(path, start, max_chars)
        if start < 0 or not 1 <= max_chars <= 50000:
            raise ValueError("Start must be nonnegative; max_chars must be 1 to 50000")
        validate_relative_path(path)
        if path.startswith("inbound/"):
            if inbound_store is None:
                raise ValueError("Inbound layer is disabled")
            return inbound_store.read_document(path, start, max_chars, frontmatter_only)
        escaped = path.replace("'", "''")
        rows = documents.search().where(f"path = '{escaped}'").limit(1).to_list()
        if not rows:
            raise ValueError("Document not present in this indexed revision")
        row = rows[0]
        source_path = snapshot / row["path"]
        if source_path.is_symlink() or not source_path.resolve().is_relative_to(
            snapshot
        ):
            raise ValueError("Unsafe source path")
        text = source_path.read_bytes().decode("utf-8")
        if hashlib.sha256(text.encode()).hexdigest() != row["sha256"]:
            raise ValueError("Indexed source snapshot integrity mismatch")
        from pi.alexandria.runtime.listing import frontmatter

        raw_frontmatter, frontmatter_error = frontmatter(text)
        if frontmatter_only:
            return {
                "path": row["path"],
                "revision": row["revision"],
                "sha256": row["sha256"],
                "text": raw_frontmatter,
                "frontmatter": raw_frontmatter,
                "frontmatter_error": frontmatter_error,
                "body_omitted": True,
                "start": 0,
                "end": len(raw_frontmatter or ""),
                "total_chars": len(text),
                "next_start": None,
            }
        length = len(text)
        if start > length:
            raise ValueError("Start exceeds document length")
        end = min(start + max_chars, length)
        return {
            "path": row["path"],
            "revision": row["revision"],
            "sha256": row["sha256"],
            "frontmatter": raw_frontmatter,
            "frontmatter_error": frontmatter_error,
            "text": text[start:end],
            "start": start,
            "end": end,
            "total_chars": length,
            "next_start": end if end < length else None,
        }

    @server.tool()
    def text_search(query: str, limit: int = 50, layer: str = "committed") -> dict:
        """Literal ripgrep search over the selected text layer."""
        if not query or len(query) > 1000 or "\n" in query or "\r" in query:
            raise ValueError("Query must be a single line of 1 to 1000 characters")
        if not 1 <= limit <= 200:
            raise ValueError("Limit must be between 1 and 200")
        if layer not in ("committed", "inbound", "all"):
            raise ValueError("Layer must be committed, inbound, or all")
        if layer in ("inbound", "all") and inbound_store is None:
            raise ValueError("Inbound layer is disabled")
        result = literal_search(
            snapshot if layer in ("committed", "all") else None,
            query,
            limit,
            inbound_root=inbound_store.content
            if inbound_store and layer in ("inbound", "all")
            else None,
            inbound_records={
                record["submission_id"]: record
                for record in inbound_store.tree_entries()
            }
            if inbound_store and layer in ("inbound", "all")
            else None,
        )
        result["layer"] = layer
        result["revision"] = manifest["revision"] if layer == "committed" else None
        return result

    @server.tool()
    def list_files(
        prefix: str = "",
        max_depth: int = 8,
        limit: int = 500,
        layer: str = "committed",
        include_frontmatter: bool = False,
    ) -> dict:
        """Return a bounded deterministic tree of authorized text files."""
        if layer not in ("committed", "inbound", "all"):
            raise ValueError("Layer must be committed, inbound, or all")
        if not 0 <= max_depth <= 32:
            raise ValueError("max_depth must be between 0 and 32")
        if not 1 <= limit <= 500:
            raise ValueError("Limit must be between 1 and 500")
        validate_relative_path(prefix, allow_empty=True)
        if layer in ("inbound", "all") and inbound_store is None:
            raise ValueError("Inbound layer is disabled")
        committed_result = (
            committed_tree(
                snapshot,
                manifest["revision"],
                prefix=prefix,
                limit=500,
                include_frontmatter=include_frontmatter,
            )
            if layer in ("committed", "all")
            else {"entries": []}
        )
        inbound_result = (
            inbound_tree(
                inbound_store.tree_entries(include_frontmatter),
                prefix=prefix,
                limit=500,
            )
            if layer in ("inbound", "all")
            else {"entries": []}
        )
        entries = [*committed_result["entries"], *inbound_result["entries"]]
        entries = [entry for entry in entries if entry["depth"] <= max_depth]
        entries.sort(key=lambda item: (item["path"], item["kind"] != "directory"))
        return {
            "entries": entries[:limit],
            "truncated": len(entries) > limit
            or committed_result.get("truncated", False)
            or inbound_result.get("truncated", False),
            "prefix": prefix,
            "layer": layer,
            "max_depth": max_depth,
            "limit": limit,
        }

    @server.tool()
    def status(graph: bool = False, submission_id: str | None = None) -> dict:
        """Report committed data, attached-pool state, or held graph availability."""
        if graph:
            if submission_id is not None:
                raise ValueError("Graph status cannot target an inbound submission")
            return graph_access.status()
        result = {
            **manifest,
            "inbound": inbound_store.status() if inbound_store else {"enabled": False},
        }
        if submission_id is not None:
            if inbound_store is None:
                raise ValueError("Inbound layer is disabled")
            result["submission"] = inbound_store.receipt(submission_id)
        return result

    if inbound_store is not None:

        @server.tool()
        def submit_inbound(
            ctx: Context,
            content: str,
            session_label: str,
            idempotency_key: str,
            title: str = "",
            facets: list[str] | None = None,
            source_ref: str | None = None,
        ) -> dict:
            """Queue uncommitted text for durable, provenance-aware indexing."""
            subject, client_id = authenticated_identity(ctx)
            return inbound_store.submit(
                content=content,
                session_label=session_label,
                idempotency_key=idempotency_key,
                title=title,
                facets=facets,
                source_ref=source_ref,
                subject=subject,
                client_id=client_id,
            )

        inbound_store.start_worker(engine, count_tokens, bounded_passages)

    server.run(transport=transport)


def _literal_search_root(snapshot, query, limit):
    process = subprocess.Popen(
        [
            "rg",
            "--json",
            "--fixed-strings",
            "--line-number",
            "--hidden",
            "--no-ignore",
            "--text",
            "--sort",
            "path",
            "--",
            query,
            ".",
        ],
        cwd=snapshot,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    matches = []
    reason = None
    buffer = b""
    deadline = time.monotonic() + 10
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not selector.select(remaining):
                reason = "time_limit"
                break
            part = os.read(process.stdout.fileno(), 65536)
            if not part:
                break
            buffer += part
            if len(buffer) > 1048576:
                reason = "output_record_size_limit"
                break
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                event = json.loads(line)
                if event["type"] != "match":
                    continue
                if len(matches) == limit:
                    reason = "result_limit"
                    break
                item = event["data"]
                text = item["lines"].get("text")
                if text is None:
                    reason = "unsupported_binary_output"
                    break
                matches.append(
                    {
                        "path": item["path"]["text"].removeprefix("./"),
                        "line": item["line_number"],
                        "text": text[:2000],
                        "line_truncated": len(text) > 2000,
                    }
                )
            if reason:
                break
        if reason is None:
            process.wait(timeout=2)
    finally:
        selector.close()
        process.stdout.close()
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
    if not reason and process.returncode not in (0, 1):
        raise RuntimeError("ripgrep could not complete the search")
    return {"matches": matches, "truncated": reason is not None, "reason": reason}


def literal_search(snapshot, query, limit, *, inbound_root=None, inbound_records=None):
    """Run fixed-string ripgrep over one or both authorized text roots."""
    roots = []
    if snapshot is not None:
        roots.append((snapshot, "committed"))
    if inbound_root is not None:
        roots.append((inbound_root, "inbound"))
    matches = []
    reason = None
    for root, layer in roots:
        remaining = max(limit - len(matches), 1)
        result = _literal_search_root(root, query, remaining)
        for item in result["matches"]:
            if layer == "inbound":
                submission_id = item["path"].removesuffix(".md")
                record = (inbound_records or {}).get(submission_id, {})
                item["path"] = f"inbound/{item['path']}"
                item["layer"] = "inbound"
                item["submission_id"] = submission_id
                if record:
                    item["title"] = record.get("title", "")
                    item["status"] = record.get("status")
            else:
                item["layer"] = "committed"
            matches.append(item)
        if result["truncated"]:
            reason = result["reason"]
        if len(matches) >= limit:
            reason = reason or "result_limit"
            break
    matches.sort(key=lambda item: (item["path"], item["line"]))
    return {
        "matches": matches[:limit],
        "truncated": reason is not None,
        "reason": reason,
    }


def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    index = commands.add_parser("index")
    index.add_argument("--source", type=Path, required=True)
    index.add_argument("--model", required=True)
    index.add_argument("--data", type=Path, required=True)
    index.add_argument(
        "--manifest",
        default=DEFAULT_MANIFEST,
        help="Committed Knowledge corpus selection manifest",
    )
    server = commands.add_parser("serve")
    server.add_argument("--data", type=Path, required=True)
    server.add_argument(
        "--transport",
        choices=("stdio", "streamable-http"),
        default="stdio",
    )
    server.add_argument("--host", default="127.0.0.1")
    server.add_argument("--port", type=int, default=8000)
    server.add_argument("--issuer-url")
    server.add_argument("--resource-url")
    server.add_argument("--jwks-url")
    server.add_argument("--required-scope")
    server.add_argument(
        "--inbound",
        type=Path,
        help="Explicit writable inbound data-block mount; disabled unless --enable-inbound is set",
    )
    server.add_argument(
        "--enable-inbound",
        action="store_true",
        help="Expose submit_inbound and start the bounded inbound worker",
    )
    server.add_argument(
        "--allowed-subjects-env",
        default="ALEXANDRIA_ALLOWED_SUBJECTS",
        help="Environment variable containing comma-separated allowed Logto subject IDs",
    )
    graph_options = server.add_mutually_exclusive_group()
    graph_options.add_argument(
        "--graph-candidate",
        type=Path,
        help="Operator-selected completed held graph bundle; read-only",
    )
    graph_options.add_argument(
        "--graph-store",
        type=Path,
        help="Explicit persistent inspection store; selected package is validated at startup",
    )
    server.add_argument(
        "--graph-inspection",
        action="store_true",
        help="Explicitly enable read-only access to a held graph candidate",
    )
    args = parser.parse_args()
    if args.command == "index":
        build(args.source.resolve(), args.data.resolve(), args.model, args.manifest)
    else:
        allowed_subjects = tuple(
            subject.strip()
            for subject in os.environ.get(args.allowed_subjects_env, "").split(",")
            if subject.strip()
        )
        serve(
            args.data.resolve(),
            args.graph_candidate,
            args.graph_store,
            transport=args.transport,
            host=args.host,
            port=args.port,
            issuer_url=args.issuer_url,
            resource_url=args.resource_url,
            jwks_url=args.jwks_url,
            required_scope=args.required_scope,
            allowed_subjects=allowed_subjects,
            inbound=args.inbound,
            enable_inbound=args.enable_inbound,
            enable_graph_inspection=args.graph_inspection,
        )


if __name__ == "__main__":
    main()
