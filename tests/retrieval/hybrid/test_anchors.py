"""Trusted structured-result to curated-corpus anchor resolution tests."""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from pydantic import TypeAdapter
from sqlalchemy import Engine, create_engine, event, func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool
from sqlalchemy.schema import CreateSchema, DropSchema

from eve_relation_rag.application.literature import LiteratureRetrievalService
from eve_relation_rag.application.rag import RagQueryApplication
from eve_relation_rag.config import get_settings
from eve_relation_rag.db import Base
from eve_relation_rag.db.models import (
    CorpusDocumentMembership,
    CorpusRelease,
    CorpusValidationReceipt,
    Document,
    DocumentAnchor,
    DocumentChunk,
    DocumentEmbedding,
)
from eve_relation_rag.generation.composer import GenerationComposer
from eve_relation_rag.generation.context import APPROVED_ANSWER_INSTRUCTIONS
from eve_relation_rag.hybrid.bindings import ApprovedHybridBindingRegistry
from eve_relation_rag.hybrid.contracts import (
    BINDING_MANIFEST_VERSION,
    ContextPack,
    EvidenceSpan,
    GeneratedAnswerDraft,
    HybridReleaseBinding,
    HybridReleaseBindingManifest,
    HybridRouteAnswer,
    LiteratureClaim,
    ProviderIdentity,
    RagQueryRequest,
    canonical_self_sha256,
)
from eve_relation_rag.literature.capability import CorpusCapability
from eve_relation_rag.literature.chunking import TokenSpan
from eve_relation_rag.literature.contracts import (
    AssemblyAnchor,
    CorpusManifest,
    LineageAnchor,
    LocusAnchor,
    MethodAnchor,
    RetrievalAnchor,
)
from eve_relation_rag.literature.embeddings import embed_candidate_corpus
from eve_relation_rag.literature.gate import PublishedCorpusGate
from eve_relation_rag.literature.hashing import anchor_key, canonical_json_sha256
from eve_relation_rag.literature.ingestion import import_candidate_corpus
from eve_relation_rag.literature.providers import DeterministicFakeEmbeddingProvider
from eve_relation_rag.planning.parser import StructuredQueryRequest
from eve_relation_rag.planning.query_plans import (
    AggregatePlan,
    AssemblyDetailPlan,
    AssemblyFilter,
    EntireReleaseScope,
    ExtractedCondition,
    FilteredScope,
    ListAssembliesPlan,
    ListLociPlan,
    ListSourceTaxaPlan,
    LocusDetailPlan,
    LocusFilter,
    PageSpec,
    PlanningAudit,
    SourceLineageFilter,
    ViralLineageFilter,
    canonical_plan_sha256,
)
from eve_relation_rag.planning.router import DeterministicRouter
from eve_relation_rag.retrieval.hybrid.anchors import (
    StructuredAnchorResolutionError,
    StructuredAnchorResolver,
    _validated_stored_anchor,
    extract_structured_anchor_targets,
)
from eve_relation_rag.retrieval.structured.results import (
    AggregateData,
    AssemblyDetailData,
    AssemblyPageData,
    AssemblySummary,
    CallDetail,
    EvidenceDetail,
    ExactPlacement,
    Limitation,
    LineageRef,
    LocusDetailData,
    LocusPageData,
    LocusSummary,
    PageInfo,
    PlanSuccess,
    PublicAssertionDetail,
    PublishedReleaseRef,
    QuerySuccess,
    ResolvedEntity,
    SourceTaxonPageData,
    SourceTaxonSummary,
    StructuredData,
    StructuredResult,
)
from tests.support.m2 import TestsOnlyQueryableRelease
from tests.support.m3 import build_trusted_receipt_fixture

ROOT = Path(__file__).resolve().parents[3]
FIXTURE_ROOT = ROOT / "tests" / "fixtures" / "literature"
MANIFEST_PATH = FIXTURE_ROOT / "synthetic_corpus_manifest.json"

SHA_A = "a" * 64
SHA_B = "b" * 64
RELEASE = "release:endoviho-rag:v0:20260827:002"
ASSEMBLY_ACCESSION = "GCA_029931535.1"
ASSEMBLY = f"assembly:ncbi:{ASSEMBLY_ACCESSION}"
LOCUS = f"locus:eve:v1:sha256:{SHA_A}"
SOURCE_SNAPSHOT = "lineage-snapshot:ncbi-taxonomy:test"
SOURCE_TERM = "lineage-term:ncbi:taxid-1"
VIRAL_SNAPSHOT = "lineage-snapshot:study:zhao-v4"
VIRAL_TERM = "lineage-term:study:orthopolintovirales"
METHOD = "method-definition:zhao-data-s1-import-v2"

