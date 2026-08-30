"""Checksum-bound ten-case human semantic benchmark tests."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from eve_relation_rag.activation.contracts import APPROVED_ASSEMBLIES
from eve_relation_rag.cli_v0 import v0_app
from eve_relation_rag.generation.composer import GenerationComposer
from eve_relation_rag.generation.context import (
    ANSWER_INSTRUCTION_POLICY_KEY,
    ANSWER_INSTRUCTION_TEXT_SHA256,
    build_hybrid_context,
)
from eve_relation_rag.generation.human_review import (
    HUMAN_BENCHMARK_DEFINITION_VERSION,
    HUMAN_REVIEW_RUBRIC_VERSION,
    HUMAN_REVIEW_SUBMISSION_VERSION,
    HumanBenchmarkAnchorTarget,
    HumanBenchmarkCaseDefinition,
    HumanBenchmarkDefinition,
    HumanClaimDecision,
    HumanReviewError,
    HumanReviewSubmission,
    build_human_benchmark_runtime_capture_policy,
    build_human_review_packet,
    evaluate_human_review,
    load_human_benchmark_definition,
    load_human_review_packet,
    load_human_review_submission,
    serialize_review_artifact,
)
from eve_relation_rag.generation.policy import build_approved_prompt_policy_manifest
from eve_relation_rag.generation.rendering import render_hybrid_answer_text
from eve_relation_rag.hybrid.contracts import (
    ExecutionFlags,
    HybridRouteAnswer,
    ProviderIdentity,
    RagQueryRequest,
    canonical_model_json,
    canonical_self_sha256,
)
from eve_relation_rag.hybrid.rendering import revalidate_rag_response
from eve_relation_rag.literature.contracts import (
    LineageAnchor,
    LiteratureRetrievalRequest,
    RetrievedChunks,
)
from eve_relation_rag.literature.hashing import anchor_key, canonical_query_sha256
from eve_relation_rag.planning.query_plans import (
    AssemblyFilter,
    FilteredScope,
    PageSpec,
    canonical_plan_sha256,
)
from eve_relation_rag.retrieval.hybrid.anchors import extract_structured_anchor_targets
from eve_relation_rag.retrieval.structured.results import (
    LocusPageData,
    QuerySuccess,
    ResolvedEntity,
)
from tests.support.m4 import (
    TEST_CORPUS_RELEASE_KEY,
    DeterministicGenerationProvider,
    make_generated_draft,
    make_retrieved_chunks,
    make_structured_success,
)

RELEASE_KEY = "release:endoviho-rag:v0:20260827:002"
RELEASE_MANIFEST_SHA = "a" * 64
CORPUS_MANIFEST_SHA = "e" * 64
BINDING_MANIFEST_SHA = "b" * 64
ANCHOR_MANIFEST_SHA = "c" * 64
MODEL_POLICY_SHA = "d" * 64
PROVIDER_KEY = "provider:local-openai-compatible:v1"
MODEL_KEY = "model:tests:human-review-v0"
MODEL_REVISION = "revision:tests:human-review-v0"
GENERATION_POLICY_KEY = "generation:json-zero-temp:v1"
TIMEOUT_SECONDS = 5


def _identity() -> ProviderIdentity:
    return ProviderIdentity(
        provider_key=PROVIDER_KEY,
        model_key=MODEL_KEY,
        model_revision=MODEL_REVISION,
        provider_artifact_sha256=MODEL_POLICY_SHA,
        generation_policy_key=GENERATION_POLICY_KEY,
        prompt_policy_key=ANSWER_INSTRUCTION_POLICY_KEY,
        prompt_policy_sha256=ANSWER_INSTRUCTION_TEXT_SHA256,
        temperature=0,
        max_output_bytes=32768,
        timeout_seconds=TIMEOUT_SECONDS,
        retry_count=0,
    )


def _response(
    ordinal: int,
    *,
    locus_assembly_accession: str | None = None,
    anchor_term_key: str | None = None,
) -> tuple[str, str, HybridRouteAnswer]:
    accession = APPROVED_ASSEMBLIES[ordinal - 1]
    assembly_key = f"assembly:ncbi:{accession}"
    structured_question = f"List loci in assembly {accession}."
    question = f"{structured_question} and explain the literature evidence"
    base = make_structured_success(
        "locus_page",
        structured_question=structured_question,
    )
    data = base.structured_result.data
    assert isinstance(data, LocusPageData)
    locus_accession = locus_assembly_accession or accession
    locus = data.items[0].model_copy(
        update={
            "assembly_accession_version": locus_accession,
            "assembly_key": f"assembly:ncbi:{locus_accession}",
        }
    )
    data = data.model_copy(update={"items": (locus,)})
    plan = base.query_plan.model_copy(
        update={
            "scope": FilteredScope(
                scope_type="filtered",
                filters=(AssemblyFilter(filter_type="assembly", assembly_key=assembly_key),),
            ),
            "original_question": structured_question,
        }
    )
    result = base.structured_result.model_copy(
        update={
            "plan_sha256": canonical_plan_sha256(plan),
            "data": data,
        }
    )
    success = QuerySuccess.model_validate(
        base.model_dump(mode="python")
        | {
            "query_plan": plan,
            "structured_result": result,
            "resolved_entities": (
                ResolvedEntity(
                    original_input=accession,
                    entity_kind="assembly",
                    match_mode="exact_identifier",
                    stable_key=assembly_key,
                    canonical_name=accession,
                ),
            ),
        }
    )

    viral_lineage = locus.viral_lineages[0]
    exact_anchor_term_key = anchor_term_key or viral_lineage.term_key
    anchor = LineageAnchor(
        anchor_type="lineage",
        anchor_key=anchor_key(
            {
                "case_ordinal": ordinal,
                "snapshot_key": viral_lineage.snapshot_key,
                "term_key": exact_anchor_term_key,
            }
        ),
        snapshot_key=viral_lineage.snapshot_key,
        term_key=exact_anchor_term_key,
    )
    chunks = make_retrieved_chunks(
        question=question,
        text="The synthetic human-review evidence supports the exact benchmark claim.",
    )
    anchored_chunk = chunks.chunks[0].model_copy(
        update={
            "retrieval_tier": "anchored",
            "matched_anchors": (anchor.anchor_key,),
        }
    )
    retrieval_request = LiteratureRetrievalRequest(
        request_schema_version="literature-retrieval-request-v1",
        corpus_release_key=TEST_CORPUS_RELEASE_KEY,
        question=question,
        top_k=8,
    )
    chunks = RetrievedChunks.model_validate(
        chunks.model_dump(mode="python")
        | {
            "query_sha256": canonical_query_sha256(retrieval_request, (anchor,)),
            "anchor_mode": "anchored_then_corpus_fill",
            "anchors_applied": (anchor,),
            "chunks": (anchored_chunk,),
        }
    )
    context = build_hybrid_context(
        original_question=question,
        query_success=success,
        retrieved_chunks=chunks,
    )
    draft = make_generated_draft(
        context_sha256=context.context_sha256,
        claim_text="The synthetic human-review evidence supports the exact benchmark claim.",
        citation_id="D1",
        evidence_quote="supports the exact benchmark claim",
    )
    provider = DeterministicGenerationProvider(
        identity=_identity(),
        output=canonical_model_json(draft),
    )
    composition = GenerationComposer(
        provider=provider,
        expected_identity=provider.identity,
    ).compose(context)
    answer_text = render_hybrid_answer_text(result, composition)
    response = HybridRouteAnswer(
        response_schema_version="hybrid-answer-v1",
        response_kind="hybrid_answer",
        route="hybrid",
        original_request=RagQueryRequest(
            request_schema_version="rag-query-request-v1",
            release_key=RELEASE_KEY,
            corpus_release_key=TEST_CORPUS_RELEASE_KEY,
            question=question,
            page=PageSpec(limit=1),
            literature_top_k=8,
        ),
        query_success=success,
        retrieved_chunks=chunks,
        anchor_diagnostics=("structured_anchor_unmatched",),
        generation=composition,
        insufficient_evidence_limitation=None,
        answer_text=answer_text,
        answer_sha256=hashlib.sha256(answer_text.encode("utf-8")).hexdigest(),
        execution=ExecutionFlags(
            structured_retrieval_executed=True,
            literature_retrieval_executed=True,
            generation_executed=True,
        ),
    )
    assert revalidate_rag_response(response) == response
    return accession, question, response


def _definition_and_answers(
    tmp_path: Path,
    *,
    invalid_case: int | None = None,
    locus_assembly_accession: str | None = None,
    anchor_term_key: str | None = None,
) -> tuple[HumanBenchmarkDefinition, Path]:
    answers_root = tmp_path / "answers"
    answers_root.mkdir()
    cases: list[HumanBenchmarkCaseDefinition] = []
    for ordinal in range(1, 11):
        accession, question, response = _response(
            ordinal,
            locus_assembly_accession=(
                locus_assembly_accession if ordinal == invalid_case else None
            ),
            anchor_term_key=(
                anchor_term_key if ordinal == invalid_case else None
            ),
        )
        response_path = f"case-{ordinal:02d}.json"
        (answers_root / response_path).write_text(
            canonical_model_json(response),
            encoding="utf-8",
        )
        targets = tuple(
            HumanBenchmarkAnchorTarget(
                target_type=target.target_type,
                locus_key=target.locus_key,
                assembly_key=target.assembly_key,
                snapshot_key=target.snapshot_key,
                term_key=target.term_key,
                method_definition_key=target.method_definition_key,
            )
            for target in extract_structured_anchor_targets(response.query_success)
        )
        locus_data = response.query_success.structured_result.data
        assert isinstance(locus_data, LocusPageData)
        expected_matched = HumanBenchmarkAnchorTarget(
            target_type="lineage",
            snapshot_key=locus_data.items[0].viral_lineages[0].snapshot_key,
            term_key=locus_data.items[0].viral_lineages[0].term_key,
        )
        cases.append(
            HumanBenchmarkCaseDefinition(
                case_ordinal=ordinal,
                case_key=f"benchmark:v0-human-hybrid:{accession}",
                assembly_accession_version=accession,
                structured_question=f"List loci in assembly {accession}.",
                question=question,
                page_limit=1,
                literature_top_k=8,
                expected_matched_targets=(expected_matched,),
                expected_unmatched_targets=tuple(
                    target for target in targets if target != expected_matched
                ),
                response_path=response_path,
                runtime_capture_path=f"case-{ordinal:02d}.runtime.json",
            )
        )
    prompt = build_approved_prompt_policy_manifest()
    payload: dict[str, object] = {
        "definition_schema_version": HUMAN_BENCHMARK_DEFINITION_VERSION,
        "rubric_version": HUMAN_REVIEW_RUBRIC_VERSION,
        "release_key": RELEASE_KEY,
        "release_manifest_sha256": RELEASE_MANIFEST_SHA,
        "corpus_release_key": TEST_CORPUS_RELEASE_KEY,
        "corpus_manifest_sha256": CORPUS_MANIFEST_SHA,
        "binding_manifest_sha256": BINDING_MANIFEST_SHA,
        "anchor_manifest_sha256": ANCHOR_MANIFEST_SHA,
        "model_policy_manifest_sha256": MODEL_POLICY_SHA,
        "prompt_policy_manifest_sha256": prompt.manifest_sha256,
        "provider_key": PROVIDER_KEY,
        "model_key": MODEL_KEY,
        "model_revision": MODEL_REVISION,
        "generation_policy_key": GENERATION_POLICY_KEY,
        "prompt_policy_key": ANSWER_INSTRUCTION_POLICY_KEY,
        "prompt_source_text_sha256": ANSWER_INSTRUCTION_TEXT_SHA256,
        "timeout_seconds": TIMEOUT_SECONDS,
        "runtime_capture_policy": build_human_benchmark_runtime_capture_policy(),
        "cases": tuple(cases),
        "definition_sha256": "0" * 64,
    }
    payload["definition_sha256"] = canonical_self_sha256(payload, "definition_sha256")
    return HumanBenchmarkDefinition.model_validate(payload), answers_root


def _submission(
    packet: object,
    *,
    label_overrides: dict[int, str] | None = None,
    omit_ordinals: set[int] | None = None,
) -> HumanReviewSubmission:
    label_overrides = label_overrides or {}
    omit_ordinals = omit_ordinals or set()
    decisions: list[HumanClaimDecision] = []
    for case in packet.cases:  # type: ignore[attr-defined]
        if case.case_ordinal in omit_ordinals:
            continue
        for claim in case.claims:
            decisions.append(
                HumanClaimDecision.model_validate(
                    {
                        "case_ordinal": case.case_ordinal,
                        "case_key": case.case_key,
                        "case_sha256": case.case_sha256,
                        "answer_sha256": case.answer_sha256,
                        "claim_id": claim.claim_id,
                        "claim_sha256": claim.claim_sha256,
                        "label": label_overrides.get(case.case_ordinal, "supported"),
                        "review_note": "Reviewed against the exact cited evidence.",
                    }
                )
            )
    payload: dict[str, object] = {
        "submission_schema_version": HUMAN_REVIEW_SUBMISSION_VERSION,
        "rubric_version": HUMAN_REVIEW_RUBRIC_VERSION,
        "packet_sha256": packet.packet_sha256,  # type: ignore[attr-defined]
        "reviewer_key": "reviewer:tests:domain-expert",
        "reviewer_name": "Tests Only Human Reviewer",
        "reviewer_role": "human_domain_reviewer",
        "reviewed_at": "2026-08-29T00:00:00Z",
        "attestation": "I reviewed every claim against only its cited current evidence.",
        "decisions": tuple(decisions),
        "submission_sha256": "0" * 64,
    }
    payload["submission_sha256"] = canonical_self_sha256(payload, "submission_sha256")
    return HumanReviewSubmission.model_validate(payload)


def test_ten_case_packet_and_all_supported_named_human_review_pass(tmp_path: Path) -> None:
    definition, answers_root = _definition_and_answers(tmp_path)

    packet = build_human_review_packet(definition, answers_root=answers_root)
    evaluation = evaluate_human_review(packet, _submission(packet))

    assert evaluation.status == "passed"
    assert evaluation.issue_codes == ()
    assert evaluation.metrics.case_count == 10
    assert evaluation.metrics.claim_count == 10
    assert evaluation.metrics.supported_count == 10
    assert evaluation.metrics.citation_existence == "1.000000000000"
    assert evaluation.metrics.release_match == "1.000000000000"
    assert evaluation.metrics.locator_validity == "1.000000000000"
    assert evaluation.metrics.citation_coverage == "1.000000000000"


@pytest.mark.parametrize(
    ("labels", "omitted", "issue"),
    (
        ({1: "partially_supported"}, set(), "partially_supported_claim"),
        ({1: "unsupported"}, set(), "unsupported_claim"),
        ({}, {1}, "unreviewed_claim"),
    ),
)
def test_partial_unsupported_or_unreviewed_claim_fails_closed(
    tmp_path: Path,
    labels: dict[int, str],
    omitted: set[int],
    issue: str,
) -> None:
    definition, answers_root = _definition_and_answers(tmp_path)
    packet = build_human_review_packet(definition, answers_root=answers_root)

    evaluation = evaluate_human_review(
        packet,
        _submission(packet, label_overrides=labels, omit_ordinals=omitted),
    )

    assert evaluation.status == "failed"
    assert issue in evaluation.issue_codes


@pytest.mark.parametrize(
    ("locus_override", "anchor_term_override"),
    (
        ("GCA_999999991.1", None),
        (None, "lineage-term:study:unexpected"),
    ),
)
def test_packet_rejects_case_without_exact_locus_and_anchor_binding(
    tmp_path: Path,
    locus_override: str | None,
    anchor_term_override: str | None,
) -> None:
    definition, answers_root = _definition_and_answers(
        tmp_path,
        invalid_case=1,
        locus_assembly_accession=locus_override,
        anchor_term_key=anchor_term_override,
    )

    with pytest.raises(
        HumanReviewError,
        match="exact assembly|anchor evidence|unexpected structured-target anchor",
    ):
        build_human_review_packet(definition, answers_root=answers_root)


def test_definition_requires_exactly_ten_unique_preregistered_assemblies(
    tmp_path: Path,
) -> None:
    definition, _answers_root = _definition_and_answers(tmp_path)
    payload = definition.model_dump(mode="python")
    payload["cases"] = tuple(definition.cases[:9])
    payload["definition_sha256"] = canonical_self_sha256(payload, "definition_sha256")
    with pytest.raises(ValidationError, match="at least 10 items"):
        HumanBenchmarkDefinition.model_validate(payload)

    duplicate = definition.cases[1].model_copy(
        update={"assembly_accession_version": definition.cases[0].assembly_accession_version}
    )
    payload = definition.model_dump(mode="python")
    payload["cases"] = (definition.cases[0], duplicate, *definition.cases[2:])
    payload["definition_sha256"] = canonical_self_sha256(payload, "definition_sha256")
    with pytest.raises(ValidationError, match="frozen assembly template|approved assemblies"):
        HumanBenchmarkDefinition.model_validate(payload)


def test_review_loaders_require_self_hash_and_separate_approval(tmp_path: Path) -> None:
    definition, answers_root = _definition_and_answers(tmp_path)
    packet = build_human_review_packet(definition, answers_root=answers_root)
    submission = _submission(packet)
    definition_path = tmp_path / "definition.json"
    packet_path = tmp_path / "packet.json"
    submission_path = tmp_path / "submission.json"
    definition_path.write_text(serialize_review_artifact(definition), encoding="utf-8")
    packet_path.write_text(serialize_review_artifact(packet), encoding="utf-8")
    submission_path.write_text(serialize_review_artifact(submission), encoding="utf-8")

    assert (
        load_human_benchmark_definition(
            definition_path,
            approved_definition_sha256=definition.definition_sha256,
        )
        == definition
    )
    assert (
        load_human_review_packet(
            packet_path,
            approved_packet_sha256=packet.packet_sha256,
        )
        == packet
    )
    assert (
        load_human_review_submission(
            submission_path,
            approved_submission_sha256=submission.submission_sha256,
        )
        == submission
    )
    with pytest.raises(HumanReviewError, match="approved checksum"):
        load_human_benchmark_definition(
            definition_path,
            approved_definition_sha256="f" * 64,
        )

    tampered = json.loads(submission_path.read_text(encoding="utf-8"))
    tampered["reviewer_name"] = "Tampered Reviewer"
    submission_path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(HumanReviewError, match="unavailable or invalid"):
        load_human_review_submission(
            submission_path,
            approved_submission_sha256=submission.submission_sha256,
        )


def test_independent_cli_exports_and_validates_without_overwrite(tmp_path: Path) -> None:
    definition, answers_root = _definition_and_answers(tmp_path)
    definition_path = tmp_path / "definition.json"
    packet_path = tmp_path / "packet.json"
    definition_path.write_text(serialize_review_artifact(definition), encoding="utf-8")
    runner = CliRunner()

    exported = runner.invoke(
        v0_app,
        [
            "human-review-export",
            "--definition-path",
            str(definition_path),
            "--approved-definition-sha256",
            definition.definition_sha256,
            "--answers-root",
            str(answers_root),
            "--output-path",
            str(packet_path),
        ],
    )

    assert exported.exit_code == 0, exported.output
    packet = load_human_review_packet(
        packet_path,
        approved_packet_sha256=json.loads(exported.output)["packet_sha256"],
    )
    assert packet_path.stat().st_mode & 0o777 == 0o600
    overwrite = runner.invoke(
        v0_app,
        [
            "human-review-export",
            "--definition-path",
            str(definition_path),
            "--approved-definition-sha256",
            definition.definition_sha256,
            "--answers-root",
            str(answers_root),
            "--output-path",
            str(packet_path),
        ],
    )
    assert overwrite.exit_code == 4

    submission = _submission(packet)
    submission_path = tmp_path / "submission.json"
    submission_path.write_text(serialize_review_artifact(submission), encoding="utf-8")
    validated = runner.invoke(
        v0_app,
        [
            "human-review-validate",
            "--packet-path",
            str(packet_path),
            "--approved-packet-sha256",
            packet.packet_sha256,
            "--submission-path",
            str(submission_path),
            "--approved-submission-sha256",
            submission.submission_sha256,
        ],
    )
    assert validated.exit_code == 0, validated.output
    assert json.loads(validated.output)["status"] == "passed"
