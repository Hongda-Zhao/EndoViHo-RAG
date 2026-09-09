"""Core-53 admission tests: all biological facts and reviewer keys below are synthetic."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from eve_relation_rag.experiments.rag_value_ablation.annotations import (
    AnnotationError,
    require_trusted_question_set,
)
from eve_relation_rag.experiments.rag_value_ablation.contracts import (
    EvidenceGroup,
    HumanApproval,
    HybridGold,
    LiteratureGold,
    QuestionFamily,
    QuestionGold,
    QuestionManifest,
    StructuredGold,
    UnsupportedGold,
    build_evaluation_question,
    build_question_manifest,
)
from eve_relation_rag.experiments.rag_value_ablation.scientific_questions import (
    build_scientific_entity_bindings_template,
)
from eve_relation_rag.experiments.rag_value_ablation.scoped_admission import (
    CORE53_CLASSIFIED_FILE_SHA256,
    ApprovedEntityBinding,
    ApprovedEntityBindings,
    Core53QuestionScope,
    ScopedAdmissionError,
    build_core53_question_scope,
    validate_core53_question_scope,
)
from eve_relation_rag.literature.hashing import canonical_json_bytes, canonical_json_sha256

PACKAGE = Path(__file__).resolve().parents[2] / (
    "benchmark/rag_value_ablation/authoring_review_core53_classified"
)
DATASET = "release:tests-only:v0:20990101:001"
CORPUS = "corpus:endoviho-rag:v0:20990101:001"
DOCUMENT = f"document:sha256:{'a' * 64}"
CHUNK = f"chunk:sha256:{'b' * 64}"


def _approval() -> HumanApproval:
    return HumanApproval(
        reviewer_key="tests-only-synthetic-reviewer",
        reviewed_at="2099-01-01T00:00:00Z",
        attestation="I independently reviewed this annotation and approve it for this benchmark.",
    )


def _hashed(payload: dict[str, Any], field: str = "manifest_sha256") -> bytes:
    payload = {key: value for key, value in payload.items() if key != field}
    return canonical_json_bytes({**payload, field: canonical_json_sha256(payload)})


@pytest.fixture(scope="module")
def entities() -> ApprovedEntityBindings:
    bindings = []
    for index, blank in enumerate(build_scientific_entity_bindings_template().bindings):
        if blank.entity_slot == "ASSEMBLY_B":
            continue
        kind = blank.required_entity_type
        key = f"tests-only:entity:{index}"
        if kind == "assembly":
            key = "assembly:ncbi:GCA_999999999.1"
        elif kind == "eve_locus":
            key = f"locus:eve:v1:sha256:{index:064x}"
        taxonomy = kind in {"assembly_source_taxon", "source_lineage", "viral_lineage"}
        role = None
        if taxonomy:
            role = "study_viral_lineage" if kind == "viral_lineage" else "assembly_source_taxonomy"
        bindings.append({
            "entity_slot": blank.entity_slot,
            "required_entity_type": kind,
            "selected_stable_key": key,
            "selected_display_name": f"Tests-only entity {index}",
            "selected_snapshot_key": "tests-only:snapshot" if taxonomy else None,
            "selected_lineage_role": role,
            "include_descendants": False if taxonomy else None,
            "source_document_keys": [DOCUMENT] if kind == "reported_viral_region" else [],
        })
    return ApprovedEntityBindings.model_validate_json(_hashed({
        "schema_version": "rag-value-core53-approved-entities-v1",
        "experiment_namespace": "rag-value-ablation:core53:classified-v1",
        "classified_candidates_sha256": CORE53_CLASSIFIED_FILE_SHA256,
        "dataset_release_key": DATASET,
        "dataset_manifest_sha256": "d" * 64,
        "corpus_release_key": CORPUS,
        "corpus_manifest_sha256": "e" * 64,
        "review_status": "approved",
        "approval": _approval(),
        "bindings": bindings,
    }))


@pytest.fixture(scope="module")
def scope(entities: ApprovedEntityBindings) -> Core53QuestionScope:
    return build_core53_question_scope(PACKAGE, entities)


def _gold(family: QuestionFamily, *, release: str = DATASET) -> QuestionGold:
    structured = StructuredGold(
        exact_count=0, metric_key="tests-only:count", release_key=release,
        release_manifest_sha256="d" * 64,
    )
    literature = LiteratureGold(
        required_document_keys=(DOCUMENT,),
        evidence_groups=(EvidenceGroup(
            group_id="tests-only:group", required_document_key=DOCUMENT,
            required_chunk_key=CHUNK,
        ),),
        required_concepts=("Synthetic testing only, not scientific evidence.",),
    )
    if family == "structured":
        return structured
    if family == "literature":
        return literature
    if family == "hybrid":
        return HybridGold(
            structured=structured, literature=literature,
            required_relationships=("Tests-only association.",),
        )
    return UnsupportedGold(
        refusal_category="external_knowledge_requested",
        prohibited_downstream_stages=("generation",),
        required_explanations=("Synthetic refusal label only.",),
        forbidden_claims=("Synthetic unsupported assertion.",),
    )


@pytest.fixture(scope="module")
def questions(scope: Core53QuestionScope) -> QuestionManifest:
    values = {row.entity_slot: row.question_value for row in scope.entities.bindings}
    rows = []
    for candidate in scope.candidates:
        text = candidate.question_text_template
        for slot, value in values.items():
            text = text.replace("{" + slot + "}", value)
        rows.append(build_evaluation_question(
            question_id=candidate.template_id, family=candidate.family,
            question_text=text, review_status="approved", approval=_approval(),
            gold=_gold(candidate.family), authoring_notes="Synthetic test fixture only.",
        ))
    return build_question_manifest(
        rows, dataset_release_key=DATASET, dataset_manifest_sha256="d" * 64,
        corpus_release_key=CORPUS, corpus_manifest_sha256="e" * 64,
    )


def _manifest(rows: list[Any], **overrides: Any) -> QuestionManifest:
    identities = {
        "dataset_release_key": DATASET, "dataset_manifest_sha256": "d" * 64,
        "corpus_release_key": CORPUS, "corpus_manifest_sha256": "e" * 64,
    }
    return build_question_manifest(rows, **{**identities, **overrides})


def test_exact_scoped_annotations_are_admitted_without_changing_default(
    scope: Core53QuestionScope, questions: QuestionManifest,
) -> None:
    assert require_trusted_question_set(questions, scope=scope) == questions.questions
    assert questions.approved_family_counts == {
        "structured": 16, "literature": 16, "hybrid": 9, "unsupported": 12,
    }
    with pytest.raises(AnnotationError, match="60-80"):
        require_trusted_question_set(questions)
    assert len(scope.entities.bindings) == 9
    assert "ASSEMBLY_B" not in {row.entity_slot for row in scope.entities.bindings}
    assert all(row.benchmark_review_status == "pending" for row in scope.candidates)
    assert all(row.gold is row.oracle is row.approval is None for row in scope.candidates)
    assigned = next(row for row in questions.questions if row.question_id == "UNSUP-09")
    assert assigned.family == "hybrid"


@pytest.mark.parametrize("change", ["replacement", "missing", "extra", "pending", "text", "family"])
def test_count_or_ids_alone_never_authorize_a_question_set(
    scope: Core53QuestionScope, questions: QuestionManifest, change: str,
) -> None:
    rows = list(questions.questions)
    old = rows[0]
    if change == "missing":
        rows.pop()
    elif change == "extra":
        rows.append(build_evaluation_question(
            question_id="extra-tests-only", family="structured", question_text="Extra?",
            review_status="pending",
        ))
    else:
        family: QuestionFamily = "structured" if change == "family" else old.family
        rows[0] = build_evaluation_question(
            question_id="replacement" if change == "replacement" else old.question_id,
            family=family,
            question_text=old.question_text + (" Changed?" if change == "text" else ""),
            review_status="pending" if change == "pending" else "approved",
            approval=None if change == "pending" else _approval(),
            gold=None if change == "pending" else _gold(family),
        )
    with pytest.raises(AnnotationError):
        require_trusted_question_set(_manifest(rows), scope=scope)


@pytest.mark.parametrize("field,value", [
    ("dataset_release_key", "release:other:v0:20990101:001"),
    ("dataset_manifest_sha256", "f" * 64),
    ("corpus_release_key", "corpus:endoviho-rag:v0:20990101:002"),
    ("corpus_manifest_sha256", "f" * 64),
])
def test_entities_bind_both_release_and_corpus_exactly(
    scope: Core53QuestionScope, questions: QuestionManifest, field: str, value: str,
) -> None:
    with pytest.raises(AnnotationError, match="identities do not match"):
        require_trusted_question_set(
            _manifest(list(questions.questions), **{field: value}), scope=scope,
        )


def test_structured_gold_release_gate_is_unchanged(
    scope: Core53QuestionScope, questions: QuestionManifest,
) -> None:
    rows = list(questions.questions)
    index = next(i for i, row in enumerate(rows) if row.family == "structured")
    old = rows[index]
    rows[index] = build_evaluation_question(
        question_id=old.question_id, family=old.family, question_text=old.question_text,
        review_status="approved", approval=_approval(),
        gold=_gold(old.family, release="release:other:v0:20990101:001"),
    )
    with pytest.raises(AnnotationError, match="structured Gold release identity"):
        require_trusted_question_set(_manifest(rows), scope=scope)


@pytest.mark.parametrize("change", ["text", "family", "id", "order"])
def test_rechecksummed_template_substitution_cannot_inherit_authoring_decision(
    scope: Core53QuestionScope, change: str,
) -> None:
    payload = scope.model_dump(mode="json")
    if change == "order":
        payload["candidates"].reverse()
    else:
        row = payload["candidates"][0]
        if change == "text":
            import hashlib

            row["question_text_template"] += " Altered?"
            row["question_text_sha256"] = hashlib.sha256(
                row["question_text_template"].encode()
            ).hexdigest()
        elif change == "id":
            row["template_id"] = "replacement"
        else:
            row["family"] = "unsupported"
        row["record_sha256"] = canonical_json_sha256({
            key: value for key, value in row.items() if key != "record_sha256"
        })
    with pytest.raises(ValidationError, match="exact classified core-53"):
        Core53QuestionScope.model_validate_json(_hashed(payload, "scope_sha256"))


@pytest.mark.parametrize("change", ["pending", "no_approval", "missing", "duplicate", "stale"])
def test_unapproved_or_incomplete_entity_table_is_rejected(
    entities: ApprovedEntityBindings, change: str,
) -> None:
    payload = entities.model_dump(mode="json")
    if change == "pending":
        payload["review_status"] = "pending"
    elif change == "no_approval":
        payload["approval"] = None
    elif change == "missing":
        payload["bindings"].pop()
    elif change == "duplicate":
        payload["bindings"][1] = payload["bindings"][0]
    else:
        payload["dataset_manifest_sha256"] = "f" * 64
    raw = canonical_json_bytes(payload) if change == "stale" else _hashed(payload)
    with pytest.raises(ValidationError):
        ApprovedEntityBindings.model_validate_json(raw)


@pytest.mark.parametrize("slot,field,value", [
    ("ASSEMBLY_A", "selected_stable_key", "assembly:ncbi:GCA_999999999"),
    ("EVE_LOCUS_A", "selected_stable_key", "locus-1"),
    ("VIRAL_LINEAGE_A", "selected_lineage_role", "assembly_source_taxonomy"),
    ("SOURCE_TAXON_LINEAGE_A", "selected_snapshot_key", None),
    ("SOURCE_TAXON_LINEAGE_A", "include_descendants", None),
    ("ASSEMBLY_SOURCE_TAXON_A", "include_descendants", True),
    ("REPORTED_REGION_A", "source_document_keys", []),
    ("VIRAL_LINEAGE_A", "selected_display_name", "{VIRAL_LINEAGE_A}"),
    ("VIRAL_LINEAGE_A", "selected_display_name", "   Example"),
    ("ASSEMBLY_A", "include_descendants", False),
])
def test_entity_identifiers_roles_and_source_scope_are_explicit(
    entities: ApprovedEntityBindings, slot: str, field: str, value: Any,
) -> None:
    row = next(item for item in entities.bindings if item.entity_slot == slot)
    with pytest.raises(ValidationError):
        ApprovedEntityBinding.model_validate_json(canonical_json_bytes({
            **row.model_dump(mode="json"), field: value,
        }))


def test_entity_rebinding_does_not_reuse_old_question_approval(
    scope: Core53QuestionScope, questions: QuestionManifest,
) -> None:
    payload = scope.entities.model_dump(mode="json")
    payload["bindings"][0]["selected_stable_key"] = "assembly:ncbi:GCA_999999998.1"
    changed = ApprovedEntityBindings.model_validate_json(_hashed(payload))
    new_scope = build_core53_question_scope(PACKAGE, changed)
    with pytest.raises(AnnotationError, match="bound template"):
        require_trusted_question_set(questions, scope=new_scope)


def test_unresolved_placeholders_cannot_be_admitted(
    scope: Core53QuestionScope, questions: QuestionManifest,
) -> None:
    candidate = next(row for row in scope.candidates if row.entity_slots)
    rows = list(questions.questions)
    index = next(i for i, row in enumerate(rows) if row.question_id == candidate.template_id)
    rows[index] = build_evaluation_question(
        question_id=candidate.template_id, family=candidate.family,
        question_text=candidate.question_text_template, review_status="approved",
        approval=_approval(), gold=_gold(candidate.family),
    )
    with pytest.raises(AnnotationError, match="bound template"):
        require_trusted_question_set(_manifest(rows), scope=scope)


def test_type_and_copy_bypasses_are_revalidated(
    scope: Core53QuestionScope, questions: QuestionManifest,
) -> None:
    with pytest.raises(AnnotationError, match="exact typed"):
        require_trusted_question_set(questions, scope=scope.model_dump())  # type: ignore[arg-type]
    with pytest.raises(AnnotationError, match="checksum revalidation"):
        require_trusted_question_set(
            questions, scope=scope.model_copy(update={"scope_sha256": "0" * 64}),
        )
    class DerivedScope(Core53QuestionScope):
        pass

    derived = DerivedScope.model_validate_json(canonical_json_bytes(scope))
    with pytest.raises(AnnotationError, match="exact typed"):
        require_trusted_question_set(questions, scope=derived)
    with pytest.raises(ScopedAdmissionError, match="exact typed"):
        validate_core53_question_scope(scope, questions.model_dump())  # type: ignore[arg-type]


def test_source_packet_and_entity_inputs_are_validated_before_binding(
    entities: ApprovedEntityBindings, tmp_path: Path,
) -> None:
    with pytest.raises(ScopedAdmissionError):
        build_core53_question_scope(tmp_path, entities)
    with pytest.raises(ScopedAdmissionError, match="exact approved"):
        build_core53_question_scope(PACKAGE, entities.model_dump())  # type: ignore[arg-type]
    with pytest.raises(ScopedAdmissionError):
        build_core53_question_scope(
            PACKAGE, entities.model_copy(update={"manifest_sha256": "0" * 64}),
        )
