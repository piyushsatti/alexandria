#!/usr/bin/env python3
"""Validate an owner disposition receipt against a deterministic quality gate."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pi.alexandria.graph.quality_gate import validate_dispositions


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gate", type=Path, required=True)
    parser.add_argument("--review", type=Path, required=True)
    args = parser.parse_args()
    try:
        gate = json.loads(args.gate.read_text(encoding="utf-8"))
        review = json.loads(args.review.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        parser.error(str(error))
    result = validate_dispositions(gate, review)
    print(json.dumps(result, indent=2, sort_keys=True))
    # A fully recorded held review is structurally complete but must still
    # block promotion, so only an all-resolved review exits successfully.
    return 0 if result["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
