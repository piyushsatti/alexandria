"""Run actual MCP JSON-RPC against two fresh offline containers; no client registration."""

import argparse
import hashlib
import json
import queue
import subprocess
import tempfile
import threading
import time
from pathlib import Path


class Client:
    def __init__(self, image, data):
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
                image,
                "serve",
                "--data",
                "/data",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
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
                "clientInfo": {
                    "name": "checkpoint-verification",
                    "version": "2026.09.18",
                },
            },
        )
        self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.container_id = self.cidfile.read_text().strip()
        details = json.loads(
            subprocess.check_output(["docker", "inspect", self.container_id])
        )[0]
        assert details["HostConfig"]["NetworkMode"] == "none"
        assert {m["Destination"] for m in details["Mounts"]} == {"/data"}
        assert not details["Mounts"][0]["RW"]
        assert details["Image"] == image

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
                raise RuntimeError("MCP exited before responding")
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


def verify(args):
    reports = []
    for iteration in range(2):
        client = Client(args.image, args.data)
        try:
            tools = {x["name"] for x in client.call("tools/list", {})["tools"]}
            assert {
                "search",
                "read_document",
                "text_search",
                "status",
                "list_files",
            } <= tools
            status = client.tool("status", {})
            tree = client.tool("list_files", {"max_depth": 2, "limit": 20})
            assert tree["layer"] == "committed"
            search_started = time.monotonic()
            hits = client.tool(
                "search",
                {
                    "query": "What version format should internal Alexandria releases use?",
                    "limit": 20,
                },
            )
            search_seconds = time.monotonic() - search_started
            target = "Alexandria/architecture-2026.09.18/versioning.md"
            hit = next(x for x in hits if x["path"] == target)
            expected = subprocess.check_output(
                ["git", "-C", args.source, "show", f"{status['revision']}:{target}"]
            ).decode("utf-8")
            assert hit["revision"] == status["revision"]
            assert (
                expected[hit["offset"] : hit["offset"] + len(hit["text"])]
                == hit["text"]
            )
            start, pieces = 0, []
            while True:
                page = client.tool(
                    "read_document", {"path": target, "start": start, "max_chars": 300}
                )
                assert page["revision"] == status["revision"]
                pieces.append(page["text"])
                if page["next_start"] is None:
                    break
                start = page["next_start"]
            assert "".join(pieces) == expected
            literal = client.tool("text_search", {"query": "YYYY.MM.DD", "limit": 200})
            match = next(x for x in literal["matches"] if x["path"] == target)
            assert "YYYY.MM.DD" in match["text"]
            assert "YYYY.MM.DD" in expected.splitlines()[match["line"] - 1]
            capped = client.tool("text_search", {"query": "the", "limit": 1})
            assert capped["truncated"] and capped["reason"] == "result_limit"
            unknown = client.call(
                "tools/call",
                {"name": "read_document", "arguments": {"path": "../../etc/passwd"}},
            )
            assert unknown.get("isError")
            reports.append(
                {
                    "iteration": iteration + 1,
                    "container_id": client.container_id,
                    "status": status,
                    "document": target,
                    "document_sha256": hashlib.sha256(expected.encode()).hexdigest(),
                    "pages": len(pieces),
                    "semantic_rank": hits.index(hit) + 1,
                    "semantic_seconds": round(search_seconds, 3),
                    "literal_line": match["line"],
                    "literal_truncated": literal["truncated"],
                    "unknown_path_rejected": True,
                    "limit_reported": True,
                    "network": "none",
                    "source_mount": False,
                }
            )
        finally:
            client.close()
    assert reports[0]["container_id"] != reports[1]["container_id"]
    assert reports[0]["status"] == reports[1]["status"]
    assert reports[0]["document_sha256"] == reports[1]["document_sha256"]
    receipt = {
        "image": args.image,
        "data_directory": args.data,
        "checks": reports,
        "container_replacement_persistence": "passed",
    }
    Path(args.report).write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    parser.add_argument("--data", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--report", required=True)
    verify(parser.parse_args())
