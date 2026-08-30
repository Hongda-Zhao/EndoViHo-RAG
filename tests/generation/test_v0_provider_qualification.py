"""Offline tests for the pre-registered fixed V0 provider qualification."""

from __future__ import annotations

import hashlib
import importlib
import json
from functools import lru_cache
from pathlib import Path

import pytest
from pydantic import ValidationError

from eve_relation_rag.generation.context import canonical_context_json
from eve_relation_rag.generation.qualification import (
    CURRENT_MODEL_POLICY_SHA256,
    CURRENT_PROMPT_POLICY_SHA256,
    CURRENT_PROVIDER_ENVIRONMENT_MANIFEST_SHA256,
    CURRENT_PROVIDER_ENVIRONMENT_SHA256,
    ProviderQualificationCandidate,
    ProviderQualificationClientRuntimeManifest,
    ProviderQualificationDefinition,
    ProviderQualificationError,
    ProviderQualificationObservation,
    ProviderQualificationReport,
    QualificationFileIdentity,
    RecordingGenerationProvider,
    build_fixed_qualification_context,
    build_provider_qualification_client_runtime_manifest,
    build_provider_qualification_definition,
    build_provider_qualification_dependency_projection,
    build_provider_qualification_report,
    load_provider_qualification_definition,
    verify_provider_qualification_client_runtime_manifest,
    verify_provider_qualification_report,
    write_provider_qualification_definition,
    write_provider_qualification_report,
)
from eve_relation_rag.hybrid.contracts import (
    ProviderIdentity,
    canonical_model_json,
    canonical_model_sha256,
    canonical_self_sha256,
)

_PROJECT_ROOT = Path(__file__).resolve(strict=True).parents[2]


def _file(relative_path: str, fill: str, *, byte_size: int = 10) -> QualificationFileIdentity:
    return QualificationFileIdentity(
        relative_path=relative_path,
        byte_size=byte_size,
        sha256=fill * 64,
    )


def _candidate() -> ProviderQualificationCandidate:
    return ProviderQualificationCandidate(
        provider_key="provider:local-openai-compatible:v1",
        model_key="model:hf:mlx-community:Qwen3-4B-Instruct-2507-4bit",
        model_revision="50d427756c6b1b2fe0c0a10f67fbda1fc8e82c1b",
        generation_policy_key="generation:local-json-zero-temp:v1",
        prompt_policy_key="answer:endoviho-rag:v0:grounded-document-claims-v1",
        model_policy_manifest_sha256=CURRENT_MODEL_POLICY_SHA256,
        prompt_policy_manifest_sha256=CURRENT_PROMPT_POLICY_SHA256,
        provider_environment_manifest_sha256=(CURRENT_PROVIDER_ENVIRONMENT_MANIFEST_SHA256),
        provider_environment_sha256=CURRENT_PROVIDER_ENVIRONMENT_SHA256,
        provider_environment_distribution_count=34,
        provider_environment_file_count=5638,
        runtime_launcher_sha256="1" * 64,
        runtime_proxy_sha256="2" * 64,
        inference_engine_wrapper_sha256="3" * 64,
        egress_profile_sha256="4" * 64,
        sandbox_executable_sha256="5" * 64,
        environment_executable_sha256="6" * 64,
        network_policy_key="network:macos-sandbox-v0-ports-only-v2",
        environment_policy_key="environment:scrubbed-allowlist-v1",
        inner_authentication_key="authentication:inherited-fd-bearer-v1",
        runtime_attestation_path="/v0/runtime-attestation",
        network_attestation_key=("egress-probe:external-and-unapproved-loopback-denied-v2"),
        outer_port=8123,
        inner_port=8124,
        timeout_seconds=300,
        retry_count=0,
        model_policy_file=_file(
            ".artifacts/v0_activation/manifests/v0_local_model_policy_manifest.v2.json",
            "7",
        ),
        prompt_policy_file=_file(
            ".artifacts/v0_activation/manifests/v0_prompt_policy_manifest.v2.json",
            "8",
        ),
        provider_environment_manifest_file=_file(
            ".artifacts/v0_activation/manifests/v0_provider_environment_manifest.json",
            "9",
        ),
    )


