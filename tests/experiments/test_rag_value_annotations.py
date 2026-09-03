from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from eve_relation_rag.experiments.rag_value_ablation.annotations import (
    AnnotationError,
    load_oracle_manifest,
    load_question_manifest,
    require_approved_questions,
    require_trusted_question_set,
    validate_oracle_coverage,
    write_new_canonical_json,
)
from eve_relation_rag.experiments.rag_value_ablation.contracts import (
    EvaluationQuestion,
    EvidenceGroup,
    HumanApproval,
    HybridGold,
    LiteratureGold,
    OracleEvidenceEntry,
    QuestionFamily,
    QuestionGold,
    QuestionManifest,
    StructuredGold,
    UnsupportedGold,
    build_evaluation_question,
    build_oracle_entry,
    build_oracle_manifest,
    build_question_manifest,
)

DATASET_RELEASE_KEY = "release:test:v0:20990101:001"
DATASET_MANIFEST_SHA256 = "d" * 64
CORPUS_RELEASE_KEY = "corpus:endoviho-rag:v0:20990101:001"
CORPUS_MANIFEST_SHA256 = "e" * 64
DOCUMENT_KEY = f"document:sha256:{'a' * 64}"
REQUIRED_CHUNK_KEY = f"chunk:sha256:{'b' * 64}"
ALTERNATIVE_CHUNK_KEY = f"chunk:sha256:{'c' * 64}"
EXCLUDED_CHUNK_KEY = f"chunk:sha256:{'d' * 64}"
ARBITRARY_CHUNK_KEY = f"chunk:sha256:{'f' * 64}"


def test_approved_question_and_separately_approved_oracle_round_trip(tmp_path: Path) -> None:
    gold = _gold()
    approval = _approval("question-reviewer")
    question = build_evaluation_question(
        question_id="structured-001",
        family="structured",
        question_text="Count included loci.",
        review_status="approved",
        approval=approval,
        gold=gold,
    )
    questions = build_question_manifest(
        (question,),
        dataset_release_key=gold.release_key,
        dataset_manifest_sha256=gold.release_manifest_sha256,
    )
    oracle_entry = build_oracle_entry(
        question_id=question.question_id,
        question_text_sha256=question.question_text_sha256,
        review_status="approved",
        approval=_approval("oracle-reviewer"),
        evidence_disposition="evidence_supplied",
        structured_facts=gold,
        literature_chunk_keys=(),
        dataset_release_key=gold.release_key,
        dataset_manifest_sha256=gold.release_manifest_sha256,
        source_attestation=(
            "Evidence was selected manually and not generated from model or retriever output."
        ),
    )
    oracle = build_oracle_manifest((oracle_entry,))
    question_path = tmp_path / "questions.json"
    oracle_path = tmp_path / "oracle.json"
    question_file_sha = write_new_canonical_json(question_path, questions)
    oracle_file_sha = write_new_canonical_json(oracle_path, oracle)

    loaded_questions = load_question_manifest(question_path, question_file_sha)
    loaded_oracle = load_oracle_manifest(oracle_path, oracle_file_sha)
    assert require_approved_questions(loaded_questions) == (question,)
    validate_oracle_coverage(loaded_questions, loaded_oracle)

    with pytest.raises(AnnotationError, match="already exists"):
        write_new_canonical_json(question_path, questions)
    with pytest.raises(AnnotationError, match="not approved"):
        load_question_manifest(question_path, "0" * 64)


def test_pending_questions_and_oracle_entries_cannot_enter_trusted_benchmark() -> None:
    pending = build_evaluation_question(
        question_id="pending-001",
        family="structured",
        question_text="Which value still needs review?",
        review_status="pending",
    )
    questions = build_question_manifest((pending,))

    with pytest.raises(AnnotationError, match="at least one approved"):
        require_approved_questions(questions)


