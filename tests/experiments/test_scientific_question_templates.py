from __future__ import annotations

import hashlib
import re
from collections import Counter
from pathlib import Path

import pytest
from pydantic import ValidationError

from eve_relation_rag.experiments.embedding_ablation.source_guard import (
    assert_production_sources_unchanged,
    capture_production_source_fingerprint,
)
from eve_relation_rag.experiments.rag_value_ablation.scientific_questions import (
    ScientificEntityBindingsTemplate,
    ScientificQuestionTemplate,
    build_scientific_entity_bindings_template,
    build_scientific_question_templates,
    scientific_entity_bindings_template_bytes,
    scientific_questions_template_bytes,
)
from eve_relation_rag.hybrid.contracts import RagQueryRequest
from eve_relation_rag.literature.hashing import canonical_json_sha256
from eve_relation_rag.planning.parser import ControlledEnglishPlanner, StructuredQueryRequest
from eve_relation_rag.planning.resolver import CatalogReleaseResolver
from eve_relation_rag.planning.router import DeterministicRouter
from eve_relation_rag.retrieval.structured.results import ErrorResponse

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
BENCHMARK_DIRECTORY = REPOSITORY_ROOT / "benchmark" / "rag_value_ablation"
SYSTEM_REGRESSION_DIRECTORY = REPOSITORY_ROOT / "benchmark" / "system_regression"
RELEASE_KEY = "release:endoviho-rag:v0:20990101:001"
CORPUS_KEY = "corpus:endoviho-rag:v0:20990101:001"
LEGACY_QUESTION_SHA256 = "9763b6bda2074fbc73aaf2347e9bf2d4153e3a13a5952ba8edfe623d912ebd34"
PLACEHOLDER_RE = re.compile(r"\{([A-Z][A-Z0-9_]*)\}")


def test_scientific_templates_have_exact_family_and_task_balance() -> None:
    templates = build_scientific_question_templates()

    assert len(templates) == 64
    assert Counter(template.family for template in templates) == {
        "structured": 16,
        "literature": 16,
        "hybrid": 16,
        "unsupported": 16,
    }
    assert Counter(template.scientific_task for template in templates) == {
        "host_eve_profile": 12,
        "viral_lineage_distribution": 12,
        "host_virus_relationship": 12,
        "assembly_locus_evidence": 12,
        "unsupported_scientific_or_operational_boundary": 16,
    }
    assert len({template.template_id for template in templates}) == 64


def test_scientific_templates_are_pending_authoring_records_only() -> None:
    templates = build_scientific_question_templates()

    assert all(template.review_status == "pending" for template in templates)
    assert all(template.gold is None for template in templates)
    assert all(template.capability_status != "supported_now" for template in templates)
    assert Counter(template.capability_status for template in templates) == {
        "requires_natural_structured_planning": 10,
        "requires_natural_literature_routing": 16,
        "requires_new_intent": 2,
        "requires_composite_plan": 3,
        "requires_natural_hybrid_decomposition": 16,
        "future_only": 1,
        "unsupported_by_design": 16,
    }
    schema_properties = ScientificQuestionTemplate.model_json_schema()["properties"]
    assert "approval" not in schema_properties
    assert "oracle" not in schema_properties
    assert "scores" not in schema_properties
    assert "evidence" not in schema_properties


def test_placeholders_and_natural_question_wording_are_strict() -> None:
    templates = build_scientific_question_templates()
    fake_locus = (
        "locus:eve:v1:sha256:"
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    )

    for template in templates:
        observed = tuple(sorted(set(PLACEHOLDER_RE.findall(template.question_text_template))))
        assert observed == template.entity_slots
        assert fake_locus not in template.question_text_template
        assert ". and explain the literature" not in template.question_text_template.casefold()


def test_committed_scientific_authoring_artifacts_are_canonical() -> None:
    question_path = BENCHMARK_DIRECTORY / "scientific_questions_template.jsonl"
    binding_path = BENCHMARK_DIRECTORY / "scientific_entity_bindings_template.json"

    assert question_path.read_bytes() == scientific_questions_template_bytes()
    assert binding_path.read_bytes() == scientific_entity_bindings_template_bytes()
    question_lines = question_path.read_text(encoding="utf-8").splitlines()
    assert len(question_lines) == 64
    assert all(ScientificQuestionTemplate.model_validate_json(line) for line in question_lines)
    assert ScientificEntityBindingsTemplate.model_validate_json(binding_path.read_bytes()) == (
        build_scientific_entity_bindings_template()
    )


