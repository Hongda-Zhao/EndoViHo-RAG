#!/usr/bin/env python3
"""Private JSONL worker for local_generation; launch only through its network sandbox."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import socket
import sys
import time
from pathlib import Path, PurePosixPath


def canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def verify(root: Path, manifest_path: Path, pinned: str) -> None:
    raw = manifest_path.read_bytes()
    if manifest_path.is_symlink() or hashlib.sha256(raw).hexdigest() != pinned:
        raise ValueError("manifest mismatch")
    manifest = json.loads(raw)
    if manifest["model_revision"] != "50d427756c6b1b2fe0c0a10f67fbda1fc8e82c1b":
        raise ValueError("revision mismatch")
    paths = set()
    if root.is_symlink() or not root.is_dir():
        raise ValueError("invalid model root")
    for item in manifest["artifacts"]:
        relative = PurePosixPath(item["relative_path"])
        if relative.is_absolute() or ".." in relative.parts or relative.as_posix() in paths:
            raise ValueError("invalid model member")
        path = root
        for component in relative.parts:
            path = path / component
            if path.is_symlink():
                raise ValueError("symlink model member")
        if not path.resolve().is_relative_to(root.resolve()) or not path.is_file():
            raise ValueError("invalid model member")
        with path.open("rb") as handle:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
        if digest != item["sha256"] or path.stat().st_size != item["byte_size"]:
            raise ValueError("model member mismatch")
        paths.add(relative.as_posix())
    if {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()} != paths:
        raise ValueError("unlisted model files")


def require_network_denied() -> None:
    if any(os.environ.get(key) != "1" for key in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE")):
        raise ValueError("offline environment required")
    # Fail closed when invoked directly outside the OS sandbox. No data is sent.
    for address in ("127.0.0.1", "192.0.2.1"):
        with socket.socket() as probe:
            probe.settimeout(0.2)
            try:
                probe.connect((address, 9))
            except PermissionError:
                continue
            except OSError:
                pass
            raise ValueError("OS egress denial was not observed")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-root", type=Path, required=True)
    parser.add_argument("--model-policy", type=Path, required=True)
    parser.add_argument("--model-policy-sha256", required=True)
    parser.add_argument("--system-sha256", required=True)
    parser.add_argument("--instruction-sha256", required=True)
    parser.add_argument("--schema-sha256", required=True)
    args = parser.parse_args()
    require_network_denied()
    verify(args.model_root, args.model_policy, args.model_policy_sha256)
    with contextlib.redirect_stdout(sys.stderr):
        import mlx.core as mx
        from mlx_lm import load, stream_generate
        from mlx_lm.sample_utils import make_sampler
        from transformers import AutoTokenizer

        tokenizer = AutoTokenizer.from_pretrained(
            str(args.model_root), local_files_only=True, trust_remote_code=False
        )
    model, generation_tokenizer = None, None
    sequence = 0
    # The full S1 bundle is transported twice in the envelope for equality
    # checking. This byte ceiling permits counting it; generation still rejects
    # more than 24576 input tokens and never truncates source material.
    while raw := sys.stdin.buffer.readline(67_108_865):
        if len(raw) > 67_108_864 or not raw.endswith(b"\n"):
            return 2
        raw = raw[:-1]
        request_sha256 = hashlib.sha256(raw).hexdigest()
        try:
            request = json.loads(raw)
            sequence += 1
            if (
                set(request)
                != {"protocol", "sequence", "operation", "messages", "visible_evidence"}
                or request["protocol"] != "rag-value-mlx-pipe-v1"
                or request["sequence"] != sequence
                or request["operation"] not in {"count", "generate"}
            ):
                raise ValueError("invalid request")
            messages = request["messages"]
            if len(messages) != 2 or [m["role"] for m in messages] != ["system", "user"]:
                raise ValueError("invalid messages")
            if any(set(m) != {"role", "content"} for m in messages):
                raise ValueError("invalid message fields")
            payload = json.loads(messages[1]["content"])
            if (
                set(payload) != {"instruction", "answer_schema", "evidence"}
                or hashlib.sha256(messages[0]["content"].encode()).hexdigest() != args.system_sha256
                or hashlib.sha256(payload["instruction"].encode()).hexdigest()
                != args.instruction_sha256
                or hashlib.sha256(canonical(payload["answer_schema"])).hexdigest()
                != args.schema_sha256
                or canonical(payload["evidence"]).decode() != request["visible_evidence"]
            ):
                raise ValueError("common prompt mismatch")
            tokens = tokenizer.apply_chat_template(
                messages, tokenize=True, add_generation_prompt=True, return_dict=False
            )
            if not isinstance(tokens, list) or any(type(token) is not int for token in tokens):
                raise ValueError("chat template did not return an exact token ID list")
            context_tokens = len(
                tokenizer.encode(request["visible_evidence"], add_special_tokens=False)
            )
            result = {
                "status": "ok",
                "sequence": sequence,
                "request_sha256": request_sha256,
                "input_tokens": len(tokens),
                "context_tokens": context_tokens,
                "execution_attestation": {
                    "schema_version": "rag-value-local-exchange-v1",
                    "operation": request["operation"],
                    "worker_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                    "model_policy_file_sha256": args.model_policy_sha256,
                    "network_denial_probes_passed": True,
                    "fresh_conversation": True,
                    "seed": 0,
                    "temperature": 0,
                },
            }
            if request["operation"] == "generate":
                if len(tokens) > 24576:
                    raise ValueError("context overflow")
                verify(args.model_root, args.model_policy, args.model_policy_sha256)
                started = time.perf_counter_ns()
                with contextlib.redirect_stdout(sys.stderr):
                    if model is None:
                        model, generation_tokenizer = load(
                            str(args.model_root), tokenizer_config={"local_files_only": True}
                        )
                    if (
                        generation_tokenizer.apply_chat_template(
                            messages, tokenize=True, add_generation_prompt=True, return_dict=False
                        )
                        != tokens
                    ):
                        raise ValueError("generation tokenizer differs")
                    mx.reset_peak_memory()
                    mx.random.seed(0)
                    answer, last, byte_limit = "", None, False
                    # No prompt_cache or conversation state is passed between calls.
                    for chunk in stream_generate(
                        model,
                        generation_tokenizer,
                        tokens,
                        max_tokens=8192,
                        max_kv_size=32768,
                        sampler=make_sampler(temp=0.0),
                    ):
                        last = chunk
                        answer += chunk.text
                        if len(answer.encode("utf-8")) > 32768:
                            byte_limit = True
                            break
                    if last is None:
                        raise ValueError("empty generation stream")
                    result.update(
                        answer=answer,
                        output_tokens=last.generation_tokens,
                        finish_reason="byte_limit" if byte_limit else last.finish_reason,
                        latency_ns=time.perf_counter_ns() - started,
                        peak_memory_bytes=mx.get_peak_memory(),
                    )
            sys.stdout.buffer.write(canonical(result) + b"\n")
            sys.stdout.buffer.flush()
        except Exception:
            sys.stdout.buffer.write(
                canonical(
                    {
                        "status": "failed",
                        "sequence": sequence,
                        "request_sha256": request_sha256,
                    }
                )
                + b"\n"
            )
            sys.stdout.buffer.flush()
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