def test_copied_question_manifest_cannot_forge_human_approval() -> None:
    pending = build_evaluation_question(
        question_id="pending-001",
        family="structured",
        question_text="Which value still needs review?",
        review_status="pending",
    )
    manifest = build_question_manifest((pending,))
    forged_question = pending.model_copy(
        update={
            "review_status": "approved",
            "approval": _approval("forged-reviewer"),
            "gold": _gold(),
        }
    )
    forged_manifest = manifest.model_copy(
        update={
            "questions": (forged_question,),
            "approved_question_count": 1,
            "approved_family_counts": {
                "structured": 1,
                "literature": 0,
                "hybrid": 0,
                "unsupported": 0,
            },
            "gold_sha256": "0" * 64,
        }
    )

    with pytest.raises(AnnotationError, match="checksum revalidation"):
        require_approved_questions(forged_manifest)
    with pytest.raises(AnnotationError, match="checksum revalidation"):
        require_trusted_question_set(forged_manifest)


def test_copied_oracle_manifest_cannot_bypass_manual_attestation() -> None:
    gold = _literature_gold()
    question, questions = _approved_question_and_manifest("literature", gold)
    approved = _approved_oracle_entry(
        question.question_id,
        question.question_text_sha256,
        literature_chunk_keys=(REQUIRED_CHUNK_KEY,),
    )
    oracle = build_oracle_manifest((approved,))
    forged_entry = approved.model_copy(update={"source_attestation": None})
    forged_oracle = oracle.model_copy(update={"entries": (forged_entry,)})

    with pytest.raises(AnnotationError, match="oracle manifest failed checksum revalidation"):
        validate_oracle_coverage(questions, forged_oracle)


def test_annotation_gates_reject_serialized_shapes_without_typed_authority() -> None:
    pending = build_question_manifest(
        (
            build_evaluation_question(
                question_id="pending-001",
                family="structured",
                question_text="Which value still needs review?",
                review_status="pending",
            ),
        )
    )

    with pytest.raises(AnnotationError, match="exact QuestionManifest"):
        require_approved_questions(pending.model_dump())  # type: ignore[arg-type]


def test_tracked_gold_and_oracle_authoring_worksheets_are_blank() -> None:
    repository_root = Path(__file__).parents[2]

    assert (
        repository_root / "benchmark/rag_value_ablation/questions_template.jsonl"
    ).read_bytes() == b"\n"
    assert (
        repository_root / "benchmark/rag_value_ablation/oracle_evidence_template.jsonl"
    ).read_bytes() == b"\n"


def test_trusted_question_set_enforces_preregistered_family_counts() -> None:
    gold = _gold()
    question = build_evaluation_question(
        question_id="structured-001",
        family="structured",
        question_text="Count included loci.",
        review_status="approved",
        approval=_approval("question-reviewer"),
        gold=gold,
    )
    manifest = build_question_manifest(
        (question,),
        dataset_release_key=gold.release_key,
        dataset_manifest_sha256=gold.release_manifest_sha256,
        corpus_release_key="corpus:endoviho-rag:v0:20990101:001",
        corpus_manifest_sha256="e" * 64,
    )

    with pytest.raises(AnnotationError, match="60-80 approved"):
        require_trusted_question_set(manifest)


def test_approved_oracle_can_attest_that_unsupported_question_has_no_evidence() -> None:
    entry = build_oracle_entry(
        question_id="unsupported-001",
        question_text_sha256="a" * 64,
        review_status="approved",
        approval=_approval("oracle-reviewer"),
        evidence_disposition="no_supporting_evidence",
        source_attestation=(
            "Evidence was selected manually and not generated from model or retriever output."
        ),
    )

    assert entry.structured_facts is None
    assert entry.literature_chunk_keys == ()


