#!/usr/bin/env python3
"""Build one exact, candidate-only V0 Checkpoint 2 approval packet."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from eve_relation_rag.activation.manifest_packet import (
    ActivationManifestPacketError,
    ActivationManifestPacketPaths,
    AuthorityPacketPaths,
    CorpusPacketPaths,
    ProviderPacketPaths,
    SourcePacketPaths,
    StructuredPacketPaths,
    build_activation_manifest_packet,
    write_activation_manifest_packet,
)


def _path_argument(
    parser: argparse.ArgumentParser | argparse._ArgumentGroup,
    name: str,
    *,
    help_text: str,
) -> None:
    parser.add_argument(name, required=True, type=Path, help=help_text)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    _path_argument(parser, "--root", help_text="repository/evidence root")
    _path_argument(parser, "--output", help_text="new packet path; never overwritten")

    contract = parser.add_argument_group("approved contract and errata")
    _path_argument(contract, "--approved-contract", help_text="approved Draft A markdown")
    _path_argument(contract, "--contract-errata", help_text="pending E1/E2 errata markdown")

    authority = parser.add_argument_group("raw authority captures")
    _path_argument(
        authority,
        "--ncbi-usage-policy-capture",
        help_text="frozen NCBI website/data usage-policy capture",
    )
    _path_argument(
        authority,
        "--ictv-usage-policy-capture",
        help_text="frozen ICTV CC BY 4.0 taxonomy-page capture",
    )
    _path_argument(
        authority,
        "--ictv-proposal-capture",
        help_text="frozen approved ICTV proposal 2024.010D workbook",
    )

    sources = parser.add_argument_group("raw frozen sources and accounted exclusions")
    _path_argument(sources, "--m1-source-manifest", help_text="tracked M1 source manifest")
    _path_argument(sources, "--m1-source-audit", help_text="tracked M1 source audit")
    _path_argument(sources, "--ncbi-taxdump-archive", help_text="verified taxdump.tar.gz")
    _path_argument(
        sources,
        "--ncbi-taxdump-checksum",
        help_text="publisher-provided taxdump MD5 sidecar",
    )
    _path_argument(sources, "--ictv-msl-workbook", help_text="frozen MSL41 v1 workbook")
    _path_argument(
        sources,
        "--ictv-vmr-workbook",
        help_text="corrected VMR MSL41.v1.20260729 workbook",
    )
    _path_argument(
        sources,
        "--full-sequence-bundle",
        help_text="raw full-sequence evidence wrapper",
    )
    _path_argument(
        sources,
        "--excluded-taxdump-md5",
        help_text="first accounted download rejected for both MD5 and size mismatch",
    )
    _path_argument(
        sources,
        "--excluded-taxdump-size",
        help_text="second accounted download rejected for both MD5 and size mismatch",
    )

    structured = parser.add_argument_group("typed structured candidate manifests")
    for option, help_text in (
        ("--ncbi-artifact-manifest", "NCBI taxonomy artifact manifest"),
        ("--ncbi-snapshot-manifest", "normalized NCBI taxonomy snapshot"),
        ("--assembly-taxon-assignment-manifest", "ten assembly taxonomy assignments"),
        ("--ictv-artifact-manifest", "ICTV MSL41/VMR artifact manifest"),
        ("--ictv-snapshot-manifest", "complete normalized MSL41 snapshot"),
        ("--study-formal-mapping-manifest", "exact Zhao-to-MSL41 rename mapping"),
        ("--adjudication-cohort-manifest", "source_high plus source_low queues"),
        ("--full-sequence-bundle-manifest", "full-sequence evidence sidecar"),
        ("--flank-request-plan-manifest", "frozen flank request plan"),
        ("--flank-evidence-manifest", "completed flank evidence"),
        ("--inclusion-decision-manifest", "policy-authorized inclusion decisions"),
        ("--structured-adjudication-manifest", "terminal structured adjudication"),
        ("--public-locus-membership-manifest", "public locus set"),
        ("--public-assertion-membership-manifest", "public assertion set"),
        ("--structured-activation-manifest", "top-level structured activation candidate"),
    ):
        _path_argument(structured, option, help_text=help_text)

    corpus = parser.add_argument_group("typed corpus, receipt, and binding manifests")
    _path_argument(corpus, "--corpus-manifest", help_text="V0 11-document corpus manifest")
    _path_argument(corpus, "--anchor-manifest", help_text="V0 exact anchor manifest")
    _path_argument(
        corpus,
        "--corpus-validation-receipt",
        help_text="typed validated trusted corpus receipt export",
    )
    _path_argument(
        corpus,
        "--hybrid-binding-manifest",
        help_text="one-pair structured/corpus binding manifest",
    )

    provider = parser.add_argument_group("typed local-only provider policy")
    _path_argument(
        provider,
        "--provider-environment-verifier",
        help_text="stdlib-only provider environment verifier source",
    )
    _path_argument(
        provider,
        "--provider-environment-manifest",
        help_text="closed RECORD-verified environment manifest",
    )
    _path_argument(
        provider,
        "--local-model-policy-manifest",
        help_text="exact local model/runtime policy manifest",
    )
    _path_argument(
        provider,
        "--prompt-policy-manifest",
        help_text="exact approved prompt policy manifest",
    )
    _path_argument(
        provider,
        "--provider-qualification-runner",
        help_text="physical fixed-provider qualification runner source",
    )
    _path_argument(
        provider,
        "--provider-qualification-module",
        help_text="physical typed qualification contract module source",
    )
    _path_argument(
        provider,
        "--provider-qualification-definition",
        help_text="approved pre-run fixed-provider qualification definition",
    )
    _path_argument(
        provider,
        "--provider-qualification-report",
        help_text="passing fixed-provider qualification report",
    )
    _path_argument(
        provider,
        "--human-benchmark-definition",
        help_text="preregistered ten-case benchmark definition only",
    )
    return parser


def _packet_paths(arguments: argparse.Namespace) -> ActivationManifestPacketPaths:
    return ActivationManifestPacketPaths(
        approved_contract=arguments.approved_contract,
        contract_errata=arguments.contract_errata,
        authority=AuthorityPacketPaths(
            ncbi_usage_policy_capture=arguments.ncbi_usage_policy_capture,
            ictv_usage_policy_capture=arguments.ictv_usage_policy_capture,
            ictv_proposal_capture=arguments.ictv_proposal_capture,
        ),
        sources=SourcePacketPaths(
            m1_source_manifest=arguments.m1_source_manifest,
            m1_source_audit=arguments.m1_source_audit,
            ncbi_taxdump_archive=arguments.ncbi_taxdump_archive,
            ncbi_taxdump_checksum=arguments.ncbi_taxdump_checksum,
            ictv_msl_workbook=arguments.ictv_msl_workbook,
            ictv_vmr_workbook=arguments.ictv_vmr_workbook,
            full_sequence_bundle=arguments.full_sequence_bundle,
            excluded_taxdump_md5=arguments.excluded_taxdump_md5,
            excluded_taxdump_size=arguments.excluded_taxdump_size,
        ),
        structured=StructuredPacketPaths(
            ncbi_artifact_manifest=arguments.ncbi_artifact_manifest,
            ncbi_snapshot_manifest=arguments.ncbi_snapshot_manifest,
            assembly_taxon_assignment_manifest=(arguments.assembly_taxon_assignment_manifest),
            ictv_artifact_manifest=arguments.ictv_artifact_manifest,
            ictv_snapshot_manifest=arguments.ictv_snapshot_manifest,
            study_formal_mapping_manifest=arguments.study_formal_mapping_manifest,
            adjudication_cohort_manifest=arguments.adjudication_cohort_manifest,
            full_sequence_bundle_manifest=arguments.full_sequence_bundle_manifest,
            flank_request_plan_manifest=arguments.flank_request_plan_manifest,
            flank_evidence_manifest=arguments.flank_evidence_manifest,
            inclusion_decision_manifest=arguments.inclusion_decision_manifest,
            structured_adjudication_manifest=(arguments.structured_adjudication_manifest),
            public_locus_membership_manifest=(arguments.public_locus_membership_manifest),
            public_assertion_membership_manifest=(arguments.public_assertion_membership_manifest),
            structured_activation_manifest=arguments.structured_activation_manifest,
        ),
        corpus=CorpusPacketPaths(
            corpus_manifest=arguments.corpus_manifest,
            anchor_manifest=arguments.anchor_manifest,
            corpus_validation_receipt=arguments.corpus_validation_receipt,
            hybrid_binding_manifest=arguments.hybrid_binding_manifest,
        ),
        provider=ProviderPacketPaths(
            provider_environment_verifier=arguments.provider_environment_verifier,
            provider_environment_manifest=arguments.provider_environment_manifest,
            local_model_policy_manifest=arguments.local_model_policy_manifest,
            prompt_policy_manifest=arguments.prompt_policy_manifest,
            provider_qualification_runner=arguments.provider_qualification_runner,
            provider_qualification_module=arguments.provider_qualification_module,
            provider_qualification_definition=(arguments.provider_qualification_definition),
            provider_qualification_report=arguments.provider_qualification_report,
        ),
        human_benchmark_definition=arguments.human_benchmark_definition,
    )


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _build_parser().parse_args(argv)
    try:
        packet = build_activation_manifest_packet(
            arguments.root,
            _packet_paths(arguments),
        )
        output_identity = write_activation_manifest_packet(arguments.output, packet)
    except ActivationManifestPacketError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"packet_sha256={packet.packet_sha256}")
    print(f"file_sha256={output_identity.file_sha256}")
    print(f"byte_size={output_identity.byte_size}")
    print("checkpoint=2")
    print("status=candidate_for_owner_approval")
    print("packet_build_database_writes=false")
    print("production_database_role_qualified=false")
    print("publication_performed=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