@lru_cache(maxsize=1)
def _client_runtime() -> ProviderQualificationClientRuntimeManifest:
    return build_provider_qualification_client_runtime_manifest(_PROJECT_ROOT)


def _runtime_file(relative_path: str) -> QualificationFileIdentity:
    return next(
        item for item in _client_runtime().source_files if item.relative_path == relative_path
    )


def _definition() -> ProviderQualificationDefinition:
    return build_provider_qualification_definition(
        candidate=_candidate(),
        runner_file=_runtime_file("scripts/run_v0_provider_qualification.py"),
        qualification_module_file=_runtime_file("src/eve_relation_rag/generation/qualification.py"),
        client_runtime_manifest=_client_runtime(),
    )


def _observation(**updates: object) -> ProviderQualificationObservation:
    context = build_fixed_qualification_context()
    context_bytes = canonical_context_json(context).encode("utf-8")
    values: dict[str, object] = {
        "started_at": "2026-08-29T01:02:03.000000Z",
        "completed_at": "2026-08-29T01:03:03.000000Z",
        "host_operating_system": "Darwin",
        "host_architecture": "arm64",
        "startup_duration_ns": 36_000_000_000,
        "generation_duration_ns": 5_000_000_000,
        "outer_ready": True,
        "inner_unauthenticated_status": 401,
        "hmac_runtime_attestation_verified": True,
        "provider_check_ready": True,
        "generation_request_count": 1,
        "retry_count": 0,
        "context_self_sha256": context.context_sha256,
        "context_canonical_sha256": hashlib.sha256(context_bytes).hexdigest(),
        "provider_output_sha256": "b" * 64,
        "provider_output_byte_size": 512,
        "composition_sha256": "c" * 64,
        "claim_count": 1,
        "citation_count": 1,
        "validation_scope": "mechanical",
        "clean_shutdown": True,
        "process_exit_code": 0,
        "outer_port_closed_after_shutdown": True,
        "inner_port_closed_after_shutdown": True,
    }
    values.update(updates)
    return ProviderQualificationObservation.model_validate(values)


def _definition_file(
    definition: ProviderQualificationDefinition | None = None,
) -> QualificationFileIdentity:
    trusted = _definition() if definition is None else definition
    raw = canonical_model_json(trusted).encode("utf-8") + b"\n"
    return QualificationFileIdentity(
        relative_path="candidate/provider_qualification_definition.json",
        byte_size=len(raw),
        sha256=hashlib.sha256(raw).hexdigest(),
    )


def test_fixed_context_matches_the_existing_canonical_synthetic_fixture() -> None:
    context = build_fixed_qualification_context()
    raw = canonical_context_json(context).encode("utf-8")

    assert context.original_question == (
        "Explain the literature evidence for the synthetic benchmark"
    )
    assert context.retrieved_chunks.chunks[0].text == (
        "The synthetic benchmark contains exact supporting evidence."
    )
    assert context.context_sha256 == (
        "ccc99956392ad9607d9e2218359587d61959500014a3651858e5b583e46b70dc"
    )
    assert hashlib.sha256(raw).hexdigest() == (
        "ad369f581ef8df74bc0dee5c3b691dd83bc46dac9b7a7bcfe1cc564a28bc5427"
    )
    assert len(raw) == 2601


def test_definition_is_exact_single_candidate_and_self_hashed() -> None:
    definition = _definition()

    assert len(definition.candidate_set) == 1
    assert definition.selection_rule == "only_passing_candidate"
    assert definition.target_operating_system == "Darwin"
    assert definition.target_architecture == "arm64"
    assert definition.definition_sha256 == canonical_self_sha256(definition, "definition_sha256")