def test_binding_template_is_empty_pending_and_complete() -> None:
    manifest = build_scientific_entity_bindings_template()

    assert manifest.binding_count == len(manifest.bindings) == 11
    assert len({binding.entity_slot for binding in manifest.bindings}) == 11
    assert all(binding.review_status == "pending" for binding in manifest.bindings)
    for binding in manifest.bindings:
        assert binding.selected_stable_key is None
        assert binding.selected_display_name is None
        assert binding.release_key is None
        assert binding.release_manifest_sha256 is None
        assert binding.selected_snapshot_key is None
        assert binding.selected_lineage_role is None
        assert binding.include_descendants is None


def test_rehashed_template_cannot_change_preregistered_content_or_metadata() -> None:
    original = build_scientific_question_templates()[0]

    changed_text = original.model_dump(mode="python")
    changed_text["question_text_template"] = "What other records are present?"
    changed_text["entity_slots"] = ()
    del changed_text["record_sha256"]
    changed_text["record_sha256"] = canonical_json_sha256(changed_text)
    with pytest.raises(ValidationError, match="differs from preregistered content"):
        ScientificQuestionTemplate.model_validate(changed_text)

    changed_metadata = original.model_dump(mode="python")
    changed_metadata["family"] = "literature"
    del changed_metadata["record_sha256"]
    changed_metadata["record_sha256"] = canonical_json_sha256(changed_metadata)
    with pytest.raises(ValidationError, match="family, scientific task, and intent"):
        ScientificQuestionTemplate.model_validate(changed_metadata)


def test_rehashed_binding_cannot_change_slot_entity_type_mapping() -> None:
    original = build_scientific_entity_bindings_template().bindings[0]
    changed = original.model_dump(mode="python")
    changed["required_entity_type"] = "locus"
    del changed["record_sha256"]
    changed["record_sha256"] = canonical_json_sha256(changed)

    with pytest.raises(ValidationError, match="slot and required entity type"):
        type(original).model_validate(changed)


def test_hybrid_limit_question_requires_literature_gold_fields() -> None:
    template = next(
        item for item in build_scientific_question_templates() if item.template_id == "REL-H-02"
    )

    assert "required_documents" in template.expected_output_types
    assert "required_evidence_groups" in template.expected_output_types


def test_natural_templates_are_not_silently_claimed_by_current_routes() -> None:
    router = DeterministicRouter()
    planner = ControlledEnglishPlanner()
    resolver = CatalogReleaseResolver(release_key=RELEASE_KEY)

    for template in build_scientific_question_templates():
        try:
            if template.family == "structured":
                request = RagQueryRequest(
                    release_key=RELEASE_KEY,
                    question=template.question_text_template,
                )
            elif template.family == "literature":
                request = RagQueryRequest(
                    corpus_release_key=CORPUS_KEY,
                    question=template.question_text_template,
                )
            else:
                request = RagQueryRequest(
                    release_key=RELEASE_KEY,
                    corpus_release_key=CORPUS_KEY,
                    question=template.question_text_template,
                )
        except ValidationError:
            assert any(ord(character) > 127 for character in template.question_text_template)
            continue
        decision = router.route(request)
        if decision.route == "structured":
            planned = planner.plan(
                StructuredQueryRequest(
                    release_key=RELEASE_KEY,
                    question=template.question_text_template,
                ),
                resolver,
            )
            assert isinstance(planned, ErrorResponse)
        else:
            assert decision.route == "unsupported"


def test_legacy_route_questions_are_preserved_byte_for_byte() -> None:
    path = SYSTEM_REGRESSION_DIRECTORY / "rag_value_route_questions_v1.jsonl"
    raw = path.read_bytes()

    assert len(raw.splitlines()) == 64
    assert hashlib.sha256(raw).hexdigest() == LEGACY_QUESTION_SHA256


def test_human_readable_docs_cover_every_exact_question_and_capability_row() -> None:
    redesign = (REPOSITORY_ROOT / "docs" / "scientific_question_redesign.md").read_text(
        encoding="utf-8"
    )
    capability_gap = (
        REPOSITORY_ROOT / "docs" / "scientific_question_capability_gap.md"
    ).read_text(encoding="utf-8")

    for template in build_scientific_question_templates():
        assert f"`{template.template_id}` — {template.question_text_template}" in redesign
        assert f"| {template.template_id} |" in capability_gap


def test_template_generation_does_not_mutate_production_sources() -> None:
    before = capture_production_source_fingerprint(REPOSITORY_ROOT)

    scientific_questions_template_bytes()
    scientific_entity_bindings_template_bytes()

    after = capture_production_source_fingerprint(REPOSITORY_ROOT)
    assert_production_sources_unchanged(before, after)
