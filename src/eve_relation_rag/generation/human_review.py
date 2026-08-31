"""Checksum-bound ten-case human semantic-support benchmark."""

from __future__ import annotations

import hashlib
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path
from typing import Annotated, Final, Literal, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from eve_relation_rag.activation.contracts import (
    APPROVED_ASSEMBLIES,
    AssemblyTaxonAssignmentManifest,
    PublicAssertionMembershipManifest,
    PublicLocusMembershipManifest,
    StructuredActivationManifest,
    StudyFormalMappingManifest,
    TaxonomySnapshotManifest,
)
from eve_relation_rag.activation.corpus import (
    V0_CORPUS_RELEASE_KEY,
    V0_STRUCTURED_RELEASE_KEY,
)
from eve_relation_rag.domain.keys import is_versioned_assembly_accession
from eve_relation_rag.generation.context import build_hybrid_context
from eve_relation_rag.generation.policy import LocalModelPolicyManifest, PromptPolicyManifest
from eve_relation_rag.hybrid.contracts import (
    AsciiQuestion,
    HybridReleaseBindingManifest,
    HybridRouteAnswer,
    ProviderIdentity,
    StrictFrozenSchema,
    canonical_model_json,
    canonical_model_sha256,
    canonical_self_sha256,
)
from eve_relation_rag.hybrid.rendering import revalidate_rag_response
from eve_relation_rag.literature.anchors import CorpusAnchorManifest
from eve_relation_rag.literature.contracts import (
    CorpusManifest,
    CorpusReleaseKey,
    LineageAnchor,
    RelativePath,
    Rfc3339Utc,
    Sha256,
    StableToken,
)
from eve_relation_rag.planning.query_plans import (
    AssemblyFilter,
    FilteredScope,
    ListLociPlan,
    PublishedReleaseKey,
)
from eve_relation_rag.retrieval.hybrid.anchors import (
    StructuredAnchorTarget,
    extract_structured_anchor_targets,
)
from eve_relation_rag.retrieval.structured.results import LocusPageData

HUMAN_BENCHMARK_DEFINITION_VERSION: Literal["v0-human-benchmark-definition-v1"] = (
    "v0-human-benchmark-definition-v1"
)
HUMAN_REVIEW_PACKET_VERSION: Literal["v0-human-review-packet-v1"] = "v0-human-review-packet-v1"
HUMAN_REVIEW_SUBMISSION_VERSION: Literal["v0-human-review-submission-v1"] = (
    "v0-human-review-submission-v1"
)
HUMAN_REVIEW_EVALUATION_VERSION: Literal["v0-human-review-evaluation-v1"] = (
    "v0-human-review-evaluation-v1"
)
HUMAN_REVIEW_RUBRIC_VERSION: Literal["rubric:v0-cited-claim-support-v1"] = (
    "rubric:v0-cited-claim-support-v1"
)
_MAX_DEFINITION_BYTES = 1_048_576
_MAX_RESPONSE_BYTES = 4 * 1024 * 1024
_MAX_PACKET_BYTES = 64 * 1024 * 1024
_MAX_REVIEW_BYTES = 8 * 1024 * 1024
_METRIC_QUANTUM = Decimal("0.000000000001")
_CASE_PAGE_LIMIT: Final = 1
_CASE_LITERATURE_TOP_K: Final = 8
_RUNTIME_CASE_FIELDS: Final = (
    "answer_byte_count",
    "answer_sha256",
    "completed_at",
    "context_byte_count",
    "context_sha256",
    "execution_flags",
    "input_token_count",
    "output_token_count",
    "provider_identity_sha256",
    "provider_wall_duration_ns",
    "response_byte_count",
    "response_sha256",
    "route_wall_duration_ns",
    "started_at",
)
_RUNTIME_RUN_FIELDS: Final = (
    "dependency_lock_sha256",
    "host_architecture",
    "model_policy_manifest_sha256",
    "operating_system",
    "prompt_policy_manifest_sha256",
    "python_version",
)


class HumanReviewError(RuntimeError):
    """Sanitized failure preparing or validating a semantic review artifact."""


def _validate_assembly_accession(value: str) -> str:
    if not is_versioned_assembly_accession(value):
        raise ValueError("assembly accession must be an exact GCA_/GCF_ accession.version")
    return value


AssemblyAccessionVersion = Annotated[
    str,
    Field(min_length=1, max_length=32),
    AfterValidator(_validate_assembly_accession),
]


class HumanBenchmarkAnchorTarget(StrictFrozenSchema):
    """One exact structured target expected before any benchmark generation call."""

    target_type: Literal["locus", "assembly", "lineage", "method"]
    locus_key: StableToken | None = None
    assembly_key: StableToken | None = None
    snapshot_key: StableToken | None = None
    term_key: StableToken | None = None
    method_definition_key: StableToken | None = None

    @model_validator(mode="after")
    def validate_target_shape(self) -> Self:
        expected = {
            "locus": (True, False, False, False),
            "assembly": (False, True, False, False),
            "lineage": (False, False, True, False),
            "method": (False, False, False, True),
        }[self.target_type]
        observed = (
            self.locus_key is not None,
            self.assembly_key is not None,
            self.snapshot_key is not None or self.term_key is not None,
            self.method_definition_key is not None,
        )
        if observed != expected:
            raise ValueError("benchmark anchor target fields do not match target_type")
        if self.target_type == "lineage" and (
            self.snapshot_key is None or self.term_key is None
        ):
            raise ValueError("benchmark lineage target requires snapshot_key and term_key")
        return self

    def sort_key(self) -> tuple[int, str, str]:
        order = {"locus": 0, "assembly": 1, "lineage": 2, "method": 3}
        first = (
            self.locus_key
            or self.assembly_key
            or self.snapshot_key
            or self.method_definition_key
            or ""
        )
        return (order[self.target_type], first, self.term_key or "")