LIMITATION_MESSAGES = {
    "assembly_local_locus_is_not_independent_integration_event": (
        "An assembly-local locus is not an independent integration event."
    ),
    "assembly_source_taxon_is_not_ancient_host": (
        "An assembly-source taxon is not evidence of an ancient host."
    ),
    "coordinates_are_zero_based_half_open": "Coordinates are zero-based and half-open.",
    "detection_calls_are_not_loci": "Detection calls and loci are distinct objects.",
}


class WhitespaceOffsetTokenizer:
    @property
    def model_key(self) -> str:
        return "tokenizer:test:whitespace-offset-v1"

    def token_spans(self, text: str) -> tuple[TokenSpan, ...]:
        return tuple(
            TokenSpan(token_index=index, char_start=match.start(), char_end=match.end())
            for index, match in enumerate(re.finditer(r"\S+", text))
        )


class _PublishedStructuredApplication:
    def __init__(self, success: QuerySuccess) -> None:
        self.success = success
        self.calls: list[StructuredQueryRequest] = []

    def query(self, _request: StructuredQueryRequest) -> QuerySuccess:
        raise AssertionError("hybrid integration must use the pre-fact binding hook")

    def query_with_pre_fact_hook(self, request: StructuredQueryRequest, hook: Any) -> QuerySuccess:
        self.calls.append(request)
        assert request.release_key == self.success.query_plan.release_key
        assert request.question == self.success.query_plan.original_question
        planned = PlanSuccess(
            query_plan=self.success.query_plan,
            planning_audit=self.success.planning_audit,
            resolved_entities=self.success.resolved_entities,
        )
        release = TestsOnlyQueryableRelease(
            release_key=RELEASE,
            manifest_sha256=SHA_A,
        )
        hook(release, planned)
        return self.success


class _ContextAwareGenerationProvider:
    def __init__(self) -> None:
        self._identity = ProviderIdentity(
            provider_key="provider:tests:m4-postgres-v1",
            model_key="model:tests:m4-postgres-v1",
            model_revision="revision:tests:m4-postgres-v1",
            provider_artifact_sha256=None,
            generation_policy_key="generation:tests:m4-postgres-json-v1",
            prompt_policy_key=APPROVED_ANSWER_INSTRUCTIONS.instruction_policy_key,
            prompt_policy_sha256=APPROVED_ANSWER_INSTRUCTIONS.source_text_sha256,
            temperature=0,
            max_output_bytes=32768,
            timeout_seconds=5,
            retry_count=0,
        )
        self.calls: list[str] = []

    @property
    def identity(self) -> ProviderIdentity:
        return self._identity

    def generate(self, context_json: str) -> str:
        self.calls.append(context_json)
        context = ContextPack.model_validate_json(context_json)
        chunk = context.retrieved_chunks.chunks[0]
        quote = next(
            token for token in ("synthetic", "Synthetic", "deterministic") if token in chunk.text
        )
        return GeneratedAnswerDraft(
            context_sha256=context.context_sha256,
            claims=(
                LiteratureClaim(
                    claim_id="C1",
                    claim_text="The retrieved fixture contains synthetic evidence.",
                    citation_ids=(chunk.citation_id,),
                    evidence_spans=(EvidenceSpan(citation_id=chunk.citation_id, quote=quote),),
                ),
            ),
            selected_limitation_codes=(
                "literature_evidence_is_explanatory",
                "mechanical_validation_is_not_semantic_entailment",
            ),
        ).model_dump_json()


