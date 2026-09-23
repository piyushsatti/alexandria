#!/usr/bin/env python3
"""Run Alexandria's semantic, authority and provenance review gates together."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from pi.alexandria.graph.review_bundle import (
    summarize_review_bundle,
    validate_review_bundle,
)


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quality-gate", type=Path, required=True)
    parser.add_argument("--quality-review", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--authority-review", type=Path, required=True)
    parser.add_argument("--provenance-review", type=Path, required=True)
    parser.add_argument(
        "--full",
        action="store_true",
        help="include validator findings; default output is safe for CI logs",
    )
    args = parser.parse_args()
    try:
        result = validate_review_bundle(
            quality_gate=_read(args.quality_gate),
            quality_review=_read(args.quality_review),
            manifest=_read(args.manifest),
            authority_review=_read(args.authority_review),
            provenance_review=_read(args.provenance_review),
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        parser.error(str(error))
    output = result if args.full else summarize_review_bundle(result)
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0 if result["release_ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
