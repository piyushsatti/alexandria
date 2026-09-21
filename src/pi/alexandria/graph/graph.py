"""Offline candidate graph builder. Model output is data, never executable code."""

import argparse
import hashlib
import json
import os
import posixpath
import re
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_JSON_BYTES = 16 * 1024 * 1024
MAX_FILES = 20000
MAX_TOTAL_BYTES = 256 * 1024 * 1024
MAX_RECORDS = 100000
SCHEMA_VERSION = "2026.09.19"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode()


def identifier(kind, *parts):
    return kind + ":" + digest(encoded(parts))


def safe_path(value):
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise ValueError("Invalid source path")
    p = PurePosixPath(value)
    if p.is_absolute() or any(x in ("", ".", "..") for x in value.split("/")):
        raise ValueError("Source path must be canonical and relative")
    return value


def no_symlinks(path):
    path = Path(os.path.abspath(path))
    for part in [*reversed(path.parents), path]:
        if part.is_symlink():
            raise ValueError("Symlinks are not allowed")
    return path


def read_bytes(path, maximum):
    path = no_symlinks(path)
    if not path.is_file() or path.stat().st_size > maximum:
        raise ValueError("Missing, nonregular, or oversized input")
    # Container source mounts must be immutable; this check is not a sandbox.
    with path.open("rb") as stream:
        result = stream.read(maximum + 1)
    if len(result) > maximum:
        raise ValueError("Oversized input")
    return result


def read_json(path, maximum=MAX_JSON_BYTES):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result

    return json.loads(read_bytes(path, maximum), object_pairs_hook=unique)


def exact_keys(value, required, optional=()):
    if (
        not isinstance(value, dict)
        or not set(required) <= value.keys()
        or value.keys() - set(required) - set(optional)
    ):
        raise ValueError("Unexpected or missing fields")


def bounded_string(value, maximum=1000):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError("Expected bounded nonempty string")
    return value


def validate_qualification(value, texts, hashes, revision):
    """Carry qualifications with the assertion, with independently checked evidence."""
    exact_keys(value, {"source_status", "scope", "annotations"}, {"review_state"})
    if value["source_status"] not in (
        "accepted",
        "proposed",
        "open",
        "requirement",
        "limitation",
        "historical",
        "deferred",
    ):
        raise ValueError("Invalid source status; not a review approval")

    review_state = value.get("review_state", "not_checked")
    if review_state not in ("not_checked", "held"):
        raise ValueError("Invalid qualification review state")

    def evidence(row, fields):
        exact_keys(row, {"path", "quote", "start", *fields})
        for field in fields:
            bounded_string(row[field], 4000)
        path = safe_path(row["path"])
        quote = bounded_string(row["quote"], MAX_FILE_BYTES)
        start = row["start"]
        if (
            path not in texts
            or type(start) is not int
            or start < 0
            or texts[path][start : start + len(quote)] != quote
        ):
            raise ValueError("Qualification evidence does not match source")
        return {
            **row,
            "revision": revision,
            "source_sha256": hashes[path],
            "end": start + len(quote),
        }

    scope = evidence(value["scope"], {"text"})
    notes = value["annotations"]
    if not isinstance(notes, list) or len(notes) > 100:
        raise ValueError("Invalid qualification annotation list")
    checked = []
    for note in notes:
        item = evidence(note, {"id", "kind", "reason", "resolution_question"})
        if item["kind"] not in (
            "ambiguity",
            "open_decision",
            "scope_tension",
            "interpretation",
        ):
            raise ValueError("Invalid annotation kind")
        checked.append(item)
    if len({n["id"] for n in checked}) != len(checked):
        raise ValueError("Duplicate qualification annotation")
    return {
        "source_status": value["source_status"],
        "review_state": review_state,
        "scope": scope,
        "annotations": checked,
    }


