"""Observed model failures, schema parity and evidence checks; no model or database calls."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from eve_relation_rag.experiments.rag_value_ablation.answer_validation import validate_answer_output
from eve_relation_rag.experiments.rag_value_ablation.contracts import (
    EvaluationAnswer,
    EvaluationEvidencePack,
    EvidenceCitation,
    build_evidence_pack,
)
from eve_relation_rag.experiments.rag_value_ablation.metrics import (
    structured_prediction_from_answer,
)
from eve_relation_rag.experiments.rag_value_ablation.prompting import build_prompt_policy

FIXTURE = Path(__file__).parents[1] / "fixtures/rag_value/local_answers_v1.json"
RECORDS = {r["system_key"]: r for r in json.loads(FIXTURE.read_text())["records"]}


def evidence(system="S5"):
    return EvaluationEvidencePack.model_validate_json(json.dumps(RECORDS[system]["evidence"]))


def check(payload, pack=None):
    return validate_answer_output(json.dumps(payload), evidence() if pack is None else pack)


def minimal_count():
    return {
        "answer_text": "The release contains 2 distinct included loci.",
        "abstained": False,
        "claims": [{
            "claim_id": "C1", "claim_type": "structured_fact", "text": "There are 2 loci.",
        }],
        "structured_facts": {"exact_count": 2, "metric_key": "distinct_included_locus_count"},
    }


@pytest.mark.parametrize("system", tuple(RECORDS))
def test_observed_answers_agree_between_public_schema_and_backend(system):
    raw = RECORDS[system]["raw_answer"]
    schema = Draft202012Validator(EvaluationAnswer.model_json_schema())
    public_errors = list(schema.iter_errors(json.loads(raw)))
    checked = validate_answer_output(raw, evidence(system))
    assert bool(public_errors) == (system != "S0")
    assert (checked.answer is None) == bool(public_errors)
    assert checked.report.semantic_support_status == "not_assessed"
    if system != "S0":
        assert checked.report.issues


def test_count_only_needs_no_invented_optional_fields_and_detects_wrong_count():
    payload = minimal_count()
    Draft202012Validator(EvaluationAnswer.model_json_schema()).validate(payload)
    checked = check(payload)
    assert not checked.report.issues
    assert checked.report.structured_evidence_status == "passed"
    assert checked.report.semantic_support_status == "not_assessed"
    payload["structured_facts"]["exact_count"] = 3
    assert check(payload).report.issues[0].code == "structured_pair_not_supported"


@pytest.mark.parametrize("system", ("S5", "S6"))
def test_fixing_envelope_does_not_accept_fabricated_details_or_associations(system):
    payload = json.loads(RECORDS[system]["raw_answer"])
    payload["structured_facts"]["release_manifest_sha256"] = "a" * 64
    checked = check(payload, evidence(system))
    assert checked.answer is not None
    assert checked.mechanical.passed
    assert checked.report.structured_evidence_status == "failed"
    bad_fields = {issue.path[-1] for issue in checked.report.issues}
    assert {"coordinates", "exact_association_set", "record_keys", "locus_keys"} <= bad_fields


def test_removing_s1_wrong_chunk_ids_does_not_hide_invented_identifiers():
    payload = json.loads(RECORDS["S1"]["raw_answer"])
    payload["cited_chunk_ids"] = []
    checked = check(payload, evidence("S1"))
    assert checked.answer is not None
    assert checked.mechanical.passed
    assert checked.report.structured_evidence_status == "failed"
    assert "release_identity_absent_from_evidence" in {i.code for i in checked.report.issues}
    assert "identifier_absent_from_evidence" in {i.code for i in checked.report.issues}


def test_abstention_and_field_pairs_are_visible_in_schema_and_diagnostics():
    cases = [
        {**minimal_count(), "abstained": True},
        {**minimal_count(), "structured_facts": {"exact_count": 2}},
        {"abstained": False, "answer_text": "A claim is missing."},
    ]
    schema = Draft202012Validator(EvaluationAnswer.model_json_schema())
    for payload in cases:
        assert list(schema.iter_errors(payload))
        assert check(payload).answer is None
    assert check(cases[0]).report.issues[0].code == "abstention_fields_conflict"
    assert check(cases[1]).report.issues[0].code == "paired_fields_required"


def test_array_order_is_preserved_in_answer_and_normalized_only_for_scoring():
    payload = minimal_count()
    payload["structured_facts"] = {"record_keys": ["record:b", "record:a"]}
    answer = EvaluationAnswer.model_validate_json(json.dumps(payload))
    assert answer.structured_facts.record_keys == ("record:b", "record:a")
    assert structured_prediction_from_answer(answer).record_keys == ("record:a", "record:b")
    assert answer.structured_facts.record_keys == ("record:b", "record:a")


def test_duplicate_json_keys_and_nonfinite_numbers_are_not_silently_coerced():
    for raw in ('{"abstained":true,"abstained":false}', '{"exact_count":NaN}', 'not JSON'):
        checked = validate_answer_output(raw, evidence())
        assert checked.answer is None
        assert checked.report.issues[0].stage == "json_syntax"


def test_raw_count_text_is_not_falsely_marked_mechanically_verified():
    checked = check(minimal_count(), evidence("S1"))
    assert checked.mechanical.passed
    assert checked.report.structured_evidence_status == "not_assessed"
    assert "exact_count" in checked.report.unassessed_fields


def test_exact_detail_coordinates_are_checked_as_tuples():
    from tests.experiments.test_rag_value_association_projection import _success

    detail = _success()
    detail_payload = detail.model_dump(mode="json")
    detail_payload["structured_result"]["data"]["locus"]["placement"]["strand"] = "+"
    detail = type(detail).model_validate_json(json.dumps(detail_payload))
    pack = build_evidence_pack(
        question_id="detail-test", question_text="What are the locus coordinates?",
        structured_success=detail, policy_sha256=build_prompt_policy().policy_sha256,
        tokenizer_key="tests-only", model_context_limit_tokens=32768,
        reserved_output_tokens=8192, input_token_count=100, context_token_count=50,
    )
    p = detail.structured_result.data.locus.placement
    payload = minimal_count()
    payload["structured_facts"] = {"coordinates": [{
        "sequence_accession_version": p.sequence_accession_version,
        "start0": p.start0, "end0": p.end0, "strand": p.strand,
    }]}
    assert check(payload, pack).report.structured_evidence_status == "passed"
    payload["structured_facts"]["coordinates"][0]["end0"] += 1
    assert check(payload, pack).report.structured_evidence_status == "failed"


def test_accession_at_sentence_end_is_supported_but_another_version_is_not():
    source_text = "The assembly is GCA_000001235.1."
    source = evidence("S2").citations[0].model_dump(mode="json")
    source.update(text=source_text, text_sha256=hashlib.sha256(source_text.encode()).hexdigest())
    citation = EvidenceCitation.model_validate_json(json.dumps(source))
    pack = build_evidence_pack(
        question_id="identifier-test", question_text="Which assembly is mentioned?",
        citations=(citation,), policy_sha256=build_prompt_policy().policy_sha256,
        tokenizer_key="tests-only", model_context_limit_tokens=32768,
        reserved_output_tokens=8192, input_token_count=100, context_token_count=50,
    )
    payload = {
        "abstained": False, "answer_text": source_text,
        "claims": [{
            "claim_id": "C1", "claim_type": "literature_fact",
            "text": source_text, "citation_ids": ["D1"],
        }],
        "cited_chunk_ids": [citation.chunk_key],
    }
    assert not check(payload, pack).report.issues
    payload["answer_text"] = "The assembly is GCA_000001235.10."
    assert check(payload, pack).report.issues[0].code == "identifier_absent_from_evidence"