def test_oracle_question_and_release_checksums_must_match_approved_questions() -> None:
    gold = _gold()
    question = build_evaluation_question(
        question_id="structured-001",
        family="structured",
        question_text="Count included loci.",
        review_status="approved",
        approval=_approval("question-reviewer"),
        gold=gold,
    )
    questions = build_question_manifest(
        (question,),
        dataset_release_key=gold.release_key,
        dataset_manifest_sha256=gold.release_manifest_sha256,
    )
    mismatched = build_oracle_entry(
        question_id=question.question_id,
        question_text_sha256=hashlib.sha256(b"different question").hexdigest(),
        review_status="approved",
        approval=_approval("oracle-reviewer"),
        evidence_disposition="evidence_supplied",
        structured_facts=gold,
        literature_chunk_keys=(),
        dataset_release_key=gold.release_key,
        dataset_manifest_sha256=gold.release_manifest_sha256,
        source_attestation=(
            "Evidence was selected manually and not generated from model or retriever output."
        ),
    )

    with pytest.raises(AnnotationError, match="question checksum"):
        validate_oracle_coverage(questions, build_oracle_manifest((mismatched,)))


@pytest.mark.parametrize("mismatched_family", ("structured", "hybrid"))
def test_trusted_admission_rejects_structured_gold_from_another_release(
    mismatched_family: str,
) -> None:
    questions = []
    for family in ("structured", "literature", "hybrid", "unsupported"):
        for index in range(15):
            structured = _gold(
                release_key=(
                    "release:other:v0:20990101:001"
                    if family == mismatched_family and index == 0
                    else DATASET_RELEASE_KEY
                )
            )
            gold: QuestionGold
            if family == "structured":
                gold = structured
            elif family == "literature":
                gold = _literature_gold()
            elif family == "hybrid":
                gold = HybridGold(
                    structured=structured,
                    literature=_literature_gold(),
                    required_relationships=("Preserve the approved association.",),
                )
            else:
                gold = _unsupported_gold()
            questions.append(
                build_evaluation_question(
                    question_id=f"{family}-{index:03d}",
                    family=family,
                    question_text=f"Approved {family} question {index}.",
                    review_status="approved",
                    approval=_approval("question-reviewer"),
                    gold=gold,
                )
            )
    manifest = build_question_manifest(
        questions,
        dataset_release_key=DATASET_RELEASE_KEY,
        dataset_manifest_sha256=DATASET_MANIFEST_SHA256,
        corpus_release_key=CORPUS_RELEASE_KEY,
        corpus_manifest_sha256=CORPUS_MANIFEST_SHA256,
    )

    with pytest.raises(AnnotationError, match="structured Gold release identity"):
        require_trusted_question_set(manifest)


def test_oracle_structured_facts_must_exactly_equal_question_gold() -> None:
    gold = _gold()
    question, questions = _approved_question_and_manifest("structured", gold)
    mismatched_facts = _gold(release_key="release:other:v0:20990101:001")
    oracle_entry = _approved_oracle_entry(
        question.question_id,
        question.question_text_sha256,
        structured_facts=mismatched_facts,
    )

    with pytest.raises(AnnotationError, match="structured facts do not exactly match"):
        validate_oracle_coverage(questions, build_oracle_manifest((oracle_entry,)))


def test_oracle_validation_rejects_matching_gold_from_another_release() -> None:
    wrong_release_gold = _gold(release_key="release:other:v0:20990101:001")
    question, questions = _approved_question_and_manifest(
        "structured",
        wrong_release_gold,
    )
    oracle_entry = _approved_oracle_entry(
        question.question_id,
        question.question_text_sha256,
        structured_facts=wrong_release_gold,
    )

    with pytest.raises(AnnotationError, match="structured Gold release identity"):
        validate_oracle_coverage(questions, build_oracle_manifest((oracle_entry,)))


def test_oracle_literature_alternative_can_cover_approved_evidence_group() -> None:
    gold = _literature_gold()
    question, questions = _approved_question_and_manifest("literature", gold)
    oracle_entry = _approved_oracle_entry(
        question.question_id,
        question.question_text_sha256,
        literature_chunk_keys=(ALTERNATIVE_CHUNK_KEY,),
    )

    validate_oracle_coverage(questions, build_oracle_manifest((oracle_entry,)))