def make_graph(source, assertion_file=None):
    source = no_symlinks(source)
    manifest = read_json(source / "manifest.json")
    exact_keys(manifest, {"revision", "files"})
    revision = bounded_string(manifest["revision"])
    files = manifest["files"]
    if not isinstance(files, list) or len(files) > MAX_FILES:
        raise ValueError("Invalid file inventory")
    texts, hashes = {}, {}
    total = 0
    for entry in files:
        exact_keys(entry, {"path", "sha256"})
        path = safe_path(entry["path"])
        if path in texts or path == "manifest.json":
            raise ValueError("Duplicate or reserved source path")
        raw = read_bytes(source / path, MAX_FILE_BYTES)
        total += len(raw)
        if total > MAX_TOTAL_BYTES:
            raise ValueError("Source size limit exceeded")
        if digest(raw) != entry["sha256"]:
            raise ValueError("Source hash mismatch")
        texts[path] = raw.decode("utf-8")
        hashes[path] = entry["sha256"]
    records = {}

    def add(kind, identity, **fields):
        record = {"id": identity, "kind": kind, **fields}
        if identity in records and records[identity] != record:
            raise ValueError("Conflicting record identity")
        if identity not in records and len(records) >= MAX_RECORDS:
            raise ValueError("Graph record limit exceeded")
        records[identity] = record
        return identity

    for path, text in sorted(texts.items()):
        doc = add("document", identifier("doc", path), path=path)
        rev = add(
            "revision",
            identifier("rev", revision, path, hashes[path]),
            document=doc,
            revision=revision,
            sha256=hashes[path],
            path=path,
        )
        # Lightweight Markdown parsing; fenced code is masked with equal-length spaces.
        lines, in_fence, mask = text.splitlines(keepends=True), None, []
        for line in lines:
            fence = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
            if fence:
                marker = fence.group(1)
                if in_fence is None:
                    in_fence = marker
                elif marker[0] == in_fence[0] and len(marker) >= len(in_fence):
                    in_fence = None
                mask.append(re.sub(r"[^\r\n]", " ", line))
            else:
                mask.append(re.sub(r"[^\r\n]", " ", line) if in_fence else line)
        masked = "".join(mask)
        headings = list(
            re.finditer(r"^ {0,3}(#{1,6})[ \t]+(.+)$", masked, re.MULTILINE)
        )
        ends = {}
        future = []
        for heading in reversed(headings):
            level = len(heading.group(1))
            while future and len(future[-1].group(1)) > level:
                future.pop()
            ends[heading.start()] = future[-1].start() if future else len(text)
            future.append(heading)
        stack = []
        for heading in headings:
            level = len(heading.group(1))
            while stack and stack[-1][0] >= level:
                stack.pop()
            end = ends[heading.start()]
            sid = add(
                "section",
                identifier("section", rev, heading.start()),
                revision_id=rev,
                path=path,
                title=heading.group(2).strip(),
                level=level,
                start=heading.start(),
                end=end,
                parent=stack[-1][1] if stack else rev,
            )
            stack.append((level, sid))
        for match in re.finditer(r"(?<!!)\[[^\]\n]*\]\(([^\s)]+)\)", masked):
            target = match.group(1).strip("<>")
            parsed = urlsplit(target)
            external = bool(parsed.scheme or parsed.netloc)
            resolved = None
            if not external:
                candidate = (
                    posixpath.normpath(
                        posixpath.join(posixpath.dirname(path), unquote(parsed.path))
                    )
                    if parsed.path
                    else path
                )
                if candidate in texts:
                    resolved = identifier("doc", candidate)
            add(
                "link",
                identifier("link", rev, match.start()),
                source=rev,
                path=path,
                start=match.start(),
                target=target,
                target_document=resolved,
                status="external"
                if external
                else "resolved-document"
                if resolved
                else "unresolved",
                fragment=parsed.fragment,
            )
    assertions = [] if assertion_file is None else read_json(assertion_file)
    if not isinstance(assertions, list) or len(assertions) > 20000:
        raise ValueError("Expected bounded assertion list")
    for assertion in assertions:
        exact_keys(
            assertion,
            {"subject", "predicate", "object", "path", "quote", "start"},
            {"status", "extraction", "qualification"},
        )
        path = safe_path(assertion["path"])
        quote = bounded_string(assertion["quote"], MAX_FILE_BYTES)
        start = assertion["start"]
        if (
            path not in texts
            or type(start) is not int
            or start < 0
            or texts[path][start : start + len(quote)] != quote
        ):
            raise ValueError("Assertion evidence does not match source")
        status = assertion.get("status", "extracted")
        if status not in ("extracted", "inferred"):
            raise ValueError("Imports cannot confer human review or truth")
        extraction = assertion.get("extraction", {"method": "supplied-assertion"})
        exact_keys(extraction, {"method"}, {"model", "provider", "prompt_version"})
        for val in extraction.values():
            bounded_string(val)
        subject = bounded_string(assertion["subject"])
        obj = bounded_string(assertion["object"])
        predicate = bounded_string(assertion["predicate"], 200)
        qualified = {}
        if "qualification" in assertion:
            qualified["qualification"] = validate_qualification(
                assertion["qualification"], texts, hashes, revision
            )
        # Names remain document-scoped candidates; no cross-document identity guesses.
        sid = add(
            "entity", identifier("entity", path, subject), label=subject, scope=path
        )
        oid = add("entity", identifier("entity", path, obj), label=obj, scope=path)
        add(
            "assertion",
            identifier("assertion", revision, assertion),
            subject=sid,
            predicate=predicate,
            object=oid,
            path=path,
            revision=revision,
            source_sha256=hashes[path],
            start=start,
            end=start + len(quote),
            quote=quote,
            status=status,
            extraction=extraction,
            **qualified,
        )
    result = sorted(records.values(), key=lambda record: record["id"])
    return result, {
        "schema_version": SCHEMA_VERSION,
        "revision": revision,
        "sources": dict(sorted(hashes.items())),
        "assertions_input_hash": digest(encoded(assertions)),
        "records": len(result),
        "status": "candidate-not-quality-approved",
    }


