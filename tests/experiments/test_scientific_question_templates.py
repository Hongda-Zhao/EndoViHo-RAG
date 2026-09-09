# ruff: noqa: E501 - exact preregistered wording is asserted inline.
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
        "source_taxon_association": 12,
        "viral_lineage_association": 12,
        "source_viral_lineage_association": 12,
        "assembly_locus_association": 12,
        "unsupported_scientific_or_operational_boundary": 16,
    }


def test_scientific_templates_are_pending_authoring_records_only() -> None:
    templates = build_scientific_question_templates()

    assert all(template.review_status == "pending" and template.gold is None for template in templates)
    assert Counter(template.capability_status for template in templates) == {
        "requires_v1_association_projection": 48,
        "unsupported_by_design": 16,
    }
    schema_properties = ScientificQuestionTemplate.model_json_schema()["properties"]
    assert {"approval", "oracle", "scores", "evidence"}.isdisjoint(schema_properties)


def test_placeholders_and_natural_question_wording_are_strict() -> None:
    for template in build_scientific_question_templates():
        observed = tuple(sorted(set(PLACEHOLDER_RE.findall(template.question_text_template))))
        assert observed == template.entity_slots
        assert "locus:eve:v1:sha256:" + "a" * 64 not in template.question_text_template
        assert ". and explain the literature" not in template.question_text_template.casefold()


def test_answerable_questions_follow_the_four_dimension_v1_chain() -> None:
    answerable = tuple(
        template for template in build_scientific_question_templates() if template.family != "unsupported"
    )

    assert len(answerable) == 48
    for template in answerable:
        text = template.question_text_template
        normalized = text.casefold()
        assert "Transferred gene" not in text
        assert "Integrated virus" not in text
        assert any(marker in normalized for marker in ("eve locus", "eve loci", "eve-locus", "viral region", "viral-region"))
        assert "viral-lineage" in normalized or "{viral_lineage_" in normalized
        assert "evidence" in normalized
        if template.family in {"structured", "hybrid"}:
            assert not any(marker in normalized for marker in ("host species", "host-species", "host taxonomic"))


def test_answerable_templates_preserve_annotations_without_classification() -> None:
    required_capabilities = {
        "association_projection",
        "evidence_source_preservation",
        "lineage_role_and_scope_preservation",
        "locus_region_identity_preservation",
        "source_annotation_preservation",
    }
    answerable = tuple(
        template for template in build_scientific_question_templates() if template.family != "unsupported"
    )

    for template in answerable:
        assert required_capabilities.issubset(template.required_capabilities)
        assert template.capability_status == "requires_v1_association_projection"
        assert "does not require a Transferred gene or Integrated virus classification" in template.authoring_notes
        assert "HCVR, VR Type, and Viral Major Taxon" in template.authoring_notes
        assert "forbidden_claims" in template.expected_output_types
        assert "required_limitations" in template.expected_output_types
        if template.family == "structured":
            assert "release_represented_source_scope" in template.required_capabilities
            assert "corpus_source_reported_scope" not in template.required_capabilities
            assert "assembly-source taxon is not an ancient or modern host" in template.authoring_notes
        elif template.family == "literature":
            assert "corpus_source_reported_scope" in template.required_capabilities
            assert "release_represented_source_scope" not in template.required_capabilities
            assert "permitted-corpus source-reported taxon wording" in template.authoring_notes
        else:
            assert {"corpus_source_reported_scope", "release_represented_source_scope"}.issubset(template.required_capabilities)
            assert "must not overwrite structured values" in template.authoring_notes


def test_family_specific_association_outputs_remain_separate() -> None:
    existing_structured_primitives = {"list_assemblies", "list_loci", "list_source_taxa", "locus_detail"}

    for template in build_scientific_question_templates():
        if template.family in {"structured", "hybrid"}:
            assert existing_structured_primitives.intersection(template.required_capabilities)
        if template.family == "structured":
            assert "exact_association_set" in template.expected_output_types
            assert "source_reported_association_set" not in template.expected_output_types
        if template.family in {"literature", "hybrid"}:
            assert "required_documents" in template.expected_output_types
            assert "required_evidence_groups" in template.expected_output_types
            assert "source_reported_association_set" in template.expected_output_types
        if template.family == "literature":
            assert not existing_structured_primitives.intersection(template.required_capabilities)
            assert not any(output.startswith("exact_") for output in template.expected_output_types)
            assert "cross_source_association_set" not in template.expected_output_types
        if template.family == "hybrid":
            assert {"exact_association_set", "cross_source_association_set"}.issubset(template.expected_output_types)
            assert "cross_source_association_alignment" in template.required_capabilities


