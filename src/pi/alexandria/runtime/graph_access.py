"""Bounded read-only inspection of a completed, held operator graph bundle.

No inference, acceptance, filesystem selection by callers, or active-index writes.
The operator-selected directory must be immutable while loaded. Hashes detect
inconsistency, not a hostile operator rewriting the entire bundle and all receipts.
"""

import copy
import hashlib
import json
import os
import re
import sqlite3
from contextlib import closing
from pathlib import Path, PurePosixPath

MAX_JSON = 16 * 1024 * 1024
MAX_GRAPH = 32 * 1024 * 1024
MAX_RECORDS = 10000
ID = re.compile(r"(?:assertion|entity|doc|rev|section|link):[0-9a-f]{64}\Z")


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def encoded(value):
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode()


def require(condition):
    if not condition:
        raise ValueError("Invalid or incomplete graph candidate bundle")


def safe_path(value):
    require(
        isinstance(value, str)
        and bool(value)
        and "\\" not in value
        and "\0" not in value
    )
    require(
        not PurePosixPath(value).is_absolute()
        and all(p not in ("", ".", "..") for p in value.split("/"))
    )
    return value


def regular(path, maximum):
    path = Path(os.path.abspath(path))
    require(all(not part.is_symlink() for part in (path, *path.parents)))
    require(path.is_file() and path.stat().st_size <= maximum)
    return path


def read_bytes(path, maximum):
    with regular(path, maximum).open("rb") as stream:
        raw = stream.read(maximum + 1)
    require(len(raw) <= maximum)
    return raw


def loads(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result)
            result[key] = value
        return result

    return json.loads(raw, object_pairs_hook=unique)


def read_json(path):
    return loads(read_bytes(path, MAX_JSON))


