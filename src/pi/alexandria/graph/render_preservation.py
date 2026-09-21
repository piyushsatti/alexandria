"""Render a held preservation trial; never approve, publish, or refresh retrieval."""

import argparse
import copy
import hashlib
import json
from pathlib import Path

from pi.alexandria.graph.graph import (
    make_graph,
    no_symlinks,
    read_bytes,
    read_json,
    safe_path,
)


def require(value, message):
    if not value:
        raise ValueError(message)


def paragraphs(data):
    return [
        p
        for page in data["pages"]
        for section in page["sections"]
        for p in section["paragraphs"]
    ]


def verify_bindings(before, after):
    expected = copy.deepcopy(before)
    original, edited = paragraphs(expected), paragraphs(after)
    require(len(original) == len(edited), "Paragraph structure changed")
    for left, right in zip(original, edited, strict=True):
        require(left["id"] == right["id"], "Paragraph identity or order changed")
        left["text"] = right["text"]
    require(expected == after, "Editor changed immutable candidate data")


def render(root):
    root = no_symlinks(root).resolve()
    before = read_json(root / "02-frozen.json")
    data = read_json(root / "03-humanizer.json")
    hold = read_json(root / "hold-receipt.json")
    digest = hashlib.sha256(
        json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()
    require(
        hold["status"] == "held-pending-owner-review"
        and hold["candidate_hash"] == digest,
        "Missing or mismatched held-candidate receipt",
    )
    verify_bindings(before, data)
    manifest = read_json(root / "source/manifest.json")
    require(
        read_json(root / "run-manifest.json")["source"] == manifest,
        "Run source changed",
    )
    docs = {}
    for row in manifest["files"]:
        path = safe_path(row["path"])
        raw = read_bytes(root / "source" / path, 16000)
        require(hashlib.sha256(raw).hexdigest() == row["sha256"], "Source changed")
        docs[path] = raw.decode()
    claims = {c["id"]: c for c in data["claims"]}
    notes = {a["id"]: a for a in data["annotations"]}
    assertions = []
    for c in claims.values():
        scope = c["scope"]
        require(c["path"] in docs and scope["path"] in docs, "Out-of-scope evidence")
        require(docs[c["path"]].count(c["quote"]) == 1, "Nonunique claim quote")
        require(docs[scope["path"]].count(scope["quote"]) == 1, "Nonunique scope quote")
        assertions.append(
            {
                "subject": c["subject"],
                "predicate": c["predicate"],
                "object": c["object"],
                "path": c["path"],
                "quote": c["quote"],
                "start": docs[c["path"]].index(c["quote"]),
                "status": "extracted",
                "qualification": {
                    "source_status": c["source_status"],
                    "review_state": c.get("review_state", "not_checked"),
                    "scope": {
                        **scope,
                        "start": docs[scope["path"]].index(scope["quote"]),
                    },
                    "annotations": [
                        {
                            **notes[aid],
                            "start": docs[notes[aid]["path"]].index(
                                notes[aid]["quote"]
                            ),
                        }
                        for aid in c["annotation_ids"]
                    ],
                },
                "extraction": {
                    "method": "pi-preservation-trial",
                    "model": "gpt-5.6-sol",
                    "provider": "chatgpt-via-litellm",
                    "prompt_version": "2026.09.19",
                },
            }
        )
    # Validate with the same graph contract before producing a graph-ready artifact.
    # No dynamic scripts, model commands or substitutions are executed.
    import tempfile

    with tempfile.TemporaryDirectory(prefix="alexandria-assertions-") as tmp:
        # macOS may expose its trusted temporary root through /var -> /private/var.
        # Resolve this newly created directory only; source paths stay strict.
        file = Path(tmp).resolve() / "assertions.json"
        file.write_text(json.dumps(assertions))
        make_graph(root / "source", file)
    pages = [
        "# Candidate reader pages",
        "",
        "Held for owner review; generated prose is not accepted knowledge.",
        "",
    ]
    for page in data["pages"]:
        pages += ["## " + page["title"], ""]
        for section in page["sections"]:
            pages += ["### " + section["heading"], ""]
            for p in section["paragraphs"]:
                refs = [f"[{cid}](evidence.md#{cid.lower()})" for cid in p["claim_ids"]]
                refs += [
                    f"[{aid}](annotations.md#{aid.lower()})"
                    for aid in p["annotation_ids"]
                ]
                pages += [
                    f"<!-- paragraph: {p['id']} -->",
                    p["text"] + " " + " ".join(refs),
                    "",
                ]
    evidence, annotations = ["# Claim evidence", ""], ["# Unresolved annotations", ""]
    for c in claims.values():
        evidence += [
            "## " + c["id"],
            "",
            f"**{c['source_status']}** ({c.get('review_state', 'not_checked')}): "
            f"{c['subject']} {c['predicate']} {c['object']}",
            "",
            "Scope: " + c["scope"]["text"],
            "",
            f"[Source](source/{c['path']})",
            "",
            "```text",
            c["quote"],
            "```",
            "",
        ]
    for a in notes.values():
        annotations += [
            "## " + a["id"],
            "",
            a["kind"] + ": " + a["reason"],
            "",
            a["resolution_question"],
            "",
            f"[Source](source/{a['path']})",
            "",
            "```text",
            a["quote"],
            "```",
            "",
        ]
    inventory = {r["id"]: r for r in read_json(root / "source-regions.json")}
    ledger = [
        "# Source coverage proposals",
        "",
        "Source-first inventory; model dispositions require owner review. One block may contain several obligations: represented is not a semantic completeness result.",
        "",
    ]
    for row in data["coverage"]:
        src = inventory[row["id"]]
        ledger += [
            "## " + row["id"],
            "",
            f"[Source](source/{src['path']}) · line {src['line']} · proposed disposition: **{row['disposition']}**",
            "",
            row["reason"],
            "",
            "Claims: " + (", ".join(row["claim_ids"]) or "none"),
            "Annotations: " + (", ".join(row["annotation_ids"]) or "none"),
            "",
            "Review state: **not checked by owner**",
            "",
            "```text",
            src["quote"],
            "```",
            "",
        ]
    queue = [
        "# Focused review queue",
        "",
        "Heuristic flags prioritize review; they do not approve any region. Repeated labels flag possible ambiguity, not proof that referents differ. All regions remain not checked.",
        "",
    ]
    for q in read_json(root / "review-queue.json"):
        queue += [
            "## " + q["id"],
            "",
            "Target: " + json.dumps(q["target"]),
            "Flags: "
            + (
                ", ".join(q["flags"]) or "none detected; baseline review still required"
            ),
            "Review state: " + q["review_state"],
            "",
        ]
    outputs = {
        "candidate-pages.md": "\n".join(pages),
        "evidence.md": "\n".join(evidence),
        "annotations.md": "\n".join(annotations),
        "coverage-ledger.md": "\n".join(ledger),
        "review-queue.md": "\n".join(queue),
        "assertions.json": json.dumps(assertions, indent=2) + "\n",
    }
    require(
        not any((root / name).exists() for name in outputs),
        "Preserve existing render; use a fresh attempt",
    )
    for name, text in outputs.items():
        with (root / name).open("x") as f:
            f.write(text)
    return {
        "claims": len(claims),
        "source_regions": len(inventory),
        "status": "held-pending-owner-review",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_root", type=Path)
    args = parser.parse_args()
    print(json.dumps(render(args.run_root)))
