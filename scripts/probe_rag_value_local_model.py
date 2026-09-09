#!/usr/bin/env python3
"""Non-scientific MLX hardware probe; run in a network-denied process, not a benchmark."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if os.environ.get("HF_HUB_OFFLINE") != "1":
        raise ValueError("explicit offline environment is required")
    root = Path(__file__).resolve().parents[1]
    model_root = root / ".artifacts/v0_activation/model/Qwen3-4B-Instruct-2507-4bit"
    policy = json.loads(
        (
            root / ".artifacts/v0_activation/manifests/v0_local_model_policy_manifest.v2.json"
        ).read_bytes()
    )
    revision = "50d427756c6b1b2fe0c0a10f67fbda1fc8e82c1b"
    if policy["model_revision"] != revision:
        raise ValueError("local revision differs from the selected model")
    for item in policy["artifacts"]:
        path = model_root / item["relative_path"]
        if path.is_symlink() or not path.resolve().is_relative_to(model_root.resolve()):
            raise ValueError("model member escapes its verified root")
        with path.open("rb") as handle:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
        if digest != item["sha256"] or path.stat().st_size != item["byte_size"]:
            raise ValueError("model member does not match the frozen manifest")
    request = json.loads(args.request.read_bytes())
    # The standalone environment is already installed; no import/download fallback exists.
    import mlx.core as mx
    from mlx_lm import load, stream_generate
    from mlx_lm.sample_utils import make_sampler

    started = time.perf_counter_ns()
    model, tokenizer = load(str(model_root), tokenizer_config={"local_files_only": True})
    tokens = tokenizer.apply_chat_template(
        request["messages"],
        tokenize=True,
        add_generation_prompt=True,
    )
    if len(tokens) > 24576:
        raise ValueError("input exceeds the accepted budget; no truncation permitted")
    response = ""
    last = None
    for chunk in stream_generate(
        model,
        tokenizer,
        tokens,
        max_tokens=8192,
        max_kv_size=32768,
        sampler=make_sampler(temp=0.0),
    ):
        response += chunk.text
        last = chunk
    result = {
        "artifact_kind": "hardware_probe_only",
        "benchmark_executed": False,
        "trust_level": "untrusted_probe",
        "question_count": 1,
        "scientific_score": None,
        "model_revision": revision,
        "temperature": 0,
        "retry_count": 0,
        "context_limit": 32768,
        "input_limit": 24576,
        "output_limit": 8192,
        "prompt_policy_sha256": request["prompt_policy_sha256"],
        "request_file_sha256": hashlib.sha256(args.request.read_bytes()).hexdigest(),
        "input_tokens": len(tokens),
        "output_tokens": last.generation_tokens if last else 0,
        "finish_reason": last.finish_reason if last else None,
        "latency_including_load_ns": time.perf_counter_ns() - started,
        "mlx_peak_memory_bytes": mx.get_peak_memory(),
        "answer": response,
    }
    with args.output.open("x") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print("hardware_probe_only: completed; no scientific benchmark score")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
