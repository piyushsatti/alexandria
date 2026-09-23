"""Verify that a quality receipt can be independently replayed."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any

REQUIRED_FIELDS = {
    "goal_id",
    "phase",
    "status",
    "source_revision",
    "source_manifest_sha256",
    "code_revision",
    "configuration_identity",
    "model_provider",
    "model_identity",
    "expectation_revision",
    "output_manifest_sha256",
    "checks",
    "active_index_changed",
    "deployment_changed",
    "next_action",
}
PLACEHOLDER_ROUTE = ("not independently verified", "gateway alias", "alias only")


def _digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            hasher.update(chunk)
    return hasher.hexdigest()


def _relative(root: Path, value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value or "\\" in value or "\0" in value:
        raise ValueError(f"Invalid {label}")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ("", ".", "..") for part in value.split("/")):
        raise ValueError(f"Unsafe {label}")
    resolved = (root / Path(path.as_posix())).resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError(f"Unsafe {label}")
    return resolved


def verify_receipt(root: Path, receipt_name: str = "comparison.json") -> dict[str, Any]:
    """Return a pass/hold receipt without executing a provider or model."""

    root = Path(root).resolve()
    receipt_path = _relative(root, receipt_name, "receipt path")
    result: dict[str, Any] = {
        "status": "hold",
        "automatic_acceptance": False,
        "receipt": receipt_name,
        "findings": [],
    }
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        result["findings"].append({"kind": "receipt_unreadable", "reason": str(error)})
        return result
    if not isinstance(receipt, dict):
        result["findings"].append(
            {"kind": "receipt_not_object", "reason": "Receipt must be a JSON object."}
        )
        return result

    missing = sorted(REQUIRED_FIELDS - set(receipt))
    if missing:
        result["findings"].append(
            {
                "kind": "required_receipt_fields_missing",
                "fields": missing,
                "reason": "A quality decision cannot be replayed without its input identities and checks.",
            }
        )
    if receipt.get("active_index_changed") is not False:
        result["findings"].append(
            {
                "kind": "active_index_changed",
                "reason": "Quality review must not mutate the active index.",
            }
        )
    if receipt.get("deployment_changed") is not False:
        result["findings"].append(
            {
                "kind": "deployment_changed",
                "reason": "Quality review must not change deployment.",
            }
        )
    checks = receipt.get("checks")
    if not isinstance(checks, dict):
        result["findings"].append(
            {
                "kind": "checks_missing",
                "reason": "The receipt has no per-check outcome map.",
            }
        )

    provider_identity = receipt.get("provider_identity")
    if (
        not isinstance(provider_identity, dict)
        or provider_identity.get("verified") is not True
    ):
        result["findings"].append(
            {
                "kind": "provider_identity_unverified",
                "reason": "The exact provider route and model identity were not independently verified.",
            }
        )
    route = str(receipt.get("provider_route", "")).casefold()
    if any(marker in route for marker in PLACEHOLDER_ROUTE):
        result["findings"].append(
            {
                "kind": "provider_route_placeholder",
                "reason": "A local alias or unverified route cannot establish provider identity.",
            }
        )

    output_path = receipt.get("output_manifest_path")
    if not output_path:
        result["findings"].append(
            {
                "kind": "output_manifest_missing",
                "reason": "The receipt does not identify a replayable output manifest.",
            }
        )
    else:
        try:
            path = _relative(root, output_path, "output manifest path")
            observed = _digest(path)
            if observed != receipt.get("output_manifest_sha256"):
                result["findings"].append(
                    {
                        "kind": "output_manifest_hash_mismatch",
                        "expected": receipt.get("output_manifest_sha256"),
                        "observed": observed,
                    }
                )
        except (OSError, ValueError) as error:
            result["findings"].append(
                {"kind": "output_manifest_unreadable", "reason": str(error)}
            )

    script_path = receipt.get("comparison_script_path")
    if not script_path:
        result["findings"].append(
            {
                "kind": "comparison_script_missing",
                "reason": "The receipt does not identify the script that produced the comparison.",
            }
        )
    else:
        try:
            path = _relative(root, script_path, "comparison script path")
            script_hash = receipt.get("comparison_script_sha256")
            if not script_hash or _digest(path) != script_hash:
                result["findings"].append(
                    {"kind": "comparison_script_hash_mismatch", "path": script_path}
                )
        except (OSError, ValueError) as error:
            result["findings"].append(
                {"kind": "comparison_script_unreadable", "reason": str(error)}
            )

    if receipt.get("independent_comparison_pass") is not True:
        result["findings"].append(
            {
                "kind": "independent_comparison_not_proven",
                "reason": "The receipt does not contain a true independent comparison result.",
            }
        )
    if receipt.get("status") not in ("pass", "hold"):
        result["findings"].append(
            {"kind": "invalid_receipt_status", "status": receipt.get("status")}
        )
    result["status"] = "pass" if not result["findings"] else "hold"
    result["finding_count"] = len(result["findings"])
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--receipt", default="comparison.json")
    args = parser.parse_args()
    print(json.dumps(verify_receipt(args.root, args.receipt), indent=2))


if __name__ == "__main__":
    main()
