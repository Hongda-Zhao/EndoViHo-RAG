#!/usr/bin/env python3
"""Isolated synthetic execution or core-53 S1-S4 retrieval rehearsal, never trusted scoring."""

from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

from sqlalchemy import create_engine

from eve_relation_rag.experiments.embedding_ablation.corpus_snapshot import (
    read_published_corpus_snapshot,
)
from eve_relation_rag.experiments.rag_value_ablation.association_projection import (
    AssociationProjectionBindings,
)
from eve_relation_rag.experiments.rag_value_ablation.display_labels import ApprovedDisplayLabels
from eve_relation_rag.experiments.rag_value_ablation.execution_gate import (
    Phase3ExecutionRequest,
    issue_phase3_execution_authority,
)
from eve_relation_rag.experiments.rag_value_ablation.literature_adapter import (
    LiteratureEvidenceAdapter,
    OfflineBgeQueryProvider,
)
from eve_relation_rag.experiments.rag_value_ablation.preflight import (
    Phase3PreflightInput,
    construct_phase3_dependencies,
    run_phase3_preflight,
)
from eve_relation_rag.experiments.rag_value_ablation.raw_context import load_raw_context
from eve_relation_rag.experiments.rag_value_ablation.runner import run_synthetic_benchmark
from eve_relation_rag.experiments.rag_value_ablation.structured_adapter import (
    build_experiment_structured_application,
    execute_core53_structured_question,
)
from eve_relation_rag.hybrid.contracts import canonical_model_json, canonical_model_sha256
from eve_relation_rag.planning.scope_policy import contains_forbidden_topic


def load_projection_options(args: argparse.Namespace) -> dict:
    """Load explicit, checksum-bound optional S4 projections before creating a database engine."""
    options = {}
    specifications = (
        ("projection_bindings", AssociationProjectionBindings, "binding_sha256",
         "approved_projection_binding_sha256"),
        ("display_labels", ApprovedDisplayLabels, "manifest_sha256",
         "approved_display_labels_sha256"),
    )
    for name, model, digest_field, output_digest in specifications:
        path = getattr(args, name, None)
        digest = getattr(args, name + "_sha256", None)
        if (path is None) != (digest is None):
            raise ValueError("projection input requires its exact approved manifest hash")
        if path is None:
            continue
        if args.mode != "s4-rehearsal" or path.is_symlink():
            raise ValueError("projection files belong to the structured rehearsal only")
        with path.open("rb") as handle:
            raw = handle.read(4 * 1024 * 1024 + 1)
        if len(raw) > 4 * 1024 * 1024:
            raise ValueError("projection input exceeds the size limit")
        value = model.model_validate_json(raw)
        if getattr(value, digest_field) != digest:
            raise ValueError("projection input differs from the approved manifest")
        options[name] = value
        options[output_digest] = digest
    if "display_labels" in options and "projection_bindings" not in options:
        raise ValueError("display labels require exact projection bindings")
    return options


