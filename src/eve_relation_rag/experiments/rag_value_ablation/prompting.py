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
    "exact-versus-descendant scope. Treat HCVR, VR Type, Viral Major Taxon, Integration, and "
    "Viral contig only as source annotations. Do not classify a record as Transferred gene or "
    "Integrated virus. Cite every literature-derived factual "
    "claim. State that evidence is insufficient and abstain when it cannot support an answer. "
    "Do not infer modern infection, prevalence, biological absence, co-divergence, or "
    "independent integration events unless the supplied evidence explicitly supports and "
    "permits the inference. Return only JSON matching the common answer schema. "
    "Output only fields relevant to the question and supported by the evidence; omit unknown "
    "optional fields or use null. Never fill schema fields with examples or guessed values. "
    "For a count question, report the count and its metric, without adding locus identities, "
    "coordinates, taxa, or associations unless independently supplied and requested. "
    "Two literature passages do not establish two release loci or any cross-source mapping. "
    "Use claim citation_ids for exact D1/D2 literature IDs or R1/R2 raw segment IDs. "
    "Use cited_chunk_ids only for the matching chunk_key values of cited D passages; "
    "raw R references never belong in cited_chunk_ids. "
    "When abstained is true, put the reason in answer_text or limitations, with claims=[], "
    "cited_chunk_ids=[] and structured_facts=null. When abstained is false, include at least "
    "one atomic claim, numbered C1, C2, and so on. Every literature_fact needs citation_ids. "
    "Return exact_count together with metric_key, and release_key together with "
    "release_manifest_sha256. A text/source checksum is not a release manifest checksum. "
    "Keep structured claims separate from literature claims and interpretation."
)
FORMAT_EXAMPLES: Final = (
    {
        "example_evidence": "No evidence is supplied for the requested count.",
        "example_answer": {
            "abstained": True,
            "answer_text": "The evidence is insufficient to determine the requested count.",
            "claims": [], "structured_facts": None, "cited_chunk_ids": [],
            "limitations": ["No count is supplied."],
        },
    },
    {
        "example_evidence": {
            "question": "How many distinct contigs are reported?",
            "data": {"kind": "aggregate", "metric_key": "distinct_contig_count", "value": 7},
        },
        "example_answer": {
            "abstained": False, "answer_text": "The supplied result reports 7 distinct contigs.",
            "claims": [{
                "claim_id": "C1", "claim_type": "structured_fact",
                "text": "The supplied result reports 7 distinct contigs.", "citation_ids": [],
            }],
            "structured_facts": {"exact_count": 7, "metric_key": "distinct_contig_count"},
            "cited_chunk_ids": [], "limitations": [],
        },
    },
)
REQUEST_INSTRUCTION: Final = (
    "Answer evidence.question. answer_text must be a nonempty English answer sentence. "
    "First decide whether the actual evidence establishes the requested answer; if not, "
    "abstain. A relevant literature passage alone is not a release-wide count. "
    "Put typed values ONLY inside structured_facts, never at the top level. "
    "Use answer_schema. These fictional examples illustrate formatting only and supply "
    "NO facts for the current question; never copy their numbers or claims: "
    + canonical_json_bytes(FORMAT_EXAMPLES).decode("utf-8")
    + " Now answer using only the actual evidence envelope, with no commentary outside JSON."
)


class PromptPolicyError(ValueError):
    """Raised when prompt identity or serialization differs between systems."""


class PromptPolicy(StrictFrozenSchema):
    """Exact prompt bytes and output schema used by S0/S1/S2/S3/S5/S6."""

    policy_schema_version: str = Field(pattern=r"^rag-value-prompt-policy-v3$")
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
        "policy_schema_version": "rag-value-prompt-policy-v3",
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
            "answer_schema": EvaluationAnswer.model_json_schema(),
            "evidence": model_visible_evidence(evidence),
        }
    )


def _text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _self_sha256(value: StrictFrozenSchema, field_name: str) -> str:
    payload = value.model_dump(mode="python")
    del payload[field_name]
    return canonical_json_sha256(payload)
