"""One checksum-bound prompt policy shared by every LLM evaluation condition."""

from __future__ import annotations

import hashlib
from typing import Final, Self

from pydantic import Field, model_validator

from eve_relation_rag.experiments.rag_value_ablation.contracts import (
    EvaluationAnswer,
    EvaluationEvidencePack,
    GenerationIdentity,
    model_visible_evidence,
)
from eve_relation_rag.literature.contracts import Sha256, StrictFrozenSchema
from eve_relation_rag.literature.hashing import canonical_json_bytes, canonical_json_sha256

SYSTEM_INSTRUCTION: Final = (
    "Answer in English. Use only the evidence supplied in the request and do not use external "
    "knowledge. Do not invent accessions, locus keys, coordinates, counts, releases, papers, or "
    "citations. Preserve structured values exactly and do not modify a supplied structured "
    "result. Preserve assembly-source taxonomy as source taxonomy; do not describe it as an "
    "ancient or modern host assertion. Preserve every viral-lineage role, snapshot, and "
    "exact-versus-descendant scope. Do not convert Integration, Viral contig, HCVR, or any "
    "literature label into Transferred gene or Integrated virus unless the supplied evidence "
    "contains an approved relation-class assertion. Cite every literature-derived factual "
    "claim. State that evidence is insufficient and abstain when it cannot support an answer. "
    "Do not infer modern infection, prevalence, biological absence, co-divergence, or "
    "independent integration events unless the supplied evidence explicitly supports and "
    "permits the inference. Return only JSON matching the common answer schema."
)
REQUEST_INSTRUCTION: Final = (
    "Answer the exact question in the evidence envelope under the system instruction."
)


class PromptPolicyError(ValueError):
    """Raised when prompt identity or serialization differs between systems."""


class PromptPolicy(StrictFrozenSchema):
    """Exact prompt bytes and output schema used by S0/S1/S2/S3/S5/S6."""

    policy_schema_version: str = Field(pattern=r"^rag-value-prompt-policy-v1$")
    system_instruction: str = Field(min_length=1, max_length=8000)
    request_instruction: str = Field(min_length=1, max_length=2000)
    system_instruction_sha256: Sha256
    request_template_sha256: Sha256
    output_schema_sha256: Sha256
    policy_sha256: Sha256

    @model_validator(mode="after")
    def validate_policy(self) -> Self:
        if self.system_instruction != SYSTEM_INSTRUCTION:
            raise ValueError("system instruction differs from the frozen evaluation policy")
        if self.request_instruction != REQUEST_INSTRUCTION:
            raise ValueError("request instruction differs from the frozen evaluation policy")
        if self.system_instruction_sha256 != _text_sha256(self.system_instruction):
            raise ValueError("system instruction checksum does not match")
        if self.request_template_sha256 != _text_sha256(self.request_instruction):
            raise ValueError("request instruction checksum does not match")
        if self.output_schema_sha256 != canonical_json_sha256(
            EvaluationAnswer.model_json_schema()
        ):
            raise ValueError("output schema checksum does not match common answer schema")
        if self.policy_sha256 != _self_sha256(self, "policy_sha256"):
            raise ValueError("prompt policy checksum does not match")
        return self


def build_prompt_policy() -> PromptPolicy:
    """Build the sole prompt policy allowed by the initial benchmark contract."""

    payload = {
        "policy_schema_version": "rag-value-prompt-policy-v1",
        "system_instruction": SYSTEM_INSTRUCTION,
        "request_instruction": REQUEST_INSTRUCTION,
        "system_instruction_sha256": _text_sha256(SYSTEM_INSTRUCTION),
        "request_template_sha256": _text_sha256(REQUEST_INSTRUCTION),
        "output_schema_sha256": canonical_json_sha256(
            EvaluationAnswer.model_json_schema()
        ),
    }
    return PromptPolicy.model_validate(
        {**payload, "policy_sha256": canonical_json_sha256(payload)}
    )


def validate_generation_identity(
    identity: GenerationIdentity,
    policy: PromptPolicy,
) -> None:
    """Require exact prompt and output-schema hashes before any provider construction."""

    if (
        identity.system_instruction_sha256 != policy.system_instruction_sha256
        or identity.request_template_sha256 != policy.request_template_sha256
        or identity.output_schema_sha256 != policy.output_schema_sha256
    ):
        raise PromptPolicyError("generation identity does not match the frozen prompt policy")


def render_user_payload(evidence: EvaluationEvidencePack) -> bytes:
    """Serialize the same user payload shape without condition names or hidden state."""

    return canonical_json_bytes(
        {
            "instruction": REQUEST_INSTRUCTION,
            "evidence": model_visible_evidence(evidence),
        }
    )


def _text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _self_sha256(value: StrictFrozenSchema, field_name: str) -> str:
    payload = value.model_dump(mode="python")
    del payload[field_name]
    return canonical_json_sha256(payload)
