"""Trusted local MCP transport. Never exposes Docker control to a model."""

import json
import os
import selectors
import subprocess
import tempfile
import time
from pathlib import Path

from pi.alexandria.graph.graph import no_symlinks
from pi.alexandria.graph.harness import IMAGE


class Client:
    def __init__(self, data):
        data = no_symlinks(data)
        if "," in str(data) or not data.is_dir():
            raise ValueError("Invalid runtime data directory")
        self.tmp = tempfile.TemporaryDirectory(prefix="graph-mcp-")
        self.cidfile = Path(self.tmp.name) / "cid"
        self.sequence = 0
        self.buffer = b""
        args = [
            "docker",
            "run",
            "--rm",
            "--cidfile",
            str(self.cidfile),
            "-i",
            "--network",
            "none",
            "--read-only",
            "--user",
            "65534:65534",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--cpus",
            "2",
            "--memory",
            "4g",
            "--pids-limit",
            "64",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=64m,mode=1777",
            "-e",
            "HF_HUB_OFFLINE=1",
            "-e",
            "HOME=/tmp",
            "--mount",
            f"type=bind,src={data},dst=/data,readonly",
            IMAGE,
            "serve",
            "--data",
            "/data",
        ]
        self.process = subprocess.Popen(
            args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        try:
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
            details = json.loads(
                subprocess.check_output(
                    ["docker", "inspect", self.cidfile.read_text().strip()]
                )
            )[0]
            host = details["HostConfig"]
            assert host["NetworkMode"] == "none" and host["ReadonlyRootfs"]
            assert details["Config"]["User"] == "65534:65534"
            assert all(not m["RW"] for m in details["Mounts"] if m["Type"] == "bind")
            assert {
                m["Destination"] for m in details["Mounts"] if m["Type"] == "bind"
            } == {"/data"}
            self.verified = True
        except BaseException:
            self.close()
            raise

    def send(self, value):
        self.process.stdin.write(json.dumps(value).encode() + b"\n")
        self.process.stdin.flush()

    def call(self, method, params):
        self.sequence += 1
        self.send(
            {"jsonrpc": "2.0", "id": self.sequence, "method": method, "params": params}
        )
        end = time.monotonic() + 120
        selector = selectors.DefaultSelector()
        selector.register(self.process.stdout, selectors.EVENT_READ)
        received = 0
        try:
            while time.monotonic() < end:
                if b"\n" in self.buffer:
                    line, self.buffer = self.buffer.split(b"\n", 1)
                    value = json.loads(line)
                    if value.get("id") == self.sequence:
                        if "error" in value:
                            raise ValueError("MCP rejected request")
                        return value["result"]
                    continue
                if selector.select(timeout=0.2):
                    chunk = os.read(self.process.stdout.fileno(), 65536)
                    if not chunk:
                        raise RuntimeError("MCP closed before responding")
                    received += len(chunk)
                    if received > 4 * 1024 * 1024:
                        raise ValueError("MCP response limit exceeded")
                    self.buffer += chunk
            raise TimeoutError("MCP response deadline exceeded")
        finally:
            selector.close()

    def tool(self, name, arguments):
        result = self.call("tools/call", {"name": name, "arguments": arguments})
        if result.get("isError"):
            raise ValueError("MCP tool rejected request")
        if "structuredContent" in result:
            value = result["structuredContent"]
            return value["result"] if list(value) == ["result"] else value
        return json.loads(result["content"][0]["text"])

    def close(self):
        if self.process.stdin and not self.process.stdin.closed:
            self.process.stdin.close()
        if self.cidfile.exists():
            cid = self.cidfile.read_text().strip()
            if len(cid) == 64 and all(c in "0123456789abcdef" for c in cid):
                subprocess.run(
                    ["docker", "rm", "-f", cid],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
        if self.process.poll() is None:
            self.process.kill()
        self.process.wait(timeout=10)
        self.process.stdout.close()
        self.tmp.cleanup()
