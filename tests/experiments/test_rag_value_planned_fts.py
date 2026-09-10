"""Planned FTS isolation and audit flow, using synthetic in-memory SQL doubles only."""

from __future__ import annotations

import hashlib
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy.dialects import postgresql

from eve_relation_rag.experiments.rag_value_ablation import source_corpus, source_experiment
from eve_relation_rag.experiments.rag_value_ablation.lexical_query import (
    LEXICAL_QUERY_POLICY_KEY,
    plan_lexical_query,
)
from eve_relation_rag.experiments.rag_value_ablation.local_generation import (
    LocalGenerationTimeout,
)
from eve_relation_rag.experiments.rag_value_ablation.planned_fts import (
    PlannedFtsCandidateProvider,
    merge_query_candidates,
)
from eve_relation_rag.literature.hashing import canonical_json_sha256
from eve_relation_rag.retrieval.literature.repository import LiteratureRepository
from tests.experiments.test_rag_value_systems import _generation_identity


class _EmptySqlSession:
    """Collect SQL without executing it; indexable queries have no matching rows."""

    def __init__(self, rankings=()):
        self.statements = []
        self.candidate_statements = []
        self.rankings = iter(rankings)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def begin(self):
        return nullcontext()

    def execute(self, statement):
        self.statements.append(statement)
        return SimpleNamespace(one=lambda: ("'synthetic'", 1))

    def scalar(self, statement):
        self.statements.append(statement)
        return 0 if "count(" in str(statement) else 1

    def scalars(self, statement):
        self.statements.append(statement)
        self.candidate_statements.append(statement)
        return next(self.rankings, ())


def _sql(statement):
    return str(statement.compile(
        dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True},
    ))


def _candidate_admission(statement):
    """Inspect the candidate subquery's WHERE, separately from its scoring expressions."""
    return _sql(statement.get_final_froms()[0].element.whereclause)


def _empty_adapter(monkeypatch, *, policy=None):
    session = _EmptySqlSession()
    capability = SimpleNamespace(release_id=7, corpus_release_key="tests-only-corpus")
    engine = SimpleNamespace(connect=lambda: SimpleNamespace(
        execution_options=lambda **kwargs: nullcontext(None),
    ))
    monkeypatch.setattr(source_corpus, "Session", lambda **kwargs: session)
    monkeypatch.setattr(source_corpus, "ValidatedCandidateGate", lambda engine: SimpleNamespace(
        authorize=lambda report: capability,
    ))
    monkeypatch.setattr(source_corpus, "read_pinned", lambda *args: b"")
    report = SimpleNamespace(
        provider_kind="local_bge", chunk_count=0,
        corpus_release_key=capability.corpus_release_key,
        chunk_rebuild_sha256=canonical_json_sha256([]),
    )
    kwargs = {} if policy is None else {"lexical_query_policy": policy}
    adapter = source_corpus.SourceCorpusEvidenceAdapter(
        engine, report, chunks_path=None, chunks_sha256="a" * 64, **kwargs,
    )
    return adapter, session, capability


def test_multiple_queries_each_get_a_top_eight_slot_despite_duplicates():
    rankings = (
        ("shared", "shared", *(f"abundant-{i}" for i in range(100))),
        ("shared", "second-object", "second-next"),
        ("third-object", "shared", "third-next"),
    )
    selected = merge_query_candidates(rankings, limit=8)
    assert selected == (
        "shared", "second-object", "third-object", "abundant-0",
        "second-next", "third-next", "abundant-1", "abundant-2",
    )
    assert len(selected) == len(set(selected)) == 8
    assert merge_query_candidates(((), ("only",), ())) == ("only",)


@pytest.mark.parametrize("entrypoint", ["has_indexable_terms", "candidates"])
def test_plan_rejects_a_different_question_before_sql(entrypoint):
    provider = PlannedFtsCandidateProvider(plan_lexical_query("Describe BRCA1."))
    session = _EmptySqlSession()
    with pytest.raises(ValueError, match="different question"):
        if entrypoint == "has_indexable_terms":
            provider.has_indexable_terms(session, "Describe TP53.")
        else:
            provider.candidates(
                session, SimpleNamespace(), question="Describe TP53.", document_ids=None,
            )
    assert session.statements == []
    assert provider.calls == []


def test_empty_document_scope_remains_empty_in_count_and_candidate_sql():
    question = "Describe BRCA1."
    provider = PlannedFtsCandidateProvider(plan_lexical_query(question))
    session = _EmptySqlSession()
    capability = SimpleNamespace(release_id=7, corpus_release_key="tests-only-corpus")
    assert provider.candidates(
        session, capability, question=question, document_ids=(),
    ) == ()
    scoped = [_sql(stmt) for stmt in session.statements if "document_id" in str(stmt)]
    assert len(scoped) == 2
    for statement in scoped:
        assert "release_id = 7" in statement
        assert "document_id IN (NULL)" in statement
        assert "1 != 1" in statement
    assert provider.diagnostics()["calls"][0]["document_ids"] == ()