@pytest.fixture(scope="module")
def anchor_corpus() -> Iterator[tuple[Engine, CorpusManifest, QuerySuccess]]:
    database_url = os.environ.get("EVE_RAG_TEST_DATABASE_URL", get_settings().database_url)
    admin_engine = create_engine(database_url, poolclass=NullPool)
    schema = f"test_m4_anchor_{uuid4().hex}"
    try:
        with admin_engine.begin() as connection:
            connection.execute(CreateSchema(schema))
    except OperationalError as exc:
        admin_engine.dispose()
        pytest.skip(f"PostgreSQL integration database is unavailable: {exc.orig}")

    with admin_engine.connect() as connection:
        connection.exec_driver_sql(f'SET search_path TO "{schema}", public')
        connection.commit()
        _upgrade_to_head(connection)

    def set_fixture_search_path(dbapi_connection: object, _record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        try:
            cursor.execute(f'SET search_path TO "{schema}", public')
        finally:
            cursor.close()
        dbapi_connection.commit()  # type: ignore[attr-defined]

    event.listen(admin_engine, "connect", set_fixture_search_path)
    admin_engine.dispose()
    engine = admin_engine.execution_options(schema_translate_map={None: schema})
    manifest = CorpusManifest.model_validate_json(MANIFEST_PATH.read_text())
    provider = DeterministicFakeEmbeddingProvider()
    success = _locus_detail_success()
    try:
        import_candidate_corpus(
            engine,
            manifest=manifest,
            import_root=FIXTURE_ROOT,
            tokenizer=WhitespaceOffsetTokenizer(),
            approved_manifest_sha256=manifest.manifest_sha256,
            importer_code_sha256="e" * 64,
            model_artifact_manifest_sha256="f" * 64,
        )
        embed_candidate_corpus(
            engine,
            corpus_release_key=manifest.corpus_release_key,
            provider=provider,
        )
        _insert_curated_anchors(engine, manifest, success)
        _publish_anchor_corpus(engine, manifest)
        capability = PublishedCorpusGate(engine).authorize(manifest.corpus_release_key)
        assert capability.status == "published"
        assert capability.manifest_sha256 == manifest.manifest_sha256
        yield engine, manifest, success
    finally:
        engine.dispose()
        event.remove(admin_engine, "connect", set_fixture_search_path)
        with admin_engine.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        admin_engine.dispose()


def test_extracts_only_trusted_targets_from_all_six_result_variants() -> None:
    source_lineage = _source_lineage()
    expected_by_result = {
        "assembly_detail": (
            ("assembly", None, ASSEMBLY, None, None, None),
            ("lineage", None, None, SOURCE_SNAPSHOT, SOURCE_TERM, None),
        ),
        "locus_detail": (
            ("locus", LOCUS, None, None, None, None),
            ("assembly", None, ASSEMBLY, None, None, None),
            ("lineage", None, None, SOURCE_SNAPSHOT, SOURCE_TERM, None),
            ("lineage", None, None, VIRAL_SNAPSHOT, VIRAL_TERM, None),
            ("method", None, None, None, None, METHOD),
        ),
        "locus_page": (
            ("locus", LOCUS, None, None, None, None),
            ("assembly", None, ASSEMBLY, None, None, None),
            ("lineage", None, None, SOURCE_SNAPSHOT, SOURCE_TERM, None),
            ("lineage", None, None, VIRAL_SNAPSHOT, VIRAL_TERM, None),
        ),
        "assembly_page": (
            ("assembly", None, ASSEMBLY, None, None, None),
            ("lineage", None, None, SOURCE_SNAPSHOT, SOURCE_TERM, None),
        ),
        "source_taxon_page": (("lineage", None, None, SOURCE_SNAPSHOT, SOURCE_TERM, None),),
        "aggregate": (
            ("assembly", None, ASSEMBLY, None, None, None),
            ("lineage", None, None, SOURCE_SNAPSHOT, SOURCE_TERM, None),
            ("lineage", None, None, VIRAL_SNAPSHOT, VIRAL_TERM, None),
        ),
    }
    successes = (
        _assembly_detail_success(),
        _locus_detail_success(),
        _locus_page_success(),
        _assembly_page_success(),
        _source_taxon_page_success(),
        _aggregate_success(filtered=True),
    )

    for success in successes:
        targets = extract_structured_anchor_targets(success)
        observed = tuple(
            (
                target.target_type,
                target.locus_key,
                target.assembly_key,
                target.snapshot_key,
                target.term_key,
                target.method_definition_key,
            )
            for target in targets
        )
        assert observed == expected_by_result[success.structured_result.data.kind]

    # CallDetail.source_method_key is deliberately different and never appears.
    locus_targets = extract_structured_anchor_targets(_locus_detail_success())
    assert all(
        target.method_definition_key != "method-definition:forbidden-call-source"
        for target in locus_targets
    )
    assert source_lineage.snapshot_key == SOURCE_SNAPSHOT


def test_round_trip_revalidation_rejects_a_tampered_query_success() -> None:
    valid = _locus_detail_success()
    tampered = valid.model_copy(
        update={
            "structured_result": valid.structured_result.model_copy(update={"plan_sha256": SHA_B})
        }
    )

    with pytest.raises(StructuredAnchorResolutionError) as raised:
        extract_structured_anchor_targets(tampered)

    assert raised.value.code == "anchor_integrity_error"


def test_more_than_64_distinct_targets_fails_closed_without_truncation() -> None:
    success = _locus_page_success(item_count=65)

    with pytest.raises(StructuredAnchorResolutionError) as raised:
        extract_structured_anchor_targets(success)

    assert raised.value.code == "anchor_limit_exceeded"


def test_resolves_actual_curated_anchors_in_exact_corpus_and_canonical_order(
    anchor_corpus: tuple[Engine, CorpusManifest, QuerySuccess],
) -> None:
    engine, manifest, success = anchor_corpus
    capability = _capability(engine, manifest)

    resolution = StructuredAnchorResolver(engine).resolve(success, capability)

    assert resolution.targets == extract_structured_anchor_targets(success)
    assert len(resolution.anchors) == 5
    assert {type(anchor) for anchor in resolution.anchors} == {
        LocusAnchor,
        AssemblyAnchor,
        LineageAnchor,
        MethodAnchor,
    }
    assert tuple(anchor.anchor_key for anchor in resolution.anchors) == tuple(
        sorted(anchor.anchor_key for anchor in resolution.anchors)
    )
    assert resolution.unmatched_targets == ()
    assert resolution.diagnostics == ()

    wrong_corpus = cast(
        CorpusCapability,
        SimpleNamespace(
            release_id=capability.release_id + 1,
            corpus_release_key="corpus:endoviho-rag:v0:20991231:999",
        ),
    )
    unmatched = StructuredAnchorResolver(engine).resolve(success, wrong_corpus)
    assert unmatched.anchors == ()
    assert unmatched.unmatched_targets == unmatched.targets
    assert unmatched.diagnostics == ("structured_anchor_unmatched",)


def test_full_m4_hybrid_path_uses_published_anchor_rich_postgres_corpus(
    anchor_corpus: tuple[Engine, CorpusManifest, QuerySuccess],
) -> None:
    engine, manifest, success = anchor_corpus
    structured = _PublishedStructuredApplication(success)
    provider = _ContextAwareGenerationProvider()
    binding_registry = _binding_registry(manifest)
    application = RagQueryApplication(
        router=DeterministicRouter(),
        structured_application_factory=lambda: structured,
        corpus_gate_factory=lambda: PublishedCorpusGate(engine),
        literature_service_factory=lambda: LiteratureRetrievalService(
            engine,
            DeterministicFakeEmbeddingProvider(),
        ),
        binding_registry_factory=lambda: binding_registry,
        anchor_resolver_factory=lambda: StructuredAnchorResolver(engine),
        composer_factory=lambda: GenerationComposer(
            provider=provider,
            expected_identity=provider.identity,
        ),
    )
    question = f"{success.query_plan.original_question} and explain the literature evidence"

    response = application.query(
        RagQueryRequest(
            release_key=RELEASE,
            corpus_release_key=manifest.corpus_release_key,
            question=question,
        )
    )

    assert isinstance(response, HybridRouteAnswer)
    assert len(structured.calls) == 1
    assert len(provider.calls) == 1
    assert len(response.retrieved_chunks.anchors_applied) == 5
    assert response.retrieved_chunks.anchor_mode == "anchored_then_corpus_fill"
    assert response.anchor_diagnostics == ()
    assert response.generation is not None
    assert response.generation.validation_scope == "mechanical"
    assert response.execution.structured_retrieval_executed is True
    assert response.execution.literature_retrieval_executed is True
    assert response.execution.generation_executed is True


def test_no_target_and_partial_match_record_the_same_stable_diagnostic(
    anchor_corpus: tuple[Engine, CorpusManifest, QuerySuccess],
) -> None:
    engine, manifest, success = anchor_corpus
    capability = _capability(engine, manifest)
    aggregate = _aggregate_success(filtered=False)

    no_target = StructuredAnchorResolver(engine).resolve(aggregate, capability)
    assert no_target.targets == ()
    assert no_target.anchors == ()
    assert no_target.unmatched_targets == ()
    assert no_target.diagnostics == ("structured_anchor_unmatched",)

    locus_data = success.structured_result.data
    assert isinstance(locus_data, LocusDetailData)
    changed_assertion = locus_data.public_assertions[0].model_copy(
        update={"method_definition_key": "method-definition:not-curated-v1"}
    )
    changed_data = locus_data.model_copy(update={"public_assertions": (changed_assertion,)})
    partial = QuerySuccess.model_validate(
        success.model_dump(mode="python")
        | {"structured_result": success.structured_result.model_copy(update={"data": changed_data})}
    )
    result = StructuredAnchorResolver(engine).resolve(partial, capability)
    assert len(result.anchors) == 4
    assert tuple(target.target_type for target in result.unmatched_targets) == ("method",)
    assert result.diagnostics == ("structured_anchor_unmatched",)


def test_full_stored_anchor_preimage_and_sha_are_revalidated(
    anchor_corpus: tuple[Engine, CorpusManifest, QuerySuccess],
) -> None:
    engine, manifest, success = anchor_corpus
    capability = _capability(engine, manifest)
    with Session(engine) as session:
        result = session.execute(
            select(
                DocumentAnchor,
                Document.document_key,
                CorpusDocumentMembership.manifest_row.label("membership_manifest_row"),
            )
            .join(Document, Document.id == DocumentAnchor.document_id)
            .join(
                CorpusDocumentMembership,
                (CorpusDocumentMembership.release_id == DocumentAnchor.release_id)
                & (CorpusDocumentMembership.document_id == DocumentAnchor.document_id),
            )
            .where(
                DocumentAnchor.release_id == capability.release_id,
                DocumentAnchor.anchor_type == "method",
            )
            .limit(1)
        ).one()
        row = result.DocumentAnchor
        document_key = result.document_key
        membership_manifest_row = result.membership_manifest_row
        session.expunge(row)
    row.curation_method = "tampered-curation-method"

    with pytest.raises(StructuredAnchorResolutionError) as raised:
        _validated_stored_anchor(
            row,
            document_key=document_key,
            membership_manifest_row=membership_manifest_row,
        )
    assert raised.value.code == "anchor_integrity_error"


def _source_lineage() -> LineageRef:
    return LineageRef(
        term_key=SOURCE_TERM,
        canonical_name="Bivalvia",
        rank="class",
        snapshot_key=SOURCE_SNAPSHOT,
        authority_namespace="ncbi-taxonomy",
        snapshot_version="test-v1",
        scheme_kind="formal_taxonomy",
        role="assembly_source_taxonomy",
    )


def _viral_lineage() -> LineageRef:
    return LineageRef(
        term_key=VIRAL_TERM,
        canonical_name="Orthopolintovirales",
        rank=None,
        snapshot_key=VIRAL_SNAPSHOT,
        authority_namespace="study-defined:10.1101/2025.04.19.649669:v4",
        snapshot_version="v4",
        scheme_kind="study_defined",
        role="study_viral_lineage",
    )


def _locus(*, index: int = 0) -> LocusSummary:
    locus_key = LOCUS if index == 0 else f"locus:eve:v1:sha256:{index + 1000:064x}"
    return LocusSummary(
        locus_key=locus_key,
        assembly_key=ASSEMBLY,
        assembly_accession_version=ASSEMBLY_ACCESSION,
        source_organism_name="Margaritifera margaritifera",
        source_taxon=_source_lineage(),
        placement=ExactPlacement(
            sequence_key="sequence:insdc:ABCD01000001.1",
            sequence_accession_version="ABCD01000001.1",
            start0=100 + index,
            end0=200 + index,
            strand="unknown",
        ),
        viral_lineages=(_viral_lineage(),),
    )


def _assembly() -> AssemblySummary:
    return AssemblySummary(
        assembly_key=ASSEMBLY,
        assembly_accession_version=ASSEMBLY_ACCESSION,
        source_organism_name="Margaritifera margaritifera",
        source_taxon=_source_lineage(),
        included_locus_count=1,
    )


def _assertion() -> PublicAssertionDetail:
    return PublicAssertionDetail(
        assertion_key="assertion:zhao-v4:viral-major-taxon:test",
        assertion_type="viral_major_taxon",
        predicate_key="predicate:viral-major-taxon",
        asserted_value="Orthopolintovirales",
        source_label=None,
        source_confidence=None,
        lineage=_viral_lineage(),
        method_definition_key=METHOD,
        method_version="zhao-data-s1-import-v2",
        process_run_key="process-run:zhao-data-s1-import-v2:test",
        supporting_evidence=EvidenceDetail(
            evidence_key="evidence:zhao-v4:row-1",
            evidence_type="supplementary_table_row",
            evidence_sha256=SHA_A,
            source_locator={"worksheet": "S3", "row": 39158},
            summary="The frozen source row.",
            artifact_key="source-artifact:biorxiv-data-s1:test",
            artifact_sha256=SHA_B,
            source_uri="https://example.invalid/data-s1.xlsx",
            verified_license_key="CC-BY-NC-ND-4.0",
        ),
    )


def _call() -> CallDetail:
    return CallDetail(
        call_key="detection-call:zhao-v4:test",
        source_method_key="method-definition:forbidden-call-source",
        process_run_key="process-run:zhao-data-s1-import-v2:test",
        source_record_key="source-record:zhao-data-s1:test",
        artifact_key="source-artifact:biorxiv-data-s1:test",
        artifact_sha256=SHA_B,
        worksheet="S3",
        row_number=39158,
    )


def _release() -> PublishedReleaseRef:
    return PublishedReleaseRef(
        dataset_key="dataset:endoviho-rag",
        release_key=RELEASE,
        schema_version="milestone-1-v1",
        manifest_sha256=SHA_A,
        published_at=datetime(2026, 8, 27, 1, 2, 3, tzinfo=UTC),
    )


def _audit() -> PlanningAudit:
    condition = ExtractedCondition(
        condition_id="c-intent",
        source_text="query",
        source_start=0,
        source_end=5,
        condition_kind="intent",
        mapped_target="intent",
    )
    return PlanningAudit(
        extracted_conditions=(condition,),
        mapped_condition_ids=(condition.condition_id,),
    )


def _limitations(*codes: str) -> tuple[Limitation, ...]:
    return tuple(
        Limitation(code=code, message=LIMITATION_MESSAGES[code])  # type: ignore[arg-type]
        for code in sorted(codes)
    )


def _resolved_entities(scope: FilteredScope | EntireReleaseScope) -> tuple[ResolvedEntity, ...]:
    if isinstance(scope, EntireReleaseScope):
        return ()
    entities: list[ResolvedEntity] = []
    for query_filter in scope.filters:
        if isinstance(query_filter, AssemblyFilter):
            entities.append(
                ResolvedEntity(
                    original_input=query_filter.assembly_key,
                    entity_kind="assembly",
                    match_mode="exact_identifier",
                    stable_key=query_filter.assembly_key,
                    canonical_name="Margaritifera margaritifera",
                )
            )
        elif isinstance(query_filter, LocusFilter):
            entities.append(
                ResolvedEntity(
                    original_input=query_filter.locus_key,
                    entity_kind="locus",
                    match_mode="exact_stable_key",
                    stable_key=query_filter.locus_key,
                    canonical_name="fixture locus",
                )
            )
        else:
            lineage = (
                _source_lineage()
                if isinstance(query_filter, SourceLineageFilter)
                else _viral_lineage()
            )
            entities.append(
                ResolvedEntity(
                    original_input=query_filter.term_key,
                    entity_kind=(
                        "source_lineage"
                        if isinstance(query_filter, SourceLineageFilter)
                        else "viral_lineage"
                    ),
                    match_mode="exact_stable_key",
                    stable_key=query_filter.term_key,
                    canonical_name=lineage.canonical_name,
                    snapshot_key=query_filter.snapshot_key,
                    authority_namespace=lineage.authority_namespace,
                    snapshot_version=lineage.snapshot_version,
                    scheme_kind=lineage.scheme_kind,
                    role=query_filter.role,
                )
            )
    return tuple(sorted(entities, key=lambda item: (item.entity_kind, item.stable_key)))


def _success(plan: Any, data: StructuredData, limitations: tuple[Limitation, ...]) -> QuerySuccess:
    return QuerySuccess(
        query_plan=plan,
        planning_audit=_audit(),
        resolved_entities=_resolved_entities(plan.scope),
        structured_result=StructuredResult(
            plan_sha256=canonical_plan_sha256(plan),
            release=_release(),
            data=data,
            limitations=limitations,
        ),
    )


def _assembly_detail_success() -> QuerySuccess:
    scope = FilteredScope(
        scope_type="filtered",
        filters=(AssemblyFilter(filter_type="assembly", assembly_key=ASSEMBLY),),
    )
    plan = AssemblyDetailPlan(
        plan_version="endoviho-query-plan-v0.1",
        route="structured",
        release_key=RELEASE,
        intent="assembly_detail",
        original_question="Show this assembly.",
        scope=scope,
    )
    return _success(
        plan,
        AssemblyDetailData(assembly=_assembly()),
        _limitations("assembly_source_taxon_is_not_ancient_host"),
    )


def _locus_detail_success() -> QuerySuccess:
    scope = FilteredScope(
        scope_type="filtered",
        filters=(LocusFilter(filter_type="locus", locus_key=LOCUS),),
    )
    plan = LocusDetailPlan(
        plan_version="endoviho-query-plan-v0.1",
        route="structured",
        release_key=RELEASE,
        intent="locus_detail",
        original_question="Show this locus.",
        scope=scope,
    )
    return _success(
        plan,
        LocusDetailData(locus=_locus(), calls=(_call(),), public_assertions=(_assertion(),)),
        _limitations(
            "assembly_local_locus_is_not_independent_integration_event",
            "assembly_source_taxon_is_not_ancient_host",
            "coordinates_are_zero_based_half_open",
            "detection_calls_are_not_loci",
        ),
    )


def _locus_page_success(*, item_count: int = 1) -> QuerySuccess:
    scope = EntireReleaseScope(scope_type="entire_release", explicitly_requested=True)
    plan = ListLociPlan(
        plan_version="endoviho-query-plan-v0.1",
        route="structured",
        release_key=RELEASE,
        intent="list_loci",
        original_question="List loci.",
        scope=scope,
        page=PageSpec(limit=max(item_count, 1)),
    )
    items = tuple(
        sorted(
            (_locus(index=index) for index in range(item_count)),
            key=lambda locus: locus.locus_key,
        )
    )
    return _success(
        plan,
        LocusPageData(
            items=items,
            page=PageInfo(
                limit=max(item_count, 1),
                returned_count=item_count,
                total_count=item_count,
                sort_key="locus_key",
            ),
        ),
        _limitations(
            "assembly_local_locus_is_not_independent_integration_event",
            "assembly_source_taxon_is_not_ancient_host",
            "coordinates_are_zero_based_half_open",
        ),
    )


def _assembly_page_success() -> QuerySuccess:
    scope = EntireReleaseScope(scope_type="entire_release", explicitly_requested=True)
    plan = ListAssembliesPlan(
        plan_version="endoviho-query-plan-v0.1",
        route="structured",
        release_key=RELEASE,
        intent="list_assemblies",
        original_question="List assemblies.",
        scope=scope,
        page=PageSpec(limit=1),
    )
    return _success(
        plan,
        AssemblyPageData(
            items=(_assembly(),),
            page=PageInfo(
                limit=1,
                returned_count=1,
                total_count=1,
                sort_key="assembly_accession",
            ),
        ),
        _limitations("assembly_source_taxon_is_not_ancient_host"),
    )


def _source_taxon_page_success() -> QuerySuccess:
    scope = EntireReleaseScope(scope_type="entire_release", explicitly_requested=True)
    plan = ListSourceTaxaPlan(
        plan_version="endoviho-query-plan-v0.1",
        route="structured",
        release_key=RELEASE,
        intent="list_source_taxa",
        original_question="List source taxa.",
        scope=scope,
        page=PageSpec(limit=1),
    )
    return _success(
        plan,
        SourceTaxonPageData(
            items=(
                SourceTaxonSummary(
                    lineage=_source_lineage(),
                    represented_assembly_count=1,
                    included_locus_count=1,
                ),
            ),
            page=PageInfo(
                limit=1,
                returned_count=1,
                total_count=1,
                sort_key="source_taxon_key",
            ),
        ),
        _limitations("assembly_source_taxon_is_not_ancient_host"),
    )


def _aggregate_success(*, filtered: bool) -> QuerySuccess:
    scope: FilteredScope | EntireReleaseScope
    resolved: FilteredScope | EntireReleaseScope
    if filtered:
        resolved = FilteredScope(
            scope_type="filtered",
            filters=(
                AssemblyFilter(filter_type="assembly", assembly_key=ASSEMBLY),
                SourceLineageFilter(
                    filter_type="source_lineage",
                    snapshot_key=SOURCE_SNAPSHOT,
                    term_key=SOURCE_TERM,
                    role="assembly_source_taxonomy",
                    include_descendants=True,
                ),
                ViralLineageFilter(
                    filter_type="viral_lineage",
                    snapshot_key=VIRAL_SNAPSHOT,
                    term_key=VIRAL_TERM,
                    role="study_viral_lineage",
                    include_descendants=True,
                ),
            ),
        )
    else:
        resolved = EntireReleaseScope(scope_type="entire_release", explicitly_requested=True)
    scope = resolved
    plan = AggregatePlan(
        plan_version="endoviho-query-plan-v0.1",
        route="structured",
        release_key=RELEASE,
        intent="aggregate",
        original_question="Count loci.",
        scope=scope,
        metric_key="distinct_included_locus_count",
    )
    return _success(
        plan,
        AggregateData(
            metric_key="distinct_included_locus_count",
            value=1,
            unit="loci",
            deduplication_key="release_key+locus_key",
        ),
        _limitations("assembly_local_locus_is_not_independent_integration_event"),
    )


def _capability(engine: Engine, manifest: CorpusManifest) -> CorpusCapability:
    return PublishedCorpusGate(engine).authorize(manifest.corpus_release_key)


def _binding_registry(manifest: CorpusManifest) -> ApprovedHybridBindingRegistry:
    payload: dict[str, object] = {
        "binding_schema_version": BINDING_MANIFEST_VERSION,
        "bindings": (
            HybridReleaseBinding(
                release_key=RELEASE,
                release_manifest_sha256=SHA_A,
                corpus_release_key=manifest.corpus_release_key,
                corpus_manifest_sha256=manifest.manifest_sha256,
            ),
        ),
        "manifest_sha256": "0" * 64,
    }
    payload["manifest_sha256"] = canonical_self_sha256(payload, "manifest_sha256")
    return ApprovedHybridBindingRegistry(HybridReleaseBindingManifest.model_validate(payload))


def _publish_anchor_corpus(engine: Engine, manifest: CorpusManifest) -> None:
    with Session(engine) as session, session.begin():
        release = session.scalar(
            select(CorpusRelease).where(
                CorpusRelease.corpus_release_key == manifest.corpus_release_key
            )
        )
        assert release is not None
        chunk_count = session.scalar(
            select(func.count())
            .select_from(DocumentChunk)
            .where(DocumentChunk.release_id == release.id)
        )
        embedding_count = session.scalar(
            select(func.count())
            .select_from(DocumentEmbedding)
            .where(DocumentEmbedding.release_id == release.id)
        )
        anchor_count = session.scalar(
            select(func.count())
            .select_from(DocumentAnchor)
            .where(DocumentAnchor.release_id == release.id)
        )
        relevant_chunk_key = session.scalar(
            select(DocumentChunk.chunk_key)
            .where(DocumentChunk.release_id == release.id)
            .order_by(DocumentChunk.chunk_key)
            .limit(1)
        )
        assert chunk_count is not None
        assert embedding_count is not None
        assert anchor_count is not None
        assert relevant_chunk_key is not None
        receipt_values = build_trusted_receipt_fixture(
            corpus_release_key=release.corpus_release_key,
            manifest_sha256=release.manifest_sha256,
            policy_graph_sha256=release.policy_graph_sha256,
            model_artifact_manifest_sha256="f" * 64,
            document_count=manifest.document_count,
            chunk_count=chunk_count,
            embedding_count=embedding_count,
            anchor_count=anchor_count,
            relevant_chunk_key=relevant_chunk_key,
            seed="m4-anchor-rich",
        )
        session.add(CorpusValidationReceipt(release_id=release.id, **receipt_values))
        session.flush()
        release.status = "validated"
        session.flush()
        release.status = "published"
        release.published_at = datetime.now(UTC)
        session.flush()


def _insert_curated_anchors(
    engine: Engine,
    manifest: CorpusManifest,
    success: QuerySuccess,
) -> None:
    targets = extract_structured_anchor_targets(success)
    with Session(engine) as session, session.begin():
        release = session.scalar(
            select(CorpusRelease).where(
                CorpusRelease.corpus_release_key == manifest.corpus_release_key
            )
        )
        document = session.scalar(
            select(Document).where(
                Document.document_key == manifest.documents[0].expected_document_key
            )
        )
        assert release is not None and document is not None
        membership = session.get(CorpusDocumentMembership, (release.id, document.id))
        assert membership is not None
        for target in targets:
            target_payload = target.anchor_target_payload()
            source_locator = {"fixture": "test_m4_structured_anchor_resolution"}
            curation_method = "m4-structured-anchor-fixture-v1"
            key = anchor_key(
                {
                    "anchor_schema_version": "document-anchor-v1",
                    "curation_method": curation_method,
                    "document_key": document.document_key,
                    "manifest_row": membership.manifest_row,
                    "source_locator": source_locator,
                    "target": target_payload,
                }
            )
            anchor: RetrievalAnchor = TypeAdapter(RetrievalAnchor).validate_python(
                {"anchor_key": key, **target_payload}
            )
            values: dict[str, Any] = {
                "locus_key": None,
                "assembly_key": None,
                "lineage_snapshot_key": None,
                "lineage_term_key": None,
                "method_definition_key": None,
                "target_document_key": None,
                "doi": None,
                "pmid": None,
                "pmcid": None,
                "keyword_phrase": None,
            }
            if target.target_type == "locus":
                values["locus_key"] = target.locus_key
            elif target.target_type == "assembly":
                values["assembly_key"] = target.assembly_key
            elif target.target_type == "lineage":
                values["lineage_snapshot_key"] = target.snapshot_key
                values["lineage_term_key"] = target.term_key
            else:
                values["method_definition_key"] = target.method_definition_key
            session.add(
                DocumentAnchor(
                    anchor_key=anchor.anchor_key,
                    release_id=release.id,
                    document_id=document.id,
                    anchor_type=anchor.anchor_type,
                    manifest_row=membership.manifest_row,
                    curation_method=curation_method,
                    source_locator=source_locator,
                    anchor_sha256=canonical_json_sha256(anchor),
                    **values,
                )
            )


def _upgrade_to_head(connection: object) -> None:
    script = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini")))
    revisions = list(reversed(list(script.walk_revisions(base="base", head="heads"))))
    for revision in revisions:
        with connection.begin():  # type: ignore[attr-defined]
            context = MigrationContext.configure(
                connection,  # type: ignore[arg-type]
                opts={"target_metadata": Base.metadata},
            )
            with Operations.context(context):
                revision.module.upgrade()