class HumanBenchmarkRuntimeCapturePolicy(StrictFrozenSchema):
    """Frozen measurements required from the later local-loopback benchmark run."""

    capture_policy_schema_version: Literal["v0-local-runtime-capture-policy-v1"]
    clock_source: Literal["time.perf_counter_ns"]
    duration_unit: Literal["nanoseconds"]
    tokenizer_source: Literal["model-policy-tokenizer"]
    execution_order: Literal["case_ordinal_ascending"]
    warmup_runs_per_case: Literal[0]
    measured_runs_per_case: Literal[1]
    request_concurrency: Literal[1]
    retry_count: Literal[0]
    required_case_fields: tuple[StableToken, ...]
    required_run_fields: tuple[StableToken, ...]
    policy_sha256: Sha256

    @model_validator(mode="after")
    def validate_capture_policy(self) -> Self:
        if self.required_case_fields != _RUNTIME_CASE_FIELDS:
            raise ValueError("runtime case capture fields do not match the frozen policy")
        if self.required_run_fields != _RUNTIME_RUN_FIELDS:
            raise ValueError("runtime run capture fields do not match the frozen policy")
        if self.policy_sha256 != canonical_self_sha256(self, "policy_sha256"):
            raise ValueError("runtime capture policy checksum does not match")
        return self


class HumanBenchmarkCaseDefinition(StrictFrozenSchema):
    """One preregistered assembly-scoped hybrid question and answer location."""

    case_ordinal: int = Field(ge=1, le=10)
    case_key: StableToken
    assembly_accession_version: AssemblyAccessionVersion
    structured_question: AsciiQuestion
    question: AsciiQuestion
    page_limit: Literal[1]
    literature_top_k: Literal[8]
    expected_matched_targets: tuple[HumanBenchmarkAnchorTarget, ...] = Field(min_length=1)
    expected_unmatched_targets: tuple[HumanBenchmarkAnchorTarget, ...] = Field(min_length=1)
    response_path: RelativePath
    runtime_capture_path: RelativePath

    @model_validator(mode="after")
    def validate_case_contract(self) -> Self:
        expected_structured = (
            f"List loci in assembly {self.assembly_accession_version}."
        )
        if self.structured_question != expected_structured:
            raise ValueError("benchmark structured question is not the frozen assembly template")
        if self.question != f"{expected_structured} and explain the literature evidence":
            raise ValueError("benchmark hybrid question is not the frozen assembly template")
        expected_case_key = f"benchmark:v0-human-hybrid:{self.assembly_accession_version}"
        if self.case_key != expected_case_key:
            raise ValueError("benchmark case key does not bind its assembly")
        for targets, label in (
            (self.expected_matched_targets, "matched"),
            (self.expected_unmatched_targets, "unmatched"),
        ):
            if targets != tuple(sorted(targets, key=HumanBenchmarkAnchorTarget.sort_key)):
                raise ValueError(f"benchmark {label} targets must use canonical target order")
            if len(targets) != len(set(targets)):
                raise ValueError(f"benchmark {label} targets must be unique")
        if set(self.expected_matched_targets).intersection(self.expected_unmatched_targets):
            raise ValueError("benchmark matched and unmatched target sets must be disjoint")
        if any(target.target_type != "lineage" for target in self.expected_matched_targets):
            raise ValueError("V0 benchmark matched targets must be exact lineage targets")
        assembly_key = f"assembly:ncbi:{self.assembly_accession_version}"
        if sum(
            target.target_type == "assembly" and target.assembly_key == assembly_key
            for target in self.expected_unmatched_targets
        ) != 1:
            raise ValueError("benchmark case must preregister its unmatched assembly target")
        if not any(
            target.target_type == "locus"
            for target in self.expected_unmatched_targets
        ):
            raise ValueError("benchmark case must preregister at least one unmatched locus target")
        return self


class HumanBenchmarkDefinition(StrictFrozenSchema):
    """Exact ten-case input frozen before local model generation."""

    definition_schema_version: Literal["v0-human-benchmark-definition-v1"] = (
        HUMAN_BENCHMARK_DEFINITION_VERSION
    )
    rubric_version: Literal["rubric:v0-cited-claim-support-v1"] = HUMAN_REVIEW_RUBRIC_VERSION
    release_key: PublishedReleaseKey
    release_manifest_sha256: Sha256
    corpus_release_key: CorpusReleaseKey
    corpus_manifest_sha256: Sha256
    binding_manifest_sha256: Sha256
    anchor_manifest_sha256: Sha256
    model_policy_manifest_sha256: Sha256
    prompt_policy_manifest_sha256: Sha256
    provider_key: StableToken
    model_key: StableToken
    model_revision: StableToken
    generation_policy_key: StableToken
    prompt_policy_key: StableToken
    prompt_source_text_sha256: Sha256
    timeout_seconds: int = Field(ge=1, le=300)
    runtime_capture_policy: HumanBenchmarkRuntimeCapturePolicy
    cases: tuple[HumanBenchmarkCaseDefinition, ...] = Field(min_length=10, max_length=10)
    definition_sha256: Sha256

    @model_validator(mode="after")
    def validate_definition(self) -> Self:
        ordinals = tuple(case.case_ordinal for case in self.cases)
        if ordinals != tuple(range(1, 11)):
            raise ValueError("human benchmark cases must use contiguous order 1..10")
        if tuple(case.assembly_accession_version for case in self.cases) != APPROVED_ASSEMBLIES:
            raise ValueError("human benchmark must cover the exact approved assemblies canonically")
        for values, label in (
            (tuple(case.case_key for case in self.cases), "case keys"),
            (
                tuple(case.assembly_accession_version for case in self.cases),
                "assembly accessions",
            ),
            (tuple(case.question for case in self.cases), "questions"),
            (tuple(case.response_path for case in self.cases), "response paths"),
            (
                tuple(case.runtime_capture_path for case in self.cases),
                "runtime capture paths",
            ),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"human benchmark {label} must be unique")
        if self.definition_sha256 != canonical_self_sha256(self, "definition_sha256"):
            raise ValueError("human benchmark definition checksum does not match")
        return self


