#!/usr/bin/env python3
"""Validate privacy-safe owner classifications for structured provenance."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pi.alexandria.graph.provenance_review import validate_provenance_review


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review", type=Path, required=True)
    args = parser.parse_args()
    try:
        review = json.loads(args.review.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        parser.error(str(error))
    result = validate_provenance_review(review)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["release_ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
