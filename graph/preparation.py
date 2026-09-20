"""Offline, explicit-scope preparation for the bounded preservation pipeline."""

import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

from graph import no_symlinks, read_bytes
from harness import git, snapshot

HERE = Path(__file__).resolve().parent
MODEL = "gpt-5.6-sol"
MAX_DOCUMENTS = 3
MAX_DOCUMENT_BYTES = 16000
SKILL_COMMIT = "9862685f575c65a8247f90369951df1b3416e3d6"
SKILL_SHA256 = "e8269e236bed06ed0fe4824c274112e54950b0cb46b0bafe5e1576ef7c9f93d5"
SKILL_ROOT = HERE / "fixtures"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def _paths(paths):
    if not 1 <= len(paths) <= MAX_DOCUMENTS or len(set(paths)) != len(paths):
        raise ValueError("Select one to three distinct documents")
    for name in paths:
        if (
            not isinstance(name, str)
            or not name
            or name.startswith("/")
            or any(part in ("", ".", "..") for part in name.split("/"))
            or any(c in name for c in "\\,\n\r\t\x00")
            or Path(name).suffix not in (".md", ".txt")
        ):
            raise ValueError("Only safe explicit Markdown/text paths are allowed")
    return sorted(paths)


def _skill():
    skill = read_bytes(SKILL_ROOT / "humanizer-SKILL.md", 100000)
    provenance = read_bytes(SKILL_ROOT / "humanizer-source.json", 10000)
    metadata = json.loads(provenance)
    if (
        digest(skill) != SKILL_SHA256
        or metadata.get("sha256") != SKILL_SHA256
        or metadata.get("commit") != SKILL_COMMIT
        or metadata.get("url")
        != f"https://raw.githubusercontent.com/blader/humanizer/{SKILL_COMMIT}/SKILL.md"
    ):
        raise ValueError("Pinned Humanizer provenance mismatch")
    return skill, provenance


def preflight(repo, revision, paths, model, approved_paths, output):
    """Read committed inputs only. Caller asserts existing exact disclosure approval.

    This assertion is not an approval registry and grants no new authorization.
    No output, temporary snapshot, network request or credential read occurs.
    """
    paths = _paths(paths)
    if model != MODEL:
        raise ValueError("An explicit existing approved model is required")
    if _paths(approved_paths) != paths:
        raise ValueError("Disclosure scope must exactly match selected documents")
    repo = no_symlinks(repo)
    output = no_symlinks(output)
    if output.exists() or not output.parent.is_dir():
        raise ValueError("Output must be fresh with an existing parent directory")
    if not revision or revision.startswith("-"):
        raise ValueError("Explicit committed revision required")
    commit = git(repo, "rev-parse", "--verify", revision + "^{commit}").decode().strip()
    records = []
    for name in paths:
        # Match snapshot's regular committed-file boundary before reading a blob.
        entry = git(repo, "ls-tree", commit, "--", name).decode()
        if not entry.startswith(("100644 blob ", "100755 blob ")):
            raise ValueError("Source must be a committed regular file")
        size = int(git(repo, "cat-file", "-s", commit + ":" + name))
        if size > MAX_DOCUMENT_BYTES:
            raise ValueError("Document exceeds 16000-byte limit")
        data = git(repo, "show", commit + ":" + name)
        data.decode("utf-8")
        if not data.strip() or len(data) > MAX_DOCUMENT_BYTES:
            raise ValueError("Document must be nonempty and within the size limit")
        records.append({"path": name, "sha256": digest(data), "bytes": len(data)})
    _skill()
    required = [
        HERE / "pi-trial/preservation-trial.mjs",
        HERE / "pi-trial/preservation.mjs",
        HERE / "render_preservation.py",
        HERE / "graph.py",
        HERE / "pi-trial/package-lock.json",
    ]
    missing = [str(p) for p in required if not p.is_file()]
    if missing:
        raise ValueError("Missing required pipeline files: " + ", ".join(missing))
    return {
        "status": "offline-preflight-passed",
        "revision": commit,
        "files": records,
        "output": str(output),
        "model": model,
        "approved_external_paths": paths,
        "authorization_basis": "Operator assertion of existing exact scope; not new approval",
        "limits": {
            "documents": MAX_DOCUMENTS,
            "bytes_per_document": MAX_DOCUMENT_BYTES,
        },
        "total_source_bytes": sum(record["bytes"] for record in records),
        "humanizer_sha256": SKILL_SHA256,
        "execution_prerequisites": {
            "node_available": shutil.which("node") is not None,
            "pi_dependencies_present": all(
                (
                    HERE
                    / "pi-trial/node_modules/@earendil-works"
                    / package
                    / "package.json"
                ).is_file()
                for package in ("pi-agent-core", "pi-ai")
            ),
            "gateway": "Not contacted; execution must check separately",
            "credentials": "Not read",
        },
    }


def prepare(repo, revision, paths, model, approved_paths, output):
    """Prepare a fresh immutable-source bundle. Never runs models or active services."""
    plan = preflight(repo, revision, paths, model, approved_paths, output)
    output = Path(plan["output"])
    output.mkdir(mode=0o700, exist_ok=False)
    try:
        source = output / "source"
        source.mkdir(mode=0o700)
        snapshot(repo, plan["revision"], plan["approved_external_paths"], source)
        for record in plan["files"]:
            if digest((source / record["path"]).read_bytes()) != record["sha256"]:
                raise ValueError("Prepared snapshot does not match preflight")
        skill, provenance = _skill()
        for name, data in (
            ("humanizer-SKILL.md", skill),
            ("humanizer-source.json", provenance),
        ):
            with (output / name).open("xb") as stream:
                stream.write(data)
        # Deliberately non-executable: task 2 must require a separate explicit run.
        config = {
            "model": model,
            "execute": False,
            "approved_external_paths": plan["approved_external_paths"],
        }
        _write(output / "runner-config.json", config)
        plan["status"] = "prepared-not-executed"
        _write(output / "preparation.json", plan)
        return plan
    except Exception as error:
        _write(
            output / "preparation-failure.json",
            {
                "status": "incomplete-preparation",
                "category": type(error).__name__,
                "recovery": "Retain this directory and prepare a fresh output after correcting input",
            },
        )
        raise


def _write(path, value):
    with path.open("x") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--path", action="append", required=True)
    parser.add_argument(
        "--approved-path",
        action="append",
        required=True,
        help="Exact scope already authorized; this does not create approval",
    )
    parser.add_argument("--model", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--prepare",
        action="store_true",
        help="Write the prepared bundle; default is read-only preflight",
    )
    args = parser.parse_args()
    operation = prepare if args.prepare else preflight
    try:
        result = operation(
            args.repo,
            args.revision,
            args.path,
            args.model,
            args.approved_path,
            args.output,
        )
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(
            1,
            f"Preparation refused ({type(error).__name__}); inspect scope, committed inputs, limits and prerequisites.\n",
        )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
