"""Read-only, scope-checked retrieval boundary. Model input never becomes code."""

import json
import re

from graph import safe_path


class Broker:
    def __init__(self, client, revision, paths, max_calls=20):
        if not re.fullmatch(r"[0-9a-f]{40}", revision):
            raise ValueError("Expected exact source commit")
        if (
            not paths
            or len(paths) > 100
            or type(max_calls) is not int
            or not 1 <= max_calls <= 100
        ):
            raise ValueError("Invalid scope or call budget")
        self.paths = frozenset(safe_path(p) for p in paths)
        self.client, self.revision = client, revision
        self.remaining = max_calls
        self.check_revision(client.tool("status", {}))

    def check_revision(self, result):
        if not isinstance(result, dict) or result.get("revision") != self.revision:
            raise ValueError("Retrieval revision mismatch")

    def call(self, name, arguments):
        if self.remaining <= 0:
            raise ValueError("Retrieval call budget exhausted")
        self.remaining -= 1
        if not isinstance(arguments, dict):
            raise TypeError("Expected arguments object")
        if name in ("search", "text_search"):
            if set(arguments) - {"query", "limit"} or "query" not in arguments:
                raise ValueError("Unknown or missing search arguments")
            query = arguments["query"]
            limit = arguments.get("limit", 10)
            if (
                not isinstance(query, str)
                or not query.strip()
                or len(query) > 1000
                or "\n" in query
                or "\r" in query
            ):
                raise ValueError("Invalid query")
            if type(limit) is not int or not 1 <= limit <= 20:
                raise ValueError("Invalid result limit")
            raw = self.client.tool(name, {"query": query, "limit": limit})
            if name == "text_search":
                self.check_revision(raw)
                rows = raw["matches"]
            else:
                rows = raw
            if not isinstance(rows, list) or len(rows) > limit:
                raise ValueError("Invalid backend results")
            visible = []
            for row in rows:
                if not isinstance(row, dict):
                    raise TypeError("Invalid backend row")
                if name == "search":
                    self.check_revision(row)
                if row.get("path") not in self.paths:
                    continue
                # Discard unknown metadata, including any embedded tool instructions.
                selected = {
                    k: row[k] for k in ("path", "text", "offset", "line") if k in row
                }
                if (
                    not isinstance(selected.get("text"), str)
                    or len(selected["text"]) > 12000
                ):
                    raise ValueError("Invalid evidence text")
                selected["revision"] = self.revision
                visible.append(selected)
            result = {
                "results": visible,
                "revision": self.revision,
                "coverage": "bounded backend search filtered to allowed paths; empty is not proof of absence",
            }
        elif name == "read_document":
            if (
                set(arguments) - {"path", "start", "max_chars"}
                or "path" not in arguments
            ):
                raise ValueError("Unknown or missing read arguments")
            path = safe_path(arguments["path"])
            if path not in self.paths:
                raise ValueError("Document outside allowed scope")
            start, maximum = arguments.get("start", 0), arguments.get("max_chars", 4000)
            if (
                type(start) is not int
                or not 0 <= start <= 10000000
                or type(maximum) is not int
                or not 1 <= maximum <= 12000
            ):
                raise ValueError("Invalid read bounds")
            raw = self.client.tool(
                name, {"path": path, "start": start, "max_chars": maximum}
            )
            self.check_revision(raw)
            if (
                raw.get("path") != path
                or not isinstance(raw.get("text"), str)
                or len(raw["text"]) > maximum
            ):
                raise ValueError("Invalid document response")
            result = {
                k: raw[k]
                for k in (
                    "path",
                    "revision",
                    "sha256",
                    "text",
                    "start",
                    "end",
                    "total_chars",
                    "next_start",
                )
                if k in raw
            }
        else:
            raise ValueError("Tool is not allowed")
        if len(json.dumps(result).encode()) > 65536:
            raise ValueError("Retrieval response exceeds size budget")
        return result