def test_unknown_accession_stays_mandatory_beside_a_broad_species_name():
    question = "Describe Homo sapiens GCA_999999999.9"
    plan = plan_lexical_query(question)
    provider = PlannedFtsCandidateProvider(plan)
    session = _EmptySqlSession()
    capability = SimpleNamespace(release_id=7, corpus_release_key="tests-only-corpus")
    assert provider.candidates(
        session, capability, question=question, document_ids=None,
    ) == ()
    assert len(session.candidate_statements) == 1
    admission = _candidate_admission(session.candidate_statements[0])
    assert "GCA_999999999" in admission and "~*" in admission
    assert "homo" not in admission and "sapiens" not in admission
    assert " OR " not in admission
    accession = next(group for group in plan.groups if group.kind == "accession")
    obj = provider.diagnostics()["calls"][0]["objects"][0]
    assert len(obj["group_keys"]) == 2
    assert obj["admission_group_keys"] == [accession.group_key]


def test_context_concepts_affect_score_without_restricting_entity_admission():
    question = "Describe Mus musculus ABCD2 region 45.6 kb on scaffold 4."
    provider = PlannedFtsCandidateProvider(plan_lexical_query(question))
    session = _EmptySqlSession()
    capability = SimpleNamespace(release_id=7, corpus_release_key="tests-only-corpus")
    provider.candidates(session, capability, question=question, document_ids=None)
    assert len(session.candidate_statements) == 1
    statement = session.candidate_statements[0]
    admission, full_query = _candidate_admission(statement), _sql(statement)
    obj = provider.diagnostics()["calls"][0]["objects"][0]
    assert "scaffold" in obj["context_terms"]
    assert "mus musculus" in admission and "abcd2" in admission
    assert " OR " in admission
    assert "scaffold" in full_query and "context_score" in full_query
    assert "scaffold" not in admission and "45.6" not in admission
    assert "to_tsvector" not in admission


def test_three_objects_share_slots_without_species_and_gene_getting_extra_turns():
    question = "Compare Arabidopsis thaliana FLC; Homo sapiens BRCA1; Danio rerio AB123."
    provider = PlannedFtsCandidateProvider(plan_lexical_query(question))
    rankings = (
        ("a0", "shared", "a1", "a2", "a3"),
        ("b0", "shared", "b1", "b2"),
        ("c0", "c1", "c2", "c3"),
    )
    session = _EmptySqlSession(rankings)
    capability = SimpleNamespace(release_id=7, corpus_release_key="tests-only-corpus")
    selected = provider.candidates(
        session, capability, question=question, document_ids=None,
    )
    call = provider.diagnostics()["calls"][0]
    assert len(call["groups"]) == 6
    assert len(call["objects"]) == len(session.candidate_statements) == 3
    assert all(len(obj["group_keys"]) == 2 for obj in call["objects"])
    assert selected[:8] == ("a0", "b0", "c0", "shared", "b1", "c1", "a1", "b2")
    assert len(selected) == len(set(selected))


def test_default_source_adapter_keeps_legacy_fts_without_invoking_planner(monkeypatch):
    adapter, session, _ = _empty_adapter(monkeypatch)
    planner = Mock(side_effect=AssertionError("legacy retrieval must not plan"))
    monkeypatch.setattr(source_corpus, "plan_lexical_query", planner)
    result = adapter.retrieve_detailed("Describe BRCA1.")
    planner.assert_not_called()
    assert adapter.lexical_query_policy == source_corpus.ORIGINAL_LEXICAL_QUERY_POLICY
    assert adapter._repository._lexical_candidates is None
    assert result.diagnostics is None
    assert any("websearch_to_tsquery" in str(stmt) for stmt in session.statements)
    assert not any("plainto_tsquery" in str(stmt) for stmt in session.statements)


def test_planned_hybrid_preserves_original_question_vector_and_anchors(monkeypatch):
    adapter, _, capability = _empty_adapter(monkeypatch, policy=LEXICAL_QUERY_POLICY_KEY)
    question = "Compare BRCA1 and TP53, grouped by evidence source."
    vector = (0.25, 0.75)
    anchors = (object(),)  # Opaque sentinel: this test verifies forwarding, not anchor validation.
    embed = Mock(return_value=vector)
    adapter._bge = SimpleNamespace(embed_query=embed)
    retrieval = Mock(return_value=SimpleNamespace(hits=(), warnings=()))
    constructor = Mock(return_value=SimpleNamespace(retrieve=retrieval))
    monkeypatch.setattr(source_corpus, "LiteratureRepository", constructor)
    result = adapter.retrieve_detailed(question, anchors=anchors)
    embed.assert_called_once_with(question)
    retrieval.assert_called_once_with(
        capability, question=question, query_vector=vector, anchors=anchors, top_k=100,
    )
    planned = constructor.call_args.kwargs["lexical_candidates"]
    assert isinstance(planned, PlannedFtsCandidateProvider)
    assert planned.plan.question_text_sha256 == hashlib.sha256(question.encode()).hexdigest()
    assert result.diagnostics["plan"] == planned.plan.to_dict()


