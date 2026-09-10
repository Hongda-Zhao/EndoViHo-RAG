"""Actual local source-reported-v1 execution with a resumable machine journal.

This path does not issue the legacy synthetic trust token or scientific scores.
Formal mode requires explicit, checksum-bound input Gold/Oracle approval. Model
errors are observations; infrastructure failures remain separate statuses.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url

from eve_relation_rag.experiments.rag_value_ablation.contracts import (
    RawContextSegment,
    SystemKey,
    build_evidence_pack,
)
from eve_relation_rag.experiments.rag_value_ablation.lexical_query import LEXICAL_QUERY_POLICY_KEY
from eve_relation_rag.experiments.rag_value_ablation.literature_adapter import (
    BgeAssetFiles,
    OfflineBgeQueryProvider,
)
from eve_relation_rag.experiments.rag_value_ablation.local_generation import (
    CONTEXT_LIMIT,
    OUTPUT_LIMIT,
    TOKENIZER_KEY,
    ContextOverflow,
    LocalGenerationConfig,
    LocalGenerationTimeout,
    OfflineMlxGenerationProvider,
    build_measured_evidence,
)
from eve_relation_rag.experiments.rag_value_ablation.prompting import build_prompt_policy
from eve_relation_rag.experiments.rag_value_ablation.source_corpus import (
    LEXICAL_QUERY_POLICIES,
    ORIGINAL_LEXICAL_QUERY_POLICY,
    SourceCorpusEvidenceAdapter,
    SourceDocumentBindings,
    read_pinned,
)
from eve_relation_rag.experiments.rag_value_ablation.source_report_queries import (
    CORE53_SOURCE_QUERY_IDS,
    SourceReportQuery,
    SourceReportRepository,
)
from eve_relation_rag.experiments.rag_value_ablation.systems import (
    ALL_SYSTEM_KEYS,
    build_source_report_system_definitions,
    validate_evidence_for_system,
)
from eve_relation_rag.literature.anchors import CorpusAnchorManifest
from eve_relation_rag.literature.hashing import canonical_json_bytes, canonical_json_sha256
from eve_relation_rag.literature.validation import RebuildValidationReport
from eve_relation_rag.planning.scope_policy import contains_forbidden_topic


def atomic_json(path: Path, value: Any) -> None:
    """Commit one complete JSON object; existing final records are immutable."""
    raw = canonical_json_bytes(value) + b"\n"
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("xb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.link(temp, path)  # Fails rather than replacing an existing result.
    finally:
        temp.unlink()


class SourceExperiment:
    def __init__(self, config_path: Path, expected_sha256: str) -> None:
        self.config = json.loads(read_pinned(config_path, expected_sha256))
        self.config_sha256 = expected_sha256
        c = self.config
        if (
            c["schema_version"] != "rag-value-source-runtime-v1"
            or c["variant"] != "source-reported-v1"
            or c["public_publication"] is not False
        ):
            raise ValueError("unsupported runtime configuration")
        self._execution_systems()
        self.lexical_query_policy = c.get(
            "lexical_query_policy", ORIGINAL_LEXICAL_QUERY_POLICY
        )
        if self.lexical_query_policy not in LEXICAL_QUERY_POLICIES:
            raise ValueError("unknown frozen lexical query policy")
        self.verify_files()
        if json.loads(self.read("amendment"))["accepted_variant"] != c["variant"]:
            raise ValueError("source variant has no matching accepted amendment")
        if c["prompt_policy_sha256"] != build_prompt_policy().policy_sha256:
            raise ValueError("common prompt differs from frozen configuration")
        self.repository = SourceReportRepository(
            Path(c["source_packet_directory"]),
            expected_manifest_file_sha256=c["files"]["source_packet_manifest"]["sha256"],
        )
        self.questions = tuple(json.loads(line) for line in self.read("questions").splitlines())
        if (
            len(self.questions) != 53
            or len({q["question_id"] for q in self.questions}) != 53
            or Counter(q["family"] for q in self.questions)
            != Counter(structured=16, literature=16, hybrid=9, unsupported=12)
        ):
            raise ValueError("frozen core53 coverage differs")
        if {
            q["question_id"] for q in self.questions if q["family"] in {"structured", "hybrid"}
        } != (CORE53_SOURCE_QUERY_IDS):
            raise ValueError("structured query matrix differs from exact core53 IDs")
        model = c["generation"]
        self.provider_config = LocalGenerationConfig(
            model_root=Path(model["model_root"]),
            model_policy_path=self.path("model_policy"),
            model_policy_file_sha256=c["files"]["model_policy"]["sha256"],
            python_executable=self.path("generation_python"),
            worker_script=self.path("generation_worker"),
        )
        self._engine: Engine | None = None
        self._fts: SourceCorpusEvidenceAdapter | None = None
        self._hybrid: SourceCorpusEvidenceAdapter | None = None
        self._bindings: SourceDocumentBindings | None = None

    def _execution_systems(self) -> tuple[SystemKey, ...]:
        if "execution_systems" not in self.config:
            return ALL_SYSTEM_KEYS
        requested = self.config["execution_systems"]
        if (
            not isinstance(requested, list)
            or not requested
            or any(not isinstance(system, str) or system not in ALL_SYSTEM_KEYS
                   for system in requested)
        ):
            raise ValueError("execution_systems must be a nonempty list of valid system keys")
        canonical = tuple(system for system in ALL_SYSTEM_KEYS if system in requested)
        if tuple(requested) != canonical:
            raise ValueError("execution_systems must be unique and in canonical S0-S6 order")
        return canonical

    def path(self, key: str) -> Path:
        return Path(self.config["files"][key]["path"])

    def read(self, key: str) -> bytes:
        return read_pinned(
            self.path(key),
            self.config["files"][key]["sha256"],
            interpreter_link=key in {"generation_python", "bge_python"},
        )

    def verify_files(self) -> None:
        if self.config.get("lexical_query_policy") == LEXICAL_QUERY_POLICY_KEY:
            required = {
                Path(__file__).with_name(name).resolve()
                for name in ("lexical_query.py", "lexical_context.py", "planned_fts.py",
                             "source_corpus.py", "source_experiment.py")
            }
            required.add(
                Path(__file__).parents[2] / "retrieval" / "literature" / "repository.py"
            )
            pinned = {Path(item["path"]).resolve()
                      for item in self.config["implementation_files"]}
            if not required <= pinned:
                raise ValueError("planned lexical policy requires all active implementation pins")
        for key in self.config["files"]:
            self.read(key)
        for item in self.config["implementation_files"]:
            read_pinned(Path(item["path"]), item["sha256"])

    def literature(self, hybrid: bool) -> SourceCorpusEvidenceAdapter:
        if self._engine is None:
            url = self.read("database_url").decode().strip()
            parsed = make_url(url)
            if (
                parsed.host != "127.0.0.1"
                or parsed.port != 5432
                or parsed.database != self.config["database_name"]
                or parsed.username != self.config["database_reader_role"]
            ):
                raise ValueError("database is outside the isolated read-only runtime")
            self._engine = create_engine(
                url, connect_args={"options": "-c default_transaction_read_only=on"}
            )
            with self._engine.connect() as connection:
                if connection.scalar(text("SHOW transaction_read_only")) != "on":
                    raise ValueError("database connection is not read-only")
        current = self._hybrid if hybrid else self._fts
        if current is None:
            bge = None
            if hybrid:
                b = self.config["bge"]
                bge = OfflineBgeQueryProvider(
                    BgeAssetFiles(
                        model_root=b["model_root"],
                        artifact_manifest_path=str(self.path("bge_manifest")),
                        python_executable=str(self.path("bge_python")),
                        python_executable_sha256=self.config["files"]["bge_python"]["sha256"],
                        worker_script=str(self.path("bge_worker")),
                        worker_script_sha256=self.config["files"]["bge_worker"]["sha256"],
                    ),
                    self.config["files"]["bge_manifest"]["sha256"],
                )
            current = SourceCorpusEvidenceAdapter(
                self._engine,
                RebuildValidationReport.model_validate_json(self.read("corpus_rebuild")),
                chunks_path=self.path("chunks"),
                chunks_sha256=self.config["files"]["chunks"]["sha256"],
                bge=bge,
                lexical_query_policy=self.lexical_query_policy,
            )
            if hybrid:
                self._hybrid = current
            else:
                self._fts = current
        return current

    def bindings(self) -> SourceDocumentBindings:
        if self._bindings is None:
            self._bindings = SourceDocumentBindings(
                self.repository,
                self.literature(True).chunks,
                row_bindings=self.path("row_bindings"),
                row_bindings_sha256=self.config["files"]["row_bindings"]["sha256"],
                corpus_preparation=self.path("corpus_preparation"),
                corpus_preparation_sha256=self.config["files"]["corpus_preparation"]["sha256"],
                anchors=CorpusAnchorManifest.model_validate_json(self.read("anchor_manifest")),
            )
        return self._bindings

    def raw_segments(self) -> tuple[RawContextSegment, ...]:
        segments = []
        for i, item in enumerate(self.config["raw_sources"], 1):
            raw = read_pinned(Path(item["path"]), item["sha256"])
            segments.append(
                RawContextSegment(
                    segment_id=f"R{i}",
                    source_kind=item["source_kind"],
                    source_key=item["source_key"],
                    source_sha256=item["sha256"],
                    byte_start=0,
                    byte_end=len(raw),
                    text=raw.decode(),
                    text_sha256=item["sha256"],
                )
            )
        return tuple(segments)

    def execute_cell(
        self,
        provider: OfflineMlxGenerationProvider,
        question: dict[str, Any],
        system: SystemKey,
        *,
        oracle: dict[str, Any] | None = None,
        development_queries: tuple[SourceReportQuery, ...] = (),
    ) -> dict[str, Any]:
        qid, wording = question["question_id"], question["question_text_draft"]
        base: dict[str, Any] = {
            "system_key": system,
            "question_id": qid,
            "question_text": wording,
            "question_text_sha256": hashlib.sha256(wording.encode()).hexdigest(),
            "generation_executed": False,
            "scientific_score": None,
            "events": ["request_validation"],
        }
        if contains_forbidden_topic(wording):
            return {**base, "status": "scope_refused", "refusal_origin": "shared_scope_policy"}
        if system in {"S4", "S5"} and question["family"] == "literature":
            return {
                **base,
                "status": "not_applicable",
                "reason": "pure_literature_has_no_structured_route",
            }
        if (
            system in {"S4", "S5"}
            and qid not in CORE53_SOURCE_QUERY_IDS
            and not development_queries
        ):
            return {**base, "status": "route_refused", "refusal_origin": "source_query_whitelist"}
        values: dict[str, Any] = dict(
            question_id=qid, question_text=wording, policy_sha256=self.config_sha256
        )
        if system in {"S4", "S5"}:
            queries = development_queries or self.repository.core53_queries(qid)
            groups = tuple(self.repository.query(q) for q in queries)
            values["source_report_groups"] = groups
            base["events"].append("complete_source_queries")
            base["source_results"] = groups
            if system == "S4":
                return {
                    **base,
                    "status": "completed",
                    "result_kind": "deterministic_source_reports",
                }
            anchors, provenance = self.bindings().resolve(groups)
            base.update(source_provenance=provenance, anchors=anchors)
            base["events"].append("exact_source_document_anchors")
            retrieved = self.literature(True).retrieve_detailed(wording, anchors=anchors)
            values["citations"] = retrieved.citations
            base.update(retrieved_chunk_keys=retrieved.keys, retrieval_warnings=retrieved.warnings)
            if retrieved.diagnostics is not None:
                base["retrieval_diagnostics"] = retrieved.diagnostics
            base["events"].append(
                "anchored_planned_hybrid_retrieval" if retrieved.diagnostics is not None
                else "anchored_original_hybrid_retrieval"
            )
        elif system in {"S2", "S3"}:
            retrieved = self.literature(system == "S3").retrieve_detailed(wording)
            values["citations"] = retrieved.citations
            base.update(retrieved_chunk_keys=retrieved.keys, retrieval_warnings=retrieved.warnings)
            if retrieved.diagnostics is not None:
                base["retrieval_diagnostics"] = retrieved.diagnostics
            base["events"].append(
                ("planned_fts" if system == "S2" else "planned_hybrid_retrieval")
                if retrieved.diagnostics is not None
                else ("original_fts" if system == "S2" else "original_hybrid_retrieval")
            )
        elif system == "S1":
            values["raw_context_segments"] = self.raw_segments()
            base["events"].append("complete_raw_materials")
        elif system == "S6":
            if oracle is None:
                return {
                    **base,
                    "status": "input_review_required",
                    "reason": "approved_oracle_missing",
                }
            if oracle["question_id"] != qid:
                raise ValueError("Oracle belongs to another question")
            queries = tuple(
                SourceReportQuery.model_validate_json(json.dumps(q))
                for q in oracle["source_queries"]
            )
            values["source_report_groups"] = tuple(self.repository.query(q) for q in queries)
            values["citations"] = self.literature(False).citations(tuple(oracle["chunk_keys"]))
            values["oracle_entry_sha256"] = canonical_json_sha256(oracle)
            base["events"].append("oracle_evidence_loaded")
        definitions = {
            s.system_key: s for s in build_source_report_system_definitions(provider.identity)
        }
        provisional = build_evidence_pack(
            **values,
            tokenizer_key=TOKENIZER_KEY,
            model_context_limit_tokens=CONTEXT_LIMIT,
            reserved_output_tokens=OUTPUT_LIMIT,
            input_token_count=0,
            context_token_count=0,
        )
        validate_evidence_for_system(definitions[system], provisional)
        try:
            measured = build_measured_evidence(provider, **values)
        except ContextOverflow as exc:
            return {
                **base,
                "status": "context_overflow",
                "input_tokens": exc.input_token_count,
                "truncated": False,
                "omitted_source_keys": (),
                "provisional_evidence": provisional,
                "exchange_receipt": provider.last_exchange_receipt,
            }
        except LocalGenerationTimeout:
            return {
                **base,
                "status": "tokenization_timeout",
                "generation_attempted": False,
                "provisional_evidence": provisional,
                "timeout_seconds": 300,
            }
        validate_evidence_for_system(definitions[system], measured)
        base["events"].append("exact_token_measurement")
        try:
            result = provider.generate(measured)
        except LocalGenerationTimeout as exc:
            attempted = exc.operation == "generate"
            return {
                **base,
                "status": "model_timeout" if attempted else "tokenization_timeout",
                "generation_executed": None if attempted else False,
                "generation_attempted": attempted,
                "evidence": measured,
                "input_tokens": measured.construction.input_token_count,
                "timeout_seconds": 300,
                "raw_answer_available": False,
                "worker_terminated": True,
                "automatic_retry": False,
            }
        receipt = provider.last_exchange_receipt
        expected = {
            "schema_version": "rag-value-local-exchange-v1",
            "operation": "generate",
            "worker_sha256": provider.runtime["worker_sha256"],
            "model_policy_file_sha256": self.config["files"]["model_policy"]["sha256"],
            "network_denial_probes_passed": True,
            "fresh_conversation": True,
            "seed": 0,
            "temperature": 0,
        }
        if receipt is None or receipt["execution_attestation"] != expected:
            raise ValueError("actual generation exchange has no matching local attestation")
        return {
            **base,
            "status": result.status,
            "generation_executed": True,
            "generation_identity": provider.identity,
            "runtime": provider.runtime,
            "result": asdict(result),
            "exchange_receipt": receipt,
            "events": [*base["events"], "generation", "answer_validation"],
        }

    def run(
        self,
        output: Path,
        *,
        approved_review: Path | None = None,
        approved_review_sha256: str | None = None,
    ) -> dict[str, Any]:
        """Formal entry point; reject pending input review before any cell or provider."""
        if approved_review is None or approved_review_sha256 is None:
            raise ValueError("formal run requires explicit input Gold and Oracle approval")
        if self.config["status"] != "frozen_for_formal_execution":
            raise ValueError("formal run requires a final engineering freeze")
        execution_systems = self._execution_systems()
        expected_cells = 53 * len(execution_systems)
        review = json.loads(read_pinned(approved_review, approved_review_sha256))
        if (
            review["schema_version"] != "rag-value-source-input-approval-v1"
            or review["runtime_config_sha256"] != self.config_sha256
            or review["gold_approved"] is not True
            or review["oracle_approved"] is not True
            or not review["reviewer"]
            or not review["user_approval_message"]
            or review["reference_packet_sha256"] != self.config["files"]["input_review"]["sha256"]
        ):
            raise ValueError("input approval does not bind this exact source experiment")
        oracles = {e["question_id"]: e for e in review["oracle_entries"]}
        if len(review["oracle_entries"]) != 53 or set(oracles) != {
            q["question_id"] for q in self.questions
        }:
            raise ValueError("approved Oracle entries do not cover the exact 53 questions")
        candidates = tuple(
            json.loads(line) for line in self.read("gold_oracle_candidates").splitlines()
        )
        if review["oracle_entries"] != [entry["oracle_entry"] for entry in candidates]:
            raise ValueError("approved Oracle differs from the frozen reviewed evidence")
        output.mkdir(exist_ok=True)
        records = []
        with OfflineMlxGenerationProvider(self.provider_config) as provider:
            for question in self.questions:
                for system in execution_systems:
                    self.verify_files()
                    qid = question["question_id"]
                    target = output / f"{qid}.{system}.json"
                    claim = output / f"{qid}.{system}.started.json"
                    identity = {
                        "runtime_config_sha256": self.config_sha256,
                        "input_approval_file_sha256": approved_review_sha256,
                        "question_id": qid,
                        "system_key": system,
                        "run_mode": "formal_machine_execution",
                    }
                    if target.exists():
                        existing = json.loads(target.read_bytes())
                        if any(existing.get(k) != v for k, v in identity.items()) or (
                            existing["record_sha256"]
                            != canonical_json_sha256(
                                {k: v for k, v in existing.items() if k != "record_sha256"}
                            )
                        ):
                            raise ValueError("existing record differs from the frozen run")
                        records.append(existing)
                        continue
                    if claim.exists():
                        if json.loads(claim.read_bytes()) != identity:
                            raise ValueError("started cell belongs to a different frozen run")
                        value = {
                            **identity,
                            "status": "execution_interrupted",
                            "generation_executed": None,
                            "scientific_score": None,
                            "reason": "started previously; automatic retry is disabled",
                        }
                    else:
                        atomic_json(claim, identity)
                        started = time.monotonic_ns()
                        try:
                            result = self.execute_cell(
                                provider, question, system, oracle=oracles[qid]
                            )
                        except Exception as exc:
                            result = {
                                "status": "infrastructure_failure",
                                "generation_executed": None,
                                "scientific_score": None,
                                "error_type": type(exc).__name__,
                            }
                        value = {
                            **identity,
                            **result,
                            "cell_latency_ns": time.monotonic_ns() - started,
                        }
                    self.verify_files()
                    value["record_sha256"] = canonical_json_sha256(value)
                    atomic_json(target, value)
                    records.append(value)
                    print(f"{qid} {system}: {value['status']}", flush=True)
        counts = Counter(r["status"] for r in records)
        summary = {
            "schema_version": "rag-value-source-machine-summary-v1",
            "runtime_config_sha256": self.config_sha256,
            "input_approval_file_sha256": approved_review_sha256,
            "expected_cells": expected_cells,
            "recorded_cells": len(records),
            "status_counts": dict(counts),
            "generation_calls_confirmed": sum(r["generation_executed"] is True for r in records),
            "generation_requests_timed_out": counts["model_timeout"],
            "scientific_scoring_completed": False,
            "public_publication": False,
            "execution_complete": len(records) == expected_cells
            and not any(
                counts[s]
                for s in (
                    "infrastructure_failure",
                    "execution_interrupted",
                    "input_review_required",
                )
            ),
            "records": {
                f"{r['question_id']}.{r['system_key']}": r["record_sha256"] for r in records
            },
        }
        # Unconfigured full runs retain the legacy summary for exact journal replay.
        if "execution_systems" in self.config:
            full_matrix = execution_systems == ALL_SYSTEM_KEYS
            summary.update({
                "execution_systems": list(execution_systems),
                "execution_scope": "full_matrix" if full_matrix else "system_subset",
                "full_matrix_expected_cells": 53 * len(ALL_SYSTEM_KEYS),
                "full_matrix_execution_complete": full_matrix and summary["execution_complete"],
            })
        if not (output / "summary.json").exists():
            atomic_json(output / "summary.json", summary)
        elif json.loads((output / "summary.json").read_bytes()) != summary:
            raise ValueError("saved summary differs from the complete record journal")
        return summary