class GraphReader:
    """Load and validate a small immutable candidate into memory, once per process."""

    def __init__(self, root):
        self.root = Path(os.path.abspath(root))
        require(all(not p.is_symlink() for p in (self.root, *self.root.parents)))
        require(
            not any(
                (self.root / name).exists()
                for name in ("operator-failure.json", "failure.json")
            )
        )
        result = read_json(self.root / "operator-result.json")
        hold = read_json(self.root / "hold-receipt.json")
        receipt = read_json(self.root / "candidate-graph/receipt.json")
        manifest = read_json(self.root / "source/manifest.json")
        candidate = read_json(self.root / "03-humanizer.json")
        frozen = read_json(self.root / "02-frozen.json")
        require(
            result["status"] == "completed-held"
            and result["automatic_acceptance"] is False
        )
        require(
            hold["status"] == "held-pending-owner-review"
            and hold["automatic_acceptance"] is False
        )
        require(
            type(hold["model_review_pass"]) is bool
            and result["model_review_pass"] == hold["model_review_pass"]
        )
        candidate_hash = digest(
            json.dumps(candidate, ensure_ascii=False, separators=(",", ":")).encode()
        )
        require(candidate_hash == hold["candidate_hash"] == result["candidate_hash"])
        # Only prose may differ from the frozen extraction; qualifications stay fixed.
        expected = copy.deepcopy(frozen)

        def paragraphs(value):
            return [
                p
                for page in value["pages"]
                for section in page["sections"]
                for p in section["paragraphs"]
            ]

        before, after = paragraphs(expected), paragraphs(candidate)
        require(len(before) == len(after))
        for left, right in zip(before, after, strict=True):
            require(left["id"] == right["id"])
            left["text"] = right["text"]
        require(expected == candidate)
        require(read_json(self.root / "run-manifest.json")["source"] == manifest)
        self.revision = manifest["revision"]
        require(isinstance(self.revision, str) and 0 < len(self.revision) <= 1000)
        require(self.revision == receipt["revision"] == result["source_revision"])
        require(
            receipt["status"] == "candidate-not-quality-approved"
            and receipt["schema_version"] == "2026.09.19"
        )
        require(
            isinstance(manifest["files"], list) and 1 <= len(manifest["files"]) <= 3
        )
        self.texts, self.hashes = {}, {}
        for row in manifest["files"]:
            path = safe_path(row["path"])
            require(path != "manifest.json" and path not in self.texts)
            raw = read_bytes(self.root / "source" / path, 16000)
            require(bool(raw) and digest(raw) == row["sha256"])
            self.texts[path], self.hashes[path] = raw.decode("utf-8"), row["sha256"]
        require(self.hashes == receipt["sources"])
        assertions = read_json(self.root / "assertions.json")
        require(isinstance(assertions, list) and len(assertions) <= MAX_RECORDS)
        require(digest(encoded(assertions)) == receipt["assertions_input_hash"])
        raw = read_bytes(self.root / "candidate-graph/graph.jsonl", MAX_GRAPH)
        self.graph_hash = digest(raw)
        require(self.graph_hash == receipt["graph_sha256"] == result["graph_sha256"])
        lines = raw.splitlines()
        require(len(lines) <= MAX_RECORDS and len(lines) == receipt["records"])
        rows = [loads(line) for line in lines]
        self.records = {}
        for row in rows:
            require(
                isinstance(row, dict)
                and isinstance(row.get("id"), str)
                and ID.fullmatch(row["id"]) is not None
            )
            require(row["id"] not in self.records)
            self.records[row["id"]] = row
        database = regular(self.root / "candidate-graph/graph.sqlite", MAX_GRAPH)
        with closing(
            sqlite3.connect(database.as_uri() + "?mode=ro&immutable=1", uri=True)
        ) as db:
            db.execute("PRAGMA query_only = ON")
            db.execute("PRAGMA trusted_schema = OFF")
            require(db.execute("PRAGMA quick_check").fetchone() == ("ok",))
            saved = db.execute(
                "SELECT id, kind, data FROM records ORDER BY id LIMIT ?",
                (MAX_RECORDS + 1,),
            ).fetchall()
        require(len(saved) == len(rows))
        for identity, kind, data in saved:
            require(
                identity in self.records
                and self.records[identity] == loads(data)
                and self.records[identity]["kind"] == kind
            )
        self._validate_assertions(assertions, candidate)
        self.entity_ids = {
            r[k]
            for r in self.records.values()
            if r["kind"] == "assertion"
            for k in ("subject", "object")
        }
        self.review = {
            "status": "held-pending-owner-review",
            "accepted": False,
            "access_mode": "held_inspection_only",
            "model_review_pass": hold["model_review_pass"],
            "warning": "Candidate relationships and qualifications are model proposals, not accepted knowledge.",
        }
        quality_path = self.root / "quality-findings.json"
        if quality_path.exists():
            quality = read_json(quality_path)
            require(
                quality.get("status") in ("clear", "held")
                and quality.get("automatic_acceptance") is False
                and type(quality.get("blocking_findings")) is int
                and isinstance(quality.get("findings"), list)
                and len(quality["findings"]) <= MAX_RECORDS
            )
            self.review["quality"] = copy.deepcopy(quality)
            self.review["blocking_findings"] = quality["blocking_findings"]
        self.candidate_hash = candidate_hash

    def _span(self, row):
        path = safe_path(row["path"])
        require(path in self.texts)
        start, end = row["start"], row["end"]
        require(
            type(start) is int
            and type(end) is int
            and 0 <= start < end <= len(self.texts[path])
        )
        require(self.texts[path][start:end] == row["quote"])
        require(
            row["revision"] == self.revision
            and row["source_sha256"] == self.hashes[path]
        )

    def _validate_assertions(self, assertions, candidate):
        relationships = [r for r in self.records.values() if r["kind"] == "assertion"]
        require(len(relationships) == len(assertions) == len(candidate["claims"]))
        claims = {c["id"]: c for c in candidate["claims"]}
        notes = {n["id"]: n for n in candidate["annotations"]}
        require(
            len(claims) == len(candidate["claims"])
            and len(notes) == len(candidate["annotations"])
        )
        for assertion, claim in zip(assertions, candidate["claims"], strict=True):
            identity = "assertion:" + digest(encoded([self.revision, assertion]))
            row = self.records.get(identity)
            require(row is not None)
            self._span(row)
            for field in ("predicate", "path", "quote"):
                require(row[field] == assertion[field] == claim[field])
            require(
                row["status"] in ("extracted", "inferred")
                and row["status"] == assertion["status"]
            )
            require(row["extraction"] == assertion["extraction"])
            require(row["start"] == assertion["start"])
            for field in ("subject", "object"):
                entity = self.records.get(row[field])
                require(entity is not None and entity["kind"] == "entity")
                require(
                    entity["label"] == assertion[field] == claim[field]
                    and entity["scope"] == row["path"]
                )
                require(
                    entity["id"]
                    == "entity:" + digest(encoded([row["path"], entity["label"]]))
                )
            qualification = row["qualification"]
            require(
                qualification["source_status"]
                == assertion["qualification"]["source_status"]
                == claim["source_status"]
            )
            require(
                qualification.get("review_state", "not_checked")
                == assertion["qualification"].get("review_state", "not_checked")
                == claim.get("review_state", "not_checked")
            )
            require(
                qualification["source_status"]
                in (
                    "accepted",
                    "proposed",
                    "open",
                    "requirement",
                    "limitation",
                    "historical",
                    "deferred",
                )
            )
            span = qualification["scope"]
            self._span(span)
            for key, value in claim["scope"].items():
                require(span[key] == value)
            require(len(qualification["annotations"]) == len(claim["annotation_ids"]))
            for note, note_id in zip(
                qualification["annotations"], claim["annotation_ids"], strict=True
            ):
                self._span(note)
                for key, value in notes[note_id].items():
                    require(note[key] == value)
            for actual, original in [
                (span, assertion["qualification"]["scope"]),
                *zip(
                    qualification["annotations"],
                    assertion["qualification"]["annotations"],
                    strict=True,
                ),
            ]:
                for key, value in original.items():
                    require(actual[key] == value)
        # Only source-bound document IDs can be used for original-document access.
        for path in self.texts:
            identity = "doc:" + digest(encoded([path]))
            require(
                self.records.get(identity)
                == {"id": identity, "kind": "document", "path": path}
            )

    def status(self):
        return {
            "available": True,
            "graph_sha256": self.graph_hash,
            "candidate_hash": self.candidate_hash,
            "source_revision": self.revision,
            "review": copy.deepcopy(self.review),
            "documents": len(self.texts),
            "relationships": sum(
                r["kind"] == "assertion" for r in self.records.values()
            ),
        }

    def _relationship(self, row):
        result = copy.deepcopy(row)
        for name in ("subject", "object"):
            result[name] = copy.deepcopy(self.records[row[name]])
        result["document_id"] = "doc:" + digest(encoded([row["path"]]))
        return result

    def search(self, query, limit=5, offset=0):
        require(isinstance(query, str) and 1 <= len(query.strip()) <= 2000)
        require(
            type(limit) is int
            and 1 <= limit <= 20
            and type(offset) is int
            and 0 <= offset <= MAX_RECORDS
        )
        needle = query.casefold()
        matches = []
        for row in sorted(self.records.values(), key=lambda r: r["id"]):
            if row["kind"] != "assertion":
                continue
            labels = [self.records[row[k]]["label"] for k in ("subject", "object")]
            if (
                needle
                in " ".join(
                    [
                        row["id"],
                        row["subject"],
                        row["object"],
                        *labels,
                        row["predicate"],
                        row["quote"],
                    ]
                ).casefold()
            ):
                matches.append(row)
        end = min(offset + limit, len(matches))
        return {
            **self.status(),
            "matches": [self._relationship(r) for r in matches[offset:end]],
            "offset": offset,
            "next_offset": end if end < len(matches) else None,
            "total_matches": len(matches),
            "search_method": "literal candidate relationship discovery",
        }

    def read(self, identity, start=0, max_chars=12000):
        require(isinstance(identity, str) and ID.fullmatch(identity) is not None)
        require(
            type(start) is int
            and start >= 0
            and type(max_chars) is int
            and 1 <= max_chars <= 50000
        )
        row = self.records.get(identity)
        require(row is not None and row["kind"] in ("assertion", "document", "entity"))
        if row["kind"] == "entity":
            require(identity in self.entity_ids)
            return {
                **self.status(),
                "entity": copy.deepcopy(row),
                "next_action": "Search this entity ID in graph mode for paginated source-bound relationships.",
            }
        path = row["path"]
        require(path in self.texts)
        if row["kind"] == "document":
            require(identity == "doc:" + digest(encoded([path])))
        text = self.texts[path]
        require(start <= len(text))
        end = min(start + max_chars, len(text))
        return {
            **self.status(),
            "record": self._relationship(row)
            if row["kind"] == "assertion"
            else copy.deepcopy(row),
            "source": {
                "path": path,
                "revision": self.revision,
                "sha256": self.hashes[path],
                "text": text[start:end],
                "start": start,
                "end": end,
                "total_chars": len(text),
                "next_start": end if end < len(text) else None,
            },
        }


