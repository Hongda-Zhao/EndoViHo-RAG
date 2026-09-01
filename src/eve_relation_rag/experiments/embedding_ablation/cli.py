"""Standalone offline CLI; intentionally not registered in the production application."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Literal

import typer

from eve_relation_rag.experiments.embedding_ablation.annotations import (
    load_annotation_manifest,
    load_legacy_benchmark,
    migrate_legacy_benchmark_to_pending,
    write_new_annotation_manifest,
)
from eve_relation_rag.experiments.embedding_ablation.artifacts import verify_model_artifact
from eve_relation_rag.experiments.embedding_ablation.reporting import (
    generate_markdown_report_bytes,
)

app = typer.Typer(
    help="Offline, read-only support commands for the embedding/reranker ablation.",
    no_args_is_help=True,
)


@app.command("verify-artifact")
def verify_artifact_command(
    model_directory: Annotated[Path, typer.Option(exists=True, file_okay=False)],
    manifest_path: Annotated[Path, typer.Option(exists=True, dir_okay=False)],
    approved_manifest_sha256: Annotated[str, typer.Option()],
    expected_model_id: Annotated[str | None, typer.Option()] = None,
    expected_revision: Annotated[str | None, typer.Option()] = None,
    expected_task_kind: Annotated[
        Literal["embedding", "reranker"] | None, typer.Option()
    ] = None,
    expected_dimension: Annotated[int | None, typer.Option(min=1)] = None,
) -> None:
    """Verify local bytes and identity without importing a model runtime."""

    try:
        artifact = verify_model_artifact(
            model_directory,
            manifest_path,
            approved_manifest_sha256,
            expected_model_id=expected_model_id,
            expected_revision=expected_revision,
            expected_task_kind=expected_task_kind,
            expected_dimension=expected_dimension,
        )
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    typer.echo(
        json.dumps(
            {
                "artifact_manifest_sha256": artifact.artifact_manifest_sha256,
                "dimension": artifact.manifest.representation.dimension,
                "exact_revision": artifact.manifest.exact_revision,
                "license": artifact.manifest.license,
                "max_sequence_length": (
                    artifact.manifest.representation.max_sequence_length
                ),
                "model_id": artifact.manifest.model_id,
                "model_key": artifact.manifest.model_key,
                "model_size_bytes": artifact.model_size_bytes,
                "normalization": artifact.manifest.representation.normalization,
                "passage_format": artifact.manifest.representation.passage_format,
                "pooling": artifact.manifest.representation.pooling,
                "query_format": artifact.manifest.representation.query_format,
                "runtime_key": artifact.manifest.runtime_key,
                "similarity": artifact.manifest.representation.similarity,
                "task_kind": artifact.manifest.representation.task_kind,
                "truncation_policy": (
                    artifact.manifest.representation.truncation_policy
                ),
                "verified": True,
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )


@app.command("migrate-legacy-annotations")
def migrate_legacy_annotations_command(
    legacy_benchmark_path: Annotated[Path, typer.Option(exists=True, dir_okay=False)],
    approved_legacy_sha256: Annotated[str, typer.Option()],
    output_path: Annotated[Path, typer.Option()],
) -> None:
    """Preserve legacy gold as pending; never assign category, alternatives, or approval."""

    try:
        legacy = load_legacy_benchmark(legacy_benchmark_path, approved_legacy_sha256)
        manifest = migrate_legacy_benchmark_to_pending(legacy)
        file_sha256 = write_new_annotation_manifest(output_path, manifest)
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    typer.echo(
        json.dumps(
            {
                "annotation_file_sha256": file_sha256,
                "annotation_manifest_sha256": manifest.annotation_manifest_sha256,
                "approved_question_count": manifest.approved_question_count,
                "question_count": manifest.question_count,
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )


@app.command("validate-annotations")
def validate_annotations_command(
    annotation_path: Annotated[Path, typer.Option(exists=True, dir_okay=False)],
    approved_annotation_sha256: Annotated[str, typer.Option()],
) -> None:
    """Revalidate exact annotation bytes, hashes, evidence groups, and review status."""

    try:
        manifest = load_annotation_manifest(annotation_path, approved_annotation_sha256)
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    typer.echo(
        json.dumps(
            {
                "annotation_manifest_sha256": manifest.annotation_manifest_sha256,
                "approved_question_count": manifest.approved_question_count,
                "gold_sha256": manifest.gold_sha256,
                "question_count": manifest.question_count,
                "valid": True,
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )


@app.command("generate-report")
def generate_report_command(
    output_directory: Annotated[Path, typer.Option(exists=True, file_okay=False)],
    report_path: Annotated[Path, typer.Option()],
) -> None:
    """Generate Markdown only from complete, canonical, trusted machine results."""

    try:
        value = generate_markdown_report_bytes(output_directory)
        if report_path.exists() or report_path.is_symlink():
            raise RuntimeError("report output already exists")
        with report_path.open("xb") as handle:
            handle.write(value)
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    typer.echo(str(report_path))


if __name__ == "__main__":
    app()
