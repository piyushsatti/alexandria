"""Offline real-MCP container checks using temporary graph packages, no activation."""

import argparse
import hashlib
import json
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

from verify_mcp import Client

GRAPH = Path(__file__).resolve().parents[2] / "graph"
sys.path.insert(0, str(GRAPH))
import generation
import recovery
from graph_access import GraphReader


class GraphClient(Client):
    """Reuse the existing JSON-RPC client with strictly read-only test mounts."""

    def __init__(self, image, data, store):
        self.temporary = tempfile.TemporaryDirectory()
        self.cidfile = Path(self.temporary.name) / "container-id"
        self.process = subprocess.Popen(
            [
                "docker",
                "run",
                "--rm",
                "--cidfile",
                str(self.cidfile),
                "-i",
                "--network",
                "none",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--pids-limit",
                "128",
                "--cpus",
                "2",
                "--memory",
                "4g",
                "--tmpfs",
                "/tmp:rw,nosuid,noexec,size=64m",
                "-e",
                "HF_HUB_OFFLINE=1",
                "--mount",
                f"type=bind,src={data},dst=/data,readonly",
                "--mount",
                f"type=bind,src={store},dst=/graph,readonly",
                image,
                "serve",
                "--data",
                "/data",
                "--graph-store",
                "/graph",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        self.messages = queue.Queue()
        self.sequence = 0
        self.stderr_tail = []

        def receive():
            for line in self.process.stdout:
                try:
                    self.messages.put(json.loads(line))
                except ValueError:
                    self.messages.put({"eof": True})
            self.messages.put({"eof": True})

        def errors():
            for line in self.process.stderr:
                self.stderr_tail.append(line)
                self.stderr_tail[:] = self.stderr_tail[-10:]

        threading.Thread(target=receive, daemon=True).start()
        threading.Thread(target=errors, daemon=True).start()
        try:
            self.call(
                "initialize",
                {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {
                        "name": "checkpoint-verification",
                        "version": "2026.09.19",
                    },
                },
            )
            self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
            self.container_id = self.cidfile.read_text().strip()
            details = json.loads(
                subprocess.check_output(["docker", "inspect", self.container_id])
            )[0]
            assert details["HostConfig"]["NetworkMode"] == "none"
            assert details["HostConfig"]["ReadonlyRootfs"]
            assert {m["Destination"] for m in details["Mounts"]} == {"/data", "/graph"}
            assert all(not m["RW"] for m in details["Mounts"])
            assert details["Image"] == image
        except Exception:
            self.close()
            raise

    def close(self):
        if self.process.stdin and not self.process.stdin.closed:
            self.process.stdin.close()
        try:
            self.process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            if self.cidfile.exists():
                subprocess.run(
                    ["docker", "stop", "--time", "5", self.cidfile.read_text().strip()],
                    check=True,
                    stdout=subprocess.DEVNULL,
                )
            self.process.wait(timeout=15)
        self.temporary.cleanup()


def check(client, expected, data, graph_available):
    names = {item["name"] for item in client.call("tools/list", {})["tools"]}
    assert names == {"search", "read_document", "text_search", "status"}
    status = client.tool("status", {})
    target = "Alexandria/architecture-2026.09.18/versioning.md"
    original = (data / status["database"] / "source" / target).read_text()
    doc = client.tool("read_document", {"path": target})
    assert doc["text"] == original
    assert doc["revision"] == status["revision"]
    hits = client.tool(
        "search",
        {
            "query": "What version format should internal Alexandria releases use?",
            "limit": 20,
        },
    )
    assert any(hit["path"] == target for hit in hits)
    literal = client.tool("text_search", {"query": "YYYY.MM.DD", "limit": 200})
    assert any(hit["path"] == target for hit in literal["matches"])
    graph_status = client.tool("status", {"graph": True})
    assert graph_status["available"] is graph_available
    if graph_available:
        assert graph_status == expected.status()
        row = next(r for r in expected.records.values() if r["kind"] == "assertion")
        found = client.tool("search", {"query": row["id"], "graph": True})
        assert found["matches"] == [expected._relationship(row)]
        read = client.tool("read_document", {"path": row["id"], "graph": True})
        assert read == expected.read(row["id"])
        assert read["record"]["qualification"]
        assert graph_status["review"]["accepted"] is False
    return {
        "container_id": client.container_id,
        "document_revision": status["revision"],
        "document_sha256": hashlib.sha256(original.encode()).hexdigest(),
        "semantic_search": "passed",
        "literal_search": "passed",
        "full_document_read": "passed",
        "graph_status": graph_status,
        "read_only_mounts": True,
        "network": "none",
    }


def verify(args):
    data = Path(args.data).resolve()
    bundle = Path(args.bundle).resolve()
    expected = GraphReader(bundle)
    before = (data / "active.json").read_bytes()
    with tempfile.TemporaryDirectory(prefix="alexandria-delivery-check-") as temp:
        root = Path(temp).resolve()
        store = root / "store"
        identity = generation.package(bundle, store)["package_id"]
        generation.select(store, identity)
        reports = []
        for iteration in range(2):
            client = GraphClient(args.image, data, store)
            try:
                reports.append(
                    {
                        "scenario": f"fresh-container-{iteration + 1}",
                        **check(client, expected, data, True),
                    }
                )
            finally:
                client.close()
        backup = root / "backup"
        recovery.backup(store, backup)
        restored = root / "restored"
        recovery.restore(backup, restored, current_policy_store=store)
        client = GraphClient(args.image, data, restored)
        try:
            reports.append(
                {"scenario": "restored-backup", **check(client, expected, data, True)}
            )
        finally:
            client.close()
        damaged = root / "damaged"
        shutil.copytree(store, damaged)
        (damaged / "packages" / identity / "candidate-graph/graph.sqlite").write_bytes(
            b"corrupted test fixture"
        )
        client = GraphClient(args.image, data, damaged)
        try:
            reports.append(
                {
                    "scenario": "damaged-optional-graph",
                    **check(client, expected, data, False),
                }
            )
        finally:
            client.close()
    assert len({row["container_id"] for row in reports}) == 4
    assert (data / "active.json").read_bytes() == before
    report = {
        "status": "passed",
        "image": args.image,
        "bundle": str(bundle),
        "checks": reports,
        "production_activated": False,
        "external_inference": False,
        "document_active_manifest_preserved": True,
        "temporary_stores_removed": True,
    }
    with Path(args.report).open("x") as stream:
        json.dump(report, stream, indent=2)
        stream.write("\n")
    print(json.dumps({"status": "passed", "containers": 4, "report": args.report}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("image", "data", "bundle", "report"):
        parser.add_argument("--" + name, required=True)
    verify(parser.parse_args())