def build(source, output, assertions=None):
    source, output = no_symlinks(source), no_symlinks(output)
    if source == output or source in output.parents or output in source.parents:
        raise ValueError("Source and output must be separate trees")
    records, receipt = make_graph(source, assertions)
    output.mkdir(parents=True, exist_ok=True)
    for name in ("graph.jsonl", "graph.sqlite", "receipt.json"):
        no_symlinks(output / name)
    payload = b"".join(encoded(record) + b"\n" for record in records)
    if len(payload) > MAX_TOTAL_BYTES:
        raise ValueError("Graph output size limit exceeded")
    receipt["graph_sha256"] = digest(payload)
    # Identical verified input reuses the completed candidate. No partial promotion.
    receipt_path = output / "receipt.json"
    if (
        receipt_path.exists()
        and read_json(receipt_path) == receipt
        and read_bytes(output / "graph.jsonl", MAX_TOTAL_BYTES) == payload
    ):
        with closing(
            sqlite3.connect((output / "graph.sqlite").as_uri() + "?mode=ro", uri=True)
        ) as db:
            existing = [
                json.loads(row[0])
                for row in db.execute("SELECT data FROM records ORDER BY id")
            ]
        if existing == records:
            return receipt
    with tempfile.TemporaryDirectory(dir=output, prefix="candidate-") as temporary:
        temp = Path(temporary)
        (temp / "graph.jsonl").write_bytes(payload)
        with closing(sqlite3.connect(temp / "graph.sqlite")) as db, db:
            db.execute(
                "CREATE TABLE records (id TEXT PRIMARY KEY, kind TEXT NOT NULL, data TEXT NOT NULL)"
            )
            db.execute("CREATE INDEX records_kind ON records(kind)")
            db.executemany(
                "INSERT INTO records VALUES (?,?,?)",
                [(r["id"], r["kind"], encoded(r).decode()) for r in records],
            )
        (temp / "receipt.json").write_bytes(encoded(receipt) + b"\n")
        # Receipt is the completion marker, removed before changing a generation.
        receipt_path.unlink(missing_ok=True)
        for name in ("graph.jsonl", "graph.sqlite", "receipt.json"):
            os.replace(temp / name, output / name)
    return receipt


def inspect(output, entity=None):
    output = no_symlinks(output)
    receipt = read_json(output / "receipt.json")
    raw = read_bytes(output / "graph.jsonl", MAX_TOTAL_BYTES)
    if digest(raw) != receipt["graph_sha256"]:
        raise ValueError("Candidate output integrity mismatch")
    records = [json.loads(line) for line in raw.splitlines()]
    if entity is None:
        return receipt
    ids = {r["id"] for r in records if r["kind"] == "entity" and r["label"] == entity}
    return [
        r
        for r in records
        if r["id"] in ids
        or r["kind"] == "assertion"
        and (r["subject"] in ids or r["object"] in ids)
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build_parser = commands.add_parser("build")
    build_parser.add_argument("--source", required=True)
    build_parser.add_argument("--output", required=True)
    build_parser.add_argument("--assertions")
    inspect_parser = commands.add_parser("inspect")
    inspect_parser.add_argument("--output", required=True)
    inspect_parser.add_argument("--entity")
    args = parser.parse_args()
    try:
        result = (
            build(args.source, args.output, args.assertions)
            if args.command == "build"
            else inspect(args.output, args.entity)
        )
    except (ValueError, OSError, sqlite3.Error) as exc:
        parser.exit(
            1,
            f"Graph operation rejected ({type(exc).__name__}); inputs or output invalid.\n",
        )
    print(json.dumps(result, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
