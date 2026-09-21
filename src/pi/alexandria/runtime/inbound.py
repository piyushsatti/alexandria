"""Durable, append-only inbound submissions and their derived LanceDB index."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from pi.alexandria.runtime.listing import _entry

MAX_CONTENT_BYTES = 256 * 1024
MAX_SUBMISSIONS = 1000
MAX_TOTAL_BYTES = 256 * 1024 * 1024
MAX_ATTEMPTS = 3
STALE_SECONDS = 300
SUBMISSION_ID = re.compile(r"^[0-9a-f]{32}$")


def _now() -> float:
    return time.time()


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _safe_text(value: object, name: str, limit: int, *, required: bool = True) -> str:
    if not isinstance(value, str) or (required and not value.strip()):
        raise ValueError(f"{name} must be non-empty text")
    if len(value) > limit or "\x00" in value:
        raise ValueError(f"{name} exceeds its safety limit")
    return value


def _atomic_bytes(root: Path, relative: str, data: bytes) -> None:
    destination = (root / relative).resolve()
    if not destination.is_relative_to(root.resolve()):
        raise ValueError("Inbound path escapes the mounted inbound directory")
    if destination.is_symlink():
        raise ValueError("Inbound destination may not be a symlink")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.parent / f".{destination.name}.{uuid.uuid4().hex}.tmp"
    try:
        with temporary.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()


class InboundStore:
    """Store queued content under one explicit writable root.

    The committed data directory is never passed to this class. Every path
    written here is derived from a server-generated submission ID.
    """

    def __init__(self, root: Path):
        root = Path(root)
        if root.exists() and root.is_symlink():
            raise ValueError("Inbound mount may not be a symlink")
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root = root.resolve()
        self.content = self.root / "content"
        self.queue = self.root / "queue"
        self.receipts = self.root / "receipts"
        self.index_root = self.root / "index"
        for directory in (self.content, self.queue, self.receipts, self.index_root):
            if directory.exists() and directory.is_symlink():
                raise ValueError(f"Inbound directory may not be a symlink: {directory}")
            directory.mkdir(mode=0o700, exist_ok=True)
        self.catalog = self.root / "catalog.sqlite3"
        self._lock = threading.RLock()
        self._initialize_catalog()

    def _connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.catalog, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=10000")
        return connection

    def _initialize_catalog(self) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS submissions (
                    id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    session_label TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    client_id TEXT NOT NULL,
                    submitted_at REAL NOT NULL,
                    title TEXT NOT NULL,
                    facets_json TEXT NOT NULL,
                    source_ref TEXT,
                    content_sha256 TEXT NOT NULL,
                    bytes INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    error TEXT,
                    updated_at REAL NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS submissions_status ON submissions(status, updated_at)"
            )

    @staticmethod
    def _validate_submission_id(submission_id: str) -> str:
        if not isinstance(submission_id, str) or not SUBMISSION_ID.fullmatch(
            submission_id
        ):
            raise ValueError("Invalid server-issued submission ID")
        return submission_id

    def _record(self, row: sqlite3.Row) -> dict:
        return {
            "submission_id": row["id"],
            "path": f"inbound/{row['id']}.md",
            "status": row["status"],
            "idempotency_key": row["idempotency_key"],
            "session_label": row["session_label"],
            "subject": row["subject"],
            "client_id": row["client_id"],
            "submitted_at": row["submitted_at"],
            "title": row["title"],
            "facets": json.loads(row["facets_json"]),
            "source_ref": row["source_ref"],
            "content_sha256": row["content_sha256"],
            "bytes": row["bytes"],
            "attempts": row["attempts"],
            "error": row["error"],
            "updated_at": row["updated_at"],
        }

    def _write_receipt(self, record: dict) -> None:
        payload = (
            json.dumps(record, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
        )
        _atomic_bytes(self.root, f"receipts/{record['submission_id']}.json", payload)

    def receipt(self, submission_id: str) -> dict:
        submission_id = self._validate_submission_id(submission_id)
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM submissions WHERE id = ?", (submission_id,)
            ).fetchone()
        if row is None:
            raise ValueError("Unknown inbound submission")
        return self._record(row)

    def submit(
        self,
        *,
        content: str,
        session_label: str,
        idempotency_key: str,
        title: str = "",
        facets: list[str] | None = None,
        source_ref: str | None = None,
        subject: str = "local",
        client_id: str = "local",
    ) -> dict:
        if not isinstance(content, str) or not content.strip() or "\x00" in content:
            raise ValueError("Content must be non-empty Markdown or plain text")
        content_bytes = content.encode("utf-8")
        if len(content_bytes) > MAX_CONTENT_BYTES:
            raise ValueError("Content exceeds the 256 KiB limit")
        session_label = _safe_text(session_label, "session_label", 128)
        idempotency_key = _safe_text(idempotency_key, "idempotency_key", 128)
        title = _safe_text(title, "title", 256, required=False)
        subject = _safe_text(subject, "subject", 256)
        client_id = _safe_text(client_id, "client_id", 256)
        if source_ref is not None:
            source_ref = _safe_text(source_ref, "source_ref", 1024, required=False)
        facets = [] if facets is None else facets
        if (
            not isinstance(facets, list)
            or len(facets) > 32
            or any(
                not isinstance(facet, str) or not facet.strip() or len(facet) > 128
                for facet in facets
            )
        ):
            raise ValueError("Facets must be a list of at most 32 short strings")
        content_hash = hashlib.sha256(content_bytes).hexdigest()
        submitted_at = _now()
        with self._lock, self._connection() as connection:
            existing = connection.execute(
                "SELECT * FROM submissions WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if existing is not None:
                if existing["content_sha256"] != content_hash:
                    raise ValueError(
                        "Idempotency key was already used for different content"
                    )
                result = self._record(existing)
                result["idempotent_replay"] = True
                return result
            count, total = connection.execute(
                "SELECT COUNT(*), COALESCE(SUM(bytes), 0) FROM submissions"
            ).fetchone()
            if count >= MAX_SUBMISSIONS or total + len(content_bytes) > MAX_TOTAL_BYTES:
                raise ValueError("Inbound queue capacity is full")
            submission_id = uuid.uuid4().hex
            record = {
                "submission_id": submission_id,
                "path": f"inbound/{submission_id}.md",
                "status": "queued",
                "idempotency_key": idempotency_key,
                "session_label": session_label,
                "subject": subject,
                "client_id": client_id,
                "submitted_at": submitted_at,
                "title": title,
                "facets": facets,
                "source_ref": source_ref,
                "content_sha256": content_hash,
                "bytes": len(content_bytes),
                "attempts": 0,
                "error": None,
                "updated_at": submitted_at,
            }
            # The database row becomes visible only after the content and queue
            # record are safely published. A crash leaves a retryable submission.
            _atomic_bytes(self.root, f"content/{submission_id}.md", content_bytes)
            _atomic_bytes(
                self.root, f"queue/{submission_id}.json", _json(record).encode() + b"\n"
            )
            connection.execute(
                """
                INSERT INTO submissions
                (id, idempotency_key, session_label, subject, client_id, submitted_at,
                 title, facets_json, source_ref, content_sha256, bytes, status, attempts, error, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    submission_id,
                    idempotency_key,
                    session_label,
                    subject,
                    client_id,
                    submitted_at,
                    title,
                    _json(facets),
                    source_ref,
                    content_hash,
                    len(content_bytes),
                    "queued",
                    0,
                    None,
                    submitted_at,
                ),
            )
            connection.commit()
            self._write_receipt(record)
            return record

    def requeue_stale(self) -> int:
        threshold = _now() - STALE_SECONDS
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                "SELECT id FROM submissions WHERE status = 'processing' AND updated_at < ?",
                (threshold,),
            ).fetchall()
            for row in rows:
                connection.execute(
                    "UPDATE submissions SET status='queued', updated_at=? WHERE id=?",
                    (_now(), row["id"]),
                )
            return len(rows)

    def _claim(self) -> dict | None:
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM submissions WHERE status = 'queued' ORDER BY submitted_at, id LIMIT 1"
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            attempts = row["attempts"] + 1
            now = _now()
            connection.execute(
                "UPDATE submissions SET status='processing', attempts=?, updated_at=? WHERE id=?",
                (attempts, now, row["id"]),
            )
            connection.commit()
            values = dict(row)
            values["attempts"] = attempts
            values["status"] = "processing"
            values["updated_at"] = now
            return values

    def _update(
        self, submission_id: str, status: str, error: str | None = None
    ) -> dict:
        now = _now()
        with self._lock, self._connection() as connection:
            connection.execute(
                "UPDATE submissions SET status=?, error=?, updated_at=? WHERE id=?",
                (status, error, now, submission_id),
            )
            row = connection.execute(
                "SELECT * FROM submissions WHERE id = ?", (submission_id,)
            ).fetchone()
        if row is None:
            raise ValueError("Inbound submission disappeared")
        record = self._record(row)
        self._write_receipt(record)
        return record

    def _repair_partial_index(self, database, names: set[str]) -> None:
        """Reset an incomplete LanceDB pair and requeue durable submissions.

        Passage and document tables are one logical index. A process crash can
        leave only one table after the first ``create_table`` succeeds. The
        catalog and source files are the durable authority, so discard the
        incomplete derived tables and let the worker rebuild them.
        """

        present = sorted(names & {"passages", "documents"})
        for name in present:
            database.drop_table(name)
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM submissions WHERE status = 'indexed'"
            ).fetchall()
            if rows:
                now = _now()
                connection.execute(
                    """
                    UPDATE submissions
                    SET status='queued', attempts=0,
                        error='inbound_index_repair', updated_at=?
                    WHERE status = 'indexed'
                    """,
                    (now,),
                )
        for row in rows:
            record = self._record(row)
            record["status"] = "queued"
            record["attempts"] = 0
            record["error"] = "inbound_index_repair"
            record["updated_at"] = now
            self._write_receipt(record)

    def _open_tables(self):
        import lancedb

        database = lancedb.connect(str(self.index_root))
        names = set(database.table_names())
        required = {"passages", "documents"}
        if names & required and not required.issubset(names):
            self._repair_partial_index(database, names)
            return database, None, None
        if not required.issubset(names):
            return database, None, None
        try:
            passages = database.open_table("passages")
            documents = database.open_table("documents")
        except Exception:
            self._repair_partial_index(database, names)
            return database, None, None
        return database, passages, documents

    def process_one(self, engine, count_tokens, chunker) -> dict | None:
        claimed = self._claim()
        if claimed is None:
            return None
        submission_id = claimed["id"]
        try:
            path = self.content / f"{submission_id}.md"
            if path.is_symlink() or not path.is_file():
                raise ValueError("Inbound content is missing")
            content = path.read_text(encoding="utf-8")
            if (
                hashlib.sha256(content.encode()).hexdigest()
                != claimed["content_sha256"]
            ):
                raise ValueError("Inbound content hash mismatch")
            chunks = list(chunker(content, count_tokens))
            if not chunks:
                raise ValueError("Inbound content has no indexable text")
            vectors = list(
                engine.passage_embed(
                    [piece for _offset, piece in chunks], batch_size=32
                )
            )
            import pyarrow as pa

            rows = [
                {
                    "path": f"inbound/{submission_id}.md",
                    "revision": f"inbound:{submission_id}",
                    "offset": offset,
                    "text": piece,
                    "sha256": claimed["content_sha256"],
                    "submission_id": submission_id,
                    "title": claimed["title"],
                    "facets": _json(json.loads(claimed["facets_json"])),
                    "source_ref": claimed["source_ref"] or "",
                    "vector": vector.tolist(),
                }
                for (offset, piece), vector in zip(chunks, vectors, strict=True)
            ]
            database, table, documents = self._open_tables()
            if table is not None:
                try:
                    table.delete(f"submission_id = '{submission_id}'")
                    documents.delete(f"submission_id = '{submission_id}'")
                except (AttributeError, RuntimeError, ValueError):
                    pass
            if table is None:
                schema = pa.schema(
                    [
                        ("path", pa.string()),
                        ("revision", pa.string()),
                        ("offset", pa.int64()),
                        ("text", pa.string()),
                        ("sha256", pa.string()),
                        ("submission_id", pa.string()),
                        ("title", pa.string()),
                        ("facets", pa.string()),
                        ("source_ref", pa.string()),
                        ("vector", pa.list_(pa.float32(), len(rows[0]["vector"]))),
                    ]
                )
                database.create_table("passages", rows, schema=schema)
                documents_schema = pa.schema(
                    [
                        ("path", pa.string()),
                        ("revision", pa.string()),
                        ("characters", pa.int64()),
                        ("sha256", pa.string()),
                        ("submission_id", pa.string()),
                        ("title", pa.string()),
                        ("facets", pa.string()),
                        ("source_ref", pa.string()),
                    ]
                )
                database.create_table(
                    "documents",
                    [
                        {
                            "path": f"inbound/{submission_id}.md",
                            "revision": f"inbound:{submission_id}",
                            "characters": len(content),
                            "sha256": claimed["content_sha256"],
                            "submission_id": submission_id,
                            "title": claimed["title"],
                            "facets": _json(json.loads(claimed["facets_json"])),
                            "source_ref": claimed["source_ref"] or "",
                        }
                    ],
                    schema=documents_schema,
                )
            else:
                table.add(rows)
                documents.add(
                    [
                        {
                            "path": f"inbound/{submission_id}.md",
                            "revision": f"inbound:{submission_id}",
                            "characters": len(content),
                            "sha256": claimed["content_sha256"],
                            "submission_id": submission_id,
                            "title": claimed["title"],
                            "facets": _json(json.loads(claimed["facets_json"])),
                            "source_ref": claimed["source_ref"] or "",
                        }
                    ]
                )
            return self._update(submission_id, "indexed")
        except Exception as error:  # noqa: BLE001 - convert worker failures to bounded receipts
            reason = f"{type(error).__name__}: {error}"[:512]
            next_status = "failed" if claimed["attempts"] >= MAX_ATTEMPTS else "queued"
            return self._update(submission_id, next_status, reason)

    def search(self, vector: list[float], limit: int) -> list[dict]:
        _database, table, _documents = self._open_tables()
        if table is None:
            return []
        rows = table.search(vector).distance_type("cosine").limit(limit).to_list()
        result = []
        for row in rows:
            row.pop("vector", None)
            row["layer"] = "inbound"
            if "_distance" in row:
                row["score"] = 1 - row["_distance"]
            result.append(row)
        return result

    def read_document(
        self, path: str, start: int, max_chars: int, frontmatter_only: bool
    ) -> dict:
        if not path.startswith("inbound/") or not path.endswith(".md"):
            raise ValueError("Inbound reads require a server-issued path")
        submission_id = path.removeprefix("inbound/").removesuffix(".md")
        self._validate_submission_id(submission_id)
        record = self.receipt(submission_id)
        source = self.content / f"{submission_id}.md"
        if source.is_symlink() or not source.is_file():
            raise ValueError("Inbound content is unavailable")
        text = source.read_text(encoding="utf-8")
        if hashlib.sha256(text.encode()).hexdigest() != record["content_sha256"]:
            raise ValueError("Inbound source snapshot integrity mismatch")
        from pi.alexandria.runtime.listing import frontmatter

        raw, error = frontmatter(text)
        if frontmatter_only:
            return {
                **record,
                "text": raw,
                "frontmatter": raw,
                "frontmatter_error": error,
                "body_omitted": True,
                "start": 0,
                "end": len(raw or ""),
                "total_chars": len(text),
                "next_start": None,
            }
        if start < 0 or start > len(text):
            raise ValueError("Start exceeds document length")
        end = min(start + max_chars, len(text))
        return {
            **record,
            "text": text[start:end],
            "frontmatter": raw,
            "frontmatter_error": error,
            "start": start,
            "end": end,
            "total_chars": len(text),
            "next_start": end if end < len(text) else None,
        }

    def tree_entries(self, include_frontmatter: bool = False) -> list[dict]:
        result: list[dict] = []
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM submissions ORDER BY id"
            ).fetchall()
        for row in rows:
            path = self.content / f"{row['id']}.md"
            if path.is_symlink() or not path.is_file():
                continue
            entry = _entry(
                path,
                f"inbound/{row['id']}.md",
                submission_id=row["id"],
                include_frontmatter=include_frontmatter,
            )
            entry.update(
                {
                    "status": row["status"],
                    "title": row["title"],
                    "facets": json.loads(row["facets_json"]),
                }
            )
            result.append(entry)
        return result

    def status(self) -> dict:
        with self._connection() as connection:
            count, total = connection.execute(
                "SELECT COUNT(*), COALESCE(SUM(bytes), 0) FROM submissions"
            ).fetchone()
            states = {
                row["status"]: row["count"]
                for row in connection.execute(
                    "SELECT status, COUNT(*) AS count FROM submissions GROUP BY status"
                ).fetchall()
            }
        return {
            "enabled": True,
            "submissions": count,
            "bytes": total,
            "states": states,
            "limits": {
                "submissions": MAX_SUBMISSIONS,
                "bytes": MAX_TOTAL_BYTES,
                "content_bytes": MAX_CONTENT_BYTES,
                "attempts": MAX_ATTEMPTS,
            },
        }

    def start_worker(self, engine, count_tokens, chunker) -> threading.Thread:
        self.requeue_stale()
        stop = threading.Event()

        def run() -> None:
            while not stop.is_set():
                result = self.process_one(engine, count_tokens, chunker)
                if result is None:
                    stop.wait(0.5)

        thread = threading.Thread(target=run, name="alexandria-inbound", daemon=True)
        thread.stop_event = stop  # type: ignore[attr-defined]
        thread.start()
        return thread


def authenticated_identity(ctx=None) -> tuple[str, str]:
    """Return the verified MCP principal, with a local fallback for stdio."""

    try:
        from mcp.server.auth.middleware.auth_context import get_access_token

        token = get_access_token()
    except (ImportError, LookupError, RuntimeError):
        token = None
    if token is not None:
        subject = getattr(token, "subject", None) or "authenticated"
        client_id = getattr(token, "client_id", None) or "authenticated"
        return str(subject), str(client_id)
    return "local", "stdio"
