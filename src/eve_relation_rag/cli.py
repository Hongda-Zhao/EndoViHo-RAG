"""Typer adapters for structured queries and fixed-corpus literature operations."""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any

import typer
from click import UsageError
from pydantic import ValidationError
from typer.core import TyperGroup

from eve_relation_rag.activation.corpus import V0_CORPUS_RELEASE_KEY
from eve_relation_rag.application.literature import CandidateBenchmarkService
from eve_relation_rag.bootstrap import (
    get_engine,
    get_literature_retrieval_service,
    get_local_bge_provider,
    get_rag_query_application,
    get_structured_query_application,
)
from eve_relation_rag.cli_v0 import register_v0_commands
from eve_relation_rag.hybrid.contracts import RagErrorResponse, RagQueryRequest
from eve_relation_rag.hybrid.rendering import serialize_rag_response
from eve_relation_rag.hybrid.transport import (
    rag_cli_exit_code_for,
    rag_internal_error_response,
    rag_request_validation_response,
)
from eve_relation_rag.literature.anchors import (
    CorpusAnchorManifest,
    import_candidate_anchors,
)
from eve_relation_rag.literature.benchmarking import (
    BenchmarkDefinition,
    collect_benchmark_runtime_fingerprint,
    run_benchmark,
)
from eve_relation_rag.literature.contracts import (
    CorpusManifest,
    LiteratureRetrievalError,
    LiteratureRetrievalInvocation,
    LiteratureRetrievalRequest,
)
from eve_relation_rag.literature.ingestion import import_candidate_corpus
from eve_relation_rag.literature.publication import (
    publish_corpus,
    record_pilot_validation_receipt,
)
from eve_relation_rag.literature.validation import validate_corpus_rebuild
from eve_relation_rag.planning.parser import StructuredQueryRequest
from eve_relation_rag.planning.query_plans import PageSpec
from eve_relation_rag.releases.publication import (
    prepare_dataset_candidate_validation_input,
    prepare_dataset_validation_input,
    publish_dataset_release,
    record_dataset_validation_receipt,
)
from eve_relation_rag.releases.receipt_integrity import (
    load_approved_validation_input,
    load_dataset_activation_evidence,
    load_dataset_candidate_activation_evidence,
    load_dataset_candidate_validation_input,
    load_validation_request,
)
from eve_relation_rag.retrieval.structured.capability import LineageRole
from eve_relation_rag.retrieval.structured.rendering import (
    render_structured_result_table,
    serialize_structured_response,
)
from eve_relation_rag.retrieval.structured.results import ErrorResponse, QuerySuccess
from eve_relation_rag.transport import (
    cli_exit_code_for,
    internal_error_response,
    request_validation_response,
)


class OutputFormat(StrEnum):
    """Approved structured query presentation formats."""

    json = "json"
    table = "table"


def _click_validation_response(exc: typer.BadParameter) -> ErrorResponse:
    """Convert Click's pre-callback refusals to the same stable JSON envelope."""

    parameter = getattr(exc, "param", None)
    field = getattr(parameter, "name", None) or "request"
    location = ("page", field) if field in {"limit", "cursor"} else (field,)
    validation_type = "missing" if exc.__class__.__name__ == "MissingParameter" else "invalid"
    message = (
        "A required command-line option is missing."
        if validation_type == "missing"
        else "The command-line option is invalid."
    )
    return request_validation_response(
        ({"loc": location, "type": validation_type, "msg": message},)
    )


def _is_rag_invocation(args: Sequence[str] | None) -> bool:
    invocation = args if args is not None else sys.argv[1:]
    return bool(invocation and invocation[0] == "rag")


def _rag_click_body(args: Sequence[str] | None) -> dict[str, str]:
    values: dict[str, str] = {}
    invocation = args if args is not None else sys.argv[1:]
    for option, field in (
        ("--release-key", "release_key"),
        ("--corpus-release-key", "corpus_release_key"),
    ):
        try:
            index = invocation.index(option)
        except ValueError:
            continue
        if index + 1 < len(invocation):
            values[field] = invocation[index + 1]
    return values