def test_oracle_hybrid_requires_both_exact_structured_and_literature_gold() -> None:
    gold = HybridGold(
        structured=_gold(),
        literature=_literature_gold(),
        required_relationships=("Preserve the approved association.",),
    )
    question, questions = _approved_question_and_manifest("hybrid", gold)
    complete = _approved_oracle_entry(
        question.question_id,
        question.question_text_sha256,
        structured_facts=gold.structured,
        literature_chunk_keys=(REQUIRED_CHUNK_KEY,),
    )
    validate_oracle_coverage(questions, build_oracle_manifest((complete,)))

    missing_literature = _approved_oracle_entry(
        question.question_id,
        question.question_text_sha256,
        structured_facts=gold.structured,
    )
    with pytest.raises(AnnotationError, match="every required Gold evidence group"):
        validate_oracle_coverage(
            questions,
            build_oracle_manifest((missing_literature,)),
        )


def test_oracle_structured_question_rejects_literature_chunks() -> None:
    gold = _gold()
    question, questions = _approved_question_and_manifest("structured", gold)
    mixed = _approved_oracle_entry(
        question.question_id,
        question.question_text_sha256,
        structured_facts=gold,
        literature_chunk_keys=(REQUIRED_CHUNK_KEY,),
    )

    with pytest.raises(AnnotationError, match="cannot carry literature chunks"):
        validate_oracle_coverage(questions, build_oracle_manifest((mixed,)))


@pytest.mark.parametrize(
    ("chunk_key", "message"),
    (
        (ARBITRARY_CHUNK_KEY, "not manually approved"),
        (EXCLUDED_CHUNK_KEY, "excluded or misleading"),
    ),
)
def test_oracle_rejects_arbitrary_and_excluded_literature_chunks(
    chunk_key: str,
    message: str,
) -> None:
    gold = _literature_gold()
    question, questions = _approved_question_and_manifest("literature", gold)
    oracle_entry = _approved_oracle_entry(
        question.question_id,
        question.question_text_sha256,
        literature_chunk_keys=(chunk_key,),
    )

    with pytest.raises(AnnotationError, match=message):
        validate_oracle_coverage(questions, build_oracle_manifest((oracle_entry,)))


def test_oracle_must_cover_every_literature_evidence_group() -> None:
    gold = _literature_gold()
    second_group = EvidenceGroup(
        group_id="group-2",
        required_document_key=DOCUMENT_KEY,
        required_chunk_key=ARBITRARY_CHUNK_KEY,
    )
    gold = gold.model_copy(
        update={"evidence_groups": (*gold.evidence_groups, second_group)}
    )
    question, questions = _approved_question_and_manifest("literature", gold)
    oracle_entry = _approved_oracle_entry(
        question.question_id,
        question.question_text_sha256,
        literature_chunk_keys=(REQUIRED_CHUNK_KEY,),
    )

    with pytest.raises(AnnotationError, match="every required Gold evidence group"):
        validate_oracle_coverage(questions, build_oracle_manifest((oracle_entry,)))


def test_answerable_question_cannot_claim_no_oracle_evidence() -> None:
    gold = _literature_gold()
    question, questions = _approved_question_and_manifest("literature", gold)
    oracle_entry = _approved_oracle_entry(
        question.question_id,
        question.question_text_sha256,
        evidence_disposition="no_supporting_evidence",
    )

    with pytest.raises(AnnotationError, match="requires supplied oracle evidence"):
        validate_oracle_coverage(questions, build_oracle_manifest((oracle_entry,)))