def test_client_runtime_manifest_is_exact_sorted_unique_and_self_hashed() -> None:
    runtime = _client_runtime()
    paths = tuple(item.relative_path for item in runtime.source_files)
    distributions = tuple(item.distribution_name for item in runtime.distributions)

    assert paths == tuple(sorted(paths))
    assert len(paths) == len(set(paths)) == 28
    assert "src/eve_relation_rag/generation/composer.py" in paths
    assert "src/eve_relation_rag/generation/local_provider.py" in paths
    assert "src/eve_relation_rag/retrieval/structured/results.py" in paths
    assert "src/eve_relation_rag/retrieval/structured/rendering.py" in paths
    assert "src/eve_relation_rag/planning/query_plans.py" in paths
    assert "src/eve_relation_rag/planning/scope_policy.py" in paths
    assert "src/eve_relation_rag/domain/keys.py" in paths
    assert "scripts/v0_provider_environment.py" in paths
    assert "pyproject.toml" not in paths
    assert "uv.lock" not in paths
    assert distributions == ("pydantic", "pydantic-core")
    assert runtime.source_manifest_sha256 == canonical_model_sha256(runtime.source_files)
    assert runtime.manifest_sha256 == canonical_self_sha256(runtime, "manifest_sha256")


def _resealed_definition_with_runtime_mutation(
    mutation: str,
) -> ProviderQualificationDefinition:
    payload = _definition().model_dump(mode="json")
    runtime = payload["client_runtime_manifest"]
    if mutation == "source":
        source = next(
            item
            for item in runtime["source_files"]
            if item["relative_path"] == "src/eve_relation_rag/generation/composer.py"
        )
        source["sha256"] = "f" * 64
        runtime["source_manifest_sha256"] = canonical_model_sha256(runtime["source_files"])
    elif mutation == "structured_results_source":
        source = next(
            item
            for item in runtime["source_files"]
            if item["relative_path"] == "src/eve_relation_rag/retrieval/structured/results.py"
        )
        source["sha256"] = "d" * 64
        runtime["source_manifest_sha256"] = canonical_model_sha256(runtime["source_files"])
    elif mutation == "lock":
        projection = runtime["dependency_projection"]
        projection["uv_runtime_projection_sha256"] = "e" * 64
        projection["projection_sha256"] = canonical_self_sha256(
            projection,
            "projection_sha256",
        )
    elif mutation == "python":
        runtime["python"]["cache_tag"] = "cpython-312-mutated"
    else:  # pragma: no cover - test helper invariant.
        raise AssertionError(mutation)
    runtime["manifest_sha256"] = canonical_self_sha256(runtime, "manifest_sha256")
    payload["definition_sha256"] = canonical_self_sha256(payload, "definition_sha256")
    return ProviderQualificationDefinition.model_validate_json(json.dumps(payload))


@pytest.mark.parametrize(
    "mutation",
    ("source", "structured_results_source", "lock", "python"),
)
def test_resealed_client_runtime_drift_is_rejected_before_provider_start(
    mutation: str,
) -> None:
    resealed = _resealed_definition_with_runtime_mutation(mutation)

    with pytest.raises(ProviderQualificationError, match="does not replay"):
        verify_provider_qualification_client_runtime_manifest(
            resealed.client_runtime_manifest,
            project_root=_PROJECT_ROOT,
        )


@pytest.mark.parametrize(
    "module_name",
    (
        "eve_relation_rag.generation.local_provider",
        "eve_relation_rag.retrieval.structured.results",
    ),
)
def test_client_runtime_rejects_import_origin_mismatch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    module_name: str,
) -> None:
    module = importlib.import_module(module_name)
    monkeypatch.setattr(module, "__file__", str(tmp_path / "imposter.py"))

    with pytest.raises(ProviderQualificationError, match="import origin"):
        build_provider_qualification_client_runtime_manifest(_PROJECT_ROOT)


