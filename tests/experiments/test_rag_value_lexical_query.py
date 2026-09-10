"""Question-only lexical planning, without retrieval or biological answer fixtures."""

from __future__ import annotations

import hashlib

import pytest

from eve_relation_rag.experiments.rag_value_ablation.lexical_query import (
    LEXICAL_QUERY_POLICY_KEY,
    MAX_ENTITY_LABELS,
    MAX_QUESTION_CHARACTERS,
    plan_lexical_query,
)
from eve_relation_rag.literature.hashing import canonical_json_sha256


def test_entities_are_separate_from_snapshot_and_answer_instructions():
    question = (
        "Which taxa within Chrysomelidae (NCBI taxid 12345; frozen taxdump snapshot) "
        "have affinity to Baculoviridae? Group by evidence source and display uncertainty."
    )
    plan = plan_lexical_query(question)
    assert plan.status == "ready"
    assert [group.terms for group in plan.groups] == [("baculoviridae",), ("chrysomelidae",)]
    assert plan.question_text_sha256 == hashlib.sha256(question.encode()).hexdigest()
    for group in plan.groups:
        for span in group.source_spans:
            assert question[span.start:span.end] == span.text


def test_multiple_species_genes_and_identifiers_are_independent_groups():
    plan = plan_lexical_query(
        "Compare Arabidopsis thaliana FLC with Homo sapiens BRCA1; cite DOI "
        "10.1000/example-test and assembly GCF_123456789.2."
    )
    assert plan.status == "ready"
    assert {group.terms for group in plan.groups} == {
        ("arabidopsis", "thaliana"), ("homo", "sapiens"), ("flc",), ("brca1",),
        ("10.1000/example-test",), ("GCF_123456789.2",),
    }
    assert {group.identifier for group in plan.groups if group.identifier} == {
        "10.1000/example-test", "GCF_123456789.2",
    }


def test_three_regions_do_not_spend_entity_slots_on_generic_region_acronyms():
    plan = plan_lexical_query(
        "Compare Chlamydomonas incerta GEVE（约475 kb）, Homo sapiens ERVWE1, "
        "and Bigelowiella natans CCMP2755 VLE; list evidence sources."
    )
    assert plan.status == "ready"
    assert len(plan.groups) == 5
    assert not any(group.terms in {("eve",), ("geve",), ("vle",)} for group in plan.groups)


def test_abbreviated_genus_is_not_expanded_using_external_knowledge():
    plan = plan_lexical_query("Describe C. elegans and HERV-W.")
    assert {group.terms for group in plan.groups} == {("elegans",), ("herv", "w")}
    assert all("caenorhabditis" not in group.query_text for group in plan.groups)
    assert any(span.text == "C. elegans" for group in plan.groups for span in group.source_spans)


def test_unversioned_accession_and_sentence_punctuation_preserve_identifier():
    plan = plan_lexical_query("Find NM_123456 and GCF_123456789.2.")
    assert {group.identifier for group in plan.groups} == {"NM_123456", "GCF_123456789.2"}
    assert all(group.kind == "accession" for group in plan.groups)


def test_generic_region_and_sentence_capitalization_do_not_create_entities():
    plan = plan_lexical_query("Classify Transferred gene or Integrated virus for EVE-locus.")
    assert plan.status == "ready"
    assert len(plan.groups) == 1 and plan.groups[0].kind == "topic"
    assert plan.groups[0].terms == ("eve", "gene", "integrated", "locus", "transferred", "virus")


def test_visible_label_cannot_smuggle_in_answer_instructions_or_external_entity():
    question = "Compare Felidae frozen taxdump snapshot with Canidae."
    plan = plan_lexical_query(question, entity_labels=("Felidae frozen taxdump snapshot",))
    assert {group.terms for group in plan.groups} == {("felidae",), ("canidae",)}
    refused = plan_lexical_query(question, entity_labels=("Hominidae",))
    assert refused.status == "rejected"
    assert refused.groups == ()
    assert refused.warnings == ("entity_label_not_in_question",)


def test_label_matching_respects_word_boundaries_and_preserves_question_whitespace():
    question = "Compare Arabidopsis\tthaliana and TP53."
    plan = plan_lexical_query(question, entity_labels=("Arabidopsis thaliana",))
    assert plan.status == "ready"
    spans = [span for group in plan.groups for span in group.source_spans]
    assert any(span.text == "Arabidopsis\tthaliana" for span in spans)
    assert all(question[span.start:span.end] == span.text for span in spans)
    assert plan_lexical_query("BRCA12", entity_labels=("BRCA1",)).status == "rejected"


def test_labels_deduplicate_and_plan_is_deterministic_and_checksum_bound():
    question = "Compare BRCA1 with TP53 and repeat BRCA1."
    first = plan_lexical_query(question, entity_labels=("BRCA1", "TP53", "BRCA1"))
    second = plan_lexical_query(question, entity_labels=("TP53", "BRCA1"))
    assert first == second
    assert len(first.groups) == 2
    assert len(first.groups[0].source_spans) == 2
    assert first.policy_key == LEXICAL_QUERY_POLICY_KEY
    dumped = first.to_dict()
    assert dumped.pop("plan_sha") == canonical_json_sha256(dumped)
    assert first.plan_sha256 == first.plan_sha


def test_hyphenated_topics_are_plain_conjunctive_text_without_websearch_operators():
    plan = plan_lexical_query("Explain viral-lineage evolution or gene-transfer mechanisms.")
    assert plan.status == "ready"
    assert len(plan.groups) == 1 and plan.groups[0].kind == "topic"
    assert plan.groups[0].terms == (
        "evolution", "gene", "lineage", "mechanisms", "transfer", "viral",
    )
    assert plan.groups[0].query_text == "evolution gene lineage mechanisms transfer viral"
    assert plan.warnings == ("topic_keyword_fallback",)


@pytest.mark.parametrize(("question", "warning"), [
    ("  ", "empty_question"),
    ("a" * (MAX_QUESTION_CHARACTERS + 1), "question_too_long"),
    ("BRCA1\x00", "unsupported_control_character"),
    (" ".join(f"GENE{i}" for i in range(9)), "too_many_query_groups"),
    ("alpha beta gamma delta epsilon zeta theta", "too_many_topic_terms"),
])
def test_out_of_bounds_plans_reject_in_full_without_silent_truncation(question, warning):
    plan = plan_lexical_query(question)
    assert plan.status == "rejected"
    assert plan.groups == ()
    assert warning in plan.warnings


def test_label_and_group_limits_are_explicit():
    plan = plan_lexical_query("BRCA1", entity_labels=("BRCA1",) * (MAX_ENTITY_LABELS + 1))
    assert plan.status == "rejected" and plan.warnings == ("too_many_entity_labels",)
    label = "alpha beta gamma delta epsilon zeta theta iota kappa"
    plan = plan_lexical_query(label, entity_labels=(label,))
    assert plan.status == "rejected" and plan.warnings == ("too_many_group_terms",)


def test_presentation_only_question_has_no_usable_terms():
    plan = plan_lexical_query("List sources and evidence, grouped by frozen snapshot.")
    assert plan.status == "no_usable_terms"
    assert plan.groups == ()
    assert "no_usable_terms" in plan.warnings
