from __future__ import annotations

import hashlib
from collections import Counter
from pathlib import Path

import pytest

from eve_relation_rag.experiments.rag_value_ablation.system_regression import (
    SYSTEM_REGRESSION_QUESTIONS_PATH,
    SYSTEM_REGRESSION_SOURCE_SHA256,
    SystemRegressionError,
    SystemRegressionQuestion,
    audit_system_regression_questions,
    load_system_regression_questions,
    system_regression_audit_bytes,
    system_regression_questions_bytes,
)
from eve_relation_rag.literature.hashing import canonical_json_bytes, canonical_json_sha256


def test_frozen_legacy_questions_use_the_isolated_contract() -> None:
    questions = load_system_regression_questions()

    assert len(questions) == 64
    assert all(isinstance(question, SystemRegressionQuestion) for question in questions)
    assert Counter(question.family for question in questions) == {
        "structured": 16,
        "literature": 16,
        "hybrid": 16,
        "unsupported": 16,
    }
    assert all(
        question.review_status == "pending"
        and question.approval is None
        and question.gold is None
        for question in questions
    )


def test_route_and_parser_audit_is_complete_and_non_gold() -> None:
    audit = audit_system_regression_questions()

    assert audit.artifact_status == "system_regression_only_not_gold"
    assert audit.question_count == 64
    assert audit.family_counts == {
        "hybrid": 16,
        "literature": 16,
        "structured": 16,
        "unsupported": 16,
    }
    assert audit.route_counts == audit.family_counts
    assert audit.pending_count == 64
    assert audit.parser_applicable_count == 32
    assert audit.parser_accepted_count == 32
    assert audit.parser_rejection_count == 0
    assert audit.route_mismatch_count == 0
    assert audit.gold_annotation_count == 0


def test_frozen_source_and_all_outputs_are_canonical_and_checksum_bound() -> None:
    source = Path(SYSTEM_REGRESSION_QUESTIONS_PATH).read_bytes()
    questions = load_system_regression_questions()
    audit = audit_system_regression_questions(questions)

    assert hashlib.sha256(source).hexdigest() == SYSTEM_REGRESSION_SOURCE_SHA256
    assert system_regression_questions_bytes(questions) == source
    assert audit.source_sha256 == SYSTEM_REGRESSION_SOURCE_SHA256
    assert audit.question_set_sha256 == canonical_json_sha256(questions)

    audit_payload = audit.model_dump(mode="python")
    del audit_payload["audit_sha256"]
    assert audit.audit_sha256 == canonical_json_sha256(audit_payload)
    assert system_regression_audit_bytes() == canonical_json_bytes(audit) + b"\n"


def test_rehashed_regression_row_cannot_replace_frozen_source_content() -> None:
    questions = list(load_system_regression_questions())
    changed = questions[0].model_dump(mode="python")
    changed["authoring_notes"] = "Changed regression fixture."
    del changed["record_sha256"]
    changed["record_sha256"] = canonical_json_sha256(changed)
    questions[0] = SystemRegressionQuestion.model_validate(changed)

    with pytest.raises(SystemRegressionError, match="differs from the frozen source"):
        audit_system_regression_questions(questions)