def test_dependency_projection_excludes_only_root_release_version() -> None:
    pyproject = (_PROJECT_ROOT / "pyproject.toml").read_bytes()
    uv_lock = (_PROJECT_ROOT / "uv.lock").read_bytes()
    original = build_provider_qualification_dependency_projection(pyproject, uv_lock)
    changed_pyproject = pyproject.replace(b'version = "0"', b'version = "0.1.0"', 1)
    changed_uv_lock = uv_lock.replace(
        b'name = "eve-relation-rag"\nversion = "0"',
        b'name = "eve-relation-rag"\nversion = "0.1.0"',
        1,
    )

    assert changed_pyproject != pyproject
    assert changed_uv_lock != uv_lock
    assert (
        build_provider_qualification_dependency_projection(
            changed_pyproject,
            changed_uv_lock,
        )
        == original
    )


def test_dependency_projection_changes_for_runtime_dependency_drift() -> None:
    pyproject = (_PROJECT_ROOT / "pyproject.toml").read_bytes()
    uv_lock = (_PROJECT_ROOT / "uv.lock").read_bytes()
    original = build_provider_qualification_dependency_projection(pyproject, uv_lock)

    changed_pyproject = pyproject.replace(b"httpx>=0.28,<1", b"httpx>=0.29,<1", 1)
    changed_uv_lock = uv_lock.replace(
        b'name = "pydantic"\nversion = "2.13.4"',
        b'name = "pydantic"\nversion = "2.13.5"',
        1,
    )

    assert (
        build_provider_qualification_dependency_projection(changed_pyproject, uv_lock) != original
    )
    assert (
        build_provider_qualification_dependency_projection(pyproject, changed_uv_lock) != original
    )


def test_definition_rejects_a_tampered_bound_runtime_hash() -> None:
    payload = _definition().model_dump(mode="json")
    payload["candidate_set"][0]["runtime_proxy_sha256"] = "f" * 64

    with pytest.raises(ValidationError, match="checksum"):
        ProviderQualificationDefinition.model_validate_json(json.dumps(payload))


def test_definition_secure_io_is_canonical_dual_hashed_and_create_only(
    tmp_path: Path,
) -> None:
    path = tmp_path / "definition.json"
    definition = _definition()
    written = write_provider_qualification_definition(path, definition)
    original = path.read_bytes()

    loaded = load_provider_qualification_definition(
        path,
        approved_definition_sha256=definition.definition_sha256,
        approved_file_sha256=written.sha256,
    )
    assert loaded == definition
    assert hashlib.sha256(original).hexdigest() == written.sha256
    with pytest.raises(ProviderQualificationError, match="new regular file"):
        write_provider_qualification_definition(path, definition)
    assert path.read_bytes() == original


def test_definition_loader_rejects_wrong_physical_hash_and_noncanonical_bytes(
    tmp_path: Path,
) -> None:
    path = tmp_path / "definition.json"
    definition = _definition()
    written = write_provider_qualification_definition(path, definition)
    with pytest.raises(ProviderQualificationError, match="both approved checksums"):
        load_provider_qualification_definition(
            path,
            approved_definition_sha256=definition.definition_sha256,
            approved_file_sha256="f" * 64,
        )

    path.write_bytes(path.read_bytes() + b"\n")
    changed_file_sha = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ProviderQualificationError, match="unavailable or invalid"):
        load_provider_qualification_definition(
            path,
            approved_definition_sha256=definition.definition_sha256,
            approved_file_sha256=changed_file_sha,
        )
    assert written.sha256 != changed_file_sha


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("host_operating_system", "Linux"),
        ("host_architecture", "x86_64"),
        ("startup_duration_ns", 300_000_000_001),
        ("generation_duration_ns", 300_000_000_001),
        ("outer_ready", False),
        ("inner_unauthenticated_status", 200),
        ("hmac_runtime_attestation_verified", False),
        ("provider_check_ready", False),
        ("generation_request_count", 2),
        ("retry_count", 1),
        ("context_self_sha256", "f" * 64),
        ("context_canonical_sha256", "f" * 64),
        ("claim_count", 0),
        ("citation_count", 0),
        ("validation_scope", "semantic"),
        ("clean_shutdown", False),
        ("process_exit_code", -15),
        ("outer_port_closed_after_shutdown", False),
        ("inner_port_closed_after_shutdown", False),
    ),
)
def test_report_builder_rejects_every_failed_gate(field: str, value: object) -> None:
    with pytest.raises(ProviderQualificationError, match="did not pass every frozen rule"):
        build_provider_qualification_report(
            _definition(),
            definition_file=_definition_file(),
            observation=_observation(**{field: value}),
        )