PACKAGE_FILES = (
    "operator-result.json",
    "hold-receipt.json",
    "candidate-graph/receipt.json",
    "source/manifest.json",
    "03-humanizer.json",
    "02-frozen.json",
    "run-manifest.json",
    "assertions.json",
    "candidate-graph/graph.jsonl",
    "candidate-graph/graph.sqlite",
)
PACKAGE_ID = re.compile(r"[0-9a-f]{32}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def selection_state(store):
    state = read_json(Path(store) / "graph-selection.json")
    require(
        state.get("version") == "2026.09.19" and state.get("inspection_only") is True
    )
    for key in ("current", "previous"):
        value = state[key]
        require(value is None or isinstance(value, str) and PACKAGE_ID.fullmatch(value))
    denied = state["withdrawn_source_hashes"]
    require(isinstance(denied, list) and len(denied) <= MAX_RECORDS)
    require(all(isinstance(h, str) and SHA256.fullmatch(h) for h in denied))
    return state


def package_reader(store, identity, denied=()):
    require(isinstance(identity, str) and PACKAGE_ID.fullmatch(identity))
    root = Path(store) / "packages" / identity
    manifest = read_json(root / "package.json")
    require(
        manifest.get("version") == "2026.09.19"
        and manifest.get("inspection_only") is True
    )
    files = manifest["files"]
    require(isinstance(files, dict) and len(files) <= len(PACKAGE_FILES) + 3)
    source = read_json(root / "source/manifest.json")
    optional = {"quality-findings.json"} if "quality-findings.json" in files else set()
    expected = (
        set(PACKAGE_FILES)
        | optional
        | {"source/" + safe_path(f["path"]) for f in source["files"]}
    )
    require(set(files) == expected)
    for name, checksum in files.items():
        require(isinstance(checksum, str) and SHA256.fullmatch(checksum))
        require(digest(read_bytes(root / name, MAX_GRAPH)) == checksum)
    reader = GraphReader(root)
    require(not set(reader.hashes.values()).intersection(denied))
    return reader


def selected_reader(store):
    state = selection_state(store)
    require(state["current"] is not None)
    return package_reader(store, state["current"], state["withdrawn_source_hashes"])


class OptionalGraph:
    """Bad optional graph artifacts cannot prevent document service startup."""

    def __init__(self, root=None, store=None):
        self.reader = None
        self.reason = "not_selected"
        if root is not None or store is not None:
            try:
                require(root is None or store is None)
                self.reader = (
                    selected_reader(store) if store is not None else GraphReader(root)
                )
            except (
                ValueError,
                OSError,
                KeyError,
                TypeError,
                AttributeError,
                IndexError,
                UnicodeError,
                sqlite3.Error,
            ):
                self.reason = "invalid_or_incomplete_candidate"

    def status(self):
        return (
            self.reader.status()
            if self.reader
            else {
                "available": False,
                "reason": self.reason,
                "review": {
                    "accepted": False,
                    "status": "unavailable",
                    "access_mode": "held_inspection_only",
                },
            }
        )

    def search(self, query, limit=5, offset=0):
        if self.reader is None:
            raise ValueError("Graph unavailable: " + self.reason)
        return self.reader.search(query, limit, offset)

    def read(self, identity, start=0, max_chars=12000):
        if self.reader is None:
            raise ValueError("Graph unavailable: " + self.reason)
        return self.reader.read(identity, start, max_chars)
