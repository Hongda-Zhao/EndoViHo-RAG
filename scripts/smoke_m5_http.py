"""HTTP assertions for the isolated Milestone 5 container smoke gate."""

from __future__ import annotations

import argparse
import json
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def _get(url: str) -> tuple[int, bytes]:
    with urlopen(url, timeout=10) as response:
        return response.status, response.read()


def _post(url: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    request = Request(
        url,
        data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        headers={"accept": "application/json", "content-type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=20) as response:
            return response.status, json.loads(response.read())
    except HTTPError as error:
        with error:
            return error.code, json.loads(error.read())


def _zero_execution(payload: dict[str, Any]) -> bool:
    return payload["execution"] == {
        "structured_retrieval_executed": False,
        "literature_retrieval_executed": False,
        "generation_executed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-base", required=True)
    parser.add_argument("--demo-base", required=True)
    args = parser.parse_args()

    api_status, api_body = _get(f"{args.api_base}/health")
    api_health = json.loads(api_body)
    if api_status != 200 or api_health != {
        "status": "ok",
        "service": "EVE Relation RAG",
        "version": "V0",
    }:
        raise RuntimeError("API liveness response did not match the V0 contract")

    demo_status, demo_body = _get(f"{args.demo_base}/_stcore/health")
    if demo_status != 200 or demo_body != b"ok":
        raise RuntimeError("Streamlit health response did not match the expected contract")

    unsupported_status, unsupported = _post(
        f"{args.api_base}/v0/query",
        {"question": "Which host lineage has the highest EVE prevalence?"},
    )
    if (
        unsupported_status != 422
        or unsupported.get("code") != "unsupported_request"
        or unsupported.get("route") != "unsupported"
        or not _zero_execution(unsupported)
    ):
        raise RuntimeError("unsupported request did not fail closed before execution")

    hybrid_status, hybrid = _post(
        f"{args.api_base}/v0/query",
        {
            "release_key": "release:endoviho-rag:v0:20260826:001",
            "corpus_release_key": "corpus:endoviho-rag:v0:20260828:001",
            "question": (
                "Count distinct included loci in this release. and explain the literature "
                "limitations"
            ),
            "literature_top_k": 8,
        },
    )
    if (
        hybrid_status != 409
        or hybrid.get("code") != "hybrid_binding_unavailable"
        or hybrid.get("route") != "hybrid"
        or not _zero_execution(hybrid)
    ):
        raise RuntimeError("unbound hybrid request did not fail closed before execution")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
