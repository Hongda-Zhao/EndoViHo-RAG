"""Common offline MLX rehearsal adapter; never issues trusted benchmark authority.

The production HTTP provider accepts only its production ContextPack/prompt. This
small pipe adapter uses the experiment's existing evidence and answer contracts,
with one scrubbed, network-denied worker and one fixed policy for all conditions.
"""

from __future__ import annotations

import hashlib
import json
import os
import selectors
import subprocess
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from eve_relation_rag.experiments.rag_value_ablation.answer_validation import (
    AnswerValidationIssue,
    AnswerValidationReport,
    validate_answer_output,
)
from eve_relation_rag.experiments.rag_value_ablation.contracts import (
    EvaluationAnswer,
    EvaluationEvidencePack,
    EvidenceCitation,
    GenerationIdentity,
    MechanicalValidation,
    RawContextSegment,
    build_evidence_pack,
    build_generation_identity,
    model_visible_evidence,
)
from eve_relation_rag.experiments.rag_value_ablation.prompting import (
    SYSTEM_INSTRUCTION,
    build_prompt_policy,
    render_user_payload,
)
from eve_relation_rag.experiments.rag_value_ablation.source_report_queries import SourceReportResult
from eve_relation_rag.experiments.rag_value_ablation.structured_evidence import (
    StructuredEvidenceGroup,
)
from eve_relation_rag.generation.policy import (
    LocalModelPolicyManifest,
    inventory_model_artifacts,
)
from eve_relation_rag.literature.hashing import canonical_json_bytes, canonical_json_sha256
from eve_relation_rag.retrieval.structured.results import QuerySuccess

MODEL_ID = "mlx-community/Qwen3-4B-Instruct-2507-4bit"
MODEL_REVISION = "50d427756c6b1b2fe0c0a10f67fbda1fc8e82c1b"
INPUT_LIMIT = 24576
OUTPUT_LIMIT = 8192
CONTEXT_LIMIT = 32768
OUTPUT_BYTE_LIMIT = 32768
TIMEOUT_SECONDS = 300
TOKENIZER_KEY = "tokenizer:rag-value:qwen3-4b-instruct-2507"


class LocalGenerationError(RuntimeError):
    """Sanitized runtime failure; no retry, fallback, or partial answer acceptance."""


class LocalGenerationTimeout(LocalGenerationError):
    """One exchange exceeded the fixed budget; its request is never retried."""

    def __init__(self, operation: str) -> None:
        self.operation = operation
        super().__init__(f"offline {operation} exceeded the fixed 300-second budget")


class ContextOverflow(LocalGenerationError):
    def __init__(self, input_token_count: int) -> None:
        self.input_token_count = input_token_count
        super().__init__("input exceeds 24576 tokens; no material was truncated")


@dataclass(frozen=True, slots=True)
class LocalGenerationConfig:
    model_root: Path
    model_policy_path: Path
    model_policy_file_sha256: str
    python_executable: Path
    worker_script: Path


def verify_generation_assets(config: LocalGenerationConfig) -> LocalModelPolicyManifest:
    """Reuse the production complete-file verifier, without production settings."""
    raw = config.model_policy_path.read_bytes()
    if (
        config.model_policy_path.is_symlink()
        or hashlib.sha256(raw).hexdigest() != config.model_policy_file_sha256
    ):
        raise LocalGenerationError("model policy file differs from its pinned identity")
    manifest = LocalModelPolicyManifest.model_validate_json(raw)
    if (
        manifest.model_revision != MODEL_REVISION
        or manifest.repository_uri != f"https://huggingface.co/{MODEL_ID}"
    ):
        raise LocalGenerationError("model differs from the selected experiment revision")
    observed = inventory_model_artifacts(
        config.model_root,
        relative_paths=tuple(str(item.relative_path) for item in manifest.artifacts),
    )
    if observed != manifest.artifacts:
        raise LocalGenerationError("model files differ from the pinned artifact manifest")
    return manifest


