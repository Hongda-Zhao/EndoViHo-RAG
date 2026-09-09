"""Audit model output without repairing it or substituting Gold for predicted values.

Exact structured values can be checked mechanically. Textual entailment, completeness
of a scientific answer and cross-source identity remain separate review questions.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import ValidationError

from eve_relation_rag.experiments.rag_value_ablation.contracts import (
    EvaluationAnswer,
    EvaluationEvidencePack,
    MechanicalValidation,
    mechanically_validate_answer,
)
from eve_relation_rag.experiments.rag_value_ablation.metrics import (
    structured_prediction_from_answer,
)
from eve_relation_rag.literature.contracts import StableToken, StrictFrozenSchema
from eve_relation_rag.literature.hashing import canonical_json_bytes
from eve_relation_rag.retrieval.structured.results import (
    AggregateData,
    AssemblyDetailData,
    AssemblyPageData,
    LocusDetailData,
    LocusPageData,
    LocusSummary,
    StructuredResult,
)

type ValidationStage = Literal[
    "json_syntax", "schema", "answer_contract", "references", "structured_evidence", "output_limit",
]
type CheckStatus = Literal["passed", "failed", "not_assessed", "not_applicable"]


class AnswerValidationIssue(StrictFrozenSchema):
    stage: ValidationStage
    code: StableToken
    path: tuple[str | int, ...] = ()


class AnswerValidationReport(StrictFrozenSchema):
    validation_schema_version: Literal["rag-value-answer-validation-v2"] = (
        "rag-value-answer-validation-v2"
    )
    issues: tuple[AnswerValidationIssue, ...] = ()
    reference_status: CheckStatus = "not_assessed"
    structured_evidence_status: CheckStatus = "not_assessed"
    # Even a matching number/identifier does not establish a textual claim's meaning.
    semantic_support_status: Literal["not_assessed"] = "not_assessed"
    unassessed_fields: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ValidatedAnswer:
    answer: EvaluationAnswer | None
    mechanical: MechanicalValidation | None
    report: AnswerValidationReport


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError("non-finite JSON number")


def validate_answer_output(raw: str, evidence: EvaluationEvidencePack) -> ValidatedAnswer:
    """Retain failures with stable codes/paths; never emit exception input or context."""
    try:
        json.loads(raw, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    except (ValueError, RecursionError):
        return ValidatedAnswer(None, None, AnswerValidationReport(issues=(
            AnswerValidationIssue(stage="json_syntax", code="invalid_json"),
        )))
    try:
        answer = EvaluationAnswer.model_validate_json(raw)
    except ValidationError as exc:
        issues = []
        for error in exc.errors(include_url=False, include_input=False, include_context=False):
            stage: ValidationStage = (
                "answer_contract" if error["type"] == "value_error" else "schema"
            )
            code = error["type"]
            for fragment, specific in (
                ("abstained answer", "abstention_fields_conflict"),
                ("literature factual claims", "literature_claim_missing_citation"),
                ("supplied together", "paired_fields_required"),
                ("contiguous and ordered", "claim_ids_not_contiguous"),
                ("must correspond", "structured_claim_projection_mismatch"),
                ("must be unique", "duplicate_values"),
            ):
                if fragment in error["msg"]:
                    code = specific
                    break
            path = tuple(
                part if isinstance(part, int) or re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", part)
                else "<extra>"
                for part in error["loc"]
            )
            issues.append(AnswerValidationIssue(stage=stage, code=code, path=path))
        return ValidatedAnswer(None, None, AnswerValidationReport(issues=tuple(issues)))

    mechanical = mechanically_validate_answer(answer, evidence)
    issues = [
        AnswerValidationIssue(stage="references", code=code)
        for code in mechanical.issue_codes
    ]
    fact_issues, unassessed = _check_structured_values(answer, evidence)
    issues.extend(fact_issues)
    fact_status: CheckStatus = (
        "failed" if fact_issues else "not_assessed" if unassessed
        else "not_applicable" if answer.structured_facts is None else "passed"
    )
    return ValidatedAnswer(answer, mechanical, AnswerValidationReport(
        issues=tuple(issues),
        reference_status="passed" if mechanical.passed else "failed",
        structured_evidence_status=fact_status,
        unassessed_fields=tuple(sorted(unassessed)),
    ))


def _check_structured_values(
    answer: EvaluationAnswer, evidence: EvaluationEvidencePack,
) -> tuple[list[AnswerValidationIssue], set[str]]:
    issues: list[AnswerValidationIssue] = []
    unassessed: set[str] = set()

    def fail(code: str, *path: str | int) -> None:
        issues.append(AnswerValidationIssue(stage="structured_evidence", code=code, path=path))

    # Search source CONTENT, excluding question, schema, raw-file hashes and other metadata.
    sources = (
        [evidence.structured_success.structured_result] if evidence.structured_success else []
    )
    for group in evidence.structured_groups:
        sources.extend(item.structured_result for item in (*group.pages, *group.details))
    content = "\n".join([
        *(canonical_json_bytes(source.model_dump(mode="json")).decode() for source in sources),
        *(canonical_json_bytes(a.model_dump(mode="json")).decode()
          for g in evidence.structured_groups for p in g.association_projections
          for a in p.associations),
        *(citation.text for citation in evidence.citations),
        *(segment.text for segment in evidence.raw_context_segments),
        *(canonical_json_bytes(g.visible()).decode() for g in evidence.source_report_groups),
    ])
    prediction = structured_prediction_from_answer(answer)
    for token in prediction.observed_identifier_tokens:
        if not _has_token(content, token):
            fail("identifier_absent_from_evidence", "answer_text_or_claims")

    facts = answer.structured_facts
    if facts is None:
        return issues, unassessed

    # Raw exports may be exact serialized StructuredResults. Natural-language exports
    # remain available to the model but are never parsed by a guessed count regex.
    for segment in evidence.raw_context_segments:
        if segment.source_kind == "structured_export":
            try:
                sources.append(StructuredResult.model_validate_json(segment.text))
            except ValueError:
                pass
    expected: dict[str, set[bytes]] = {}

    def add(name: str, value: Any) -> None:
        expected.setdefault(name, set()).add(canonical_json_bytes(value))

    for source_group in evidence.source_report_groups:
        add("count_and_metric", [source_group.source_report_count, "source_report_count"])
        expected.setdefault("record_keys", set())
        for key in (*tuple(r.record_key for r in source_group.rows),
                    *tuple(r.record_key for r in source_group.regions)):
            add("record_keys", key)
        for row in source_group.rows:
            add("assembly_accession_versions", row.cell("A"))
            add("sequence_accession_versions", row.cell("B"))
            # Identity is a source occurrence. No exact placement or public membership is inferred.
            add("locus_keys", row.source_occurrence_locus_key)
        for region in source_group.regions:
            if region.assembly_accession_version is not None:
                add("assembly_accession_versions", region.assembly_accession_version)
            if region.sequence_accession_version is not None:
                add("sequence_accession_versions", region.sequence_accession_version)
    if len(evidence.source_report_groups) > 1 and facts.exact_count is not None:
        unassessed.add("exact_count_subquery_scope")

    for group in evidence.structured_groups:
        for projection in group.association_projections:
            for association in projection.associations:
                add("exact_association_set", association)
    if evidence.structured_groups and len(evidence.structured_groups) > 1:
        # The v1 answer projection has no field binding a scalar count to a subquery.
        # Checking membership in any subquery's counts would hide a swapped scope.
        if facts.exact_count is not None:
            unassessed.add("exact_count_subquery_scope")

    for source in sources:
        add("release_identity", [source.release.release_key, source.release.manifest_sha256])
        for limitation in source.limitations:
            add("limitation_codes", limitation.code)
        data = source.data
        if isinstance(data, AggregateData):
            add("count_and_metric", [data.value, data.metric_key])
        loci: tuple[LocusSummary, ...] = ()
        if isinstance(data, LocusDetailData):
            loci = (data.locus,)
            expected.setdefault("record_keys", set())
            expected.setdefault("detection_call_keys", set())
            for call in data.calls:
                add("record_keys", call.source_record_key)
                add("detection_call_keys", call.call_key)
        elif isinstance(data, LocusPageData):
            loci = data.items
            expected.setdefault("locus_keys", set())
            add("count_and_metric", [data.page.total_count, "distinct_included_locus_count"])
        elif isinstance(data, (AssemblyDetailData, AssemblyPageData)):
            assemblies = (data.assembly,) if isinstance(data, AssemblyDetailData) else data.items
            expected.setdefault("assembly_accession_versions", set())
            for assembly in assemblies:
                add("assembly_accession_versions", assembly.assembly_accession_version)
        for locus in loci:
            add("locus_keys", locus.locus_key)
            add("assembly_accession_versions", locus.assembly_accession_version)
            add("sequence_accession_versions", locus.placement.sequence_accession_version)
            add("coordinates", {
                "sequence_accession_version": locus.placement.sequence_accession_version,
                "start0": locus.placement.start0,
                "end0": locus.placement.end0,
                "strand": locus.placement.strand,
                "coordinate_convention": "0-based-half-open",
            })

    pairs = (
        ("count_and_metric", "exact_count", [facts.exact_count, facts.metric_key]),
        ("release_identity", "release_key", [facts.release_key, facts.release_manifest_sha256]),
    )
    for name, field, values in pairs:
        if values[0] is None:
            continue
        if sources or evidence.source_report_groups:
            if canonical_json_bytes(values) not in expected.get(name, set()):
                fail("structured_pair_not_supported", "structured_facts", field)
        else:
            unassessed.add(field)
            if name == "release_identity" and any(not _has_token(content, str(v)) for v in values):
                fail("release_identity_absent_from_evidence", "structured_facts", field)

    for name in (
        "record_keys", "assembly_accession_versions", "sequence_accession_versions",
        "locus_keys", "coordinates", "detection_call_keys", "exact_association_set",
        "limitation_codes",
    ):
        values = getattr(facts, name)
        if values is None or (name == "limitation_codes" and not values):
            continue
        if sources or evidence.source_report_groups:
            allowed = expected.get(name)
            if allowed is None or any(canonical_json_bytes(v) not in allowed for v in values):
                fail("structured_value_not_supported", "structured_facts", name)
        else:
            unassessed.add(name)
            # Presence is a necessary check, never a proof of a raw-text assertion.
            for index, value in enumerate(values):
                if isinstance(value, str) and not _has_token(content, value):
                    fail("identifier_absent_from_evidence", "structured_facts", name, index)
                elif not isinstance(value, str):
                    # Exact tuples cannot be proven by unrelated tokens occurring in text.
                    fail("exact_tuple_without_typed_evidence", "structured_facts", name, index)
    return issues, unassessed


def _has_token(text: str, token: str) -> bool:
    # A sentence-ending period is punctuation; a suffix like .10 is part of an ID.
    pattern = r"(?<![A-Za-z0-9_:.-])" + re.escape(token) + r"(?![A-Za-z0-9_:-]|\.[A-Za-z0-9])"
    return re.search(pattern, text) is not None
