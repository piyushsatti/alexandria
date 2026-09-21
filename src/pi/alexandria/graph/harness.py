"""Trusted host launcher; extraction workers have no host write mounts or network."""

import argparse
import base64
import hashlib
import json
import selectors
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from pi.alexandria.graph.graph import no_symlinks

IMAGE = "sha256:4bb1353d8aee3c3b37acf6953780be2baa1352203a28739b825795e1d38e61ad"
PACKAGE_DIR = Path(__file__).resolve().parent
MAX_BYTES = 48 * 1024 * 1024


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args])


def snapshot(repo, revision, paths, target):
    revision = (
        git(repo, "rev-parse", "--verify", revision + "^{commit}").decode().strip()
    )
    records = []
    total_size = 0
    for name in sorted(set(paths)):
        p = Path(name)
        if (
            p.is_absolute()
            or ".." in p.parts
            or "," in name
            or p.suffix not in (".md", ".txt")
        ):
            raise ValueError("Only safe, explicit Markdown/text paths are allowed")
        entry = git(repo, "ls-tree", revision, "--", name).decode()
        if not entry.startswith("100644 blob ") and not entry.startswith(
            "100755 blob "
        ):
            raise ValueError("Source must be a committed regular file")
        size = int(git(repo, "cat-file", "-s", revision + ":" + name))
        total_size += size
        if size > 2 * 1024 * 1024 or total_size > 16 * 1024 * 1024:
            raise ValueError("Snapshot exceeds bounded pilot size")
        content = git(repo, "show", revision + ":" + name)
        if len(content) > 2 * 1024 * 1024:
            raise ValueError("Source exceeds per-document limit")
        content.decode("utf-8")
        dest = target / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)
        records.append({"path": name, "sha256": hashlib.sha256(content).hexdigest()})
    (target / "manifest.json").write_text(
        json.dumps({"revision": revision, "files": records})
    )
    return revision


def run_worker(source, probe=False):
    # No host-writable mount. Results leave through a size-bounded stdout envelope.
    with tempfile.TemporaryDirectory(prefix="alexandria-graph-run-") as tmp:
        cidfile = Path(tmp) / "cid"
        runtime = Path(tmp) / "runtime"
        runtime.mkdir(mode=0o755)
        for name in ("worker.py", "graph.py"):
            (runtime / name).write_bytes((PACKAGE_DIR / name).read_bytes())
            (runtime / name).chmod(0o644)
        args = [
            "docker",
            "run",
            "--rm",
            "--cidfile",
            str(cidfile),
            "--network",
            "none",
            "--read-only",
            "--user",
            "65534:65534",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--memory",
            "256m",
            "--cpus",
            "1",
            "--pids-limit",
            "32",
            "--ulimit",
            "fsize=33554432:33554432",  # Match the existing 32 MiB output mount.
            "--tmpfs",
            "/output:rw,noexec,nosuid,size=32m,mode=1777",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=8m,mode=1777",
            "--mount",
            f"type=bind,src={source},dst=/source,readonly",
            "--mount",
            f"type=bind,src={runtime},dst=/worker,readonly",
            "--entrypoint",
            "python",
            IMAGE,
            "-B",
            "/worker/worker.py",
        ]
        if probe:
            args.append("--probe")
        started = time.monotonic()
        process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ, "stdout")
        selector.register(process.stderr, selectors.EVENT_READ, "stderr")
        output = bytearray()
        total = 0
        try:
            while selector.get_map():
                if time.monotonic() - started > 120:
                    raise RuntimeError("Worker exceeded time limit")
                for key, _ in selector.select(timeout=0.1):
                    chunk = key.fileobj.read1(65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    total += len(chunk)
                    if total > MAX_BYTES:
                        raise RuntimeError("Worker exceeded output limit")
                    if key.data == "stdout":
                        output.extend(chunk)
            if process.wait(timeout=5):
                raise RuntimeError("Worker failed; private stderr withheld")
            return json.loads(output)
        finally:
            selector.close()
            if cidfile.exists():
                cid = cidfile.read_text().strip()
                if len(cid) == 64 and all(c in "0123456789abcdef" for c in cid):
                    subprocess.run(
                        ["docker", "rm", "-f", cid],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        check=False,
                    )
            if process.poll() is None:
                process.kill()
            process.wait()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--path", action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--snapshot-only", action="store_true")
    parser.add_argument("--assertions", type=Path)
    args = parser.parse_args()
    args.output = no_symlinks(args.output)
    if args.output.exists():
        raise ValueError(
            "Use a new output directory; existing results are never overwritten"
        )
    with tempfile.TemporaryDirectory(prefix="alexandria-graph-source-") as tmp:
        source = Path(tmp)
        source.chmod(0o755)
        revision = snapshot(args.repo, args.revision, args.path, source)
        if args.snapshot_only:
            shutil.copytree(source, args.output)
            print(json.dumps({"revision": revision, "snapshot": str(args.output)}))
            return
        if args.assertions:
            if (
                args.assertions.is_symlink()
                or args.assertions.stat().st_size > 1024 * 1024
            ):
                raise ValueError("Unsafe or oversized assertions input")
            (source / "assertions.json").write_bytes(args.assertions.read_bytes())
        before = {
            str(p.relative_to(source)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in source.rglob("*")
            if p.is_file()
        }
        result = run_worker(source, args.probe)
        after = {
            str(p.relative_to(source)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in source.rglob("*")
            if p.is_file()
        }
        if before != after:
            raise RuntimeError("Source preservation check failed")
        if args.probe:
            expected = {
                "non_root",
                "source_write_blocked",
                "source_delete_blocked",
                "root_write_blocked",
                "network_blocked",
                "docker_socket_absent",
                "host_home_absent",
                "no_api_credentials",
                "no_new_privileges",
                "capabilities_dropped",
            }
            if set(result) != expected or not all(
                value is True for value in result.values()
            ):
                raise RuntimeError("Security probe failed")
            payloads = {"security-receipt.json": json.dumps(result, indent=2).encode()}
        else:
            if set(result) != {"graph.sqlite", "graph.jsonl", "receipt.json"}:
                raise ValueError("Unexpected output artifacts")
            payloads = {
                name: base64.b64decode(data, validate=True)
                for name, data in result.items()
            }
            if sum(map(len, payloads.values())) > 32 * 1024 * 1024:
                raise ValueError("Decoded outputs exceed budget")
        args.output.mkdir(parents=True, exist_ok=False)
        for name, data in payloads.items():
            (args.output / name).write_bytes(data)
        print(
            json.dumps(
                {
                    "revision": revision,
                    "source_preserved": True,
                    "files": len(args.path),
                    "output": str(args.output),
                }
            )
        )


if __name__ == "__main__":
    main()