def generation_identity(manifest: LocalModelPolicyManifest) -> GenerationIdentity:
    policy = build_prompt_policy()
    # A verified file inventory alone is not the production runtime attestation.
    return build_generation_identity(
        provider_key="provider:rag-value:offline-mlx-rehearsal-v1",
        provider_kind="unverified",
        model_id=MODEL_ID,
        exact_revision=MODEL_REVISION,
        model_artifact_manifest_sha256=canonical_json_sha256(manifest.artifacts),
        tokenizer_id=MODEL_ID,
        tokenizer_revision=MODEL_REVISION,
        tokenizer_artifact_manifest_sha256=canonical_json_sha256(
            tuple(
                item
                for item in manifest.artifacts
                if str(item.relative_path)
                in {
                    "added_tokens.json",
                    "chat_template.jinja",
                    "merges.txt",
                    "vocab.json",
                    "special_tokens_map.json",
                    "tokenizer.json",
                    "tokenizer_config.json",
                }
            )
        ),
        system_instruction_sha256=policy.system_instruction_sha256,
        request_template_sha256=policy.request_template_sha256,
        output_schema_sha256=policy.output_schema_sha256,
        temperature=0,
        max_output_tokens=OUTPUT_LIMIT,
        max_output_bytes=OUTPUT_BYTE_LIMIT,
        context_limit_tokens=CONTEXT_LIMIT,
        timeout_seconds=TIMEOUT_SECONDS,
        seed=0,
        retry_count=0,
        request_concurrency=1,
        tools_enabled=False,
        web_enabled=False,
        conversation_memory_enabled=False,
    )


@dataclass(frozen=True, slots=True)
class LocalGenerationResult:
    evidence: EvaluationEvidencePack
    answer: EvaluationAnswer | None
    mechanical_validation: MechanicalValidation | None
    raw_answer: str
    status: Literal[
        "completed", "invalid_answer", "mechanical_failure", "evidence_failure", "output_limit"
    ]
    input_tokens: int
    output_tokens: int
    latency_ns: int
    peak_memory_bytes: int
    finish_reason: str
    validation_report: AnswerValidationReport