class _StructuredTyperGroup(TyperGroup):
    """Keep Click parsing errors inside the structured-query error contract."""

    def main(
        self,
        args: Sequence[str] | None = None,
        prog_name: str | None = None,
        complete_var: str | None = None,
        standalone_mode: bool = True,
        windows_expand_args: bool = True,
        **extra: Any,
    ) -> Any:
        try:
            result = super().main(
                args=args,
                prog_name=prog_name,
                complete_var=complete_var,
                standalone_mode=False,
                windows_expand_args=windows_expand_args,
                **extra,
            )
        except typer.BadParameter as exc:
            if _is_rag_invocation(args):
                rag_response = rag_request_validation_response(
                    (
                        {
                            "loc": (
                                getattr(getattr(exc, "param", None), "name", None) or "request",
                            ),
                            "type": "invalid",
                            "msg": "The command-line option is invalid.",
                        },
                    ),
                    body=_rag_click_body(args),
                )
                typer.echo(serialize_rag_response(rag_response), err=True)
                if standalone_mode:
                    raise SystemExit(rag_cli_exit_code_for(rag_response)) from None
                return rag_cli_exit_code_for(rag_response)
            structured_response = _click_validation_response(exc)
            typer.echo(serialize_structured_response(structured_response), err=True)
            if standalone_mode:
                raise SystemExit(cli_exit_code_for(structured_response)) from None
            return cli_exit_code_for(structured_response)
        except UsageError:
            if not _is_rag_invocation(args):
                raise
            rag_response = rag_request_validation_response(
                (
                    {
                        "loc": ("request",),
                        "type": "invalid",
                        "msg": "The command-line invocation is invalid.",
                    },
                ),
                body=_rag_click_body(args),
            )
            typer.echo(serialize_rag_response(rag_response), err=True)
            if standalone_mode:
                raise SystemExit(rag_cli_exit_code_for(rag_response)) from None
            return rag_cli_exit_code_for(rag_response)

        if standalone_mode and isinstance(result, int) and result != 0:
            raise SystemExit(result)
        return result


app = typer.Typer(
    help="Auditable EndoViHo-RAG command-line interface.",
    cls=_StructuredTyperGroup,
)
structured_app = typer.Typer(
    help="Plan or execute a deterministic controlled-English structured query.",
    no_args_is_help=True,
)
app.add_typer(structured_app, name="structured")
literature_app = typer.Typer(
    help="Operate or directly test the fixed-corpus literature layer.",
    no_args_is_help=True,
)
app.add_typer(literature_app, name="literature")
rag_app = typer.Typer(
    help="Route an English question through the Milestone 4 RAG contract.",
    no_args_is_help=True,
)
app.add_typer(rag_app, name="rag")
register_v0_commands(app)


def _request(
    *,
    release_key: str,
    question: str,
    limit: int | None = None,
    cursor: str | None = None,
) -> StructuredQueryRequest:
    page = (
        PageSpec(limit=limit if limit is not None else 50, cursor=cursor)
        if limit is not None or cursor is not None
        else None
    )
    return StructuredQueryRequest(
        release_key=release_key,
        question=question,
        page=page,
    )


def _validation_failure(exc: ValidationError) -> None:
    response = request_validation_response(exc.errors(include_url=False))
    typer.echo(serialize_structured_response(response), err=True)
    raise typer.Exit(cli_exit_code_for(response))


def _emit_error(response: ErrorResponse) -> None:
    typer.echo(serialize_structured_response(response), err=True)
    raise typer.Exit(cli_exit_code_for(response))