def test_unsupported_questions_cover_unsafe_mapping_and_operational_boundaries() -> None:
    unsupported_text = "\n".join(
        template.question_text_template
        for template in build_scientific_question_templates()
        if template.family == "unsupported"
    )

    for required_term in (
        "no relation-class dimension",
        "HCVR",
        "Integration",
        "Viral contig",
        "study-defined, formal, and extended viral-lineage roles",
        "name similarity alone",
        "every taxon within {SOURCE_TAXON_LINEAGE_A}",
        "unapproved or unversioned releases and corpora",
        "first page or a truncated result",
        "live web",
        "BLAST",
        "HMMER",
        "arbitrary SQL",
    ):
        assert required_term in unsupported_text


def test_committed_scientific_authoring_artifacts_are_canonical() -> None:
    question_path = BENCHMARK_DIRECTORY / "scientific_questions_template.jsonl"
    binding_path = BENCHMARK_DIRECTORY / "scientific_entity_bindings_template.json"

    assert question_path.read_bytes() == scientific_questions_template_bytes()
    assert binding_path.read_bytes() == scientific_entity_bindings_template_bytes()
    assert len(question_path.read_text(encoding="utf-8").splitlines()) == 64
    assert all(
        ScientificQuestionTemplate.model_validate_json(line)
        for line in question_path.read_text(encoding="utf-8").splitlines()
    )
    assert ScientificEntityBindingsTemplate.model_validate_json(binding_path.read_bytes()) == build_scientific_entity_bindings_template()


def test_binding_template_is_empty_pending_and_complete() -> None:
    manifest = build_scientific_entity_bindings_template()

    assert manifest.binding_count == len(manifest.bindings) == 10
    assert {binding.required_entity_type for binding in manifest.bindings} == {
        "assembly",
        "assembly_source_taxon",
        "eve_locus",
        "reported_viral_region",
        "source_lineage",
        "viral_lineage",
    }
    for binding in manifest.bindings:
        assert binding.review_status == "pending"
        assert binding.selected_stable_key is None
        assert binding.selected_display_name is None
        assert binding.release_key is None


def test_rehashed_template_cannot_change_preregistered_content_or_metadata() -> None:
    original = build_scientific_question_templates()[0]
    changed_text = original.model_dump(mode="python")
    changed_text["question_text_template"] = "Which other EVE loci have viral-lineage affinities and evidence sources?"
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
    changed["required_entity_type"] = "eve_locus"
    del changed["record_sha256"]
    changed["record_sha256"] = canonical_json_sha256(changed)

    with pytest.raises(ValidationError, match="slot and required entity type"):
        type(original).model_validate(changed)


def test_natural_templates_are_not_silently_claimed_by_current_routes() -> None:
    router = DeterministicRouter()
    planner = ControlledEnglishPlanner()
    resolver = CatalogReleaseResolver(release_key=RELEASE_KEY)

    for template in build_scientific_question_templates():
        request_values = {"question": template.question_text_template}
        if template.family in {"structured", "hybrid"}:
            request_values["release_key"] = RELEASE_KEY
        if template.family in {"literature", "hybrid"}:
            request_values["corpus_release_key"] = CORPUS_KEY
        try:
            request = RagQueryRequest(**request_values)
        except ValidationError:
            assert any(ord(character) > 127 for character in template.question_text_template)
            continue
        decision = router.route(request)
        if decision.route == "structured":
            planned = planner.plan(
                StructuredQueryRequest(release_key=RELEASE_KEY, question=template.question_text_template),
                resolver,
            )
            assert isinstance(planned, ErrorResponse)
        else:
            assert decision.route == "unsupported"


def test_legacy_route_questions_are_preserved_byte_for_byte() -> None:
    raw = (SYSTEM_REGRESSION_DIRECTORY / "rag_value_route_questions_v1.jsonl").read_bytes()

    assert len(raw.splitlines()) == 64
    assert hashlib.sha256(raw).hexdigest() == LEGACY_QUESTION_SHA256


def test_human_readable_docs_cover_every_exact_question_and_capability_row() -> None:
    redesign = (REPOSITORY_ROOT / "docs" / "scientific_question_redesign.md").read_text(encoding="utf-8")
    capability_gap = (REPOSITORY_ROOT / "docs" / "scientific_question_capability_gap.md").read_text(encoding="utf-8")

    for template in build_scientific_question_templates():
        assert redesign.count(f"- `{template.template_id}` — {template.question_text_template}") == 1
        assert capability_gap.count(f"| {template.template_id} |") == 1


def test_template_generation_does_not_mutate_production_sources() -> None:
    before = capture_production_source_fingerprint(REPOSITORY_ROOT)

    scientific_questions_template_bytes()
    scientific_entity_bindings_template_bytes()

    assert_production_sources_unchanged(before, capture_production_source_fingerprint(REPOSITORY_ROOT))