class HumanReviewClaimTarget(StrictFrozenSchema):
    """One exact generated claim the named reviewer must label."""

    claim_id: str = Field(pattern=r"^C[1-9][0-9]*$")
    claim_sha256: Sha256


class HumanReviewCase(StrictFrozenSchema):
    """One complete strict hybrid response in the reviewer packet."""

    case_ordinal: int = Field(ge=1, le=10)
    case_key: StableToken
    assembly_accession_version: AssemblyAccessionVersion
    response: HybridRouteAnswer
    response_sha256: Sha256
    answer_sha256: Sha256
    context_sha256: Sha256
    provider_identity_sha256: Sha256
    claims: tuple[HumanReviewClaimTarget, ...] = Field(min_length=1)
    case_sha256: Sha256

    @model_validator(mode="after")
    def validate_case_hash(self) -> Self:
        if self.response_sha256 != canonical_model_sha256(self.response):
            raise ValueError("human review response checksum does not match")
        if self.answer_sha256 != self.response.answer_sha256:
            raise ValueError("human review answer checksum does not match")
        generation = self.response.generation
        if generation is None:
            raise ValueError("human review case requires generated claims")
        if self.context_sha256 != generation.context_sha256:
            raise ValueError("human review context checksum does not match")
        if self.provider_identity_sha256 != canonical_model_sha256(generation.provider_identity):
            raise ValueError("human review provider checksum does not match")
        expected_claims = tuple(
            HumanReviewClaimTarget(
                claim_id=claim.claim_id,
                claim_sha256=canonical_model_sha256(claim),
            )
            for claim in generation.claims
        )
        if self.claims != expected_claims:
            raise ValueError("human review claim identities do not match")
        if self.case_sha256 != canonical_self_sha256(self, "case_sha256"):
            raise ValueError("human review case checksum does not match")
        return self


class HumanReviewPacket(StrictFrozenSchema):
    """Immutable evidence packet presented to the accountable reviewer."""

    packet_schema_version: Literal["v0-human-review-packet-v1"] = HUMAN_REVIEW_PACKET_VERSION
    rubric_version: Literal["rubric:v0-cited-claim-support-v1"] = HUMAN_REVIEW_RUBRIC_VERSION
    definition_sha256: Sha256
    release_key: PublishedReleaseKey
    release_manifest_sha256: Sha256
    corpus_release_key: CorpusReleaseKey
    corpus_manifest_sha256: Sha256
    binding_manifest_sha256: Sha256
    anchor_manifest_sha256: Sha256
    model_policy_manifest_sha256: Sha256
    prompt_policy_manifest_sha256: Sha256
    cases: tuple[HumanReviewCase, ...] = Field(min_length=10, max_length=10)
    packet_sha256: Sha256

    @model_validator(mode="after")
    def validate_packet(self) -> Self:
        if tuple(case.case_ordinal for case in self.cases) != tuple(range(1, 11)):
            raise ValueError("human review packet must retain all ten cases in order")
        if len({case.case_key for case in self.cases}) != 10:
            raise ValueError("human review packet contains duplicate case keys")
        if self.packet_sha256 != canonical_self_sha256(self, "packet_sha256"):
            raise ValueError("human review packet checksum does not match")
        return self


type HumanClaimLabel = Literal["supported", "partially_supported", "unsupported"]


class HumanClaimDecision(StrictFrozenSchema):
    """One named human decision bound to exact case, answer, and claim bytes."""

    case_ordinal: int = Field(ge=1, le=10)
    case_key: StableToken
    case_sha256: Sha256
    answer_sha256: Sha256
    claim_id: str = Field(pattern=r"^C[1-9][0-9]*$")
    claim_sha256: Sha256
    label: HumanClaimLabel
    review_note: AsciiQuestion | None = None


class HumanReviewSubmission(StrictFrozenSchema):
    """Self-checksummed sign-off completed by one accountable human reviewer."""

    submission_schema_version: Literal["v0-human-review-submission-v1"] = (
        HUMAN_REVIEW_SUBMISSION_VERSION
    )
    rubric_version: Literal["rubric:v0-cited-claim-support-v1"] = HUMAN_REVIEW_RUBRIC_VERSION
    packet_sha256: Sha256
    reviewer_key: StableToken
    reviewer_name: AsciiQuestion
    reviewer_role: Literal["human_domain_reviewer"]
    reviewed_at: Rfc3339Utc
    attestation: Literal["I reviewed every claim against only its cited current evidence."]
    decisions: tuple[HumanClaimDecision, ...] = Field(min_length=1)
    submission_sha256: Sha256

    @field_validator("decisions")
    @classmethod
    def validate_decision_order(
        cls, decisions: tuple[HumanClaimDecision, ...]
    ) -> tuple[HumanClaimDecision, ...]:
        keys = tuple((decision.case_ordinal, decision.claim_id) for decision in decisions)
        if keys != tuple(sorted(keys, key=lambda item: (item[0], int(item[1][1:])))):
            raise ValueError("human claim decisions must use canonical case/claim order")
        if len(keys) != len(set(keys)):
            raise ValueError("human claim decisions must not contain duplicates")
        return decisions

    @model_validator(mode="after")
    def validate_submission_hash(self) -> Self:
        if self.submission_sha256 != canonical_self_sha256(self, "submission_sha256"):
            raise ValueError("human review submission checksum does not match")
        return self


