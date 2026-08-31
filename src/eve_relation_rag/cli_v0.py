"""Independent Typer registration for V0 activation review operations.

The root CLI imports and registers this module in one small integration point.  Keeping the
commands here avoids coupling human-review artifact logic to existing query adapters.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Annotated

import typer
from pydantic import BaseModel

from eve_relation_rag.activation.contracts import (
    AssemblyTaxonAssignmentManifest,
    PublicAssertionMembershipManifest,
    PublicLocusMembershipManifest,
    StructuredActivationManifest,
    StudyFormalMappingManifest,
    TaxonomySnapshotManifest,
)
from eve_relation_rag.activation.release_state import (
    CorpusValidationExport,
    V0CleanActivationRebuildReport,
    V0RouteBenchmarkReport,
)
from eve_relation_rag.activation.runner import (
    ActivationRunnerError,
    build_clean_activation_rebuild_input,
    build_corpus_publication_evidence,
    build_dataset_activation_evidence_from_reports,
    build_dataset_publication_evidence,
    build_hybrid_route_benchmark_report,
    build_structured_route_benchmark_report,
    run_candidate_route_answers,
)
from eve_relation_rag.bootstrap import build_v0_candidate_rag_query_application
from eve_relation_rag.generation.human_review import (
    HumanBenchmarkDefinition,
    HumanReviewError,
    HumanReviewEvaluation,
    HumanReviewPacket,
    build_human_benchmark_definition,
    build_human_review_packet,
    evaluate_human_review,
    load_human_benchmark_definition,
    load_human_review_packet,
    load_human_review_submission,
    serialize_review_artifact,
)
from eve_relation_rag.generation.policy import LocalModelPolicyManifest, PromptPolicyManifest
from eve_relation_rag.hybrid.contracts import (
    HybridReleaseBindingManifest,
    HybridRouteAnswer,
    StructuredRouteAnswer,
    canonical_model_json,
)
from eve_relation_rag.literature.anchors import CorpusAnchorManifest
from eve_relation_rag.literature.contracts import CorpusManifest
from eve_relation_rag.literature.publication import PublicationReport
from eve_relation_rag.releases.publication import DatasetPublicationReport
from eve_relation_rag.releases.receipt_integrity import (
    DatasetCandidateValidationInput,
)

v0_app = typer.Typer(
    help="Prepare and validate checksum-bound V0 activation evidence.",
    no_args_is_help=True,
)


def register_v0_commands(root: typer.Typer) -> None:
    """Attach the independent V0 command group to the project root CLI."""

    root.add_typer(v0_app, name="v0")


@v0_app.command("human-benchmark-build-definition")
def human_benchmark_build_definition_command(
    structured_manifest_path: Annotated[
        Path,
        typer.Option("--structured-manifest-path", exists=True, dir_okay=False),
    ],
    public_locus_manifest_path: Annotated[
        Path,
        typer.Option("--public-locus-manifest-path", exists=True, dir_okay=False),
    ],
    public_assertion_manifest_path: Annotated[
        Path,
        typer.Option("--public-assertion-manifest-path", exists=True, dir_okay=False),
    ],
    ncbi_snapshot_manifest_path: Annotated[
        Path,
        typer.Option("--ncbi-snapshot-manifest-path", exists=True, dir_okay=False),
    ],
    assembly_assignment_manifest_path: Annotated[
        Path,
        typer.Option("--assembly-assignment-manifest-path", exists=True, dir_okay=False),
    ],
    study_formal_mapping_manifest_path: Annotated[
        Path,
        typer.Option("--study-formal-mapping-manifest-path", exists=True, dir_okay=False),
    ],
    corpus_manifest_path: Annotated[
        Path,
        typer.Option("--corpus-manifest-path", exists=True, dir_okay=False),
    ],
    anchor_manifest_path: Annotated[
        Path,
        typer.Option("--anchor-manifest-path", exists=True, dir_okay=False),
    ],
    binding_manifest_path: Annotated[
        Path,
        typer.Option("--binding-manifest-path", exists=True, dir_okay=False),
    ],
    model_policy_manifest_path: Annotated[
        Path,
        typer.Option("--model-policy-manifest-path", exists=True, dir_okay=False),
    ],
    prompt_policy_manifest_path: Annotated[
        Path,
        typer.Option("--prompt-policy-manifest-path", exists=True, dir_okay=False),
    ],
    output_path: Annotated[Path, typer.Option("--output-path")],
) -> None:
    """Build the ten-case candidate definition without provider or database I/O."""

    try:
        definition = build_human_benchmark_definition(
            structured_manifest=_load_candidate_manifest(
                structured_manifest_path,
                StructuredActivationManifest,
            ),
            public_locus_manifest=_load_candidate_manifest(
                public_locus_manifest_path,
                PublicLocusMembershipManifest,
            ),
            public_assertion_manifest=_load_candidate_manifest(
                public_assertion_manifest_path,
                PublicAssertionMembershipManifest,
            ),
            ncbi_snapshot_manifest=_load_candidate_manifest(
                ncbi_snapshot_manifest_path,
                TaxonomySnapshotManifest,
            ),
            assembly_assignment_manifest=_load_candidate_manifest(
                assembly_assignment_manifest_path,
                AssemblyTaxonAssignmentManifest,
            ),
            study_formal_mapping_manifest=_load_candidate_manifest(
                study_formal_mapping_manifest_path,
                StudyFormalMappingManifest,
            ),
            corpus_manifest=_load_candidate_manifest(corpus_manifest_path, CorpusManifest),
            anchor_manifest=_load_candidate_manifest(
                anchor_manifest_path,
                CorpusAnchorManifest,
            ),
            binding_manifest=_load_candidate_manifest(
                binding_manifest_path,
                HybridReleaseBindingManifest,
            ),
            model_policy_manifest=_load_candidate_manifest(
                model_policy_manifest_path,
                LocalModelPolicyManifest,
            ),
            prompt_policy_manifest=_load_candidate_manifest(
                prompt_policy_manifest_path,
                PromptPolicyManifest,
            ),
        )
        _write_new_file(output_path, serialize_review_artifact(definition) + "\n")
    except HumanReviewError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(4) from None
    except Exception:
        typer.echo("The V0 human benchmark definition could not be built safely.", err=True)
        raise typer.Exit(4) from None
    typer.echo(
        canonical_model_json(
            {
                "case_count": len(definition.cases),
                "definition_sha256": definition.definition_sha256,
                "human_review_signed": False,
                "provider_invoked": False,
                "status": "candidate",
            }
        )
    )


@v0_app.command("human-review-export")
def human_review_export_command(
    definition_path: Annotated[
        Path,
        typer.Option("--definition-path", exists=True, dir_okay=False),
    ],
    approved_definition_sha256: Annotated[
        str,
        typer.Option("--approved-definition-sha256"),
    ],
    answers_root: Annotated[
        Path,
        typer.Option("--answers-root", exists=True, file_okay=False),
    ],
    output_path: Annotated[Path, typer.Option("--output-path")],
) -> None:
    """Export all ten preregistered strict hybrid answers for human review."""

    try:
        definition = load_human_benchmark_definition(
            definition_path,
            approved_definition_sha256=approved_definition_sha256,
        )
        packet = build_human_review_packet(definition, answers_root=answers_root)
        _write_new_file(output_path, serialize_review_artifact(packet) + "\n")
    except HumanReviewError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(4) from None
    except Exception:
        typer.echo("The human semantic review packet could not be exported safely.", err=True)
        raise typer.Exit(4) from None
    typer.echo(
        canonical_model_json(
            {
                "status": "exported",
                "case_count": len(packet.cases),
                "packet_sha256": packet.packet_sha256,
            }
        )
    )


@v0_app.command("human-review-validate")
def human_review_validate_command(
    packet_path: Annotated[
        Path,
        typer.Option("--packet-path", exists=True, dir_okay=False),
    ],
    approved_packet_sha256: Annotated[
        str,
        typer.Option("--approved-packet-sha256"),
    ],
    submission_path: Annotated[
        Path,
        typer.Option("--submission-path", exists=True, dir_okay=False),
    ],
    approved_submission_sha256: Annotated[
        str,
        typer.Option("--approved-submission-sha256"),
    ],
) -> None:
    """Validate one named human submission and emit its fail-closed evaluation."""

    try:
        packet = load_human_review_packet(
            packet_path,
            approved_packet_sha256=approved_packet_sha256,
        )
        submission = load_human_review_submission(
            submission_path,
            approved_submission_sha256=approved_submission_sha256,
        )
        evaluation = evaluate_human_review(packet, submission)
    except HumanReviewError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(4) from None
    except Exception:
        typer.echo("The human semantic review could not be validated safely.", err=True)
        raise typer.Exit(4) from None
    typer.echo(serialize_review_artifact(evaluation))
    if evaluation.status != "passed":
        raise typer.Exit(4)


@v0_app.command("route-benchmark-build-structured")
def route_benchmark_build_structured_command(
    definition_path: Annotated[
        Path, typer.Option("--definition-path", exists=True, dir_okay=False)
    ],
    approved_definition_sha256: Annotated[str, typer.Option("--approved-definition-sha256")],
    candidate_input_path: Annotated[
        Path, typer.Option("--candidate-input-path", exists=True, dir_okay=False)
    ],
    approved_candidate_input_sha256: Annotated[
        str, typer.Option("--approved-candidate-input-sha256")
    ],
    answers_root: Annotated[Path, typer.Option("--answers-root", exists=True, file_okay=False)],
    output_path: Annotated[Path, typer.Option("--output-path")],
) -> None:
    """Seal structured-case-01.json through -10.json into one candidate report."""

    try:
        definition = _load_approved_model(
            definition_path,
            HumanBenchmarkDefinition,
            identity_field="definition_sha256",
            approved_sha256=approved_definition_sha256,
        )
        candidate = _load_approved_model(
            candidate_input_path,
            DatasetCandidateValidationInput,
            identity_field="input_sha256",
            approved_sha256=approved_candidate_input_sha256,
        )
        responses = tuple(
            _load_candidate_manifest(
                answers_root / f"structured-case-{ordinal:02d}.json",
                StructuredRouteAnswer,
            )
            for ordinal in range(1, 11)
        )
        report = build_structured_route_benchmark_report(
            definition=definition,
            candidate_validation_input=candidate,
            responses=responses,
        )
        _write_new_file(output_path, canonical_model_json(report) + "\n")
    except (ActivationRunnerError, HumanReviewError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(4) from None
    except Exception:
        typer.echo("The structured route report could not be built safely.", err=True)
        raise typer.Exit(4) from None
    typer.echo(
        canonical_model_json(
            {
                "case_count": len(report.cases),
                "report_sha256": report.report_sha256,
                "route": report.route,
                "status": "candidate_evidence",
            }
        )
    )


@v0_app.command("route-benchmark-run-candidate")
def route_benchmark_run_candidate_command(
    definition_path: Annotated[
        Path, typer.Option("--definition-path", exists=True, dir_okay=False)
    ],
    approved_definition_sha256: Annotated[str, typer.Option("--approved-definition-sha256")],
    candidate_input_path: Annotated[
        Path, typer.Option("--candidate-input-path", exists=True, dir_okay=False)
    ],
    approved_candidate_input_sha256: Annotated[
        str, typer.Option("--approved-candidate-input-sha256")
    ],
    corpus_export_path: Annotated[
        Path, typer.Option("--corpus-export-path", exists=True, dir_okay=False)
    ],
    approved_corpus_export_sha256: Annotated[
        str, typer.Option("--approved-corpus-export-sha256")
    ],
    output_root: Annotated[Path, typer.Option("--output-root")],
) -> None:
    """Execute and retain all ten structured and hybrid candidate routes in order."""

    try:
        definition = _load_approved_model(
            definition_path,
            HumanBenchmarkDefinition,
            identity_field="definition_sha256",
            approved_sha256=approved_definition_sha256,
        )
        candidate = _load_approved_model(
            candidate_input_path,
            DatasetCandidateValidationInput,
            identity_field="input_sha256",
            approved_sha256=approved_candidate_input_sha256,
        )
        corpus_export = _load_approved_model(
            corpus_export_path,
            CorpusValidationExport,
            identity_field="manifest_sha256",
            approved_sha256=approved_corpus_export_sha256,
        )
        if (
            corpus_export.corpus_release.corpus_release_key
            != definition.corpus_release_key
            or corpus_export.corpus_release.manifest_sha256
            != definition.corpus_manifest_sha256
        ):
            raise ActivationRunnerError(
                "corpus receipt export targets another benchmark definition"
            )
        application = build_v0_candidate_rag_query_application(
            candidate_validation_input=candidate,
            corpus_rebuild_report=(
                corpus_export.receipt.validation_report.rebuild_report
            ),
        )
        structured, hybrid = run_candidate_route_answers(
            application=application,
            definition=definition,
            candidate_validation_input=candidate,
        )
        structured_report = build_structured_route_benchmark_report(
            definition=definition,
            candidate_validation_input=candidate,
            responses=structured,
        )
        _write_candidate_route_run(
            output_root,
            definition=definition,
            structured=structured,
            hybrid=hybrid,
            structured_report=structured_report,
        )
    except (ActivationRunnerError, HumanReviewError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(4) from None
    except Exception:
        typer.echo("The candidate route benchmark could not be run safely.", err=True)
        raise typer.Exit(4) from None
    typer.echo(
        canonical_model_json(
            {
                "hybrid_case_count": len(hybrid),
                "output_root": output_root.as_posix(),
                "structured_case_count": len(structured),
                "structured_report_sha256": structured_report.report_sha256,
                "status": "candidate_evidence",
            }
        )
    )


@v0_app.command("route-benchmark-build-hybrid")
def route_benchmark_build_hybrid_command(
    definition_path: Annotated[
        Path, typer.Option("--definition-path", exists=True, dir_okay=False)
    ],
    approved_definition_sha256: Annotated[str, typer.Option("--approved-definition-sha256")],
    candidate_input_path: Annotated[
        Path, typer.Option("--candidate-input-path", exists=True, dir_okay=False)
    ],
    approved_candidate_input_sha256: Annotated[
        str, typer.Option("--approved-candidate-input-sha256")
    ],
    packet_path: Annotated[Path, typer.Option("--packet-path", exists=True, dir_okay=False)],
    approved_packet_sha256: Annotated[str, typer.Option("--approved-packet-sha256")],
    evaluation_path: Annotated[
        Path, typer.Option("--evaluation-path", exists=True, dir_okay=False)
    ],
    approved_evaluation_sha256: Annotated[str, typer.Option("--approved-evaluation-sha256")],
    corpus_export_path: Annotated[
        Path, typer.Option("--corpus-export-path", exists=True, dir_okay=False)
    ],
    approved_corpus_export_sha256: Annotated[str, typer.Option("--approved-corpus-export-sha256")],
    binding_manifest_path: Annotated[
        Path, typer.Option("--binding-manifest-path", exists=True, dir_okay=False)
    ],
    approved_binding_manifest_sha256: Annotated[
        str, typer.Option("--approved-binding-manifest-sha256")
    ],
    output_path: Annotated[Path, typer.Option("--output-path")],
) -> None:
    """Seal the reviewed ten-case hybrid packet into one candidate report."""

    try:
        definition = _load_approved_model(
            definition_path,
            HumanBenchmarkDefinition,
            identity_field="definition_sha256",
            approved_sha256=approved_definition_sha256,
        )
        candidate = _load_approved_model(
            candidate_input_path,
            DatasetCandidateValidationInput,
            identity_field="input_sha256",
            approved_sha256=approved_candidate_input_sha256,
        )
        packet = _load_approved_model(
            packet_path,
            HumanReviewPacket,
            identity_field="packet_sha256",
            approved_sha256=approved_packet_sha256,
        )
        evaluation = _load_approved_model(
            evaluation_path,
            HumanReviewEvaluation,
            identity_field="evaluation_sha256",
            approved_sha256=approved_evaluation_sha256,
        )
        corpus_export = _load_approved_model(
            corpus_export_path,
            CorpusValidationExport,
            identity_field="manifest_sha256",
            approved_sha256=approved_corpus_export_sha256,
        )
        binding = _load_approved_model(
            binding_manifest_path,
            HybridReleaseBindingManifest,
            identity_field="manifest_sha256",
            approved_sha256=approved_binding_manifest_sha256,
        )
        report = build_hybrid_route_benchmark_report(
            definition=definition,
            candidate_validation_input=candidate,
            packet=packet,
            evaluation=evaluation,
            corpus_export=corpus_export,
            binding_manifest=binding,
        )
        _write_new_file(output_path, canonical_model_json(report) + "\n")
    except (ActivationRunnerError, HumanReviewError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(4) from None
    except Exception:
        typer.echo("The hybrid route report could not be built safely.", err=True)
        raise typer.Exit(4) from None
    typer.echo(
        canonical_model_json(
            {
                "case_count": len(report.cases),
                "report_sha256": report.report_sha256,
                "route": report.route,
                "status": "candidate_evidence",
            }
        )
    )


@v0_app.command("clean-rebuild-build-input")
def clean_rebuild_build_input_command(
    activation_evidence_commit: Annotated[str, typer.Option("--activation-evidence-commit")],
    candidate_input_path: Annotated[
        Path, typer.Option("--candidate-input-path", exists=True, dir_okay=False)
    ],
    approved_candidate_input_sha256: Annotated[
        str, typer.Option("--approved-candidate-input-sha256")
    ],
    corpus_export_path: Annotated[
        Path, typer.Option("--corpus-export-path", exists=True, dir_okay=False)
    ],
    approved_corpus_export_sha256: Annotated[str, typer.Option("--approved-corpus-export-sha256")],
    structured_report_path: Annotated[
        Path, typer.Option("--structured-report-path", exists=True, dir_okay=False)
    ],
    approved_structured_report_sha256: Annotated[
        str, typer.Option("--approved-structured-report-sha256")
    ],
    hybrid_report_path: Annotated[
        Path, typer.Option("--hybrid-report-path", exists=True, dir_okay=False)
    ],
    approved_hybrid_report_sha256: Annotated[str, typer.Option("--approved-hybrid-report-sha256")],
    evaluation_path: Annotated[
        Path, typer.Option("--evaluation-path", exists=True, dir_okay=False)
    ],
    approved_evaluation_sha256: Annotated[str, typer.Option("--approved-evaluation-sha256")],
    dependency_lock_path: Annotated[
        Path, typer.Option("--dependency-lock-path", exists=True, dir_okay=False)
    ],
    output_path: Annotated[Path, typer.Option("--output-path")],
) -> None:
    """Freeze the complete input graph for a later empty-database activation replay."""

    try:
        candidate = _load_approved_model(
            candidate_input_path,
            DatasetCandidateValidationInput,
            identity_field="input_sha256",
            approved_sha256=approved_candidate_input_sha256,
        )
        corpus_export = _load_approved_model(
            corpus_export_path,
            CorpusValidationExport,
            identity_field="manifest_sha256",
            approved_sha256=approved_corpus_export_sha256,
        )
        structured_report = _load_approved_model(
            structured_report_path,
            V0RouteBenchmarkReport,
            identity_field="report_sha256",
            approved_sha256=approved_structured_report_sha256,
        )
        hybrid_report = _load_approved_model(
            hybrid_report_path,
            V0RouteBenchmarkReport,
            identity_field="report_sha256",
            approved_sha256=approved_hybrid_report_sha256,
        )
        evaluation = _load_approved_model(
            evaluation_path,
            HumanReviewEvaluation,
            identity_field="evaluation_sha256",
            approved_sha256=approved_evaluation_sha256,
        )
        rebuild_input = build_clean_activation_rebuild_input(
            activation_evidence_commit=activation_evidence_commit,
            candidate_validation_input=candidate,
            corpus_export=corpus_export,
            structured_report=structured_report,
            hybrid_report=hybrid_report,
            evaluation=evaluation,
            dependency_lock_path=dependency_lock_path,
        )
        _write_new_file(output_path, canonical_model_json(rebuild_input) + "\n")
    except (ActivationRunnerError, HumanReviewError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(4) from None
    except Exception:
        typer.echo("The clean rebuild input could not be built safely.", err=True)
        raise typer.Exit(4) from None
    typer.echo(
        canonical_model_json(
            {
                "input_sha256": rebuild_input.input_sha256,
                "status": "rebuild_input_frozen",
            }
        )
    )


@v0_app.command("dataset-activation-evidence-build")
def dataset_activation_evidence_build_command(
    candidate_input_path: Annotated[
        Path, typer.Option("--candidate-input-path", exists=True, dir_okay=False)
    ],
    approved_candidate_input_sha256: Annotated[
        str, typer.Option("--approved-candidate-input-sha256")
    ],
    clean_rebuild_report_path: Annotated[
        Path, typer.Option("--clean-rebuild-report-path", exists=True, dir_okay=False)
    ],
    approved_clean_rebuild_report_sha256: Annotated[
        str, typer.Option("--approved-clean-rebuild-report-sha256")
    ],
    structured_report_path: Annotated[
        Path, typer.Option("--structured-report-path", exists=True, dir_okay=False)
    ],
    approved_structured_report_sha256: Annotated[
        str, typer.Option("--approved-structured-report-sha256")
    ],
    hybrid_report_path: Annotated[
        Path, typer.Option("--hybrid-report-path", exists=True, dir_okay=False)
    ],
    approved_hybrid_report_sha256: Annotated[str, typer.Option("--approved-hybrid-report-sha256")],
    evaluation_path: Annotated[
        Path, typer.Option("--evaluation-path", exists=True, dir_okay=False)
    ],
    approved_evaluation_sha256: Annotated[str, typer.Option("--approved-evaluation-sha256")],
    output_path: Annotated[Path, typer.Option("--output-path")],
) -> None:
    """Build receipt activation evidence from the exact passing pre-receipt reports."""

    try:
        candidate = _load_approved_model(
            candidate_input_path,
            DatasetCandidateValidationInput,
            identity_field="input_sha256",
            approved_sha256=approved_candidate_input_sha256,
        )
        rebuild = _load_approved_model(
            clean_rebuild_report_path,
            V0CleanActivationRebuildReport,
            identity_field="rebuild_sha256",
            approved_sha256=approved_clean_rebuild_report_sha256,
        )
        structured_report = _load_approved_model(
            structured_report_path,
            V0RouteBenchmarkReport,
            identity_field="report_sha256",
            approved_sha256=approved_structured_report_sha256,
        )
        hybrid_report = _load_approved_model(
            hybrid_report_path,
            V0RouteBenchmarkReport,
            identity_field="report_sha256",
            approved_sha256=approved_hybrid_report_sha256,
        )
        evaluation = _load_approved_model(
            evaluation_path,
            HumanReviewEvaluation,
            identity_field="evaluation_sha256",
            approved_sha256=approved_evaluation_sha256,
        )
        evidence = build_dataset_activation_evidence_from_reports(
            candidate_validation_input=candidate,
            clean_rebuild_report=rebuild,
            structured_report=structured_report,
            hybrid_report=hybrid_report,
            evaluation=evaluation,
        )
        _write_new_file(output_path, canonical_model_json(evidence) + "\n")
    except (ActivationRunnerError, HumanReviewError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(4) from None
    except Exception:
        typer.echo("Dataset activation evidence could not be built safely.", err=True)
        raise typer.Exit(4) from None
    typer.echo(
        canonical_model_json(
            {
                "evidence_sha256": evidence.evidence_sha256,
                "status": "candidate_evidence",
            }
        )
    )


@v0_app.command("dataset-publication-evidence-build")
def dataset_publication_evidence_build_command(
    report_path: Annotated[Path, typer.Option("--report-path", exists=True, dir_okay=False)],
    approved_report_file_sha256: Annotated[str, typer.Option("--approved-report-file-sha256")],
    receipt_key: Annotated[str, typer.Option("--receipt-key")],
    output_path: Annotated[Path, typer.Option("--output-path")],
) -> None:
    """Seal one separately hash-approved structured publication CLI report."""

    try:
        report = _load_approved_raw_model(
            report_path,
            DatasetPublicationReport,
            approved_file_sha256=approved_report_file_sha256,
        )
        evidence = build_dataset_publication_evidence(report, receipt_key=receipt_key)
        _write_new_file(output_path, canonical_model_json(evidence) + "\n")
    except (ActivationRunnerError, HumanReviewError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(4) from None
    except Exception:
        typer.echo("Dataset publication evidence could not be built safely.", err=True)
        raise typer.Exit(4) from None
    typer.echo(canonical_model_json({"publication_sha256": evidence.publication_sha256}))


@v0_app.command("corpus-publication-evidence-build")
def corpus_publication_evidence_build_command(
    report_path: Annotated[Path, typer.Option("--report-path", exists=True, dir_okay=False)],
    approved_report_file_sha256: Annotated[str, typer.Option("--approved-report-file-sha256")],
    receipt_key: Annotated[str, typer.Option("--receipt-key")],
    output_path: Annotated[Path, typer.Option("--output-path")],
) -> None:
    """Seal one separately hash-approved corpus publication CLI report."""

    try:
        report = _load_approved_raw_model(
            report_path,
            PublicationReport,
            approved_file_sha256=approved_report_file_sha256,
        )
        evidence = build_corpus_publication_evidence(report, receipt_key=receipt_key)
        _write_new_file(output_path, canonical_model_json(evidence) + "\n")
    except (ActivationRunnerError, HumanReviewError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(4) from None
    except Exception:
        typer.echo("Corpus publication evidence could not be built safely.", err=True)
        raise typer.Exit(4) from None
    typer.echo(canonical_model_json({"publication_sha256": evidence.publication_sha256}))


def _write_candidate_route_run(
    output_root: Path,
    *,
    definition: HumanBenchmarkDefinition,
    structured: tuple[StructuredRouteAnswer, ...],
    hybrid: tuple[HybridRouteAnswer, ...],
    structured_report: V0RouteBenchmarkReport,
) -> None:
    if (
        output_root.exists()
        or output_root.is_symlink()
        or output_root.parent.is_symlink()
        or not output_root.parent.is_dir()
    ):
        raise HumanReviewError("The candidate route output root must be a new directory.")
    try:
        output_root.mkdir(mode=0o700)
        structured_root = output_root / "structured"
        hybrid_root = output_root / "hybrid"
        structured_root.mkdir(mode=0o700)
        hybrid_root.mkdir(mode=0o700)
        for ordinal, structured_response in enumerate(structured, start=1):
            _write_new_file(
                structured_root / f"structured-case-{ordinal:02d}.json",
                canonical_model_json(structured_response) + "\n",
            )
        for case, hybrid_response in zip(definition.cases, hybrid, strict=True):
            destination = hybrid_root / case.response_path
            destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            _write_new_file(destination, canonical_model_json(hybrid_response) + "\n")
        _write_new_file(
            output_root / "structured-report.json",
            canonical_model_json(structured_report) + "\n",
        )
    except HumanReviewError:
        raise
    except OSError:
        raise HumanReviewError(
            "The candidate route output directory could not be written safely."
        ) from None


def _write_new_file(path: Path, content: str) -> None:
    try:
        if path.parent.is_symlink() or not path.parent.is_dir():
            raise OSError
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError:
        raise HumanReviewError(
            "The review output path must be a new file in an existing directory."
        ) from None


def _load_candidate_manifest[ModelT: BaseModel](
    path: Path,
    schema: type[ModelT],
) -> ModelT:
    if path.is_symlink() or not path.is_file():
        raise HumanReviewError("A benchmark identity manifest is unavailable or invalid.")
    raw = path.read_bytes()
    if not raw or len(raw) > 64 * 1024 * 1024:
        raise HumanReviewError("A benchmark identity manifest is unavailable or invalid.")
    try:
        return schema.model_validate_json(raw, strict=True)
    except Exception:
        raise HumanReviewError("A benchmark identity manifest is unavailable or invalid.") from None


def _load_approved_model[ModelT: BaseModel](
    path: Path,
    schema: type[ModelT],
    *,
    identity_field: str,
    approved_sha256: str,
) -> ModelT:
    model = _load_candidate_manifest(path, schema)
    if getattr(model, identity_field, None) != approved_sha256:
        raise HumanReviewError(
            "A benchmark identity manifest does not match its approved checksum."
        )
    return model


def _load_approved_raw_model[ModelT: BaseModel](
    path: Path,
    schema: type[ModelT],
    *,
    approved_file_sha256: str,
) -> ModelT:
    if path.is_symlink() or not path.is_file():
        raise HumanReviewError("A publication report is unavailable or invalid.")
    try:
        raw = path.read_bytes()
    except OSError:
        raise HumanReviewError("A publication report is unavailable or invalid.") from None
    if not raw or len(raw) > 1024 * 1024 or hashlib.sha256(raw).hexdigest() != approved_file_sha256:
        raise HumanReviewError("A publication report does not match its approved raw checksum.")
    try:
        return schema.model_validate_json(raw, strict=True)
    except Exception:
        raise HumanReviewError("A publication report is unavailable or invalid.") from None


__all__ = ["register_v0_commands", "v0_app"]
