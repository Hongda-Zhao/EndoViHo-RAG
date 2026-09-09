#!/usr/bin/env python3
"""Exercise the common real local generator on one explicitly synthetic S0/S1/S2/S3/S5/S6 case."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

from eve_relation_rag.experiments.rag_value_ablation.local_generation import (
    LocalGenerationConfig,
    OfflineMlxGenerationProvider,
)
from eve_relation_rag.experiments.rag_value_ablation.runner import (
    execute_synthetic_harness,
    generate_prepared_rehearsal,
)
from eve_relation_rag.experiments.rag_value_ablation.systems import LLM_SYSTEM_KEYS
from eve_relation_rag.hybrid.contracts import canonical_model_json, canonical_model_sha256


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-root", type=Path, required=True)
    parser.add_argument("--model-policy", type=Path, required=True)
    parser.add_argument("--model-policy-sha256", required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(exist_ok=False)
    config = LocalGenerationConfig(
        model_root=args.model_root,
        model_policy_path=args.model_policy,
        model_policy_file_sha256=args.model_policy_sha256,
        python_executable=args.python,
        worker_script=Path(__file__).with_name("rag_value_mlx_worker.py"),
    )
    fixture = execute_synthetic_harness()
    artifacts = {
        a.system_key: a for a in fixture.artifacts if a.question_id == "synthetic-hybrid-001"
    }
    failures = []
    with OfflineMlxGenerationProvider(config) as provider:
        for system in LLM_SYSTEM_KEYS:
            evidence = artifacts[system].evidence
            if evidence is None:
                raise ValueError("synthetic case lacks evidence")
            base = {
                "artifact_kind": "real_local_model_on_synthetic_evidence",
                "benchmark_executed": False,
                "evidence_origin": "tests_only",
                "system_key": system,
                "scientific_score": None,
            }
            try:
                result = {**base, **generate_prepared_rehearsal(provider, system, evidence)}
            except Exception:
                result = {**base, "status": "runtime_failed", "trust_level": "untrusted_rehearsal"}
            if result["status"] != "completed":
                failures.append(system)
            result["record_sha256"] = canonical_model_sha256(result)
            with (args.output / f"{system}.json").open("x") as handle:
                handle.write(canonical_model_json(result) + "\n")
            print(
                f"{system}: {result['status']} (synthetic evidence; no scientific score)",
                flush=True,
            )
    summary = {
        "artifact_kind": "real_local_model_on_synthetic_evidence",
        "benchmark_executed": False,
        "systems": LLM_SYSTEM_KEYS,
        "failed_systems": failures,
        "scientific_score": None,
        "records": {
            system: hashlib.sha256((args.output / f"{system}.json").read_bytes()).hexdigest()
            for system in LLM_SYSTEM_KEYS
        },
    }
    (args.output / "summary.json").write_text(canonical_model_json(summary) + "\n")
    return 2 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