def test_injected_fts_replaces_only_lexical_branch_and_preserves_dense_scope(monkeypatch):
    question, vector, scope = "Describe BRCA1.", (0.25, 0.75), (11, 19)
    keys = tuple(f"chunk:sha256:{digit * 64}" for digit in "abc")
    lexical = Mock()
    lexical.candidates.return_value = (keys[0],)
    repository = LiteratureRepository(None, lexical_candidates=lexical)
    legacy = Mock(side_effect=AssertionError("planned FTS must replace legacy branch"))
    dense, summary = Mock(return_value=(keys[1],)), Mock(return_value=(keys[2],))
    monkeypatch.setattr(repository, "_fts_candidates", legacy)
    monkeypatch.setattr(repository, "_vector_candidates", dense)
    monkeypatch.setattr(repository, "_summary_vector_candidates", summary)
    session, capability = object(), object()
    result = repository._retrieve_branch_fusion(
        session, capability, question=question, query_vector=vector,
        document_ids=scope, fts_indexable=True,
    )
    lexical.candidates.assert_called_once_with(
        session, capability, question=question, document_ids=scope,
    )
    for branch in (dense, summary):
        branch.assert_called_once_with(
            session, capability, query_vector=vector, document_ids=scope,
        )
    legacy.assert_not_called()
    assert {candidate.chunk_key for candidate in result} == set(keys)
    lexical.reset_mock()
    dense.reset_mock()
    summary.reset_mock()
    assert repository._retrieve_branch_fusion(
        session, capability, question=question, query_vector=vector,
        document_ids=(), fts_indexable=True,
    ) == ()
    lexical.candidates.assert_not_called()
    dense.assert_not_called()
    summary.assert_not_called()


def test_empty_planned_retrieval_keeps_warning_and_checksum_bound_diagnostics(monkeypatch):
    adapter, _, _ = _empty_adapter(monkeypatch, policy=LEXICAL_QUERY_POLICY_KEY)
    result = adapter.retrieve_detailed("Describe BRCA1.")
    assert result.keys == result.citations == ()
    assert result.warnings == ("no_chunks_retrieved",)
    diagnostics = dict(result.diagnostics)
    assert diagnostics.pop("diagnostics_sha256") == canonical_json_sha256(diagnostics)
    call = diagnostics["calls"][0]
    assert call["merged_candidate_count"] == 0
    assert call["objects"][0]["returned_candidate_count"] == 0
    assert call["objects"][0]["candidate_keys"] == ()
    assert call["groups"][0]["total_matching_chunks"] == 0
    assert call["groups"][0]["normalized_tsquery"] == "'synthetic'"


def test_retrieval_warning_and_diagnostics_survive_runtime_tokenization_failure(monkeypatch):
    adapter, _, _ = _empty_adapter(monkeypatch, policy=LEXICAL_QUERY_POLICY_KEY)
    runtime = source_experiment.SourceExperiment.__new__(source_experiment.SourceExperiment)
    runtime.config_sha256 = "a" * 64
    runtime.literature = Mock(return_value=adapter)
    monkeypatch.setattr(source_experiment, "build_measured_evidence", Mock(
        side_effect=LocalGenerationTimeout("tokenize"),
    ))
    provider = SimpleNamespace(identity=_generation_identity(), generate=Mock())
    result = runtime.execute_cell(provider, {
        "question_id": "synthetic-planned-fts", "family": "literature",
        "question_text_draft": "Describe BRCA1.",
    }, "S2")
    assert result["status"] == "tokenization_timeout"
    assert result["retrieved_chunk_keys"] == ()
    assert result["retrieval_warnings"] == ("no_chunks_retrieved",)
    assert result["retrieval_diagnostics"]["calls"][0]["merged_candidate_count"] == 0
    assert result["events"] == ["request_validation", "planned_fts"]
    assert result["generation_attempted"] is False
    provider.generate.assert_not_called()


def test_planned_policy_still_refuses_scope_before_accessing_dependencies():
    adapter = source_corpus.SourceCorpusEvidenceAdapter.__new__(
        source_corpus.SourceCorpusEvidenceAdapter,
    )
    adapter.lexical_query_policy = LEXICAL_QUERY_POLICY_KEY
    with pytest.raises(source_corpus.LiteratureAdapterError, match="scope refusal"):
        adapter.retrieve_detailed("Run HMMER on a new sequence.")
