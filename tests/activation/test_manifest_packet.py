"""Checkpoint 2 approval-packet boundary and identity tests."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal, Self, cast

import pytest
from pydantic import BaseModel, ValidationError, model_validator

import eve_relation_rag.activation.manifest_packet as packet_module
from eve_relation_rag.activation.contracts import (
    ACTIVATION_RELEASE_KEY,
    FlankEvidenceManifest,
    FlankEvidenceRequestPlan,
    InclusionDecisionManifest,
    PublicAssertionMembershipManifest,
    PublicAssertionMembershipRecord,
    PublicLocusMembershipManifest,
    StructuredActivationManifest,
    StructuredAdjudicationManifest,
    seal_manifest_payload,
)
from eve_relation_rag.activation.corpus import (
    FORMAL_AMPHINTOVIRALES_TERM_KEY,
    STUDY_ORTHOPOLINTOVIRALES_TERM_KEY,
)
from eve_relation_rag.activation.flanks import materialize_primary_flank_artifacts
from eve_relation_rag.activation.manifest_packet import (
    ActivationManifestPacketError,
    AuthorityCapture,
    BenchmarkPacketArtifacts,
    BenchmarkPacketSummary,
    CandidateApprovalBoundary,
    ContractEvidence,
    CorpusPacketArtifacts,
    CorpusPacketSummary,
    ExcludedSourceArtifact,
    FrozenSourceEvidence,
    PacketSummary,
    ProviderEnvironmentManifest,
    ProviderPacketArtifacts,
    ProviderPacketSummary,
    RawFileIdentity,
    StructuredPacketArtifacts,
    StructuredPacketSummary,
    TypedArtifactIdentity,
    TypedSemanticIdentity,
    V0ActivationManifestPacket,
    observe_raw_file,
    verify_activation_manifest_packet,
    verify_raw_file_identity,
    write_activation_manifest_packet,
)
from eve_relation_rag.activation.policy import (
    DependencyBindings,
    InclusionEvaluationInput,
    build_adjudication_manifest,
    build_inclusion_manifest,
    build_public_assertion_membership_manifest,
    build_public_locus_membership_manifest,
)
from eve_relation_rag.generation.qualification import (
    ProviderQualificationDefinition,
    ProviderQualificationReport,
    build_provider_qualification_report,
)
from eve_relation_rag.hybrid.contracts import (
    StrictFrozenSchema,
    canonical_model_json,
    canonical_model_sha256,
    canonical_self_sha256,
)
from tests.activation.test_cohort_flanks_policy import _bundle, _cohort
from tests.generation.test_v0_provider_qualification import (
    _definition as _provider_qualification_definition,
)
from tests.generation.test_v0_provider_qualification import (
    _definition_file as _provider_qualification_definition_file,
)
from tests.generation.test_v0_provider_qualification import (
    _observation as _provider_qualification_observation,
)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _raw(path: str, *, sha256: str | None = None, byte_size: int = 1) -> RawFileIdentity:
    return RawFileIdentity(
        path=path,
        byte_size=byte_size,
        file_sha256=sha256 or _sha(path),
    )


def _typed(
    path: str,
    *,
    schema_version: str = "tests-manifest-v1",
    schema_field: str = "manifest_schema_version",
    digest_field: Literal[
        "manifest_sha256",
        "anchor_manifest_sha256",
        "definition_sha256",
        "report_sha256",
    ] = "manifest_sha256",
) -> TypedArtifactIdentity:
    return TypedArtifactIdentity(
        raw_file=_raw(path),
        semantic=TypedSemanticIdentity(
            schema_version_field=schema_field,
            schema_version=schema_version,
            digest_field=digest_field,
            semantic_sha256=_sha(f"semantic:{path}"),
        ),
    )


def _authority_captures() -> tuple[AuthorityCapture, AuthorityCapture, AuthorityCapture]:
    return (
        AuthorityCapture(
            capture_key="authority-capture:ncbi-usage-policy:20260829",
            source_uri="https://www.ncbi.nlm.nih.gov/home/about/policies/",
            retrieved_at="2026-08-29T06:41:28Z",
            media_type="text/html",
            raw_file=_raw(
                "evidence/ncbi-policy.html",
                sha256=("8ad8f6f186ca51ec73a5fb8935ecfa17b8cbaad300b7025b381898ab72621869"),
                byte_size=38_936,
            ),
        ),
        AuthorityCapture(
            capture_key="authority-capture:ictv-taxonomy-cc-by-4.0:20260829",
            source_uri="https://ictv.global/taxonomy",
            retrieved_at="2026-08-29T06:41:28Z",
            media_type="text/html",
            raw_file=_raw(
                "evidence/ictv-policy.html",
                sha256=("4c8bc175029519fe34003254cc2c01fbac9ba00bb2086cf08a96f03a54efc4df"),
                byte_size=62_480,
            ),
        ),
        AuthorityCapture(
            capture_key="authority-capture:ictv-proposal-2024.010D",
            source_uri=(
                "https://ictv.global/system/files/proposals/approved/"
                "Animal_DNA_viruses_and_Retroviruses/"
                "2024.010D.Varidnaviria_reorg.xlsx"
            ),
            retrieved_at="2026-08-29T06:41:28Z",
            media_type=("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            raw_file=_raw(
                "evidence/proposal.xlsx",
                sha256=("c11d6f496ff610a33862e1993b6f27d967563478e8c24b80b882037ba16bfd62"),
                byte_size=26_852,
            ),
        ),
    )


def _synthetic_packet() -> V0ActivationManifestPacket:
    structured_values = {
        name: _typed(f"evidence/structured/{name}.json")
        for name in StructuredPacketArtifacts.model_fields
    }
    corpus_values = {
        name: _typed(
            f"evidence/corpus/{name}.json",
            schema_version="corpus-anchor-manifest-v1" if name == "anchor_manifest" else "v1",
            schema_field=(
                "anchor_manifest_schema_version"
                if name == "anchor_manifest"
                else "manifest_schema_version"
            ),
            digest_field=(
                "anchor_manifest_sha256" if name == "anchor_manifest" else "manifest_sha256"
            ),
        )
        for name in CorpusPacketArtifacts.model_fields
    }
    provider_values: dict[str, Any] = {
        "provider_environment_verifier": _raw("scripts/provider-environment.py"),
        "provider_environment_manifest": _typed(
            "evidence/provider/environment.json",
            schema_version="v0-provider-environment-manifest-v1",
        ),
        "local_model_policy_manifest": _typed("evidence/provider/model.json"),
        "prompt_policy_manifest": _typed("evidence/provider/prompt.json"),
        "provider_qualification_runner": _raw("scripts/run_v0_provider_qualification.py"),
        "provider_qualification_module": _raw("src/eve_relation_rag/generation/qualification.py"),
        "provider_qualification_definition": _typed(
            "evidence/provider/qualification-definition.json",
            schema_version="v0-provider-qualification-definition-v1",
            schema_field="definition_schema_version",
            digest_field="definition_sha256",
        ),
        "provider_qualification_report": _typed(
            "evidence/provider/qualification-report.json",
            schema_version="v0-provider-qualification-report-v1",
            schema_field="report_schema_version",
            digest_field="report_sha256",
        ),
    }
    payload: dict[str, object] = {
        "packet_schema_version": "v0-activation-manifest-packet-v1",
        "packet_key": "activation-manifest-packet:endoviho-rag:v0:checkpoint-2:a",
        "checkpoint": 2,
        "status": "candidate_for_owner_approval",
        "product_version": "V0",
        "release_key": "release:endoviho-rag:v0:20260826:001",
        "corpus_release_key": "corpus:endoviho-rag:v0:20260829:001",
        "contract": ContractEvidence(
            contract_name="V0 Activation and Publication Contract — Draft A",
            contract_status="approved",
            approved_on="2026-08-29",
            approved_contract=_raw("docs/contract.md"),
            errata_status="pending_activation_manifest_packet_approval",
            errata_ids=("E1", "E2"),
            errata=_raw("docs/errata.md"),
        ),
        "authority_captures": _authority_captures(),
        "frozen_sources": FrozenSourceEvidence(
            m1_source_manifest=_raw("sources/m1-manifest.json"),
            m1_source_audit=_raw("sources/m1-audit.json"),
            ncbi_taxdump_archive=_raw("sources/taxdump.tar.gz"),
            ncbi_taxdump_checksum=_raw("sources/taxdump.md5"),
            ictv_msl_workbook=_raw("sources/msl.xlsx"),
            ictv_vmr_workbook=_raw("sources/vmr.xlsx"),
            full_sequence_bundle=_raw("sources/sequences.json"),
            excluded_taxdump_candidates=(
                ExcludedSourceArtifact(
                    reason_codes=(
                        "publisher_md5_mismatch",
                        "retrieved_byte_size_mismatch",
                    ),
                    raw_file=_raw("sources/taxdump-corrupt-md5.tar.gz"),
                    used_by_candidate=False,
                ),
                ExcludedSourceArtifact(
                    reason_codes=(
                        "publisher_md5_mismatch",
                        "retrieved_byte_size_mismatch",
                    ),
                    raw_file=_raw("sources/taxdump-corrupt-size.tar.gz"),
                    used_by_candidate=False,
                ),
            ),
        ),
        "structured": StructuredPacketArtifacts(**structured_values),
        "corpus": CorpusPacketArtifacts(**corpus_values),
        "provider": ProviderPacketArtifacts(**provider_values),
        "benchmark": BenchmarkPacketArtifacts(
            human_benchmark_definition=_typed(
                "evidence/benchmark/definition.json",
                schema_version="v0-human-benchmark-definition-v1",
                schema_field="definition_schema_version",
                digest_field="definition_sha256",
            )
        ),
        "summary": PacketSummary(
            structured=StructuredPacketSummary(
                primary_assessed_count=71,
                expansion_assessed_count=0,
                source_low_invoked=False,
                include_count=70,
                quarantine_decision_count=1,
                review_decision_count=0,
                exclude_decision_count=0,
                public_locus_count=70,
                public_assertion_count=210,
                ncbi_term_count=51,
                ictv_term_count=22_670,
                assembly_assignment_count=10,
                study_formal_mapping_count=1,
                family_mapping_count=0,
                mapping_relation="renamed_to",
                all_ten_assemblies_passing=True,
            ),
            corpus=CorpusPacketSummary(
                document_count=11,
                anchor_count=30,
                structured_lineage_anchor_count=8,
                corpus_release_status="validated",
                receipt_status="passed",
                receipt_trusted=True,
                published_status_claimed=False,
            ),
            provider=ProviderPacketSummary(
                provider_key="provider:local-openai-compatible:v1",
                model_key="model:tests:v0",
                model_revision="revision:tests:v0",
                environment_distribution_count=34,
                environment_file_count=5_638,
                network_policy_key="network:macos-sandbox-v0-ports-only-v2",
                qualification_candidate_count=1,
                qualification_status="passed",
                qualification_selection="only_passing_candidate",
                qualification_request_count=1,
                qualification_retry_count=0,
                qualification_hmac_attestation_verified=True,
                qualification_inner_unauthenticated_status=401,
                qualification_clean_shutdown=True,
                external_provider_authorized=False,
            ),
            benchmark=BenchmarkPacketSummary(
                case_count=10,
                expected_matched_target_count=10,
                expected_unmatched_target_count=30,
                assembly_count=10,
                human_semantic_verdict_included=False,
            ),
        ),
        "boundary": CandidateApprovalBoundary(
            checkpoint=2,
            owner_approval_required=True,
            packet_build_database_writes_performed=False,
            production_database_role_qualified=False,
            structured_validation_receipt_included=False,
            human_semantic_verdict_included=False,
            published_status_claimed=False,
            publication_authorized=False,
            external_tag_release_or_image_authorized=False,
        ),
        "packet_sha256": "0" * 64,
    }
    payload["packet_sha256"] = canonical_self_sha256(payload, "packet_sha256")
    return V0ActivationManifestPacket.model_validate(payload)


def _reseal(payload: dict[str, Any]) -> dict[str, Any]:
    payload["packet_sha256"] = "0" * 64
    payload["packet_sha256"] = canonical_self_sha256(payload, "packet_sha256")
    return payload


def test_packet_is_checkpoint_2_candidate_with_fail_closed_boundary() -> None:
    packet = _synthetic_packet()

    assert packet.checkpoint == 2
    assert packet.status == "candidate_for_owner_approval"
    assert not packet.boundary.human_semantic_verdict_included
    assert not packet.boundary.structured_validation_receipt_included
    assert not packet.boundary.production_database_role_qualified
    assert not packet.boundary.published_status_claimed
    assert not packet.boundary.publication_authorized
    assert packet.packet_sha256 == canonical_self_sha256(packet, "packet_sha256")


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("human_semantic_verdict_included", True),
        ("structured_validation_receipt_included", True),
        ("production_database_role_qualified", True),
        ("published_status_claimed", True),
        ("publication_authorized", True),
    ),
)
def test_packet_schema_cannot_claim_forbidden_checkpoint_3_evidence(
    field: str, value: bool
) -> None:
    payload = _synthetic_packet().model_dump(mode="json")
    payload["boundary"][field] = value

    with pytest.raises(ValidationError):
        V0ActivationManifestPacket.model_validate(_reseal(payload))


def test_packet_rejects_extra_structured_receipt_even_if_resealed() -> None:
    payload = _synthetic_packet().model_dump(mode="json")
    payload["structured_validation_receipt"] = {"status": "passed"}

    with pytest.raises(ValidationError, match="Extra inputs"):
        V0ActivationManifestPacket.model_validate(_reseal(payload))


def test_packet_binds_semantic_and_raw_identities_as_distinct_values() -> None:
    packet = _synthetic_packet()
    payload = packet.model_dump(mode="json")
    artifact = payload["structured"]["structured_activation_manifest"]
    semantic_sha256 = artifact["semantic"]["semantic_sha256"]
    original_packet_sha256 = packet.packet_sha256
    artifact["raw_file"]["file_sha256"] = "f" * 64

    changed = V0ActivationManifestPacket.model_validate_json(json.dumps(_reseal(payload)))

    assert (
        changed.structured.structured_activation_manifest.semantic.semantic_sha256
        == semantic_sha256
    )
    assert changed.packet_sha256 != original_packet_sha256


def test_packet_rejects_duplicate_artifact_paths() -> None:
    payload = _synthetic_packet().model_dump(mode="json")
    payload["contract"]["errata"]["path"] = payload["contract"]["approved_contract"]["path"]

    with pytest.raises(ValidationError, match="paths must be unique"):
        V0ActivationManifestPacket.model_validate_json(json.dumps(_reseal(payload)))


def test_packet_paths_and_raw_refs_cover_all_provider_qualification_evidence() -> None:
    packet = _synthetic_packet()
    provider_paths = packet_module._paths_from_packet(packet).provider
    raw_paths = {row.path for row in packet_module._all_raw_refs(packet)}

    assert provider_paths.provider_qualification_runner == Path(
        "scripts/run_v0_provider_qualification.py"
    )
    assert provider_paths.provider_qualification_module == Path(
        "src/eve_relation_rag/generation/qualification.py"
    )
    assert provider_paths.provider_qualification_definition == Path(
        "evidence/provider/qualification-definition.json"
    )
    assert provider_paths.provider_qualification_report == Path(
        "evidence/provider/qualification-report.json"
    )
    assert {
        str(provider_paths.provider_qualification_runner),
        str(provider_paths.provider_qualification_module),
        str(provider_paths.provider_qualification_definition),
        str(provider_paths.provider_qualification_report),
    }.issubset(raw_paths)


def test_provider_environment_manifest_is_sorted_counted_and_self_hashed() -> None:
    payload: dict[str, object] = {
        "manifest_schema_version": "v0-provider-environment-manifest-v1",
        "identity_schema_version": "v0-provider-environment-identity-v1",
        "provider_environment_sha256": "1" * 64,
        "provider_environment_distribution_count": 2,
        "provider_environment_file_count": 7,
        "distributions": (
            {
                "canonical_name": "alpha",
                "version": "1.0",
                "file_count": 3,
                "record_sha256": "2" * 64,
            },
            {
                "canonical_name": "beta-package",
                "version": "2.0",
                "file_count": 4,
                "record_sha256": "3" * 64,
            },
        ),
        "manifest_sha256": "0" * 64,
    }
    payload["manifest_sha256"] = canonical_self_sha256(payload, "manifest_sha256")

    manifest = ProviderEnvironmentManifest.model_validate(payload)

    assert manifest.provider_environment_file_count == 7
    wrong_order = dict(payload)
    distributions = payload["distributions"]
    assert isinstance(distributions, tuple)
    wrong_order["distributions"] = tuple(reversed(distributions))
    wrong_order["manifest_sha256"] = canonical_self_sha256(wrong_order, "manifest_sha256")
    with pytest.raises(ValidationError, match="canonically ordered"):
        ProviderEnvironmentManifest.model_validate(wrong_order)


def test_raw_file_identity_rejects_drift_and_symlinks(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence.json"
    evidence.write_bytes(b'{"candidate":true}\n')
    reference = observe_raw_file(tmp_path, Path("evidence.json"))

    verify_raw_file_identity(tmp_path, reference)
    evidence.write_bytes(b'{"candidate":fals}\n')
    with pytest.raises(ActivationManifestPacketError, match="drifted"):
        verify_raw_file_identity(tmp_path, reference)

    target = tmp_path / "target.json"
    target.write_text("{}\n", encoding="utf-8")
    link = tmp_path / "link.json"
    link.symlink_to(target)
    with pytest.raises(ActivationManifestPacketError, match="symbolic"):
        observe_raw_file(tmp_path, Path("link.json"))


class _ExampleManifest(StrictFrozenSchema):
    manifest_schema_version: Literal["example-manifest-v1"]
    value: str
    manifest_sha256: str

    @model_validator(mode="after")
    def validate_digest(self) -> Self:
        if self.manifest_sha256 != canonical_self_sha256(self, "manifest_sha256"):
            raise ValueError("digest mismatch")
        return self


def test_typed_loader_extracts_semantic_identity_after_strict_validation(
    tmp_path: Path,
) -> None:
    payload = {
        "manifest_schema_version": "example-manifest-v1",
        "value": "candidate",
        "manifest_sha256": "0" * 64,
    }
    payload["manifest_sha256"] = canonical_self_sha256(payload, "manifest_sha256")
    path = tmp_path / "typed.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    loaded = packet_module._load_manifest(
        tmp_path,
        Path("typed.json"),
        _ExampleManifest,
        role="example",
    )

    assert loaded.identity.semantic.semantic_sha256 == payload["manifest_sha256"]
    assert loaded.identity.raw_file.file_sha256 != payload["manifest_sha256"]
    payload["value"] = "drifted"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ActivationManifestPacketError, match="typed evidence"):
        packet_module._load_manifest(
            tmp_path,
            Path("typed.json"),
            _ExampleManifest,
            role="example",
        )


def _qualification_evidence() -> tuple[
    ProviderQualificationDefinition,
    RawFileIdentity,
    ProviderQualificationReport,
]:
    definition = _provider_qualification_definition()
    definition_file = _provider_qualification_definition_file(definition)
    raw_definition_file = RawFileIdentity(
        path=definition_file.relative_path,
        byte_size=definition_file.byte_size,
        file_sha256=definition_file.sha256,
    )
    report = build_provider_qualification_report(
        definition,
        definition_file=definition_file,
        observation=_provider_qualification_observation(),
    )
    return definition, raw_definition_file, report


def test_qualification_loader_requires_canonical_definition_bytes(tmp_path: Path) -> None:
    definition, _definition_file, _report = _qualification_evidence()
    path = tmp_path / "qualification-definition.json"
    canonical = (canonical_model_json(definition) + "\n").encode("utf-8")
    path.write_bytes(canonical)

    loaded = packet_module._load_typed_artifact(
        tmp_path,
        Path("qualification-definition.json"),
        ProviderQualificationDefinition,
        role="provider_qualification_definition",
        schema_version_field="definition_schema_version",
        digest_field="definition_sha256",
        require_canonical_bytes=True,
    )
    assert loaded.model == definition
    assert loaded.identity.raw_file.file_sha256 == hashlib.sha256(canonical).hexdigest()

    path.write_text(
        json.dumps(definition.model_dump(mode="json"), indent=2) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ActivationManifestPacketError, match="canonical typed evidence"):
        packet_module._load_typed_artifact(
            tmp_path,
            Path("qualification-definition.json"),
            ProviderQualificationDefinition,
            role="provider_qualification_definition",
            schema_version_field="definition_schema_version",
            digest_field="definition_sha256",
            require_canonical_bytes=True,
        )


def test_qualification_rejects_resealed_definition_cross_binding_mutation() -> None:
    definition, definition_file, report = _qualification_evidence()
    payload = definition.model_dump(mode="json")
    payload["candidate_set"][0]["runtime_proxy_sha256"] = "f" * 64
    payload["definition_sha256"] = canonical_self_sha256(payload, "definition_sha256")
    mutated = ProviderQualificationDefinition.model_validate_json(json.dumps(payload))

    with pytest.raises(ActivationManifestPacketError, match="does not replay"):
        packet_module._validate_qualification_report_binding(
            definition=mutated,
            definition_file=definition_file,
            report=report,
        )


def test_qualification_rejects_resealed_report_cross_binding_mutation() -> None:
    definition, definition_file, report = _qualification_evidence()
    payload = report.model_dump(mode="json")
    payload["candidate"]["runtime_proxy_sha256"] = "f" * 64
    payload["report_sha256"] = canonical_self_sha256(payload, "report_sha256")
    mutated = ProviderQualificationReport.model_validate_json(json.dumps(payload))

    with pytest.raises(ActivationManifestPacketError, match="does not replay"):
        packet_module._validate_qualification_report_binding(
            definition=definition,
            definition_file=definition_file,
            report=mutated,
        )


@pytest.mark.parametrize("artifact", ("definition", "report"))
def test_qualification_rejects_resealed_client_runtime_cross_binding_mutation(
    artifact: str,
) -> None:
    definition, definition_file, report = _qualification_evidence()
    value = definition if artifact == "definition" else report
    digest_field = "definition_sha256" if artifact == "definition" else "report_sha256"
    payload = value.model_dump(mode="json")
    runtime = payload["client_runtime_manifest"]
    source_files = runtime["source_files"]
    composer = next(
        item
        for item in source_files
        if item["relative_path"] == "src/eve_relation_rag/generation/composer.py"
    )
    composer["sha256"] = "f" * 64
    runtime["source_manifest_sha256"] = canonical_model_sha256(source_files)
    runtime["manifest_sha256"] = canonical_self_sha256(runtime, "manifest_sha256")
    payload[digest_field] = canonical_self_sha256(payload, digest_field)

    if artifact == "definition":
        mutated_definition = ProviderQualificationDefinition.model_validate_json(
            json.dumps(payload)
        )
        mutated_report = report
    else:
        mutated_definition = definition
        mutated_report = ProviderQualificationReport.model_validate_json(json.dumps(payload))
    with pytest.raises(ActivationManifestPacketError, match="does not replay"):
        packet_module._validate_qualification_report_binding(
            definition=mutated_definition,
            definition_file=definition_file,
            report=mutated_report,
        )


def test_qualification_report_must_bind_packet_definition_raw_identity() -> None:
    definition, definition_file, report = _qualification_evidence()
    drifted_file = definition_file.model_copy(update={"file_sha256": "f" * 64})

    with pytest.raises(ActivationManifestPacketError, match="definition bytes"):
        packet_module._validate_qualification_report_binding(
            definition=definition,
            definition_file=drifted_file,
            report=report,
        )


def test_qualification_graph_cross_checks_model_prompt_environment_physical_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    definition, definition_file, report = _qualification_evidence()
    candidate = definition.candidate_set[0]

    def raw_identity(value: object) -> RawFileIdentity:
        identity = cast(Any, value)
        return RawFileIdentity(
            path=identity.relative_path,
            byte_size=identity.byte_size,
            file_sha256=identity.sha256,
        )

    environment = cast(
        Any,
        SimpleNamespace(
            manifest_schema_version="v0-provider-environment-manifest-v1",
            identity_schema_version="v0-provider-environment-identity-v1",
            manifest_sha256=candidate.provider_environment_manifest_sha256,
            provider_environment_sha256=candidate.provider_environment_sha256,
            provider_environment_distribution_count=(
                candidate.provider_environment_distribution_count
            ),
            provider_environment_file_count=candidate.provider_environment_file_count,
        ),
    )
    model = cast(
        Any,
        SimpleNamespace(manifest_sha256=candidate.model_policy_manifest_sha256),
    )
    prompt = cast(
        Any,
        SimpleNamespace(manifest_sha256=candidate.prompt_policy_manifest_sha256),
    )
    model_file = raw_identity(candidate.model_policy_file)
    prompt_file = raw_identity(candidate.prompt_policy_file)
    environment_file = raw_identity(candidate.provider_environment_manifest_file)
    runner_file = raw_identity(definition.runner_file)
    module_file = raw_identity(definition.qualification_module_file)
    monkeypatch.setattr(
        packet_module,
        "verify_provider_qualification_definition",
        lambda *_args, **_kwargs: definition,
    )

    arguments = {
        "project_root": Path("."),
        "environment": environment,
        "environment_file": environment_file,
        "model": model,
        "model_file": model_file,
        "prompt": prompt,
        "prompt_file": prompt_file,
        "qualification_runner": runner_file,
        "qualification_module": module_file,
        "qualification_definition": definition,
        "qualification_definition_file": definition_file,
        "qualification_report": report,
    }
    packet_module._validate_provider_qualification_graph(**arguments)

    arguments["model_file"] = model_file.model_copy(update={"file_sha256": "f" * 64})
    with pytest.raises(ActivationManifestPacketError, match="candidate differs"):
        packet_module._validate_provider_qualification_graph(**arguments)


def _structured_graph(tmp_path: Path) -> dict[str, Any]:
    digests = {
        name: _sha(name)
        for name in (
            "ncbi_artifact",
            "ncbi_snapshot",
            "assignments",
            "ictv_artifact",
            "ictv_snapshot",
            "mapping",
        )
    }
    cohort = _cohort()
    bundle = _bundle(tmp_path, cohort)
    flank_artifacts = materialize_primary_flank_artifacts(
        cohort,
        bundle,
        assessed_by="method:v0-flank-context-v1",
        assessed_at="2026-08-29T05:46:54Z",
    )
    flank_by_locus = {
        record.locus_key: record for record in flank_artifacts.evidence_manifest.records
    }
    dependencies = DependencyBindings(
        ncbi_snapshot_manifest_sha256=digests["ncbi_snapshot"],
        ictv_snapshot_manifest_sha256=digests["ictv_snapshot"],
        mapping_manifest_sha256=digests["mapping"],
    )
    inclusions = build_inclusion_manifest(
        cohort,
        flank_artifacts.evidence_manifest,
        (
            InclusionEvaluationInput(
                record=record,
                flank=flank_by_locus[record.locus_key],
                dependencies=dependencies,
                m1_gates_pass=True,
                exact_placement_count=1 if record.placement_key is not None else 0,
            )
            for record in cohort.primary_records
        ),
    )
    adjudication = build_adjudication_manifest(
        cohort,
        flank_artifacts.evidence_manifest,
        inclusions,
    )
    public_loci = build_public_locus_membership_manifest(
        cohort,
        flank_artifacts.evidence_manifest,
        inclusions,
        adjudication,
    )
    predicates: dict[
        Literal["hcvr", "viral_major_taxon", "vr_type"],
        str,
    ] = {
        "hcvr": "source:hcvr",
        "viral_major_taxon": "source:viral-major-taxon",
        "vr_type": "source:vr-type",
    }
    assertion_types: tuple[
        Literal["hcvr", "viral_major_taxon", "vr_type"],
        ...,
    ] = ("hcvr", "viral_major_taxon", "vr_type")
    public_assertions = build_public_assertion_membership_manifest(
        public_loci,
        (
            PublicAssertionMembershipRecord(
                assertion_key=(
                    f"assertion:test:{membership.locus_key.rsplit(':', maxsplit=1)[-1]}:"
                    f"{assertion_type}"
                ),
                locus_key=membership.locus_key,
                assertion_type=assertion_type,
                predicate_key=predicates[assertion_type],
                evidence_sha256s=(membership.inclusion_decision_sha256,),
            )
            for membership in public_loci.memberships
            for assertion_type in assertion_types
        ),
    )
    activation_payload: dict[str, object] = {
        "manifest_schema_version": "structured-activation-manifest-v1",
        "release_key": ACTIVATION_RELEASE_KEY,
        "source_manifest_sha256": cohort.source_manifest_sha256,
        "source_audit_sha256": cohort.source_audit_sha256,
        "ncbi_artifact_manifest_sha256": digests["ncbi_artifact"],
        "ncbi_snapshot_manifest_sha256": digests["ncbi_snapshot"],
        "assembly_taxon_assignment_manifest_sha256": digests["assignments"],
        "ictv_artifact_manifest_sha256": digests["ictv_artifact"],
        "ictv_snapshot_manifest_sha256": digests["ictv_snapshot"],
        "study_formal_mapping_manifest_sha256": digests["mapping"],
        "cohort_manifest_sha256": cohort.manifest_sha256,
        "full_sequence_bundle_manifest_sha256": bundle.manifest.manifest_sha256,
        "flank_request_plan_manifest_sha256": flank_artifacts.request_plan.manifest_sha256,
        "adjudication_manifest_sha256": adjudication.manifest_sha256,
        "flank_manifest_sha256": flank_artifacts.evidence_manifest.manifest_sha256,
        "inclusion_manifest_sha256": inclusions.manifest_sha256,
        "public_locus_membership_manifest_sha256": public_loci.manifest_sha256,
        "public_assertion_membership_manifest_sha256": public_assertions.manifest_sha256,
        "counts": {
            "audited_source_records": 39_495,
            "exact_placements": 38_968,
            "accounted_quarantine": 527,
            "adjudicated_records": len(adjudication.selections),
            "included_loci": public_loci.membership_count,
            "public_locus_memberships": public_loci.membership_count,
            "public_assertion_memberships": public_assertions.membership_count,
        },
    }
    activation = StructuredActivationManifest.model_validate(
        seal_manifest_payload(activation_payload)
    )
    upstream_absent = SimpleNamespace(
        upstream_checksum=None,
        upstream_checksum_algorithm=None,
        checksum_source_uri=None,
        upstream_checksum_verified=False,
    )
    mapping_row = SimpleNamespace(
        study_term_key=STUDY_ORTHOPOLINTOVIRALES_TERM_KEY,
        formal_term_key=FORMAL_AMPHINTOVIRALES_TERM_KEY,
        relation="renamed_to",
        evidence_artifact_sha256=packet_module._PROPOSAL_CAPTURE_SHA256,
    )
    return {
        "ncbi_artifact": SimpleNamespace(manifest_sha256=digests["ncbi_artifact"]),
        "ncbi_snapshot": SimpleNamespace(
            manifest_sha256=digests["ncbi_snapshot"],
            artifact_manifest_sha256=digests["ncbi_artifact"],
            terms=(SimpleNamespace(term_key="ncbi:one"),),
        ),
        "assignments": SimpleNamespace(
            manifest_sha256=digests["assignments"],
            ncbi_snapshot_manifest_sha256=digests["ncbi_snapshot"],
        ),
        "ictv_artifact": SimpleNamespace(
            manifest_sha256=digests["ictv_artifact"],
            msl=upstream_absent,
            corrected_vmr=upstream_absent,
        ),
        "ictv_snapshot": SimpleNamespace(
            manifest_sha256=digests["ictv_snapshot"],
            artifact_manifest_sha256=digests["ictv_artifact"],
            snapshot_key="snapshot:ictv",
            terms=(SimpleNamespace(term_key=FORMAL_AMPHINTOVIRALES_TERM_KEY),),
        ),
        "mapping": SimpleNamespace(
            manifest_sha256=digests["mapping"],
            formal_snapshot_manifest_sha256=digests["ictv_snapshot"],
            formal_snapshot_key="snapshot:ictv",
            mappings=(mapping_row,),
        ),
        "cohort": cohort,
        "sequence_bundle": bundle.manifest,
        "request_plan": flank_artifacts.request_plan,
        "flanks": flank_artifacts.evidence_manifest,
        "inclusions": inclusions,
        "adjudication": adjudication,
        "public_loci": public_loci,
        "public_assertions": public_assertions,
        "activation": activation,
    }


def test_structured_graph_enforces_errata_e2_and_exact_cross_bindings(
    tmp_path: Path,
) -> None:
    graph = _structured_graph(tmp_path)
    summary = packet_module._validate_structured_graph(**graph)
    assert summary.primary_assessed_count == 71
    assert summary.family_mapping_count == 0

    graph["ictv_artifact"].msl = SimpleNamespace(
        upstream_checksum="project-computed-digest",
        upstream_checksum_algorithm="sha256",
        checksum_source_uri="https://example.test/checksum",
        upstream_checksum_verified=True,
    )
    with pytest.raises(ActivationManifestPacketError, match="publisher checksums"):
        packet_module._validate_structured_graph(**graph)


def _reseal_model[ModelT: BaseModel](
    model: ModelT,
    *,
    digest_field: str = "manifest_sha256",
    **updates: object,
) -> ModelT:
    payload = model.model_dump(mode="python")
    payload.update(updates)
    payload[digest_field] = "0" * 64
    payload[digest_field] = canonical_self_sha256(payload, digest_field)
    return type(model).model_validate(payload)


def _rechain_structured_envelopes(graph: dict[str, Any]) -> None:
    cohort = graph["cohort"]
    request_plan = graph["request_plan"]
    flanks = graph["flanks"]
    inclusions = graph["inclusions"]
    adjudication = graph["adjudication"]
    public_loci = graph["public_loci"]
    public_assertions = graph["public_assertions"]
    activation = graph["activation"]
    assert isinstance(request_plan, FlankEvidenceRequestPlan)
    assert isinstance(flanks, FlankEvidenceManifest)
    assert isinstance(inclusions, InclusionDecisionManifest)
    assert isinstance(adjudication, StructuredAdjudicationManifest)
    assert isinstance(public_loci, PublicLocusMembershipManifest)
    assert isinstance(public_assertions, PublicAssertionMembershipManifest)
    assert isinstance(activation, StructuredActivationManifest)

    flanks = _reseal_model(
        flanks,
        cohort_manifest_sha256=cohort.manifest_sha256,
        request_plan_manifest_sha256=request_plan.manifest_sha256,
    )
    graph["flanks"] = flanks
    inclusions = _reseal_model(
        inclusions,
        cohort_manifest_sha256=cohort.manifest_sha256,
        flank_manifest_sha256=flanks.manifest_sha256,
    )
    graph["inclusions"] = inclusions
    adjudication = _reseal_model(
        adjudication,
        cohort_manifest_sha256=cohort.manifest_sha256,
        flank_manifest_sha256=flanks.manifest_sha256,
        inclusion_manifest_sha256=inclusions.manifest_sha256,
    )
    graph["adjudication"] = adjudication
    public_loci = _reseal_model(
        public_loci,
        adjudication_manifest_sha256=adjudication.manifest_sha256,
    )
    graph["public_loci"] = public_loci
    public_assertions = _reseal_model(
        public_assertions,
        locus_membership_manifest_sha256=public_loci.manifest_sha256,
    )
    graph["public_assertions"] = public_assertions
    graph["activation"] = _reseal_model(
        activation,
        cohort_manifest_sha256=cohort.manifest_sha256,
        full_sequence_bundle_manifest_sha256=graph["sequence_bundle"].manifest_sha256,
        flank_request_plan_manifest_sha256=request_plan.manifest_sha256,
        flank_manifest_sha256=flanks.manifest_sha256,
        inclusion_manifest_sha256=inclusions.manifest_sha256,
        adjudication_manifest_sha256=adjudication.manifest_sha256,
        public_locus_membership_manifest_sha256=public_loci.manifest_sha256,
        public_assertion_membership_manifest_sha256=public_assertions.manifest_sha256,
    )


def _mutate_structured_layer(graph: dict[str, Any], layer: str) -> None:
    if layer == "request_plan":
        manifest = graph[layer]
        assert isinstance(manifest, FlankEvidenceRequestPlan)
        requests = list(manifest.requests)
        requests[0] = _reseal_model(
            requests[0],
            digest_field="request_sha256",
            source_record_key=f"{requests[0].source_record_key}:mutated",
        )
        graph[layer] = _reseal_model(manifest, requests=tuple(requests))
    elif layer == "flanks":
        manifest = graph[layer]
        assert isinstance(manifest, FlankEvidenceManifest)
        records = list(manifest.records)
        records[0] = _reseal_model(
            records[0],
            digest_field="record_sha256",
            assessed_by="method:mutated-flank-context-v1",
        )
        graph[layer] = _reseal_model(manifest, records=tuple(records))
    elif layer == "inclusions":
        manifest = graph[layer]
        assert isinstance(manifest, InclusionDecisionManifest)
        decisions = list(manifest.decisions)
        decisions[0] = _reseal_model(
            decisions[0],
            digest_field="decision_sha256",
            flank_record_sha256="f" * 64,
        )
        graph[layer] = _reseal_model(manifest, decisions=tuple(decisions))
    elif layer == "adjudication":
        manifest = graph[layer]
        assert isinstance(manifest, StructuredAdjudicationManifest)
        selections = list(manifest.selections)
        selections[0] = selections[0].model_copy(
            update={"source_record_key": f"{selections[0].source_record_key}:mutated"}
        )
        graph[layer] = _reseal_model(manifest, selections=tuple(selections))
    elif layer == "public_loci":
        manifest = graph[layer]
        assert isinstance(manifest, PublicLocusMembershipManifest)
        locus_memberships = list(manifest.memberships)
        locus_memberships[0] = locus_memberships[0].model_copy(
            update={"left_flank_record_sha256": "f" * 64}
        )
        graph[layer] = _reseal_model(
            manifest,
            memberships=tuple(locus_memberships),
        )
    elif layer == "public_assertions":
        manifest = graph[layer]
        assert isinstance(manifest, PublicAssertionMembershipManifest)
        assertion_memberships = list(manifest.memberships)
        assertion_memberships[0] = assertion_memberships[0].model_copy(
            update={"predicate_key": "source:mutated-predicate"}
        )
        graph[layer] = _reseal_model(
            manifest,
            memberships=tuple(assertion_memberships),
        )
    elif layer == "activation":
        manifest = graph[layer]
        assert isinstance(manifest, StructuredActivationManifest)
        counts = manifest.counts.model_copy(
            update={"adjudicated_records": manifest.counts.adjudicated_records + 1}
        )
        graph[layer] = _reseal_model(manifest, counts=counts)
    else:  # pragma: no cover - test helper contract
        raise AssertionError(f"unsupported structured layer: {layer}")

    _rechain_structured_envelopes(graph)
    changed = graph[layer]
    assert isinstance(changed, BaseModel)
    changed_payload = changed.model_dump(mode="python")
    changed_sha256 = changed_payload["manifest_sha256"]
    assert isinstance(changed_sha256, str)
    assert changed_sha256 == canonical_self_sha256(
        changed,
        "manifest_sha256",
    )


@pytest.mark.parametrize(
    ("layer", "error"),
    (
        ("request_plan", "exact deterministic cohort projection"),
        ("flanks", "exact cohort/flank policy projection"),
        ("inclusions", "exact cohort/flank policy projection"),
        ("adjudication", "exact inclusion-policy selection"),
        ("public_loci", "exact adjudicated include projection"),
        ("public_assertions", "predicates differ"),
        ("activation", "terminal counts differ"),
    ),
)
def test_structured_graph_rejects_resealed_field_level_mutation(
    tmp_path: Path,
    layer: str,
    error: str,
) -> None:
    graph = _structured_graph(tmp_path)

    _mutate_structured_layer(graph, layer)

    activation = graph["activation"]
    assert isinstance(activation, StructuredActivationManifest)
    assert activation.flank_request_plan_manifest_sha256 == graph["request_plan"].manifest_sha256
    assert activation.flank_manifest_sha256 == graph["flanks"].manifest_sha256
    assert activation.inclusion_manifest_sha256 == graph["inclusions"].manifest_sha256
    assert activation.adjudication_manifest_sha256 == graph["adjudication"].manifest_sha256
    assert (
        activation.public_locus_membership_manifest_sha256 == graph["public_loci"].manifest_sha256
    )
    assert (
        activation.public_assertion_membership_manifest_sha256
        == graph["public_assertions"].manifest_sha256
    )
    with pytest.raises(ActivationManifestPacketError, match=error):
        packet_module._validate_structured_graph(**graph)


def test_writer_is_canonical_and_never_overwrites(tmp_path: Path) -> None:
    packet = _synthetic_packet()
    output = tmp_path / "packet.json"

    identity = write_activation_manifest_packet(output, packet)

    expected = (canonical_model_json(packet) + "\n").encode("utf-8")
    assert output.read_bytes() == expected
    assert identity.byte_size == len(expected)
    assert identity.file_sha256 == hashlib.sha256(expected).hexdigest()
    with pytest.raises(ActivationManifestPacketError, match="already exists"):
        write_activation_manifest_packet(output, packet)


def test_verifier_binds_canonical_bytes_and_both_approval_hashes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    packet = _synthetic_packet()
    output = tmp_path / "packet.json"
    identity = write_activation_manifest_packet(output, packet)
    monkeypatch.setattr(
        packet_module,
        "build_activation_manifest_packet",
        lambda _root, _paths: packet,
    )

    verified = verify_activation_manifest_packet(
        tmp_path,
        Path("packet.json"),
        expected_packet_sha256=packet.packet_sha256,
        expected_file_sha256=identity.file_sha256,
    )

    assert verified == packet
    with pytest.raises(ActivationManifestPacketError, match="semantic checksum"):
        verify_activation_manifest_packet(
            tmp_path,
            Path("packet.json"),
            expected_packet_sha256="f" * 64,
            expected_file_sha256=identity.file_sha256,
        )
    with pytest.raises(ActivationManifestPacketError, match="physical file checksum"):
        verify_activation_manifest_packet(
            tmp_path,
            Path("packet.json"),
            expected_packet_sha256=packet.packet_sha256,
            expected_file_sha256="f" * 64,
        )


def test_verifier_rejects_noncanonical_bytes_even_with_matching_raw_hash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    packet = _synthetic_packet()
    output = tmp_path / "packet.json"
    pretty_bytes = (json.dumps(packet.model_dump(mode="json"), indent=2) + "\n").encode()
    output.write_bytes(pretty_bytes)
    monkeypatch.setattr(
        packet_module,
        "build_activation_manifest_packet",
        lambda _root, _paths: packet,
    )

    with pytest.raises(ActivationManifestPacketError, match="canonical JSON bytes"):
        verify_activation_manifest_packet(
            tmp_path,
            Path("packet.json"),
            expected_packet_sha256=packet.packet_sha256,
            expected_file_sha256=hashlib.sha256(pretty_bytes).hexdigest(),
        )


@pytest.mark.parametrize("drift", ("md5", "size"))
def test_excluded_taxdump_reasons_are_replayed_from_observed_bytes(
    drift: str,
) -> None:
    accepted = _raw("sources/taxdump.tar.gz", byte_size=10)
    excluded = (
        _raw("sources/taxdump-corrupt-md5.tar.gz", byte_size=11),
        _raw("sources/taxdump-corrupt-size.tar.gz", byte_size=12),
    )
    sources = FrozenSourceEvidence(
        m1_source_manifest=_raw("sources/m1-manifest.json"),
        m1_source_audit=_raw("sources/m1-audit.json"),
        ncbi_taxdump_archive=accepted,
        ncbi_taxdump_checksum=_raw("sources/taxdump.md5"),
        ictv_msl_workbook=_raw("sources/msl.xlsx"),
        ictv_vmr_workbook=_raw("sources/vmr.xlsx"),
        full_sequence_bundle=_raw("sources/sequences.json"),
        excluded_taxdump_candidates=(
            ExcludedSourceArtifact(
                reason_codes=(
                    "publisher_md5_mismatch",
                    "retrieved_byte_size_mismatch",
                ),
                raw_file=excluded[0],
                used_by_candidate=False,
            ),
            ExcludedSourceArtifact(
                reason_codes=(
                    "publisher_md5_mismatch",
                    "retrieved_byte_size_mismatch",
                ),
                raw_file=excluded[1],
                used_by_candidate=False,
            ),
        ),
    )
    publisher_md5 = "a" * 32
    observations = tuple(
        packet_module._FileObservation(
            raw_file=raw_file,
            content=None,
            md5="b" * 32,
        )
        for raw_file in excluded
    )
    if drift == "md5":
        observations = (
            packet_module._FileObservation(
                raw_file=excluded[0],
                content=None,
                md5=publisher_md5,
            ),
            observations[1],
        )
    else:
        observations = (
            packet_module._FileObservation(
                raw_file=excluded[0].model_copy(update={"byte_size": 10}),
                content=None,
                md5="b" * 32,
            ),
            observations[1],
        )

    with pytest.raises(ActivationManifestPacketError, match="rejection reasons"):
        packet_module._validate_frozen_sources(
            sources=sources,
            ncbi_artifact=cast(
                Any,
                SimpleNamespace(
                    archive=SimpleNamespace(
                        sha256=accepted.file_sha256,
                        byte_size=accepted.byte_size,
                        upstream_checksum=publisher_md5,
                    )
                ),
            ),
            ictv_artifact=cast(
                Any,
                SimpleNamespace(
                    msl=SimpleNamespace(
                        sha256=sources.ictv_msl_workbook.file_sha256,
                        byte_size=sources.ictv_msl_workbook.byte_size,
                    ),
                    corrected_vmr=SimpleNamespace(
                        sha256=sources.ictv_vmr_workbook.file_sha256,
                        byte_size=sources.ictv_vmr_workbook.byte_size,
                    ),
                ),
            ),
            sequence_bundle=cast(
                Any,
                SimpleNamespace(
                    artifact_sha256=sources.full_sequence_bundle.file_sha256,
                    artifact_byte_size=sources.full_sequence_bundle.byte_size,
                ),
            ),
            activation=cast(
                Any,
                SimpleNamespace(
                    source_manifest_sha256=sources.m1_source_manifest.file_sha256,
                    source_audit_sha256=sources.m1_source_audit.file_sha256,
                ),
            ),
            taxdump_md5=publisher_md5,
            checksum_content=f"{publisher_md5}  taxdump.tar.gz\n".encode(),
            excluded_observations=observations,
        )
