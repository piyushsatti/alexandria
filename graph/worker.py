"""Fixed worker entrypoint. No model output is ever executed."""

import base64
import json
import os
import socket
import sys
from pathlib import Path


def blocked(operation):
    try:
        operation()
    except OSError:
        return True
    return False


if "--probe" in sys.argv:
    source = next(p for p in Path("/source").rglob("*") if p.is_file())

    def connect():
        with socket.create_connection(("1.1.1.1", 443), timeout=2):
            pass

    checks = {
        "non_root": os.getuid() != 0,
        "source_write_blocked": blocked(lambda: source.write_text("probe")),
        "source_delete_blocked": blocked(lambda: source.unlink()),
        "root_write_blocked": blocked(lambda: Path("/probe").write_text("probe")),
        "network_blocked": blocked(connect),
        "docker_socket_absent": not Path("/var/run/docker.sock").exists(),
        "host_home_absent": not Path("/Users").exists(),
        "no_api_credentials": not any(
            k.endswith(("API_KEY", "TOKEN", "SECRET")) for k in os.environ
        ),
        "no_new_privileges": "NoNewPrivs:\t1" in Path("/proc/self/status").read_text(),
        "capabilities_dropped": "CapEff:\t0000000000000000"
        in Path("/proc/self/status").read_text(),
    }
    print(json.dumps(checks))
else:
    import graph

    sys.argv = [
        "graph.py",
        "build",
        "--source",
        "/source",
        "--output",
        "/output/result",
    ]
    if Path("/source/assertions.json").exists():
        sys.argv += ["--assertions", "/source/assertions.json"]
    # Keep worker stdout exclusively for the fixed artifact envelope.
    import contextlib

    with contextlib.redirect_stdout(sys.stderr):
        graph.main()
    print(
        json.dumps(
            {
                name: base64.b64encode(
                    (Path("/output/result") / name).read_bytes()
                ).decode()
                for name in ("graph.sqlite", "graph.jsonl", "receipt.json")
            }
        )
    )