def rehearse(args: argparse.Namespace) -> int:
    # No default settings or credential files are read. The caller names one test URL.
    args.output.mkdir(parents=False, exist_ok=False)
    engine = None
    try:
        projection_options = load_projection_options(args)
        evidence = Phase3PreflightInput.model_validate_json(args.preflight.read_bytes())
        request = Phase3ExecutionRequest.model_validate_json(args.request.read_bytes())
        system_key = args.mode.split("-")[0].upper()
        if (
            request.system_keys != (system_key,)
            or evidence.questions.scope_revision != "single-source-v2"
        ):
            raise ValueError("this rehearsal requires one exact active core-53 system")
        manifest = evidence.questions.bound_question_manifest
        if manifest is None:
            raise ValueError("exact bound questions are required")
        engine = create_engine(os.environ["RAG_VALUE_DATABASE_URL"])
        decision = run_phase3_preflight(evidence)
        authority = issue_phase3_execution_authority(
            engine=engine,
            evidence=evidence,
            decision=decision,
            request=request,
            approved_request_file_sha256=args.approved_request_sha256,
        )
        bound_engine = engine
        provider = None
        application = None
        raw_bundle = None
        if system_key in {"S2", "S3"}:

            def make_literature_provider():
                published = read_published_corpus_snapshot(
                    bound_engine, evidence.corpus_release.release_key
                )
                bge = None
                if system_key == "S3":
                    if request.bge_asset_files is None:
                        raise ValueError("explicit BGE files missing")
                    bge = OfflineBgeQueryProvider(
                        request.bge_asset_files, evidence.retrieval.bge_artifact.approved_sha256
                    )
                return LiteratureEvidenceAdapter(bound_engine, published, bge=bge)

            provider = construct_phase3_dependencies(
                decision,
                make_literature_provider,
                execution_authority=authority,
            )
        elif system_key == "S4":
            def make_structured_application():
                if "display_labels" in projection_options:
                    published = read_published_corpus_snapshot(
                        bound_engine, evidence.corpus_release.release_key
                    )
                    if published.corpus_manifest_sha256 != manifest.corpus_manifest_sha256:
                        raise ValueError("display documentation corpus differs from the manifest")
                    projection_options["permitted_document_keys"] = frozenset(
                        document.document_key for document in published.documents
                    )
                return build_experiment_structured_application(bound_engine)

            application = construct_phase3_dependencies(
                decision,
                make_structured_application,
                execution_authority=authority,
            )
        elif system_key == "S1":
            if request.raw_context_files is None:
                raise ValueError("explicit raw files missing")
            raw_bundle = construct_phase3_dependencies(
                decision,
                lambda: load_raw_context(
                    request.raw_context_files,
                    approved_material_sha256=evidence.raw_context.material_manifest.approved_sha256,
                    approved_policy_sha256=evidence.raw_context.construction_policy.approved_sha256,
                    approved_tokenizer_sha256=evidence.raw_context.tokenizer_artifact.approved_sha256,
                ),
                execution_authority=authority,
            )
        any_failure = False
        for question in manifest.questions:
            started = time.perf_counter_ns()
            record = {
                "question_id": question.question_id,
                "system_key": system_key,
                "status": "scope_refused",
                "returned_chunk_keys": (),
                "generation_executed": False,
                "trust_level": "untrusted_rehearsal",
                "score": None,
                "preflight_input_sha256": evidence.input_sha256,
            }
            if not contains_forbidden_topic(question.question_text):
                try:
                    if provider is not None:
                        keys, citations = provider.retrieve(question.question_text)
                        record["returned_chunk_keys"] = keys
                        record["citations"] = citations
                        record["status"] = "retrieved"
                    elif raw_bundle is not None:
                        record["raw_material_manifest"] = raw_bundle[0]
                        record["raw_policy"] = raw_bundle[1]
                        record["raw_context_segments"] = raw_bundle[2]
                        record["input_tokens"] = None
                        record["status"] = "raw_materials_verified_pending_tokenization"
                    elif application is not None and question.family in {"structured", "hybrid"}:
                        scope = evidence.questions.core53_scope
                        if scope is None:
                            raise ValueError("bound scope missing")
                        record["structured"] = execute_core53_structured_question(
                            application,
                            scope,
                            manifest,
                            question.question_id,
                            **projection_options,
                        )
                        record["status"] = "structured_retrieved"
                    else:
                        record["status"] = "adapter_not_applicable"
                except Exception:
                    record["status"] = "retrieval_failed"
                    any_failure = True
            record["latency_ns"] = time.perf_counter_ns() - started
            record["record_sha256"] = canonical_model_sha256(record)
            with (args.output / f"{question.question_id}.json").open("xb") as handle:
                handle.write(canonical_model_json(record).encode("utf-8") + b"\n")
        return 2 if any_failure else 0
    except Exception:
        # Error strings can contain connection URLs or document text; do not serialize them.
        with (args.output / "failure.json").open("xb") as handle:
            handle.write(
                canonical_model_json(
                    {
                        "status": "execution_blocked_or_failed",
                        "trust_level": "untrusted_rehearsal",
                        "score": None,
                    }
                ).encode("utf-8")
                + b"\n"
            )
        return 2
    finally:
        if engine is not None:
            engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    synthetic = sub.add_parser("synthetic")
    synthetic.add_argument("--output", type=Path, required=True)
    for mode in ("s1-rehearsal", "s2-rehearsal", "s3-rehearsal", "s4-rehearsal"):
        rehearsal = sub.add_parser(mode)
        rehearsal.add_argument("--output", type=Path, required=True)
        rehearsal.add_argument("--preflight", type=Path, required=True)
        rehearsal.add_argument("--request", type=Path, required=True)
        rehearsal.add_argument("--approved-request-sha256", required=True)
        if mode == "s4-rehearsal":
            rehearsal.add_argument("--projection-bindings", type=Path)
            rehearsal.add_argument(
                "--projection-bindings-sha256", help="approved manifest's binding_sha256"
            )
            rehearsal.add_argument("--display-labels", type=Path)
            rehearsal.add_argument(
                "--display-labels-sha256", help="approved display manifest's manifest_sha256"
            )
    args = parser.parse_args()
    if args.mode == "synthetic":
        run_synthetic_benchmark(args.output)
        print("tests_only: synthetic S0-S6 complete; no real experiment scores")
        return 0
    return rehearse(args)


if __name__ == "__main__":
    raise SystemExit(main())
