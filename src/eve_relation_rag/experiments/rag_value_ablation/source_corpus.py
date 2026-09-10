"""Private candidate-corpus retrieval and exact source-document bindings.

Uses the existing candidate gate and PostgreSQL ranking implementation. Source
row provenance is retained separately from document-level retrieval anchors.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from eve_relation_rag.experiments.rag_value_ablation.contracts import EvidenceCitation
from eve_relation_rag.experiments.rag_value_ablation.lexical_query import (
    LEXICAL_QUERY_POLICY_KEY,
    plan_lexical_query,
)
from eve_relation_rag.experiments.rag_value_ablation.literature_adapter import (
    LiteratureAdapterError,
    OfflineBgeQueryProvider,
)
from eve_relation_rag.experiments.rag_value_ablation.planned_fts import (
    PlannedFtsCandidateProvider,
)
from eve_relation_rag.experiments.rag_value_ablation.source_report_queries import (
    SOURCE_FIELD_NAMES,
    SourceReportRepository,
    SourceReportResult,
)
from eve_relation_rag.experiments.rag_value_ablation.systems import GENERATION_CONTEXT_CHUNK_LIMIT
from eve_relation_rag.literature.anchors import CorpusAnchorManifest
from eve_relation_rag.literature.candidate_gate import ValidatedCandidateGate
from eve_relation_rag.literature.chunking import DocumentChunkDraft
from eve_relation_rag.literature.contracts import RetrievalAnchor
from eve_relation_rag.literature.hashing import canonical_json_sha256
from eve_relation_rag.literature.validation import RebuildValidationReport
from eve_relation_rag.planning.scope_policy import contains_forbidden_topic
from eve_relation_rag.retrieval.literature.repository import LiteratureRepository

ORIGINAL_LEXICAL_QUERY_POLICY = "original-question-v1"
LEXICAL_QUERY_POLICIES = (ORIGINAL_LEXICAL_QUERY_POLICY, LEXICAL_QUERY_POLICY_KEY)


@dataclass(frozen=True)
class SourceRetrievalResult:
    keys: tuple[str, ...]
    citations: tuple[EvidenceCitation, ...]
    warnings: tuple[str, ...]
    diagnostics: dict[str, Any] | None = None


def read_pinned(path: Path, sha256: str, *, interpreter_link: bool = False) -> bytes:
    raw = path.read_bytes()
    if (path.is_symlink() and not interpreter_link) or hashlib.sha256(raw).hexdigest() != sha256:
        raise LiteratureAdapterError("runtime file differs from the frozen input")
    return raw


class SourceDocumentBindings:
    """Validate every row against its exact source paragraph, then deduplicate documents."""

    def __init__(
        self,
        repository: SourceReportRepository,
        chunks: dict[str, DocumentChunkDraft],
        *,
        row_bindings: Path,
        row_bindings_sha256: str,
        corpus_preparation: Path,
        corpus_preparation_sha256: str,
        anchors: CorpusAnchorManifest,
    ) -> None:
        self.repository = repository
        self._anchors = {e.document_key: e.anchor for e in anchors.anchors}
        preparation = json.loads(read_pinned(corpus_preparation, corpus_preparation_sha256))
        if preparation["corpus_release_key"] != anchors.corpus_release_key:
            raise LiteratureAdapterError("source bindings differ from the candidate corpus")
        source_to_docs: dict[str, list[str]] = {}
        for doc in preparation["documents"]:
            source_to_docs.setdefault(doc["input_source_sha256"], []).append(
                doc["expected_document_key"]
            )
        rows = {r.record_key: r for r in repository.rows}
        self._bindings: dict[str, dict[str, object]] = {}
        for line in read_pinned(row_bindings, row_bindings_sha256).splitlines():
            binding = json.loads(line)
            key = binding["source_record_key"]
            if key in self._bindings or key not in rows:
                raise LiteratureAdapterError("source row binding is duplicate or out of scope")
            row = rows[key]
            chunk = chunks.get(binding["chunk_key"])
            if chunk is None or (
                binding["source_occurrence_locus_key"] != row.source_occurrence_locus_key
                or binding["worksheet"] != row.worksheet
                or binding["excel_row"] != row.excel_row
                or binding["document_key"] != chunk.document_key
                or binding["chunk_sha256"] != chunk.text_sha256
                or chunk.document_key not in source_to_docs.get(row.source_artifact_sha256, [])
                or binding["binding_kind"] != "same_source_row_exact_locator"
                or binding["independent_publication"] is not False
                or binding["biological_validation_claimed"] is not False
            ):
                raise LiteratureAdapterError("source row locator or artifact binding differs")
            # Exact field reconstruction, not a substring match that could attach the wrong row.
            expected = f"Worksheet {row.worksheet}; Excel row {row.excel_row}. "
            expected += "; ".join(
                f"{f.column} {SOURCE_FIELD_NAMES[f.column]} = "
                f"{json.dumps(f.value, ensure_ascii=False)}"
                for f in row.fields
            )
            if chunk.text != expected:
                raise LiteratureAdapterError("bound passage does not preserve all 21 source fields")
            self._bindings[key] = binding
        if set(self._bindings) != set(rows):
            raise LiteratureAdapterError("row-to-passage bindings are incomplete")
        self._region_docs: dict[str, str] = {}
        for region in repository.regions:
            docs = source_to_docs.get(region.source_artifact_sha256, [])
            if len(docs) != 1:
                raise LiteratureAdapterError("region source artifact has no unique document")
            self._region_docs[region.record_key] = docs[0]
        targets = {str(b["document_key"]) for b in self._bindings.values()}
        if not (targets | set(self._region_docs.values())) <= self._anchors.keys():
            raise LiteratureAdapterError("source document has no curated retrieval anchor")

    def resolve(
        self, groups: tuple[SourceReportResult, ...]
    ) -> tuple[
        tuple[RetrievalAnchor, ...],
        tuple[dict[str, object], ...],
    ]:
        if not groups:
            raise LiteratureAdapterError("S5 source variant requires explicit source queries")
        documents: set[str] = set()
        provenance: list[dict[str, object]] = []
        for group in groups:
            self.repository.verify_complete_result(group)
            for row in group.rows:
                binding = self._bindings[row.record_key]
                documents.add(str(binding["document_key"]))
                provenance.append(dict(binding))
            for region in group.regions:
                doc_key = self._region_docs[region.record_key]
                documents.add(doc_key)
                provenance.append(
                    {
                        "source_record_key": region.record_key,
                        "source_artifact_sha256": region.source_artifact_sha256,
                        "source_locator": region.source_locator,
                        "document_key": doc_key,
                        "binding_kind": "same_primary_source_artifact",
                        "independent_publication": False,
                        "biological_validation_claimed": False,
                    }
                )
        anchors = tuple(sorted((self._anchors[d] for d in documents), key=lambda a: a.anchor_key))
        if len(anchors) > 64:
            raise LiteratureAdapterError("S5 exceeds the frozen 64-document-anchor limit")
        return anchors, tuple(provenance)


class SourceCorpusEvidenceAdapter:
    """Original FTS or FTS/BGE/summary/RRF through a genuine non-public capability."""

    def __init__(
        self,
        engine: Engine,
        report: RebuildValidationReport,
        *,
        chunks_path: Path,
        chunks_sha256: str,
        bge: OfflineBgeQueryProvider | None = None,
        lexical_query_policy: str = ORIGINAL_LEXICAL_QUERY_POLICY,
    ) -> None:
        if lexical_query_policy not in LEXICAL_QUERY_POLICIES:
            raise LiteratureAdapterError("unknown lexical query policy")
        self.lexical_query_policy = lexical_query_policy
        if report.provider_kind != "local_bge":
            raise LiteratureAdapterError("actual local-BGE corpus rebuild is required")
        self.capability = ValidatedCandidateGate(engine).authorize(report)
        if bge is not None and (
            type(bge) is not OfflineBgeQueryProvider
            or bge.artifact_manifest_sha256 != report.model_artifact_manifest_sha256
        ):
            raise LiteratureAdapterError("BGE query and corpus model identities differ")
        parsed = tuple(
            DocumentChunkDraft.model_validate_json(line)
            for line in read_pinned(chunks_path, chunks_sha256).splitlines()
        )
        self.chunks = {c.chunk_key: c for c in parsed}
        if len(self.chunks) != len(parsed) or len(parsed) != report.chunk_count:
            raise LiteratureAdapterError("fixed corpus chunks are duplicate or incomplete")
        for chunk in parsed:
            if (
                chunk.corpus_release_key != report.corpus_release_key
                or hashlib.sha256(chunk.text.encode()).hexdigest() != chunk.text_sha256
            ):
                raise LiteratureAdapterError("fixed passage identity differs from corpus")
        rebuilt = [
            c.model_dump(mode="json", exclude={
                "corpus_release_key", "parser_policy_key", "chunking_policy_key"
            }) for c in sorted(parsed, key=lambda c: c.chunk_key)
        ]
        if canonical_json_sha256(rebuilt) != report.chunk_rebuild_sha256:
            raise LiteratureAdapterError("frozen chunks differ from the actual rebuild report")
        self._engine, self._bge = engine, bge
        self._repository = LiteratureRepository(engine)
        self._report = report

    def retrieve(
        self, question: str, *, anchors: tuple[RetrievalAnchor, ...] = ()
    ) -> tuple[
        tuple[str, ...],
        tuple[EvidenceCitation, ...],
        tuple[str, ...],
    ]:
        result = self.retrieve_detailed(question, anchors=anchors)
        return result.keys, result.citations, result.warnings

    def retrieve_detailed(
        self,
        question: str,
        *,
        anchors: tuple[RetrievalAnchor, ...] = (),
    ) -> SourceRetrievalResult:
        if contains_forbidden_topic(question):
            raise LiteratureAdapterError("shared scope refusal precedes retrieval")
        # Recheck immutable policy and release metadata on each invocation.
        capability = ValidatedCandidateGate(self._engine).authorize(self._report)
        warnings: tuple[str, ...] = ()
        planned = None
        repository = self._repository
        if self.lexical_query_policy == LEXICAL_QUERY_POLICY_KEY:
            # Planning consumes the original question only, never the answer or Oracle packet.
            planned = PlannedFtsCandidateProvider(plan_lexical_query(question))
            repository = LiteratureRepository(self._engine, lexical_candidates=planned)
        if self._bge is None:
            if anchors:
                raise LiteratureAdapterError("FTS-only S2 cannot receive source anchors")
            with self._engine.connect().execution_options(postgresql_readonly=True) as connection:
                with Session(bind=connection) as session, session.begin():
                    if planned is not None:
                        if planned.has_indexable_terms(session, question):
                            keys = planned.candidates(
                                session, capability, question=question, document_ids=None
                            )
                        else:
                            keys, warnings = (), ("fts_no_indexable_terms",)
                    elif repository._fts_has_nodes(session, question):
                        keys = repository._fts_candidates(
                            session, capability, question=question, document_ids=None
                        )
                    else:
                        keys, warnings = (), ("fts_no_indexable_terms",)
        else:
            result = repository.retrieve(
                capability,
                question=question,
                query_vector=self._bge.embed_query(question),
                anchors=anchors,
                top_k=100,
            )
            keys = tuple(h.chunk_key for h in result.hits)
            warnings = result.warnings
            for hit in result.hits:
                c = self.chunks.get(hit.chunk_key)
                if c is None or (hit.document_key, hit.text, hit.text_sha256, hit.locator_text) != (
                    c.document_key,
                    c.text,
                    c.text_sha256,
                    c.locator_text,
                ):
                    raise LiteratureAdapterError("database passage differs from frozen source")
        if len(keys) != len(set(keys)) or not set(keys) <= self.chunks.keys():
            raise LiteratureAdapterError("ranked keys repeat or escape the frozen corpus")
        if planned is not None:
            context_warnings = () if planned.context is None else planned.context.warnings
            warnings = tuple(dict.fromkeys((
                *warnings, *planned.plan.warnings, *context_warnings,
            )))
        if not keys:
            warnings = tuple(dict.fromkeys((*warnings, "no_chunks_retrieved")))
        return SourceRetrievalResult(
            keys, self.citations(keys[:GENERATION_CONTEXT_CHUNK_LIMIT]), warnings,
            None if planned is None else planned.diagnostics(),
        )

    def citations(self, keys: tuple[str, ...]) -> tuple[EvidenceCitation, ...]:
        if len(keys) != len(set(keys)) or not set(keys) <= self.chunks.keys():
            raise LiteratureAdapterError("citation keys repeat or escape the frozen corpus")
        return tuple(
            EvidenceCitation(
                citation_id=f"D{i}",
                document_key=self.chunks[k].document_key,
                chunk_key=k,
                locator_text=self.chunks[k].locator_text,
                text=self.chunks[k].text,
                text_sha256=self.chunks[k].text_sha256,
            )
            for i, k in enumerate(keys, 1)
        )