class OfflineMlxGenerationProvider:
    """Serial, pipe-only worker; model weights persist but each request has a fresh KV cache."""

    def __init__(self, config: LocalGenerationConfig) -> None:
        self._config = config
        self._manifest = verify_generation_assets(config)
        self.identity = generation_identity(self._manifest)
        self._process: subprocess.Popen[bytes] | None = None
        self._sequence = 0
        self.last_exchange_receipt: dict[str, Any] | None = None
        self.runtime = {
            "worker_sha256": hashlib.sha256(config.worker_script.read_bytes()).hexdigest(),
            "python_executable_sha256": _digest(config.python_executable),
            "network_policy": "macos-sandbox-deny-network",
            "trust_level": "untrusted_rehearsal",
            "implementation_files_sha256": {
                name: _digest(Path(__file__).parent / name)
                for name in (
                    "local_generation.py", "contracts.py", "answer_schema.py",
                    "answer_validation.py", "metrics.py", "prompting.py",
                    "structured_evidence.py", "association_projection.py", "complete_sets.py",
                    "systems.py",
                    "source_reports.py", "source_report_queries.py",
                )
            },
        }

    def __enter__(self) -> OfflineMlxGenerationProvider:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def close(self) -> None:
        process, self._process = self._process, None
        self._sequence = 0
        self.last_exchange_receipt = None
        if process is not None:
            process.kill() if process.poll() is None else None
            process.communicate()

    def _start(self) -> subprocess.Popen[bytes]:
        if self._process is None:
            if (
                hashlib.sha256(self._config.worker_script.read_bytes()).hexdigest()
                != (self.runtime["worker_sha256"])
                or _digest(self._config.python_executable)
                != self.runtime["python_executable_sha256"]
            ):
                raise LocalGenerationError("worker or interpreter changed before startup")
            self._process = subprocess.Popen(
                [
                    "/usr/bin/sandbox-exec",
                    "-p",
                    "(version 1)(allow default)(deny network*)",
                    str(self._config.python_executable.absolute()),
                    "-I",
                    str(self._config.worker_script.absolute()),
                    "--model-root",
                    str(self._config.model_root.absolute()),
                    "--model-policy",
                    str(self._config.model_policy_path.absolute()),
                    "--model-policy-sha256",
                    self._config.model_policy_file_sha256,
                    "--system-sha256",
                    self.identity.system_instruction_sha256,
                    "--instruction-sha256",
                    self.identity.request_template_sha256,
                    "--schema-sha256",
                    self.identity.output_schema_sha256,
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env={
                    "PATH": "/usr/bin:/bin",
                    "HF_HUB_OFFLINE": "1",
                    "TRANSFORMERS_OFFLINE": "1",
                    "HF_HUB_DISABLE_TELEMETRY": "1",
                    "TOKENIZERS_PARALLELISM": "false",
                },
            )
        return self._process

    def _exchange(self, operation: str, evidence: EvaluationEvidencePack) -> dict[str, Any]:
        self.last_exchange_receipt = None
        self._sequence += 1
        request = {
            "protocol": "rag-value-mlx-pipe-v1",
            "sequence": self._sequence,
            "operation": operation,
            "messages": [
                {"role": "system", "content": SYSTEM_INSTRUCTION},
                {"role": "user", "content": render_user_payload(evidence).decode("utf-8")},
            ],
            "visible_evidence": canonical_json_bytes(model_visible_evidence(evidence)).decode(),
        }
        raw = canonical_json_bytes(request)
        try:
            process = self._start()
            assert process.stdin is not None and process.stdout is not None
            process.stdin.write(raw + b"\n")
            process.stdin.flush()
            response = _read_response(process.stdout.fileno(), timeout=TIMEOUT_SECONDS)
            value = json.loads(response)
            if (
                not isinstance(value, dict)
                or value.get("request_sha256") != hashlib.sha256(raw).hexdigest()
                or value.get("sequence") != self._sequence
                or value.get("status") != "ok"
            ):
                raise LocalGenerationError("worker rejected the exact request")
            for field in ("input_tokens", "context_tokens"):
                if type(value.get(field)) is not int or value[field] < 0:
                    raise LocalGenerationError("worker token accounting is invalid")
            self.last_exchange_receipt = {
                "request_sha256": value["request_sha256"],
                "response_sha256": hashlib.sha256(response).hexdigest(),
                "sequence": value["sequence"],
                "execution_attestation": value.get("execution_attestation"),
            }
            return value
        except LocalGenerationTimeout:
            self.close()
            raise LocalGenerationTimeout(operation) from None
        except Exception:
            self.close()
            raise LocalGenerationError("offline worker failed; no retry or fallback") from None

    def measure(self, evidence: EvaluationEvidencePack) -> tuple[int, int]:
        value = self._exchange("count", evidence)
        return value["input_tokens"], value["context_tokens"]

    def generate(self, evidence: EvaluationEvidencePack) -> LocalGenerationResult:
        evidence = EvaluationEvidencePack.model_validate_json(evidence.model_dump_json())
        construction = evidence.construction
        if (
            construction.model_context_limit_tokens != CONTEXT_LIMIT
            or construction.reserved_output_tokens != OUTPUT_LIMIT
            or construction.tokenizer_key != TOKENIZER_KEY
            or construction.truncated
        ):
            raise LocalGenerationError("evidence differs from the fixed generation budget")
        counts = self.measure(evidence)
        if counts != (construction.input_token_count, construction.context_token_count):
            raise LocalGenerationError("actual tokenizer counts differ from evidence")
        if counts[0] > INPUT_LIMIT:
            raise ContextOverflow(counts[0])
        if verify_generation_assets(self._config) != self._manifest:
            raise LocalGenerationError("model assets changed after construction")
        value = self._exchange("generate", evidence)
        if (value["input_tokens"], value["context_tokens"]) != counts:
            raise LocalGenerationError("tokenizer changed between counting and generation")
        for key in ("output_tokens", "latency_ns", "peak_memory_bytes"):
            if type(value.get(key)) is not int or value[key] < 0:
                raise LocalGenerationError("generation telemetry is invalid")
        raw_answer = value.get("answer")
        finish = value.get("finish_reason")
        if not isinstance(raw_answer, str) or finish not in {"stop", "length", "byte_limit"}:
            raise LocalGenerationError("generation response is malformed")
        status: Literal[
            "completed", "invalid_answer", "mechanical_failure", "evidence_failure", "output_limit"
        ]
        answer, validation = None, None
        report = AnswerValidationReport()
        if (
            finish != "stop"
            or value["output_tokens"] > OUTPUT_LIMIT
            or len(raw_answer.encode("utf-8")) > OUTPUT_BYTE_LIMIT
        ):
            status = "output_limit"
            report = AnswerValidationReport(issues=(
                AnswerValidationIssue(stage="output_limit", code="output_limit_exceeded"),
            ))
        else:
            checked = validate_answer_output(raw_answer, evidence)
            answer, validation, report = checked.answer, checked.mechanical, checked.report
            if answer is None:
                status = "invalid_answer"
            elif validation is not None and not validation.passed:
                status = "mechanical_failure"
            elif report.structured_evidence_status == "failed":
                status = "evidence_failure"
            else:
                status = "completed"
        return LocalGenerationResult(
            evidence=evidence,
            answer=answer,
            mechanical_validation=validation,
            raw_answer=raw_answer,
            status=status,
            input_tokens=counts[0],
            output_tokens=value["output_tokens"],
            latency_ns=value["latency_ns"],
            peak_memory_bytes=value["peak_memory_bytes"],
            finish_reason=finish,
            validation_report=report,
        )


def build_measured_evidence(
    provider: OfflineMlxGenerationProvider,
    *,
    question_id: str,
    question_text: str,
    policy_sha256: str,
    structured_success: QuerySuccess | None = None,
    structured_groups: Sequence[StructuredEvidenceGroup] = (),
    source_report_groups: Sequence[SourceReportResult] = (),
    citations: Sequence[EvidenceCitation] = (),
    raw_context_segments: Sequence[RawContextSegment] = (),
    production_context_pack_sha256: str | None = None,
    oracle_entry_sha256: str | None = None,
) -> EvaluationEvidencePack:
    """Count the exact shared chat template, then seal; overflows produce no partial pack."""
    values: dict[str, Any] = dict(
        question_id=question_id,
        question_text=question_text,
        policy_sha256=policy_sha256,
        tokenizer_key=TOKENIZER_KEY,
        model_context_limit_tokens=CONTEXT_LIMIT,
        reserved_output_tokens=OUTPUT_LIMIT,
        structured_success=structured_success,
        structured_groups=structured_groups,
        source_report_groups=source_report_groups,
        citations=citations,
        raw_context_segments=raw_context_segments,
        production_context_pack_sha256=production_context_pack_sha256,
        oracle_entry_sha256=oracle_entry_sha256,
    )
    provisional = build_evidence_pack(**values, input_token_count=0, context_token_count=0)
    input_tokens, context_tokens = provider.measure(provisional)
    if input_tokens > INPUT_LIMIT:
        raise ContextOverflow(input_tokens)
    return build_evidence_pack(
        **values,
        input_token_count=input_tokens,
        context_token_count=context_tokens,
    )


def _digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _read_response(fd: int, *, timeout: float) -> bytes:
    deadline = time.monotonic() + timeout
    buffer = bytearray()
    with selectors.DefaultSelector() as selector:
        selector.register(fd, selectors.EVENT_READ)
        while len(buffer) <= 1_048_576:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not selector.select(remaining):
                raise LocalGenerationTimeout("receive")
            chunk = os.read(fd, 65536)
            if not chunk:
                raise LocalGenerationError("offline worker closed its response pipe")
            buffer.extend(chunk)
            if b"\n" in chunk:
                line, trailing = bytes(buffer).split(b"\n", 1)
                if trailing:
                    raise LocalGenerationError("worker emitted unsolicited output")
                return line
    raise LocalGenerationError("worker response exceeded its byte budget")
