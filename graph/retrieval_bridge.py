"""Trusted newline-JSON bridge for Pi tools; no provider keys or model loop."""

import json
import sys

from local_mcp import Client
from retrieval import Broker


def receive():
    line = sys.stdin.buffer.readline(8193)
    if not line:
        return None
    if len(line) > 8192 or not line.endswith(b"\n"):
        raise ValueError("Request limit exceeded")
    return json.loads(line)


def emit(value):
    print(json.dumps(value), flush=True)


def main():
    # Configuration comes from the trusted parent before any model tool requests.
    config = receive()
    client = Client(config["data"])
    try:
        broker = Broker(client, config["revision"], config["paths"], max_calls=20)
        emit({"ready": True})
        while (request := receive()) is not None:
            try:
                if set(request) != {"tool", "arguments"}:
                    raise ValueError("Unknown fields")
                emit({"result": broker.call(request["tool"], request["arguments"])})
            except (ValueError, TypeError, KeyError):
                emit({"error": "Retrieval request rejected"})
    finally:
        client.close()


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001 -- redact private backend error details at process boundary
        raise SystemExit("Retrieval bridge failed; private details withheld.") from None