def _emit_rag_error(response: RagErrorResponse) -> None:
    try:
        rendered = serialize_rag_response(response)
        trusted = RagErrorResponse.model_validate_json(rendered)
    except Exception:
        trusted = rag_internal_error_response()
        rendered = serialize_rag_response(trusted)
    typer.echo(rendered, err=True)
    raise typer.Exit(rag_cli_exit_code_for(trusted))


@structured_app.command("plan")
def plan_command(
    release_key: Annotated[
        str,
        typer.Option("--release-key", help="Exact immutable release key."),
    ],
    question: Annotated[
        str,
        typer.Option("--question", help="Controlled-English question."),
    ],
) -> None:
    """Validate and display the server-authored query plan without fact retrieval."""

    try:
        request = _request(release_key=release_key, question=question)
        response = get_structured_query_application().plan(request)
    except ValidationError as exc:
        _validation_failure(exc)
    except Exception:  # pragma: no cover - last-resort CLI safety boundary.
        _emit_error(internal_error_response())
    if isinstance(response, ErrorResponse):
        _emit_error(response)
    typer.echo(serialize_structured_response(response))


@structured_app.command("query")
def query_command(
    release_key: Annotated[
        str,
        typer.Option("--release-key", help="Exact immutable release key."),
    ],
    question: Annotated[
        str,
        typer.Option("--question", help="Controlled-English question."),
    ],
    limit: Annotated[int | None, typer.Option("--limit", min=1, max=100)] = None,
    cursor: Annotated[str | None, typer.Option("--cursor")] = None,
    output_format: Annotated[
        OutputFormat,
        typer.Option("--format", case_sensitive=False),
    ] = OutputFormat.json,
) -> None:
    """Execute a controlled-English query against one published release."""

    try:
        request = _request(
            release_key=release_key,
            question=question,
            limit=limit,
            cursor=cursor,
        )
        response = get_structured_query_application().query(request)
    except ValidationError as exc:
        _validation_failure(exc)
    except Exception:  # pragma: no cover - last-resort CLI safety boundary.
        _emit_error(internal_error_response())
    if isinstance(response, ErrorResponse):
        _emit_error(response)
    if output_format is OutputFormat.table and isinstance(response, QuerySuccess):
        typer.echo(render_structured_result_table(response.structured_result))
    else:
        typer.echo(serialize_structured_response(response))


@structured_app.command("release-validate")
def structured_release_validate_command(
    input_path: Annotated[
        Path,
        typer.Option(
            "--input-path",
            exists=True,
            dir_okay=False,
            help="Exact canonical dataset-validation-input-v1 JSON.",
        ),
    ],
    approved_input_sha256: Annotated[
        str,
        typer.Option(
            "--approved-input-sha256",
            help="Separately approved self-checksum of the validation input.",
        ),
    ],
) -> None:
    """Replay approved science, record a trusted receipt, and validate the candidate."""

    try:
        approved = load_approved_validation_input(
            input_path,
            approved_input_sha256=approved_input_sha256,
        )
        report = record_dataset_validation_receipt(
            get_engine(),
            approved_input=approved,
            approved_input_sha256=approved_input_sha256,
        )
    except Exception as exc:
        _structured_admin_operation_error(str(exc), exit_code=4)
    typer.echo(report.model_dump_json())