def test_passing_report_is_self_hashed_replayable_and_create_only(tmp_path: Path) -> None:
    definition = _definition()
    definition_file = _definition_file()
    report = build_provider_qualification_report(
        definition,
        definition_file=definition_file,
        observation=_observation(),
    )

    assert report.report_sha256 == canonical_self_sha256(report, "report_sha256")
    assert report.selection == "only_passing_candidate"
    assert report.client_runtime_manifest == definition.client_runtime_manifest
    assert report.candidate.model_policy_manifest_sha256 == CURRENT_MODEL_POLICY_SHA256
    assert (
        verify_provider_qualification_report(
            report,
            definition=definition,
            definition_file=definition_file,
        )
        == report
    )
    path = tmp_path / "report.json"
    written = write_provider_qualification_report(path, report)
    assert written.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ProviderQualificationError, match="new regular file"):
        write_provider_qualification_report(path, report)


def test_replay_rejects_a_resealed_report_bound_to_a_different_candidate() -> None:
    definition = _definition()
    definition_file = _definition_file()
    report = build_provider_qualification_report(
        definition,
        definition_file=definition_file,
        observation=_observation(),
    )
    payload = report.model_dump(mode="json")
    payload["candidate"]["runtime_proxy_sha256"] = "e" * 64
    payload["report_sha256"] = canonical_self_sha256(payload, "report_sha256")
    resealed = ProviderQualificationReport.model_validate_json(json.dumps(payload))

    with pytest.raises(ProviderQualificationError, match="does not replay"):
        verify_provider_qualification_report(
            resealed,
            definition=definition,
            definition_file=definition_file,
        )


class _StaticProvider:
    def __init__(self) -> None:
        self._identity = ProviderIdentity(
            provider_key="provider:tests:qualification",
            model_key="model:tests:qualification",
            model_revision="revision:tests:qualification",
            provider_artifact_sha256=None,
            generation_policy_key="generation:tests:qualification",
            prompt_policy_key="prompt:tests:qualification",
            prompt_policy_sha256="f" * 64,
            temperature=0,
            max_output_bytes=32768,
            timeout_seconds=5,
            retry_count=0,
        )

    @property
    def identity(self) -> ProviderIdentity:
        return self._identity

    def generate(self, context_json: str) -> str:
        assert context_json
        return '{"fixed":"output"}'


def test_recording_wrapper_captures_exact_bytes_and_forbids_a_second_request() -> None:
    provider = RecordingGenerationProvider(_StaticProvider())
    context_json = canonical_context_json(build_fixed_qualification_context())

    assert provider.generate(context_json) == '{"fixed":"output"}'
    assert provider.record.request_count == 1
    assert (
        provider.record.context_canonical_sha256
        == hashlib.sha256(context_json.encode("utf-8")).hexdigest()
    )
    assert (
        provider.record.provider_output_sha256 == hashlib.sha256(b'{"fixed":"output"}').hexdigest()
    )
    with pytest.raises(ProviderQualificationError, match="exactly one"):
        provider.generate(context_json)
