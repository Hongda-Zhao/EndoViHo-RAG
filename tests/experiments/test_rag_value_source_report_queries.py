"""Source reports stay complete and cannot be promoted to public-locus evidence."""

import hashlib
import json

import pytest

from eve_relation_rag.experiments.rag_value_ablation.answer_validation import validate_answer_output
from eve_relation_rag.experiments.rag_value_ablation.contracts import (
    EvaluationEvidencePack,
    build_evidence_pack,
    model_visible_evidence,
)
from eve_relation_rag.experiments.rag_value_ablation.source_report_queries import (
    SourceReportQuery,
    SourceReportRepository,
    SourceReportResult,
)
from eve_relation_rag.experiments.rag_value_ablation.source_reports import seal_literature_region
from eve_relation_rag.experiments.rag_value_ablation.systems import (
    SystemPolicyError,
    build_source_report_system_definitions,
    build_system_definitions,
    validate_evidence_for_system,
)
from eve_relation_rag.literature.hashing import canonical_json_bytes, canonical_json_sha256
from tests.experiments.test_rag_value_source_reports import row


def packet(tmp_path):
    rows = (row(), row(index=3, viral="Retroviridae"))
    region = seal_literature_region(
        source_doi="10.1234/test", source_artifact_sha256="c" * 64,
        source_locator="Figure 1", taxon_name="Source organism", region_name="reported region",
        lineage_as_reported="putative viral lineage", location_as_reported="about 475 kb",
        approximate_length_bp=475000, reported_claim="Source reports a region.",
        reported_uncertainty="Approximate boundary; no accession.version resolved.",
    )
    (tmp_path / "source_reports.jsonl").write_bytes(b"".join(
        canonical_json_bytes(r) + b"\n" for r in rows))
    (tmp_path / "literature_regions.jsonl").write_bytes(canonical_json_bytes(region) + b"\n")
    (tmp_path / "entity_region_bindings.json").write_bytes(canonical_json_bytes({
        "EVE_LOCUS_A": region.record_key, "note": "synthetic test binding"}))
    manifest = {
        "schema_version": "rag-value-source-report-packet-v1", "public_validated_release": False,
        "source_row_count": 2, "literature_region_count": 1,
        "files": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in tmp_path.iterdir()},
    }
    (tmp_path / "packet_manifest.json").write_bytes(canonical_json_bytes({
        **manifest, "manifest_sha256": canonical_json_sha256(manifest)}))
    digest = hashlib.sha256((tmp_path / "packet_manifest.json").read_bytes()).hexdigest()
    return SourceReportRepository(tmp_path, expected_manifest_file_sha256=digest), digest


def evidence(groups):
    return build_evidence_pack(
        question_id="development-source-reports", question_text="Describe these source reports.",
        source_report_groups=groups, policy_sha256="a" * 64, tokenizer_key="test-only",
        model_context_limit_tokens=32768, reserved_output_tokens=8192,
        input_token_count=100, context_token_count=50,
    )


def test_complete_query_retains_failed_record_and_rejects_resealed_partial_result(tmp_path):
    repository, _ = packet(tmp_path)
    result = repository.query(SourceReportQuery())
    assert result.source_report_count == 2
    assert {r.cell("T") for r in result.rows} == {"Failed"}
    payload = result.model_dump(mode="json", exclude={"result_sha256"})
    payload["rows"] = payload["rows"][:1]
    payload["source_report_count"] = 1
    partial = SourceReportResult.model_validate_json(json.dumps({
        **payload, "result_sha256": canonical_json_sha256(payload)}))
    with pytest.raises(ValueError, match="complete selection"):
        repository.verify_complete_result(partial)
    with pytest.raises(ValueError, match="exact 25"):
        repository.core53_queries("HOST-S-99")


def test_changed_packet_is_rejected_before_querying(tmp_path):
    _, digest = packet(tmp_path)
    p = tmp_path / "source_reports.jsonl"
    p.write_bytes(p.read_bytes().replace(b'"Failed"', b'"Passed"', 1))
    with pytest.raises(ValueError, match="file changed"):
        SourceReportRepository(tmp_path, expected_manifest_file_sha256=digest)


def test_v3_round_trip_and_source_s5_are_separate_from_public_s5(tmp_path):
    repository, _ = packet(tmp_path)
    pack = evidence((repository.query(SourceReportQuery()),))
    assert EvaluationEvidencePack.model_validate_json(pack.model_dump_json()) == pack
    assert pack.pack_schema_version == "rag-value-evidence-pack-v3"
    assert "original_field_names" in model_visible_evidence(pack)["source_report_queries"][0]
    original = build_system_definitions(None)
    amended = build_source_report_system_definitions(None)
    assert [a == b for a, b in zip(original, amended, strict=True)] == [
        True, True, True, True, False, False, True]
    validate_evidence_for_system(amended[5], pack)
    with pytest.raises(SystemPolicyError, match="separately versioned"):
        validate_evidence_for_system(original[5], pack)
    for index in (0, 1, 2, 3):
        with pytest.raises(SystemPolicyError):
            validate_evidence_for_system(amended[index], pack)


def test_source_count_is_checked_and_unknown_exact_coordinates_fail(tmp_path):
    repository, _ = packet(tmp_path)
    result = repository.query(SourceReportQuery())
    pack = evidence((result,))
    answer = {"answer_text": "Two source reports are supplied.", "abstained": False,
              "claims": [{"claim_id": "C1", "text": "Two source reports are supplied.",
                          "claim_type": "structured_fact"}],
              "structured_facts": {"exact_count": 2, "metric_key": "source_report_count"}}
    assert validate_answer_output(json.dumps(answer), pack).report.structured_evidence_status == (
        "passed")
    answer["structured_facts"]["metric_key"] = "distinct_included_locus_count"
    assert validate_answer_output(json.dumps(answer), pack).report.structured_evidence_status == (
        "failed")
    region = repository.query(SourceReportQuery(query_kind="reported_regions",
        reported_region_keys=(repository.region_bindings["EVE_LOCUS_A"],)))
    assert region.regions[0].exact_coordinates is None
    answer["structured_facts"] = {"coordinates": [{
        "sequence_accession_version": "NC_000001.1", "start0": 95000, "end0": 570000,
        "strand": "+", "coordinate_convention": "0-based-half-open"}]}
    checked = validate_answer_output(json.dumps(answer), evidence((region,)))
    assert checked.report.structured_evidence_status == "failed"