@structured_app.command("release-prepare-candidate-validation-input")
def structured_release_prepare_candidate_validation_input_command(
    request_path: Annotated[
        Path,
        typer.Option(
            "--request-path",
            exists=True,
            dir_okay=False,
            help="Complete canonical ReleaseValidationRequest JSON.",
        ),
    ],
    candidate_activation_evidence_path: Annotated[
        Path,
        typer.Option(
            "--candidate-activation-evidence-path",
            exists=True,
            dir_okay=False,
            help="Exact dataset-candidate-activation-evidence-v1 JSON.",
        ),
    ],
    approved_candidate_activation_evidence_sha256: Annotated[
        str,
        typer.Option(
            "--approved-candidate-activation-evidence-sha256",
            help="Separately approved checksum of the candidate activation evidence.",
        ),
    ],
    include_study_viral_lineage: Annotated[
        bool,
        typer.Option(
            "--include-study-viral-lineage",
            help="Also attest the release-bound study viral lineage closure.",
        ),
    ] = False,
    include_extended_viral_lineage: Annotated[
        bool,
        typer.Option(
            "--include-extended-viral-lineage",
            help="Also attest the release-bound extended viral lineage closure.",
        ),
    ] = False,
) -> None:
    """Build the acyclic checksum approval artifact without changing release state."""

    optional_roles: tuple[LineageRole, ...] = (
        *(("study_viral_lineage",) if include_study_viral_lineage else ()),
        *(("extended_viral_lineage",) if include_extended_viral_lineage else ()),
    )
    roles: tuple[LineageRole, ...] = (
        "assembly_source_taxonomy",
        "formal_viral_taxonomy",
        *optional_roles,
    )
    try:
        request = load_validation_request(request_path)
        candidate_activation_evidence = load_dataset_candidate_activation_evidence(
            candidate_activation_evidence_path,
            approved_evidence_sha256=approved_candidate_activation_evidence_sha256,
        )
        candidate = prepare_dataset_candidate_validation_input(
            get_engine(),
            request=request,
            candidate_activation_evidence=candidate_activation_evidence,
            complete_lineage_closure_roles=roles,
        )
    except Exception as exc:
        _structured_admin_operation_error(str(exc), exit_code=3)
    typer.echo(candidate.model_dump_json())


@structured_app.command("release-prepare-validation-input")
def structured_release_prepare_validation_input_command(
    candidate_input_path: Annotated[
        Path,
        typer.Option(
            "--candidate-input-path",
            exists=True,
            dir_okay=False,
            help="Exact dataset-candidate-validation-input-v1 JSON.",
        ),
    ],
    approved_candidate_input_sha256: Annotated[
        str,
        typer.Option(
            "--approved-candidate-input-sha256",
            help="Separately approved candidate input self-checksum.",
        ),
    ],
    activation_evidence_path: Annotated[
        Path,
        typer.Option(
            "--activation-evidence-path",
            exists=True,
            dir_okay=False,
            help="Exact post-report dataset-activation-evidence-v2 JSON.",
        ),
    ],
    approved_activation_evidence_sha256: Annotated[
        str,
        typer.Option(
            "--approved-activation-evidence-sha256",
            help="Separately approved post-report evidence self-checksum.",
        ),
    ],
) -> None:
    """Finalize the receipt input after independently approved reports exist."""

    try:
        candidate = load_dataset_candidate_validation_input(
            candidate_input_path,
            approved_input_sha256=approved_candidate_input_sha256,
        )
        activation_evidence = load_dataset_activation_evidence(
            activation_evidence_path,
            approved_evidence_sha256=approved_activation_evidence_sha256,
        )
        approved = prepare_dataset_validation_input(
            get_engine(),
            candidate_validation_input=candidate,
            activation_evidence=activation_evidence,
        )
    except Exception as exc:
        _structured_admin_operation_error(str(exc), exit_code=3)
    typer.echo(approved.model_dump_json())


@structured_app.command("release-publish")
def structured_release_publish_command(
    release_key: Annotated[str, typer.Option("--release-key")],
    expected_manifest_sha256: Annotated[
        str, typer.Option("--expected-manifest-sha256")
    ],
    expected_receipt_sha256: Annotated[
        str, typer.Option("--expected-receipt-sha256")
    ],
) -> None:
    """Explicitly publish one validated dataset release named by exact checksums."""

    try:
        report = publish_dataset_release(
            get_engine(),
            release_key=release_key,
            expected_manifest_sha256=expected_manifest_sha256,
            expected_receipt_sha256=expected_receipt_sha256,
        )
    except Exception as exc:
        _structured_admin_operation_error(str(exc), exit_code=5)
    typer.echo(report.model_dump_json())