def test_unsupported_question_requires_empty_no_evidence_oracle() -> None:
    gold = _unsupported_gold()
    question, questions = _approved_question_and_manifest("unsupported", gold)
    wrong = _approved_oracle_entry(
        question.question_id,
        question.question_text_sha256,
        literature_chunk_keys=(ARBITRARY_CHUNK_KEY,),
    )

    with pytest.raises(AnnotationError, match="no-supporting-evidence"):
        validate_oracle_coverage(questions, build_oracle_manifest((wrong,)))

    correct = _approved_oracle_entry(
        question.question_id,
        question.question_text_sha256,
        evidence_disposition="no_supporting_evidence",
    )
    validate_oracle_coverage(questions, build_oracle_manifest((correct,)))


def _approval(reviewer_key: str) -> HumanApproval:
    return HumanApproval(
        reviewer_key=reviewer_key,
        reviewed_at="2099-01-01T00:00:00Z",
        attestation=(
            "I independently reviewed this annotation and approve it for this benchmark."
        ),
    )


def _gold(
    *,
    release_key: str = DATASET_RELEASE_KEY,
    release_manifest_sha256: str = DATASET_MANIFEST_SHA256,
) -> StructuredGold:
    return StructuredGold(
        exact_count=1,
        metric_key="distinct_included_locus_count",
        release_key=release_key,
        release_manifest_sha256=release_manifest_sha256,
    )


def _literature_gold() -> LiteratureGold:
    return LiteratureGold(
        required_document_keys=(DOCUMENT_KEY,),
        evidence_groups=(
            EvidenceGroup(
                group_id="group-1",
                required_document_key=DOCUMENT_KEY,
                required_chunk_key=REQUIRED_CHUNK_KEY,
                acceptable_alternative_chunk_keys=(ALTERNATIVE_CHUNK_KEY,),
            ),
        ),
        excluded_chunk_keys=(EXCLUDED_CHUNK_KEY,),
        required_concepts=("The approved source association.",),
    )


def _unsupported_gold() -> UnsupportedGold:
    return UnsupportedGold(
        refusal_category="unsupported_biological_inference",
        prohibited_downstream_stages=("generation",),
        required_explanations=("The requested inference is unsupported.",),
        forbidden_claims=("The requested inference is established.",),
    )


def _approved_question_and_manifest(
    family: QuestionFamily,
    gold: StructuredGold | LiteratureGold | HybridGold | UnsupportedGold,
) -> tuple[EvaluationQuestion, QuestionManifest]:
    question = build_evaluation_question(
        question_id=f"{family}-001",
        family=family,
        question_text=f"Approved {family} question.",
        review_status="approved",
        approval=_approval("question-reviewer"),
        gold=gold,
    )
    manifest = build_question_manifest(
        (question,),
        dataset_release_key=DATASET_RELEASE_KEY,
        dataset_manifest_sha256=DATASET_MANIFEST_SHA256,
        corpus_release_key=CORPUS_RELEASE_KEY,
        corpus_manifest_sha256=CORPUS_MANIFEST_SHA256,
    )
    return question, manifest


def _approved_oracle_entry(
    question_id: str,
    question_text_sha256: str,
    *,
    evidence_disposition: str = "evidence_supplied",
    structured_facts: StructuredGold | None = None,
    literature_chunk_keys: tuple[str, ...] = (),
) -> OracleEvidenceEntry:
    return build_oracle_entry(
        question_id=question_id,
        question_text_sha256=question_text_sha256,
        review_status="approved",
        approval=_approval("oracle-reviewer"),
        evidence_disposition=evidence_disposition,
        structured_facts=structured_facts,
        literature_chunk_keys=literature_chunk_keys,
        dataset_release_key=DATASET_RELEASE_KEY,
        dataset_manifest_sha256=DATASET_MANIFEST_SHA256,
        corpus_release_key=CORPUS_RELEASE_KEY,
        corpus_manifest_sha256=CORPUS_MANIFEST_SHA256,
        source_attestation=(
            "Evidence was selected manually and not generated from model or retriever output."
        ),
    )
