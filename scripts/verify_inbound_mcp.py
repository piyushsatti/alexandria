"""Verify inbound MCP persistence across two fresh containers.

This is a manual release check. It expects a prepared data block whose
``.alexandria-inbound`` directory is writable by the container UID.
"""

from __future__ import annotations

import argparse
import json
import queue
import subprocess
import tempfile
import threading
import time
from pathlib import Path


class Client:
    def __init__(self, image: str, data: Path, inbound: Path):
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
                "--cpus",
                "2",
                "--memory",
                "4g",
                "--read-only",
                "--tmpfs",
                "/tmp:rw,noexec,nosuid,size=64m",
                "-e",
                "HF_HUB_OFFLINE=1",
                "--mount",
                f"type=bind,src={data},dst=/data,readonly",
                "--mount",
                f"type=bind,src={inbound},dst=/inbound",
                image,
                "serve",
                "--data",
                "/data",
                "--inbound",
                "/inbound",
                "--enable-inbound",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        self.messages = queue.Queue()
        self.sequence = 0

        def receive():
            for line in self.process.stdout:
                self.messages.put(json.loads(line))
            self.messages.put({"eof": True})

        threading.Thread(target=receive, daemon=True).start()
        self.call(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "inbound-verification", "version": "2026.09.20"},
            },
        )
        self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.container_id = self.cidfile.read_text().strip()

    def send(self, message):
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()

    def call(self, method, params):
        self.sequence += 1
        self.send(
            {"jsonrpc": "2.0", "id": self.sequence, "method": method, "params": params}
        )
        while True:
            message = self.messages.get(timeout=120)
            if message.get("eof"):
                detail = self.process.stderr.read().strip()
                raise RuntimeError(f"MCP exited before responding: {detail}")
            if message.get("id") == self.sequence:
                if "error" in message:
                    raise RuntimeError(message["error"])
                return message["result"]

    def tool(self, name, arguments):
        result = self.call("tools/call", {"name": name, "arguments": arguments})
        if result.get("isError"):
            raise RuntimeError(result)
        if "structuredContent" in result:
            data = result["structuredContent"]
            return data["result"] if list(data) == ["result"] else data
        return json.loads(result["content"][0]["text"])

    def close(self):
        self.process.stdin.close()
        self.process.wait(timeout=30)
        if self.process.returncode:
            raise RuntimeError(f"Container exited {self.process.returncode}")
        assert (
            subprocess.run(
                ["docker", "inspect", self.container_id],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            ).returncode
            != 0
        )
        self.temporary.cleanup()


def wait_for_indexed(client: Client, submission_id: str) -> dict:
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        receipt = client.tool("status", {"submission_id": submission_id})["submission"]
        if receipt["status"] in ("indexed", "failed"):
            return receipt
        time.sleep(0.5)
    raise TimeoutError("inbound submission did not finish within 120 seconds")


def verify(args) -> None:
    data = Path(args.data)
    content = "---\ntitle: Inbound verification\n---\nA durable uncommitted note.\n"
    reports = []
    with tempfile.TemporaryDirectory(prefix="alexandria-inbound-check-") as temporary:
        # The release image runs as UID 10001. A TemporaryDirectory itself is
        # mode 0700 and owned by the host user, so bind a child mount with
        # explicit write bits for the container identity.
        inbound = Path(temporary) / "inbound"
        inbound.mkdir(mode=0o777)
        inbound.chmod(0o777)
        client = Client(args.image, data, inbound)
        try:
            names = {item["name"] for item in client.call("tools/list", {})["tools"]}
            assert names == {
                "list_files",
                "read_document",
                "search",
                "status",
                "submit_inbound",
                "text_search",
            }
            submitted = client.tool(
                "submit_inbound",
                {
                    "content": content,
                    "session_label": "release-check",
                    "idempotency_key": "release-check-2026-09-20",
                    "title": "Inbound verification",
                    "facets": ["verification"],
                    "source_ref": "test://release-check",
                },
            )
            replay = client.tool(
                "submit_inbound",
                {
                    "content": content,
                    "session_label": "different-session",
                    "idempotency_key": "release-check-2026-09-20",
                },
            )
            assert replay["submission_id"] == submitted["submission_id"]
            receipt = wait_for_indexed(client, submitted["submission_id"])
            assert receipt["status"] == "indexed", receipt
            path = submitted["path"]
            document = client.tool("read_document", {"path": path})
            assert document["text"] == content
            tree = client.tool("list_files", {"layer": "inbound", "limit": 20})
            assert any(entry["path"] == path for entry in tree["entries"])
            literal = client.tool(
                "text_search", {"query": "durable", "layer": "inbound"}
            )
            assert any(match["path"] == path for match in literal["matches"])
            semantic = client.tool(
                "search",
                {
                    "query": "uncommitted durable note",
                    "layer": "inbound",
                    "limit": 5,
                },
            )
            assert any(hit["path"] == path for hit in semantic)
            reports.append({"submission": receipt, "semantic_hits": len(semantic)})
        finally:
            client.close()

        client = Client(args.image, data, inbound)
        try:
            receipt = client.tool(
                "status", {"submission_id": submitted["submission_id"]}
            )["submission"]
            assert receipt["status"] == "indexed"
            reports.append({"restart_status": receipt["status"]})
        finally:
            client.close()

    report = {
        "image": args.image,
        "data_directory": str(data),
        "checks": reports,
        "inbound_restart_persistence": "passed",
        "committed_layer_modified": False,
    }
    Path(args.report).write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    parser.add_argument("--data", required=True)
    parser.add_argument("--report", required=True)
    verify(parser.parse_args())