class HumanReviewMetrics(StrictFrozenSchema):
    case_count: int = Field(ge=0)
    claim_count: int = Field(ge=0)
    reviewed_claim_count: int = Field(ge=0)
    supported_count: int = Field(ge=0)
    partially_supported_count: int = Field(ge=0)
    unsupported_count: int = Field(ge=0)
    unreviewed_count: int = Field(ge=0)
    citation_existence: str = Field(pattern=r"^(?:0|1)\.[0-9]{12}$")
    release_match: str = Field(pattern=r"^(?:0|1)\.[0-9]{12}$")
    locator_validity: str = Field(pattern=r"^(?:0|1)\.[0-9]{12}$")
    citation_coverage: str = Field(pattern=r"^(?:0|1)\.[0-9]{12}$")


class HumanReviewEvaluation(StrictFrozenSchema):
    """Fail-closed machine evaluation of a human-authored submission."""

    evaluation_schema_version: Literal["v0-human-review-evaluation-v1"] = (
        HUMAN_REVIEW_EVALUATION_VERSION
    )
    status: Literal["passed", "failed"]
    packet_sha256: Sha256
    submission_sha256: Sha256
    reviewer_key: StableToken
    reviewed_at: Rfc3339Utc
    metrics: HumanReviewMetrics
    issue_codes: tuple[StableToken, ...]
    evaluation_sha256: Sha256

    @field_validator("issue_codes")
    @classmethod
    def validate_issue_codes(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if values != tuple(sorted(values)) or len(values) != len(set(values)):
            raise ValueError("human review issue codes must be sorted and unique")
        return values

    @model_validator(mode="after")
    def validate_evaluation(self) -> Self:
        passed = not self.issue_codes
        if self.status != ("passed" if passed else "failed"):
            raise ValueError("human review status does not match issue codes")
        if self.evaluation_sha256 != canonical_self_sha256(self, "evaluation_sha256"):
            raise ValueError("human review evaluation checksum does not match")
        return self


def build_human_benchmark_runtime_capture_policy() -> HumanBenchmarkRuntimeCapturePolicy:
    """Build the fixed measurement contract without running the local provider."""

    payload: dict[str, object] = {
        "capture_policy_schema_version": "v0-local-runtime-capture-policy-v1",
        "clock_source": "time.perf_counter_ns",
        "duration_unit": "nanoseconds",
        "tokenizer_source": "model-policy-tokenizer",
        "execution_order": "case_ordinal_ascending",
        "warmup_runs_per_case": 0,
        "measured_runs_per_case": 1,
        "request_concurrency": 1,
        "retry_count": 0,
        "required_case_fields": _RUNTIME_CASE_FIELDS,
        "required_run_fields": _RUNTIME_RUN_FIELDS,
        "policy_sha256": "0" * 64,
    }
    payload["policy_sha256"] = canonical_self_sha256(payload, "policy_sha256")
    return HumanBenchmarkRuntimeCapturePolicy.model_validate(payload)


def build_human_benchmark_definition(
    *,
    structured_manifest: StructuredActivationManifest,
    public_locus_manifest: PublicLocusMembershipManifest,
    public_assertion_manifest: PublicAssertionMembershipManifest,
    ncbi_snapshot_manifest: TaxonomySnapshotManifest,
    assembly_assignment_manifest: AssemblyTaxonAssignmentManifest,
    study_formal_mapping_manifest: StudyFormalMappingManifest,
    corpus_manifest: CorpusManifest,
    anchor_manifest: CorpusAnchorManifest,
    binding_manifest: HybridReleaseBindingManifest,
    model_policy_manifest: LocalModelPolicyManifest,
    prompt_policy_manifest: PromptPolicyManifest,
) -> HumanBenchmarkDefinition:
    """Derive ten preregistered cases from exact activation-packet manifests.

    The builder performs no database or provider I/O.  In particular, it does not treat an
    unmatched assembly or locus target as literature evidence.  Those exact targets are frozen
    as expected-unmatched values before generation, while the study-lineage target must resolve
    to a curator-authored anchor in the candidate corpus.
    """

    try:
        structured = _round_trip_model(structured_manifest)
        public_loci = _round_trip_model(public_locus_manifest)
        public_assertions = _round_trip_model(public_assertion_manifest)
        ncbi = _round_trip_model(ncbi_snapshot_manifest)
        assignments = _round_trip_model(assembly_assignment_manifest)
        mapping = _round_trip_model(study_formal_mapping_manifest)
        corpus = _round_trip_model(corpus_manifest)
        anchors = _round_trip_model(anchor_manifest)
        binding = _round_trip_model(binding_manifest)
        model = _round_trip_model(model_policy_manifest)
        prompt = _round_trip_model(prompt_policy_manifest)

        if (
            structured.release_key != V0_STRUCTURED_RELEASE_KEY
            or public_loci.release_key != structured.release_key
            or public_assertions.release_key != structured.release_key
            or assignments.release_key != structured.release_key
            or mapping.release_key != structured.release_key
            or structured.public_locus_membership_manifest_sha256
            != public_loci.manifest_sha256
            or structured.public_assertion_membership_manifest_sha256
            != public_assertions.manifest_sha256
            or public_assertions.locus_membership_manifest_sha256
            != public_loci.manifest_sha256
            or structured.ncbi_snapshot_manifest_sha256 != ncbi.manifest_sha256
            or assignments.ncbi_snapshot_manifest_sha256 != ncbi.manifest_sha256
            or structured.study_formal_mapping_manifest_sha256 != mapping.manifest_sha256
        ):
            raise ValueError("structured benchmark inputs do not form one activation packet")
        if (
            ncbi.release_role != "assembly_source_taxonomy"
            or ncbi.domain != "host"
            or corpus.corpus_release_key != V0_CORPUS_RELEASE_KEY
            or anchors.corpus_release_key != corpus.corpus_release_key
            or anchors.corpus_manifest_sha256 != corpus.manifest_sha256
            or anchors.anchor_policy_key != corpus.anchor_policy_key
        ):
            raise ValueError("corpus or host-taxonomy benchmark identities do not match")
        if len(binding.bindings) != 1:
            raise ValueError("V0 benchmark requires exactly one hybrid binding")
        exact_binding = binding.bindings[0]
        if (
            exact_binding.release_key != structured.release_key
            or exact_binding.release_manifest_sha256 != structured.manifest_sha256
            or exact_binding.corpus_release_key != corpus.corpus_release_key
            or exact_binding.corpus_manifest_sha256 != corpus.manifest_sha256
        ):
            raise ValueError("hybrid binding does not authorize the exact benchmark pair")

        provider_identity = model.provider_identity(prompt)
        matched_lineage = _select_benchmark_lineage_target(
            mapping=mapping,
            anchors=anchors,
        )
        memberships_by_assembly: dict[str, list[str]] = {
            accession: [] for accession in APPROVED_ASSEMBLIES
        }
        for row in public_loci.memberships:
            memberships_by_assembly[row.assembly_accession_version].append(row.locus_key)
        assignments_by_assembly = {
            row.assembly_accession_version: row for row in assignments.assignments
        }
        viral_assertion_loci = tuple(
            row.locus_key
            for row in public_assertions.memberships
            if row.assertion_type == "viral_major_taxon"
        )
        if (
            len(viral_assertion_loci) != len(set(viral_assertion_loci))
            or set(viral_assertion_loci)
            != {row.locus_key for row in public_loci.memberships}
        ):
            raise ValueError("every public locus requires one study viral-lineage assertion")

        cases: list[HumanBenchmarkCaseDefinition] = []
        for ordinal, accession in enumerate(APPROVED_ASSEMBLIES, start=1):
            locus_keys = tuple(sorted(memberships_by_assembly[accession]))
            if not locus_keys:
                raise ValueError("an approved assembly has no public benchmark locus")
            selected_locus_key = locus_keys[0]
            host_assignment = assignments_by_assembly.get(accession)
            if host_assignment is None:
                raise ValueError("an approved assembly has no host-taxonomy assignment")
            expected_unmatched = tuple(
                sorted(
                    (
                        HumanBenchmarkAnchorTarget(
                            target_type="locus",
                            locus_key=selected_locus_key,
                        ),
                        HumanBenchmarkAnchorTarget(
                            target_type="assembly",
                            assembly_key=f"assembly:ncbi:{accession}",
                        ),
                        HumanBenchmarkAnchorTarget(
                            target_type="lineage",
                            snapshot_key=ncbi.snapshot_key,
                            term_key=host_assignment.term_key,
                        ),
                    ),
                    key=HumanBenchmarkAnchorTarget.sort_key,
                )
            )
            structured_question = f"List loci in assembly {accession}."
            cases.append(
                HumanBenchmarkCaseDefinition(
                    case_ordinal=ordinal,
                    case_key=f"benchmark:v0-human-hybrid:{accession}",
                    assembly_accession_version=accession,
                    structured_question=structured_question,
                    question=(
                        f"{structured_question} and explain the literature evidence"
                    ),
                    page_limit=_CASE_PAGE_LIMIT,
                    literature_top_k=_CASE_LITERATURE_TOP_K,
                    expected_matched_targets=(matched_lineage,),
                    expected_unmatched_targets=expected_unmatched,
                    response_path=f"case-{ordinal:02d}.json",
                    runtime_capture_path=f"case-{ordinal:02d}.runtime.json",
                )
            )

        runtime_policy = build_human_benchmark_runtime_capture_policy()
        payload: dict[str, object] = {
            "definition_schema_version": HUMAN_BENCHMARK_DEFINITION_VERSION,
            "rubric_version": HUMAN_REVIEW_RUBRIC_VERSION,
            "release_key": structured.release_key,
            "release_manifest_sha256": structured.manifest_sha256,
            "corpus_release_key": corpus.corpus_release_key,
            "corpus_manifest_sha256": corpus.manifest_sha256,
            "binding_manifest_sha256": binding.manifest_sha256,
            "anchor_manifest_sha256": anchors.anchor_manifest_sha256,
            "model_policy_manifest_sha256": model.manifest_sha256,
            "prompt_policy_manifest_sha256": prompt.manifest_sha256,
            "provider_key": provider_identity.provider_key,
            "model_key": provider_identity.model_key,
            "model_revision": provider_identity.model_revision,
            "generation_policy_key": provider_identity.generation_policy_key,
            "prompt_policy_key": provider_identity.prompt_policy_key,
            "prompt_source_text_sha256": provider_identity.prompt_policy_sha256,
            "timeout_seconds": provider_identity.timeout_seconds,
            "runtime_capture_policy": runtime_policy,
            "cases": tuple(cases),
            "definition_sha256": "0" * 64,
        }
        payload["definition_sha256"] = canonical_self_sha256(
            payload,
            "definition_sha256",
        )
        return HumanBenchmarkDefinition.model_validate(payload)
    except HumanReviewError:
        raise
    except Exception:
        raise HumanReviewError(
            "The V0 human benchmark definition inputs are incomplete or inconsistent."
        ) from None


def load_human_benchmark_definition(
    path: Path, *, approved_definition_sha256: str
) -> HumanBenchmarkDefinition:
    definition = _load_json(path, HumanBenchmarkDefinition, _MAX_DEFINITION_BYTES)
    if definition.definition_sha256 != approved_definition_sha256:
        raise HumanReviewError(
            "The human benchmark definition does not match the approved checksum."
        )
    return definition


def build_human_review_packet(
    definition: HumanBenchmarkDefinition,
    *,
    answers_root: Path,
) -> HumanReviewPacket:
    """Load and revalidate all ten preregistered real hybrid responses."""

    try:
        trusted_definition = HumanBenchmarkDefinition.model_validate_json(
            definition.model_dump_json()
        )
        if answers_root.is_symlink() or not answers_root.is_dir():
            raise ValueError
        cases = tuple(
            _build_case(trusted_definition, case, answers_root=answers_root)
            for case in trusted_definition.cases
        )
        payload: dict[str, object] = {
            "packet_schema_version": HUMAN_REVIEW_PACKET_VERSION,
            "rubric_version": trusted_definition.rubric_version,
            "definition_sha256": trusted_definition.definition_sha256,
            "release_key": trusted_definition.release_key,
            "release_manifest_sha256": trusted_definition.release_manifest_sha256,
            "corpus_release_key": trusted_definition.corpus_release_key,
            "corpus_manifest_sha256": trusted_definition.corpus_manifest_sha256,
            "binding_manifest_sha256": trusted_definition.binding_manifest_sha256,
            "anchor_manifest_sha256": trusted_definition.anchor_manifest_sha256,
            "model_policy_manifest_sha256": (trusted_definition.model_policy_manifest_sha256),
            "prompt_policy_manifest_sha256": (trusted_definition.prompt_policy_manifest_sha256),
            "cases": cases,
            "packet_sha256": "0" * 64,
        }
        payload["packet_sha256"] = canonical_self_sha256(payload, "packet_sha256")
        return HumanReviewPacket.model_validate(payload)
    except HumanReviewError:
        raise
    except Exception:
        raise HumanReviewError(
            "The human semantic review packet could not be built safely."
        ) from None


def load_human_review_packet(path: Path, *, approved_packet_sha256: str) -> HumanReviewPacket:
    packet = _load_json(path, HumanReviewPacket, _MAX_PACKET_BYTES)
    if packet.packet_sha256 != approved_packet_sha256:
        raise HumanReviewError("The human review packet does not match the approved checksum.")
    return packet


def load_human_review_submission(
    path: Path, *, approved_submission_sha256: str
) -> HumanReviewSubmission:
    submission = _load_json(path, HumanReviewSubmission, _MAX_REVIEW_BYTES)
    if submission.submission_sha256 != approved_submission_sha256:
        raise HumanReviewError("The human review submission does not match the approved checksum.")
    return submission


def evaluate_human_review(
    packet: HumanReviewPacket,
    submission: HumanReviewSubmission,
) -> HumanReviewEvaluation:
    """Require exactly one supported decision for every retained factual claim."""

    trusted_packet = HumanReviewPacket.model_validate_json(packet.model_dump_json())
    trusted_submission = HumanReviewSubmission.model_validate_json(submission.model_dump_json())
    issues: set[str] = set()
    if trusted_submission.packet_sha256 != trusted_packet.packet_sha256:
        issues.add("packet_identity_mismatch")

    expected: dict[tuple[int, str], tuple[HumanReviewCase, HumanReviewClaimTarget]] = {}
    for case in trusted_packet.cases:
        for claim in case.claims:
            expected[(case.case_ordinal, claim.claim_id)] = (case, claim)
    observed = {
        (decision.case_ordinal, decision.claim_id): decision
        for decision in trusted_submission.decisions
    }
    if set(observed) - set(expected):
        issues.add("unexpected_claim_decision")
    missing = set(expected) - set(observed)
    if missing:
        issues.add("unreviewed_claim")

    supported = 0
    partial = 0
    unsupported = 0
    reviewed = 0
    for key, (case, claim) in expected.items():
        decision = observed.get(key)
        if decision is None:
            continue
        if (
            decision.case_key != case.case_key
            or decision.case_sha256 != case.case_sha256
            or decision.answer_sha256 != case.answer_sha256
            or decision.claim_sha256 != claim.claim_sha256
        ):
            issues.add("claim_identity_mismatch")
            continue
        reviewed += 1
        if decision.label == "supported":
            supported += 1
        elif decision.label == "partially_supported":
            partial += 1
            issues.add("partially_supported_claim")
        else:
            unsupported += 1
            issues.add("unsupported_claim")

    claim_count = len(expected)
    unreviewed = claim_count - reviewed
    if unreviewed:
        issues.add("unreviewed_claim")
    if len(trusted_packet.cases) != 10:
        issues.add("ten_case_cohort_incomplete")
    mechanical = _mechanical_metrics(trusted_packet)
    if any(value != "1.000000000000" for value in mechanical):
        issues.add("mechanical_gate_failed")

    metrics = HumanReviewMetrics(
        case_count=len(trusted_packet.cases),
        claim_count=claim_count,
        reviewed_claim_count=reviewed,
        supported_count=supported,
        partially_supported_count=partial,
        unsupported_count=unsupported,
        unreviewed_count=unreviewed,
        citation_existence=mechanical[0],
        release_match=mechanical[1],
        locator_validity=mechanical[2],
        citation_coverage=mechanical[3],
    )
    payload: dict[str, object] = {
        "evaluation_schema_version": HUMAN_REVIEW_EVALUATION_VERSION,
        "status": "passed" if not issues else "failed",
        "packet_sha256": trusted_packet.packet_sha256,
        "submission_sha256": trusted_submission.submission_sha256,
        "reviewer_key": trusted_submission.reviewer_key,
        "reviewed_at": trusted_submission.reviewed_at,
        "metrics": metrics,
        "issue_codes": tuple(sorted(issues)),
        "evaluation_sha256": "0" * 64,
    }
    payload["evaluation_sha256"] = canonical_self_sha256(payload, "evaluation_sha256")
    return HumanReviewEvaluation.model_validate(payload)


def serialize_review_artifact(value: StrictFrozenSchema) -> str:
    """Return the same canonical JSON used by every review checksum."""

    return canonical_model_json(value)


def _build_case(
    definition: HumanBenchmarkDefinition,
    case: HumanBenchmarkCaseDefinition,
    *,
    answers_root: Path,
) -> HumanReviewCase:
    path = answers_root / case.response_path
    if _has_symlink_component(answers_root, case.response_path):
        raise HumanReviewError("A preregistered hybrid response path is unavailable.")
    response = _load_json(path, HybridRouteAnswer, _MAX_RESPONSE_BYTES)
    trusted_response = revalidate_rag_response(response)
    if not isinstance(trusted_response, HybridRouteAnswer):
        raise HumanReviewError("A preregistered response is not a hybrid answer.")
    _validate_case_response(definition, case, trusted_response)
    generation = trusted_response.generation
    assert generation is not None
    claims = tuple(
        HumanReviewClaimTarget(
            claim_id=claim.claim_id,
            claim_sha256=canonical_model_sha256(claim),
        )
        for claim in generation.claims
    )
    payload: dict[str, object] = {
        "case_ordinal": case.case_ordinal,
        "case_key": case.case_key,
        "assembly_accession_version": case.assembly_accession_version,
        "response": trusted_response,
        "response_sha256": canonical_model_sha256(trusted_response),
        "answer_sha256": trusted_response.answer_sha256,
        "context_sha256": generation.context_sha256,
        "provider_identity_sha256": canonical_model_sha256(generation.provider_identity),
        "claims": claims,
        "case_sha256": "0" * 64,
    }
    payload["case_sha256"] = canonical_self_sha256(payload, "case_sha256")
    return HumanReviewCase.model_validate(payload)


def _validate_case_response(
    definition: HumanBenchmarkDefinition,
    case: HumanBenchmarkCaseDefinition,
    response: HybridRouteAnswer,
) -> None:
    request = response.original_request
    result = response.query_success.structured_result
    retrieved = response.retrieved_chunks
    generation = response.generation
    if (
        request.question != case.question
        or request.release_key != definition.release_key
        or request.corpus_release_key != definition.corpus_release_key
        or request.page is None
        or request.page.limit != case.page_limit
        or request.page.cursor is not None
        or request.literature_top_k != case.literature_top_k
        or result.release.release_key != definition.release_key
        or result.release.manifest_sha256 != definition.release_manifest_sha256
        or retrieved.corpus_release_key != definition.corpus_release_key
        or retrieved.corpus_manifest_sha256 != definition.corpus_manifest_sha256
        or generation is None
        or not generation.claims
        or not response.execution.structured_retrieval_executed
        or not response.execution.literature_retrieval_executed
        or not response.execution.generation_executed
    ):
        raise HumanReviewError("A preregistered hybrid response identity is inconsistent.")
    expected_identity = ProviderIdentity(
        provider_key=definition.provider_key,
        model_key=definition.model_key,
        model_revision=definition.model_revision,
        provider_artifact_sha256=definition.model_policy_manifest_sha256,
        generation_policy_key=definition.generation_policy_key,
        prompt_policy_key=definition.prompt_policy_key,
        prompt_policy_sha256=definition.prompt_source_text_sha256,
        temperature=0,
        max_output_bytes=32768,
        timeout_seconds=definition.timeout_seconds,
        retry_count=0,
    )
    if generation.provider_identity != expected_identity:
        raise HumanReviewError("A preregistered provider identity is inconsistent.")

    plan = response.query_success.query_plan
    data = result.data
    assembly_key = f"assembly:ncbi:{case.assembly_accession_version}"
    if (
        not isinstance(plan, ListLociPlan)
        or not isinstance(plan.scope, FilteredScope)
        or not isinstance(data, LocusPageData)
        or not data.items
        or plan.original_question != case.structured_question
        or plan.page.limit != case.page_limit
        or plan.page.cursor is not None
        or sum(
            isinstance(query_filter, AssemblyFilter) and query_filter.assembly_key == assembly_key
            for query_filter in plan.scope.filters
        )
        != 1
    ):
        raise HumanReviewError("A preregistered case is not an assembly-scoped locus result.")
    if not any(item.assembly_key == assembly_key for item in data.items):
        raise HumanReviewError(
            "A preregistered case has no locus for its exact assembly selector."
        )
    try:
        extracted_targets = tuple(
            _benchmark_target_from_structured(target)
            for target in extract_structured_anchor_targets(response.query_success)
        )
        expected_targets = tuple(
            sorted(
                (*case.expected_matched_targets, *case.expected_unmatched_targets),
                key=HumanBenchmarkAnchorTarget.sort_key,
            )
        )
        if extracted_targets != expected_targets:
            raise ValueError
        anchor_target_by_key = {
            anchor.anchor_key: _benchmark_target_from_anchor(anchor)
            for anchor in retrieved.anchors_applied
        }
    except Exception:
        raise HumanReviewError(
            "A preregistered case produced an unexpected structured anchor target."
        ) from None
    expected_matched = set(case.expected_matched_targets)
    if set(anchor_target_by_key.values()) != expected_matched:
        raise HumanReviewError(
            "A preregistered case applied an unexpected structured-target anchor."
        )
    anchored_target_hits = {
        anchor_target_by_key[anchor_key]
        for chunk in retrieved.chunks
        if chunk.retrieval_tier == "anchored"
        for anchor_key in chunk.matched_anchors
    }
    expected_diagnostics = (
        ("structured_anchor_unmatched",)
        if case.expected_unmatched_targets
        else ()
    )
    if (
        retrieved.anchor_mode != "anchored_then_corpus_fill"
        or not retrieved.anchors_applied
        or not any(chunk.retrieval_tier == "anchored" for chunk in retrieved.chunks)
        or anchored_target_hits != expected_matched
        or "anchor_miss" in retrieved.warnings
        or response.anchor_diagnostics != expected_diagnostics
    ):
        raise HumanReviewError(
            "A preregistered case lacks exact structured-target anchor evidence."
        )
    context = build_hybrid_context(
        original_question=request.question,
        query_success=response.query_success,
        retrieved_chunks=retrieved,
    )
    if context.context_sha256 != generation.context_sha256:
        raise HumanReviewError("A preregistered ContextPack identity is inconsistent.")


def _round_trip_model[ModelT: BaseModel](value: ModelT) -> ModelT:
    return type(value).model_validate_json(value.model_dump_json(), strict=True)


def _select_benchmark_lineage_target(
    *,
    mapping: StudyFormalMappingManifest,
    anchors: CorpusAnchorManifest,
) -> HumanBenchmarkAnchorTarget:
    lineage_targets = {
        (anchor.anchor.snapshot_key, anchor.anchor.term_key)
        for anchor in anchors.anchors
        if isinstance(anchor.anchor, LineageAnchor)
    }
    candidates = tuple(
        row
        for row in mapping.mappings
        if row.relation == "renamed_to"
        and (row.study_snapshot_key, row.study_term_key) in lineage_targets
        and (row.formal_snapshot_key, row.formal_term_key) in lineage_targets
    )
    if len(candidates) != 1:
        raise ValueError("benchmark requires one exact study/formal lineage anchor bridge")
    row = candidates[0]
    return HumanBenchmarkAnchorTarget(
        target_type="lineage",
        snapshot_key=row.study_snapshot_key,
        term_key=row.study_term_key,
    )


def _benchmark_target_from_structured(
    target: StructuredAnchorTarget,
) -> HumanBenchmarkAnchorTarget:
    return HumanBenchmarkAnchorTarget(
        target_type=target.target_type,
        locus_key=target.locus_key,
        assembly_key=target.assembly_key,
        snapshot_key=target.snapshot_key,
        term_key=target.term_key,
        method_definition_key=target.method_definition_key,
    )


def _benchmark_target_from_anchor(anchor: object) -> HumanBenchmarkAnchorTarget:
    try:
        payload = anchor.model_dump(mode="python")  # type: ignore[attr-defined]
        target_type = payload.pop("anchor_type")
        payload.pop("anchor_key")
        if target_type not in {"locus", "assembly", "lineage", "method"}:
            raise ValueError
        return HumanBenchmarkAnchorTarget(target_type=target_type, **payload)
    except (AttributeError, TypeError, ValidationError, ValueError):
        raise ValueError("anchor is not an exact structured target") from None


def _mechanical_metrics(packet: HumanReviewPacket) -> tuple[str, str, str, str]:
    citation_checks: list[bool] = []
    release_checks: list[bool] = []
    locator_checks: list[bool] = []
    coverage_checks: list[bool] = []
    for case in packet.cases:
        response = case.response
        generation = response.generation
        if generation is None:
            continue
        chunks = {chunk.citation_id: chunk for chunk in response.retrieved_chunks.chunks}
        citations = {citation.citation_id: citation for citation in generation.citations}
        release_checks.append(
            response.query_success.structured_result.release.release_key == packet.release_key
            and response.query_success.structured_result.release.manifest_sha256
            == packet.release_manifest_sha256
            and response.retrieved_chunks.corpus_release_key == packet.corpus_release_key
            and response.retrieved_chunks.corpus_manifest_sha256 == packet.corpus_manifest_sha256
        )
        for claim in generation.claims:
            coverage_checks.append(bool(claim.citation_ids))
            for span in claim.evidence_spans:
                chunk = chunks.get(span.citation_id)
                citation = citations.get(span.citation_id)
                citation_checks.append(
                    chunk is not None
                    and citation is not None
                    and span.quote in chunk.text
                    and citation.chunk_key == chunk.chunk_key
                )
                locator_checks.append(
                    chunk is not None
                    and bool(chunk.locator_text)
                    and hashlib.sha256(chunk.text.encode("utf-8")).hexdigest() == chunk.text_sha256
                )
    return tuple(
        _ratio(values)
        for values in (citation_checks, release_checks, locator_checks, coverage_checks)
    )  # type: ignore[return-value]


def _ratio(values: list[bool]) -> str:
    if not values:
        return "0.000000000000"
    value = Decimal(sum(values)) / Decimal(len(values))
    return format(value.quantize(_METRIC_QUANTUM, rounding=ROUND_HALF_EVEN), "f")


def _load_json[SchemaT: StrictFrozenSchema](
    path: Path,
    schema: type[SchemaT],
    maximum_bytes: int,
) -> SchemaT:
    try:
        if path.is_symlink() or not path.is_file():
            raise OSError
        raw = path.read_bytes()
        if not raw or len(raw) > maximum_bytes:
            raise OSError
        return schema.model_validate_json(raw)
    except (OSError, UnicodeError, ValidationError):
        raise HumanReviewError(
            "A human semantic review artifact is unavailable or invalid."
        ) from None


def _has_symlink_component(root: Path, relative_path: str) -> bool:
    current = root
    for part in Path(relative_path).parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


__all__ = [
    "HUMAN_BENCHMARK_DEFINITION_VERSION",
    "HUMAN_REVIEW_EVALUATION_VERSION",
    "HUMAN_REVIEW_PACKET_VERSION",
    "HUMAN_REVIEW_RUBRIC_VERSION",
    "HUMAN_REVIEW_SUBMISSION_VERSION",
    "HumanBenchmarkAnchorTarget",
    "HumanBenchmarkCaseDefinition",
    "HumanBenchmarkDefinition",
    "HumanBenchmarkRuntimeCapturePolicy",
    "HumanClaimDecision",
    "HumanReviewCase",
    "HumanReviewError",
    "HumanReviewEvaluation",
    "HumanReviewPacket",
    "HumanReviewSubmission",
    "build_human_benchmark_definition",
    "build_human_benchmark_runtime_capture_policy",
    "build_human_review_packet",
    "evaluate_human_review",
    "load_human_benchmark_definition",
    "load_human_review_packet",
    "load_human_review_submission",
    "serialize_review_artifact",
]
