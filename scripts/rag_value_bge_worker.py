#!/usr/bin/env python3
"""One sandboxed query through the original pinned BGE provider; never writes embeddings."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import socket
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--manifest-sha256", required=True)
    args = parser.parse_args()
    if any(os.environ.get(key) != "1" for key in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE")):
        raise ValueError("offline environment required")
    for host in ("127.0.0.1", "192.0.2.1"):
        with socket.socket() as probe:
            probe.settimeout(0.2)
            try:
                probe.connect((host, 9))
            except PermissionError:
                continue
            except OSError:
                pass
            raise ValueError("OS network denial not observed")
    raw = sys.stdin.buffer.read(32769)
    request = json.loads(raw)
    if len(raw) > 32768 or set(request) != {"question"}:
        raise ValueError("invalid query envelope")
    with contextlib.redirect_stdout(sys.stderr):
        from eve_relation_rag.literature.local_bge import LocalBgeProvider
        from eve_relation_rag.planning.scope_policy import contains_forbidden_topic

        if contains_forbidden_topic(request["question"]):
            raise ValueError("scope refusal")
        provider = LocalBgeProvider(
            args.model_root,
            artifact_manifest_path=args.manifest,
            approved_artifact_manifest_sha256=args.manifest_sha256,
        )
        vector = provider.embed_query(request["question"])
    print(json.dumps({"request_sha256": hashlib.sha256(raw).hexdigest(), "vector": vector}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