@rag_app.command(
    "query",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
def rag_query_command(
    context: typer.Context,
    question: Annotated[
        str,
        typer.Option("--question", help="Strict ASCII English question."),
    ],
    release_key: Annotated[
        str | None,
        typer.Option("--release-key", help="Exact immutable structured release key."),
    ] = None,
    corpus_release_key: Annotated[
        str | None,
        typer.Option("--corpus-release-key", help="Exact immutable corpus release key."),
    ] = None,
    limit: Annotated[int | None, typer.Option("--limit", min=1, max=100)] = None,
    cursor: Annotated[str | None, typer.Option("--cursor")] = None,
    literature_top_k: Annotated[
        int | None,
        typer.Option("--literature-top-k", min=1, max=8),
    ] = None,
) -> None:
    """Execute one deterministic structured, literature, hybrid, or refusal route."""

    if context.args:
        _emit_rag_error(
            rag_request_validation_response(
                (
                    {
                        "loc": ("request",),
                        "type": "invalid",
                        "msg": "The command-line invocation is invalid.",
                    },
                ),
                body={
                    "release_key": release_key,
                    "corpus_release_key": corpus_release_key,
                },
            )
        )
    body: dict[str, object] = {
        "request_schema_version": "rag-query-request-v1",
        "release_key": release_key,
        "corpus_release_key": corpus_release_key,
        "question": question,
        "page": (
            PageSpec(limit=limit if limit is not None else 50, cursor=cursor)
            if limit is not None or cursor is not None
            else None
        ),
        "literature_top_k": literature_top_k,
    }
    try:
        request = RagQueryRequest.model_validate(body)
        response = get_rag_query_application().query(request)
    except ValidationError as exc:
        error = rag_request_validation_response(
            exc.errors(include_url=False),
            body=body,
        )
        _emit_rag_error(error)
    except Exception:  # pragma: no cover - last-resort CLI safety boundary.
        _emit_rag_error(rag_internal_error_response())
    if isinstance(response, RagErrorResponse):
        _emit_rag_error(response)
    try:
        rendered = serialize_rag_response(response)
    except Exception:  # pragma: no cover - last-resort CLI response safety boundary.
        _emit_rag_error(rag_internal_error_response())
    typer.echo(rendered)


@literature_app.command("retrieve")
def literature_retrieve_command(
    corpus_release_key: Annotated[
        str,
        typer.Option("--corpus-release-key", help="Exact published corpus release key."),
    ],
    question: Annotated[str, typer.Option("--question", help="English literature question.")],
    top_k: Annotated[int, typer.Option("--top-k", min=1, max=20)] = 8,
) -> None:
    """Run direct M3 retrieval; this is not a public HTTP endpoint."""

    try:
        invocation = LiteratureRetrievalInvocation(
            request=LiteratureRetrievalRequest(
                request_schema_version="literature-retrieval-request-v1",
                corpus_release_key=corpus_release_key,
                question=question,
                top_k=top_k,
            )
        )
        response = get_literature_retrieval_service().retrieve(invocation)
    except ValidationError:
        response = LiteratureRetrievalError(
            error_schema_version="literature-retrieval-error-v1",
            status="error",
            code="unsupported_request",
            message="literature retrieval request is invalid",
            requested_corpus_release_key=corpus_release_key or None,
            retrieval_executed=False,
        )
    except Exception:
        response = LiteratureRetrievalError(
            error_schema_version="literature-retrieval-error-v1",
            status="error",
            code="embedding_provider_failed",
            message="verified local embedding provider is unavailable",
            requested_corpus_release_key=corpus_release_key or None,
            retrieval_executed=False,
        )
    serialized = response.model_dump_json()
    if isinstance(response, LiteratureRetrievalError):
        typer.echo(serialized, err=True)
        raise typer.Exit(2 if response.code in {"unsupported_request", "query_too_long"} else 3)
    typer.echo(serialized)


@literature_app.command("manifest-validate")
def literature_manifest_validate_command(
    manifest_path: Annotated[Path, typer.Option("--manifest-path", exists=True, dir_okay=False)],
    approved_manifest_sha256: Annotated[str, typer.Option("--approved-manifest-sha256")],
) -> None:
    """Validate one canonical corpus manifest against an explicitly approved checksum."""

    try:
        manifest = _load_corpus_manifest(manifest_path, approved_manifest_sha256)
    except Exception as exc:
        _literature_operation_error(str(exc), exit_code=2)
    typer.echo(
        json.dumps(
            {
                "corpus_release_key": manifest.corpus_release_key,
                "document_count": manifest.document_count,
                "manifest_sha256": manifest.manifest_sha256,
                "status": "valid",
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )


@literature_app.command("corpus-stage")
def literature_corpus_stage_command(
    manifest_path: Annotated[Path, typer.Option("--manifest-path", exists=True, dir_okay=False)],
    approved_manifest_sha256: Annotated[str, typer.Option("--approved-manifest-sha256")],
    import_root: Annotated[Path, typer.Option("--import-root", exists=True, file_okay=False)],
    importer_code_sha256: Annotated[str, typer.Option("--importer-code-sha256")],
    model_artifact_manifest_sha256: Annotated[
        str, typer.Option("--model-artifact-manifest-sha256")
    ],
    anchor_manifest_path: Annotated[
        Path, typer.Option("--anchor-manifest-path", exists=True, dir_okay=False)
    ],
    approved_anchor_manifest_sha256: Annotated[
        str, typer.Option("--approved-anchor-manifest-sha256")
    ],
    policy_code_sha256: Annotated[
        str | None,
        typer.Option(
            "--policy-code-sha256",
            help=(
                "Exact immutable code identity already bound to reused policy rows; "
                "defaults to the importer identity for backward-compatible replay."
            ),
        ),
    ] = None,
) -> None:
    """Parse, chunk, embed, and atomically stage one exact candidate corpus."""

    try:
        manifest = _load_corpus_manifest(manifest_path, approved_manifest_sha256)
        if (
            manifest.corpus_release_key == V0_CORPUS_RELEASE_KEY
            and policy_code_sha256 is None
        ):
            raise ValueError(
                "V0 corpus staging requires an explicit immutable policy code SHA-256"
            )
        anchor_manifest = _load_anchor_manifest(
            anchor_manifest_path,
            approved_anchor_manifest_sha256,
            manifest,
        )
        provider = get_local_bge_provider()
        report = import_candidate_corpus(
            get_engine(),
            manifest=manifest,
            import_root=import_root,
            tokenizer=provider,
            approved_manifest_sha256=approved_manifest_sha256,
            importer_code_sha256=importer_code_sha256,
            policy_code_sha256=policy_code_sha256,
            model_artifact_manifest_sha256=model_artifact_manifest_sha256,
            embedding_provider=provider,
        )
        anchor_report = import_candidate_anchors(
            get_engine(),
            manifest=anchor_manifest,
            approved_anchor_manifest_sha256=approved_anchor_manifest_sha256,
        )
    except Exception as exc:
        _literature_operation_error(str(exc), exit_code=3)
    typer.echo(
        json.dumps(
            {
                "anchors": anchor_report.model_dump(mode="json"),
                "corpus": report.model_dump(mode="json"),
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )


@literature_app.command("benchmark")
def literature_benchmark_command(
    manifest_path: Annotated[Path, typer.Option("--manifest-path", exists=True, dir_okay=False)],
    approved_manifest_sha256: Annotated[str, typer.Option("--approved-manifest-sha256")],
    import_root: Annotated[Path, typer.Option("--import-root", exists=True, file_okay=False)],
    benchmark_path: Annotated[Path, typer.Option("--benchmark-path", exists=True, dir_okay=False)],
    approved_benchmark_sha256: Annotated[str, typer.Option("--approved-benchmark-sha256")],
    anchor_manifest_path: Annotated[
        Path, typer.Option("--anchor-manifest-path", exists=True, dir_okay=False)
    ],
    approved_anchor_manifest_sha256: Annotated[
        str, typer.Option("--approved-anchor-manifest-sha256")
    ],
    uv_lock_path: Annotated[
        Path | None,
        typer.Option(
            "--uv-lock-path",
            exists=True,
            dir_okay=False,
            help="Exact approved uv.lock; required outside a source checkout.",
        ),
    ] = None,
) -> None:
    """Run the frozen benchmark against an exact rebuilt candidate corpus."""

    try:
        manifest = _load_corpus_manifest(manifest_path, approved_manifest_sha256)
        anchor_manifest = _load_anchor_manifest(
            anchor_manifest_path,
            approved_anchor_manifest_sha256,
            manifest,
        )
        definition = _load_benchmark(benchmark_path, approved_benchmark_sha256, manifest)
        provider = get_local_bge_provider()
        engine = get_engine()
        rebuild = validate_corpus_rebuild(
            engine,
            manifest=manifest,
            import_root=import_root,
            tokenizer=provider,
            provider=provider,
            anchor_manifest=anchor_manifest,
        )
        if not rebuild.passed:
            raise RuntimeError("candidate rebuild validation failed")
        runtime_fingerprint = (
            collect_benchmark_runtime_fingerprint(
                engine,
                uv_lock_path=_resolve_uv_lock_path(uv_lock_path),
            )
            if definition.tier == "pilot_release"
            else None
        )
        report = run_benchmark(
            CandidateBenchmarkService(engine, provider, rebuild),
            definition,
            runtime_fingerprint=runtime_fingerprint,
        )
    except Exception as exc:
        _literature_operation_error(str(exc), exit_code=4)
    serialized = report.model_dump_json()
    if not report.passed:
        typer.echo(serialized, err=True)
        raise typer.Exit(4)
    typer.echo(serialized)


@literature_app.command("corpus-validate")
def literature_corpus_validate_command(
    manifest_path: Annotated[Path, typer.Option("--manifest-path", exists=True, dir_okay=False)],
    approved_manifest_sha256: Annotated[str, typer.Option("--approved-manifest-sha256")],
    import_root: Annotated[Path, typer.Option("--import-root", exists=True, file_okay=False)],
    benchmark_path: Annotated[Path, typer.Option("--benchmark-path", exists=True, dir_okay=False)],
    approved_benchmark_sha256: Annotated[str, typer.Option("--approved-benchmark-sha256")],
    validator_code_sha256: Annotated[str, typer.Option("--validator-code-sha256")],
    anchor_manifest_path: Annotated[
        Path, typer.Option("--anchor-manifest-path", exists=True, dir_okay=False)
    ],
    approved_anchor_manifest_sha256: Annotated[
        str, typer.Option("--approved-anchor-manifest-sha256")
    ],
    uv_lock_path: Annotated[
        Path | None,
        typer.Option(
            "--uv-lock-path",
            exists=True,
            dir_okay=False,
            help="Exact approved uv.lock; required outside a source checkout.",
        ),
    ] = None,
) -> None:
    """Rebuild, benchmark, and record the trusted pilot validation receipt."""

    try:
        manifest = _load_corpus_manifest(manifest_path, approved_manifest_sha256)
        anchor_manifest = _load_anchor_manifest(
            anchor_manifest_path,
            approved_anchor_manifest_sha256,
            manifest,
        )
        definition = _load_benchmark(benchmark_path, approved_benchmark_sha256, manifest)
        provider = get_local_bge_provider()
        engine = get_engine()
        runtime_fingerprint = collect_benchmark_runtime_fingerprint(
            engine,
            uv_lock_path=_resolve_uv_lock_path(uv_lock_path),
        )
        receipt = record_pilot_validation_receipt(
            engine,
            manifest=manifest,
            import_root=import_root,
            anchor_manifest=anchor_manifest,
            benchmark_definition=definition,
            runtime_fingerprint=runtime_fingerprint,
            validator_code_sha256=validator_code_sha256,
            provider=provider,
        )
    except Exception as exc:
        _literature_operation_error(str(exc), exit_code=4)
    typer.echo(receipt.model_dump_json())


@literature_app.command("corpus-publish")
def literature_corpus_publish_command(
    corpus_release_key: Annotated[str, typer.Option("--corpus-release-key")],
    expected_manifest_sha256: Annotated[str, typer.Option("--expected-manifest-sha256")],
    expected_receipt_sha256: Annotated[str, typer.Option("--expected-receipt-sha256")],
) -> None:
    """Explicitly publish the exact corpus named by manifest and trusted receipt checksums."""

    try:
        report = publish_corpus(
            get_engine(),
            corpus_release_key=corpus_release_key,
            expected_manifest_sha256=expected_manifest_sha256,
            expected_receipt_sha256=expected_receipt_sha256,
        )
    except Exception as exc:
        _literature_operation_error(str(exc), exit_code=5)
    typer.echo(report.model_dump_json())


def _resolve_uv_lock_path(explicit_path: Path | None) -> Path:
    if explicit_path is not None:
        return explicit_path
    source_checkout_path = Path(__file__).resolve().parents[2] / "uv.lock"
    if not source_checkout_path.is_file():
        raise RuntimeError(
            "uv.lock is unavailable outside a source checkout; provide --uv-lock-path"
        )
    return source_checkout_path


def _load_corpus_manifest(path: Path, approved_sha256: str) -> CorpusManifest:
    manifest = CorpusManifest.model_validate_json(path.read_text(encoding="utf-8"))
    if manifest.manifest_sha256 != approved_sha256:
        raise ValueError("approved manifest checksum does not match canonical manifest")
    return manifest


def _load_benchmark(
    path: Path,
    approved_sha256: str,
    manifest: CorpusManifest,
) -> BenchmarkDefinition:
    definition = BenchmarkDefinition.model_validate_json(path.read_text(encoding="utf-8"))
    if definition.benchmark_manifest_sha256 != approved_sha256:
        raise ValueError("approved benchmark checksum does not match definition")
    if (
        definition.corpus_release_key != manifest.corpus_release_key
        or definition.corpus_manifest_sha256 != manifest.manifest_sha256
    ):
        raise ValueError("benchmark definition does not bind the exact corpus manifest")
    return definition


def _load_anchor_manifest(
    path: Path,
    approved_sha256: str,
    corpus_manifest: CorpusManifest,
) -> CorpusAnchorManifest:
    manifest = CorpusAnchorManifest.model_validate_json(path.read_text(encoding="utf-8"))
    if manifest.anchor_manifest_sha256 != approved_sha256:
        raise ValueError("approved anchor manifest checksum does not match")
    if (
        manifest.corpus_release_key != corpus_manifest.corpus_release_key
        or manifest.corpus_manifest_sha256 != corpus_manifest.manifest_sha256
        or manifest.anchor_policy_key != corpus_manifest.anchor_policy_key
    ):
        raise ValueError("anchor manifest does not bind the exact corpus manifest")
    return manifest


def _literature_operation_error(message: str, *, exit_code: int) -> None:
    typer.echo(
        json.dumps(
            {"message": message or "literature operation failed", "status": "error"},
            separators=(",", ":"),
            sort_keys=True,
        ),
        err=True,
    )
    raise typer.Exit(exit_code)


def _structured_admin_operation_error(message: str, *, exit_code: int) -> None:
    typer.echo(
        json.dumps(
            {"message": message or "structured release operation failed", "status": "error"},
            separators=(",", ":"),
            sort_keys=True,
        ),
        err=True,
    )
    raise typer.Exit(exit_code)


if __name__ == "__main__":  # pragma: no cover
    app()
