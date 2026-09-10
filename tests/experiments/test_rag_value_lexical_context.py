"""Question-local object grouping and soft context, without corpus/model fixtures."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace

import pytest

from eve_relation_rag.experiments.rag_value_ablation.lexical_context import (
    LEXICAL_CONTEXT_POLICY_KEY,
    MAX_CONTEXT_TERMS,
    MAX_RETRIEVAL_OBJECTS,
    build_lexical_context,
)
from eve_relation_rag.experiments.rag_value_ablation.lexical_query import (
    LexicalQueryPlan,
    SourceSpan,
    plan_lexical_query,
)
from eve_relation_rag.literature.hashing import canonical_json_sha256


def _rebind(plan: LexicalQueryPlan) -> LexicalQueryPlan:
    payload = plan.to_dict()
    payload.pop("plan_sha")
    return replace(plan, plan_sha=canonical_json_sha256(payload))


def test_species_strain_and_gene_share_object_with_local_numeric_context():
    question = (
        "Compare Chlamydomonas incerta GEVE (475 kb, middle scaffold); "
        "Bigelowiella natans CCMP2755 VLE (33.3 kb, proviral region); "
        "and Homo sapiens ERVWE1 (7q21.2); report evidence sources."
    )
    lexical = plan_lexical_query(question)
    result = build_lexical_context(question, lexical)
    assert result.status == "ready"
    assert len(result.objects) == 3
    assert [len(obj.group_keys) for obj in result.objects] == [1, 2, 2]
    assert "475" in result.objects[0].context_terms
    assert "scaffold" in result.objects[0].context_terms
    assert "33.3" in result.objects[1].context_terms
    assert "proviral" in result.objects[1].context_terms
    assert "7q21.2" in result.objects[2].context_terms
    assert "33.3" not in result.objects[0].context_terms
    assert "475" not in result.objects[1].context_terms
    assert "ccmp2755" not in result.objects[1].context_terms
    for obj in result.objects:
        assert not set(obj.context_terms) & {"compare", "report", "evidence", "sources"}
        for span in obj.source_spans:
            assert question[span.start:span.end] == span.text


@pytest.mark.parametrize("separator", [",", ";", "，", "；", " and ", " or "])
def test_top_level_separators_make_objects_but_bracketed_separators_do_not(separator):
    question = f"Arabidopsis thaliana FLC (stem, leaf and root){separator}Homo sapiens BRCA1"
    result = build_lexical_context(question, plan_lexical_query(question))
    assert result.status == "ready"
    assert len(result.objects) == 2
    assert [len(obj.group_keys) for obj in result.objects] == [2, 2]
    assert {"stem", "leaf", "root"} <= set(result.objects[0].context_terms)
    assert not set(result.objects[1].context_terms) & {"stem", "leaf", "root"}


def test_single_object_uses_whole_question_and_excludes_metadata_and_all_anchor_terms():
    question = (
        "List Arabidopsis thaliana FLC (taxid 12345; frozen taxdump snapshot), "
        "the 33.3 kb scaffold, and evidence sources in JSON version 2."
    )
    lexical = plan_lexical_query(question)
    result = build_lexical_context(question, lexical)
    assert result.status == "ready"
    assert len(result.objects) == 1
    obj = result.objects[0]
    assert obj.context_terms == ("33.3", "kb", "scaffold")
    assert obj.source_spans == (SourceSpan(0, len(question), question),)
    assert set(obj.group_keys) == {group.group_key for group in lexical.groups}


def test_identifier_groups_remain_identifiable_and_doi_separator_is_not_split():
    question = "Compare GCA_123456789.1 DOI 10.1234/example/and/data with proviral scaffold."
    lexical = plan_lexical_query(question)
    result = build_lexical_context(question, lexical)
    assert result.status == "ready"
    assert len(result.objects) == 1
    assert len(result.objects[0].group_keys) == 2
    assert {group.identifier for group in lexical.groups} == {
        "GCA_123456789.1", "10.1234/example/and/data",
    }
    assert result.objects[0].context_terms == ("proviral", "scaffold")


def test_entity_free_prefix_does_not_make_object_and_species_keeps_accession_group():
    question = (
        "Which taxon and viral-lineage affinity for Homo sapiens GCA_999999999.9 "
        "at 7q21.2, grouped by evidence sources?"
    )
    lexical = plan_lexical_query(question)
    result = build_lexical_context(question, lexical)
    assert result.status == "ready"
    assert len(result.objects) == 1
    assert len(result.objects[0].group_keys) == 2
    assert "7q21.2" in result.objects[0].context_terms
    assert "gca_999999999.9" not in result.objects[0].context_terms
    assert {group.identifier for group in lexical.groups if group.identifier} == {
        "GCA_999999999.9",
    }


def test_repeated_group_can_belong_to_separate_objects_with_distinct_context():
    question = "Homo sapiens BRCA1 scaffold or Homo sapiens TP53 proviral region"
    lexical = plan_lexical_query(question)
    result = build_lexical_context(question, lexical)
    assert len(result.objects) == 2
    shared = set(result.objects[0].group_keys) & set(result.objects[1].group_keys)
    assert len(shared) == 1
    assert result.objects[0].context_terms == ("scaffold",)
    assert result.objects[1].context_terms == ("proviral", "region")


def test_topic_only_fallback_preserves_one_group_without_turning_clauses_into_objects():
    question = "Explain viral evolution and gene transfer."
    lexical = plan_lexical_query(question)
    result = build_lexical_context(question, lexical)
    assert result.status == "ready"
    assert len(result.objects) == 1
    assert lexical.groups[0].kind == "topic"
    assert result.objects[0].group_keys == (lexical.groups[0].group_key,)
    assert result.objects[0].context_terms == ()
    assert result.objects[0].source_spans == (SourceSpan(0, len(question), question),)
    assert result.warnings == ()


def test_object_and_plan_hashes_are_deterministic_bound_and_dataclasses_are_frozen():
    question = "Arabidopsis thaliana FLC (33.3 kb)"
    lexical = plan_lexical_query(question)
    result = build_lexical_context(question, lexical)
    assert result == build_lexical_context(question, lexical)
    assert result.policy_key == LEXICAL_CONTEXT_POLICY_KEY
    assert result.lexical_plan_sha == lexical.plan_sha
    payload = result.to_dict()
    assert payload.pop("context_sha") == canonical_json_sha256(payload)
    with pytest.raises(FrozenInstanceError):
        result.objects[0].context_terms = ()


def test_plan_question_hash_and_source_span_tampering_reject():
    question = "BRCA1 scaffold"
    lexical = plan_lexical_query(question)
    wrong_question = build_lexical_context(question + "!", lexical)
    assert wrong_question.warnings == ("question_sha_mismatch",)
    wrong_hash = build_lexical_context(question, replace(lexical, plan_sha="0" * 64))
    assert wrong_hash.warnings == ("lexical_plan_sha_mismatch",)
    forged = replace(lexical.groups[0], source_spans=(SourceSpan(0, 5, "TP53!"),))
    wrong_span = build_lexical_context(question, _rebind(replace(lexical, groups=(forged,))))
    assert wrong_span.status == "rejected"
    assert wrong_span.warnings == ("invalid_group_source_span",)


@pytest.mark.parametrize("question", ["BRCA1 (scaffold", "BRCA1 scaffold)"])
def test_unbalanced_brackets_are_an_explicit_rejection(question):
    result = build_lexical_context(question, plan_lexical_query(question))
    assert result.status == "rejected"
    assert result.objects == ()
    assert result.warnings == ("unbalanced_question_brackets",)


def test_context_and_object_limits_reject_in_full_without_truncation():
    question = "BRCA1 " + " ".join("topic" + "x" * i for i in range(MAX_CONTEXT_TERMS + 1))
    result = build_lexical_context(question, plan_lexical_query(question))
    assert result.status == "rejected"
    assert result.objects == ()
    assert result.warnings == ("too_many_context_terms",)
    question = "; ".join("BRCA1 scaffold" for _ in range(MAX_RETRIEVAL_OBJECTS + 1))
    result = build_lexical_context(question, plan_lexical_query(question))
    assert result.status == "rejected"
    assert result.objects == ()
    assert result.warnings == ("too_many_retrieval_objects",)
