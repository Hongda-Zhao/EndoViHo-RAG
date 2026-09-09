"""Exact core-53 annotation admission, separate from any execution capability.

The authoring decision fixes question membership and scoring families only. This
module requires a separately human-approved entity table and existing approved
Question/Gold contracts; it cannot approve data, construct providers, or run queries.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, TypeAdapter, model_validator

from eve_relation_rag.experiments.rag_value_ablation.associations import ViralLineageRole
from eve_relation_rag.experiments.rag_value_ablation.contracts import (
    HumanApproval,
    QuestionManifest,
)
from eve_relation_rag.experiments.rag_value_ablation.family_assignment import (
    ClassifiedCandidate,
    verify_classified_package,
)
from eve_relation_rag.experiments.rag_value_ablation.scientific_questions import (
    EntitySlot,
    RequiredEntityType,
    build_scientific_entity_bindings_template,
)
from eve_relation_rag.experiments.rag_value_ablation.scope_amendment import _read_input
from eve_relation_rag.literature.contracts import (
    AssemblyKey,
    CorpusReleaseKey,
    DocumentKey,
    LocusKey,
    QuestionText,
    Sha256,
    StableToken,
    StrictFrozenSchema,
)
from eve_relation_rag.literature.hashing import canonical_json_bytes, canonical_json_sha256

CORE53_CLASSIFIED_FILE_SHA256 = "c16009a6263c26d3b4ecfa6999f7cedc4c6ed76e5aa6ca94681577a71350c699"
CORE53_CLASSIFIED_PACKAGE_SHA256 = (
    "266ddb2f0127fd242f3e4d00dfdd02cfa8c518a391f45ae039b101cb004aae22"
)
_PLACEHOLDER_RE = re.compile(r"\{([A-Z][A-Z0-9_]*)\}")
_ENTITY_TYPES = {
    row.entity_slot: row.required_entity_type
    for row in build_scientific_entity_bindings_template().bindings
}


class ScopedAdmissionError(ValueError):
    """The supplied annotations do not bind the exact approved authoring scope."""


class ApprovedEntityBinding(StrictFrozenSchema):
    """One exact selection; approval covers all rows in the enclosing manifest."""

    entity_slot: EntitySlot
    required_entity_type: RequiredEntityType
    selected_stable_key: StableToken
    selected_display_name: QuestionText
    selected_snapshot_key: StableToken | None = None
    selected_lineage_role: ViralLineageRole | Literal["assembly_source_taxonomy"] | None = None
    include_descendants: bool | None = None
    source_document_keys: tuple[DocumentKey, ...] = ()

    @model_validator(mode="after")
    def validate_selection(self) -> Self:
        if self.required_entity_type != _ENTITY_TYPES[self.entity_slot]:
            raise ValueError("entity slot and required entity type do not match")
        if any("{" in value or "}" in value for value in (
            self.selected_stable_key, self.selected_display_name,
        )):
            raise ValueError("entity selections cannot contain template placeholders")
        if self.selected_display_name != self.selected_display_name.strip():
            raise ValueError("entity display name must preserve an exact trimmed source label")
        kind = self.required_entity_type
        if kind == "assembly":
            TypeAdapter(AssemblyKey).validate_python(self.selected_stable_key)
        elif kind == "eve_locus":
            TypeAdapter(LocusKey).validate_python(self.selected_stable_key)
        if kind in {"assembly_source_taxon", "source_lineage", "viral_lineage"}:
            if self.selected_snapshot_key is None or self.include_descendants is None:
                raise ValueError("taxonomy selections require a snapshot and explicit scope")
            if kind == "viral_lineage":
                if self.selected_lineage_role not in {
                    "formal_viral_taxonomy", "study_viral_lineage", "extended_viral_lineage",
                }:
                    raise ValueError("viral lineage requires an explicit viral role")
            elif self.selected_lineage_role != "assembly_source_taxonomy":
                raise ValueError("source taxon cannot use a viral-lineage role")
            if kind == "assembly_source_taxon" and self.include_descendants:
                raise ValueError("one assembly-source taxon must use exact taxon scope")
        elif any(value is not None for value in (
            self.selected_snapshot_key, self.selected_lineage_role, self.include_descendants,
        )):
            raise ValueError("non-taxonomy selections cannot introduce taxonomy scope")
        if self.source_document_keys != tuple(sorted(set(self.source_document_keys))):
            raise ValueError("entity source documents must be sorted and unique")
        if kind == "reported_viral_region" and not self.source_document_keys:
            raise ValueError("a source-reported viral region requires a source document")
        return self

    @property
    def question_value(self) -> str:
        """Use identifiers for assemblies/loci, approved source labels elsewhere."""

        if self.required_entity_type == "assembly":
            return self.selected_stable_key.removeprefix("assembly:ncbi:")
        if self.required_entity_type == "eve_locus":
            return self.selected_stable_key
        return self.selected_display_name


class ApprovedEntityBindings(StrictFrozenSchema):
    """One human can approve the exact nine selections together; no default signer."""

    schema_version: Literal["rag-value-core53-approved-entities-v1"]
    experiment_namespace: Literal["rag-value-ablation:core53:classified-v1"]
    classified_candidates_sha256: Literal[
        "c16009a6263c26d3b4ecfa6999f7cedc4c6ed76e5aa6ca94681577a71350c699"
    ]
    dataset_release_key: StableToken
    dataset_manifest_sha256: Sha256
    corpus_release_key: CorpusReleaseKey
    corpus_manifest_sha256: Sha256
    review_status: Literal["approved"]
    approval: HumanApproval
    bindings: tuple[ApprovedEntityBinding, ...] = Field(min_length=9, max_length=9)
    manifest_sha256: Sha256

    @model_validator(mode="after")
    def validate_integrity(self) -> Self:
        slots = tuple(row.entity_slot for row in self.bindings)
        required = tuple(sorted(slot for slot in _ENTITY_TYPES if slot != "ASSEMBLY_B"))
        if slots != required:
            raise ValueError("entity selections must cover the nine core-53 slots exactly")
        selections = tuple(
            (row.required_entity_type, row.selected_stable_key,
             row.selected_snapshot_key, row.selected_lineage_role)
            for row in self.bindings
        )
        if len(selections) != len(set(selections)):
            raise ValueError("separate entity slots must not repeat the same scoped entity")
        if self.manifest_sha256 != canonical_json_sha256(
            self.model_dump(mode="json", exclude={"manifest_sha256"})
        ):
            raise ValueError("approved entity manifest checksum does not match")
        return self


class Core53QuestionScope(StrictFrozenSchema):
    """A precise admission input, not release activation or model authorization."""

    schema_version: Literal["rag-value-core53-question-scope-v1"]
    experiment_namespace: Literal["rag-value-ablation:core53:classified-v1"]
    classified_package_sha256: Literal[
        "266ddb2f0127fd242f3e4d00dfdd02cfa8c518a391f45ae039b101cb004aae22"
    ]
    candidates: tuple[ClassifiedCandidate, ...] = Field(min_length=53, max_length=53)
    entities: ApprovedEntityBindings
    scope_sha256: Sha256

    @model_validator(mode="after")
    def validate_integrity(self) -> Self:
        raw = b"".join(canonical_json_bytes(row) + b"\n" for row in self.candidates)
        if hashlib.sha256(raw).hexdigest() != CORE53_CLASSIFIED_FILE_SHA256:
            raise ValueError("scope must contain the exact classified core-53 templates")
        slots = {slot for row in self.candidates for slot in row.entity_slots}
        if slots != {row.entity_slot for row in self.entities.bindings}:
            raise ValueError("entities must cover exactly the placeholders in this scope")
        if self.scope_sha256 != canonical_json_sha256(
            self.model_dump(mode="json", exclude={"scope_sha256"})
        ):
            raise ValueError("question scope checksum does not match")
        return self


def build_core53_question_scope(
    classified_package: Path, entities: ApprovedEntityBindings,
) -> Core53QuestionScope:
    """Verify the saved authoring packet and bind separately approved entity selections."""

    if type(entities) is not ApprovedEntityBindings:
        raise ScopedAdmissionError("scope requires an exact approved entity manifest")
    try:
        entities = ApprovedEntityBindings.model_validate_json(canonical_json_bytes(entities))
        verify_classified_package(classified_package)
        package_bytes = _read_input(classified_package / "package_manifest.json")
        if hashlib.sha256(package_bytes).hexdigest() != CORE53_CLASSIFIED_PACKAGE_SHA256:
            raise ValueError("classified package differs from the approved core-53 package")
        candidate_bytes = _read_input(classified_package / "classified_candidates.jsonl")
        candidates = tuple(
            ClassifiedCandidate.model_validate_json(line)
            for line in candidate_bytes.splitlines()
        )
        payload = {
            "schema_version": "rag-value-core53-question-scope-v1",
            "experiment_namespace": "rag-value-ablation:core53:classified-v1",
            "classified_package_sha256": CORE53_CLASSIFIED_PACKAGE_SHA256,
            "candidates": candidates,
            "entities": entities,
        }
        return Core53QuestionScope.model_validate_json(canonical_json_bytes({
            **payload, "scope_sha256": canonical_json_sha256(payload),
        }))
    except (OSError, ValueError, TypeError) as exc:
        raise ScopedAdmissionError("classified scope or entity approval failed validation") from exc


def validate_core53_question_scope(
    scope: Core53QuestionScope, manifest: QuestionManifest,
    *, revision: Literal["classified-v1", "single-source-v2"] = "classified-v1",
) -> None:
    """Require exact bound text/family/IDs and independently approved Gold for all 53."""

    if type(scope) is not Core53QuestionScope or type(manifest) is not QuestionManifest:
        raise ScopedAdmissionError("scoped admission requires exact typed manifests")
    try:
        scope = Core53QuestionScope.model_validate_json(canonical_json_bytes(scope))
        manifest = QuestionManifest.model_validate_json(canonical_json_bytes(manifest))
    except (ValueError, TypeError) as exc:
        raise ScopedAdmissionError("scoped admission failed checksum revalidation") from exc
    identities = (
        "dataset_release_key", "dataset_manifest_sha256", "corpus_release_key",
        "corpus_manifest_sha256",
    )
    if any(getattr(scope.entities, key) != getattr(manifest, key) for key in identities):
        raise ScopedAdmissionError("entity and question release/corpus identities do not match")
    if revision not in {"classified-v1", "single-source-v2"}:
        raise ScopedAdmissionError("unknown core-53 wording revision")
    rows = scope.candidates
    if revision == "single-source-v2":
        from eve_relation_rag.experiments.rag_value_ablation.question_revision import (
            revise_candidates,
        )

        rows = revise_candidates(rows)
    candidates = {row.template_id: row for row in rows}
    if tuple(question.question_id for question in manifest.questions) != tuple(sorted(candidates)):
        raise ScopedAdmissionError("question IDs do not exactly match the classified core-53 scope")
    values: dict[str, str] = {
        row.entity_slot: row.question_value for row in scope.entities.bindings
    }
    for question in manifest.questions:
        candidate = candidates[question.question_id]
        if question.review_status != "approved":
            raise ScopedAdmissionError("all scoped questions require independent Gold approval")
        expected_text = _PLACEHOLDER_RE.sub(
            lambda match: values[match.group(1)], candidate.question_text_template,
        )
        if question.family != candidate.family or question.question_text != expected_text:
            raise ScopedAdmissionError("question text or family differs from its bound template")
