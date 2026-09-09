"""Live, experiment-only Phase 3 authority using the existing production gates.

No serialized receipt authorizes execution. A short-lived, single-use handle is
issued only after replaying the exact approved preflight, SELECT-only role audit
and applicable published-release gates against an explicit isolated local engine.
This authorizes dependency construction, not publication of trusted results.
"""

from __future__ import annotations

import hashlib
import time
import weakref
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal, Self

from pydantic import Field, model_validator
from sqlalchemy import Engine, text

from eve_relation_rag.experiments.rag_value_ablation.literature_adapter import (
    BgeAssetFiles,
    verify_bge_assets,
)
from eve_relation_rag.experiments.rag_value_ablation.preflight import (
    Phase3PreflightDecision,
    Phase3PreflightInput,
    Phase3SystemKey,
    is_issued_phase3_preflight_decision,
    run_phase3_preflight,
)
from eve_relation_rag.experiments.rag_value_ablation.raw_context import (
    RawContextFiles,
    load_raw_context,
)
from eve_relation_rag.literature.contracts import (
    Rfc3339Utc,
    Sha256,
    StableToken,
    StrictFrozenSchema,
)
from eve_relation_rag.literature.gate import PublishedCorpusGate
from eve_relation_rag.literature.hashing import canonical_json_bytes, canonical_json_sha256
from eve_relation_rag.operations.database_role import audit_database_runtime_role
from eve_relation_rag.retrieval.structured.gate import PublishedReleaseGate


class ExecutionGateError(ValueError):
    """A sanitized failure; never echo a URL, credential or source payload."""


class Phase3ExecutionRequest(StrictFrozenSchema):
    """Externally pinned operator request; no approval or endpoint discovery."""

    schema_version: Literal["rag-value-phase3-execution-request-v1"]
    experiment_namespace: str = Field(pattern=r"^rag-value-ablation:[a-z0-9][a-z0-9:-]*$")
    preflight_input_sha256: Sha256
    system_keys: tuple[Phase3SystemKey, ...] = Field(min_length=1)
    database_name: str = Field(pattern=r"^endoviho_rag_value_[a-z0-9_]+$")
    database_role: str = Field(pattern=r"^rag_value_[a-z0-9_]+$")
    operator_key: StableToken
    authorized_at: Rfc3339Utc
    expires_at: Rfc3339Utc
    authorization_text: Literal[
        "I authorize this exact isolated, read-only Phase 3 request; no production change."
    ]
    network_policy: Literal["loopback-postgresql-only"]
    generation_allowed: Literal[False]
    output_trust_authorized: Literal[False]
    raw_context_files: RawContextFiles | None = None
    bge_asset_files: BgeAssetFiles | None = None

    @model_validator(mode="after")
    def validate_order_and_time(self) -> Self:
        if self.system_keys != tuple(sorted(set(self.system_keys))):
            raise ValueError("requested systems must be unique and sorted")
        start, end = _timestamp(self.authorized_at), _timestamp(self.expires_at)
        if not 0 < (end - start).total_seconds() <= 24 * 60 * 60:
            raise ValueError("runtime approval must expire within one day")
        return self


_ISSUER = object()


@dataclass(frozen=True, slots=True, weakref_slot=True)
class Phase3ExecutionAuthority:
    """Issuer-owned ephemeral lease bound to one engine, request and decision."""

    request: Phase3ExecutionRequest
    preflight_report_sha256: str
    live_fingerprint: str
    _engine: Engine = field(repr=False, compare=False)
    _evidence: Phase3PreflightInput = field(repr=False, compare=False)
    _deadline: float = field(repr=False, compare=False)
    _issuer: object = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._issuer is not _ISSUER:
            raise TypeError("execution authority must be issued by the live gate")


_ISSUED: dict[int, weakref.ReferenceType[Phase3ExecutionAuthority]] = {}


def _timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _validate_target(engine: Engine, request: Phase3ExecutionRequest) -> None:
    if (
        type(engine) is not Engine
        or engine.url.get_backend_name() != "postgresql"
        or engine.url.host not in {"127.0.0.1", "::1"}
        or engine.url.database != request.database_name
        or engine.url.username != request.database_role
        or bool(engine.url.query)
    ):
        raise ExecutionGateError("an exact isolated loopback PostgreSQL engine is required")
    if ("S1" in request.system_keys) != (request.raw_context_files is not None):
        raise ExecutionGateError(
            "S1 requires exact raw artifact paths; other systems cannot use them"
        )
    if ("S3" in request.system_keys) != (request.bge_asset_files is not None):
        raise ExecutionGateError(
            "S3 requires exact BGE artifact paths; other systems cannot use them"
        )
    now = datetime.now(UTC)
    if not _timestamp(request.authorized_at) <= now < _timestamp(request.expires_at):
        raise ExecutionGateError("runtime approval is not currently valid")


def _live_fingerprint(
    engine: Engine,
    request: Phase3ExecutionRequest,
    evidence: Phase3PreflightInput,
) -> str:
    """Replay real privilege and release checks without writes or model construction."""

    _validate_target(engine, request)
    identities = _artifact_fingerprint(request, evidence)
    with engine.connect().execution_options(postgresql_readonly=True) as connection:
        row = connection.execute(
            text(
                "SELECT current_database(), current_user, "
                "current_setting('transaction_read_only'), "
                "has_database_privilege(current_user, current_database(), 'TEMP')"
            )
        ).one()
        if tuple(row) != (request.database_name, request.database_role, "on", False):
            raise ExecutionGateError("live connection identity or read-only state differs")
    audit = audit_database_runtime_role(engine)
    if not audit.runtime_readonly:
        raise ExecutionGateError("live database role is not SELECT-only")
    audit_hash = canonical_json_sha256(audit.model_dump(mode="json"))
    if audit_hash != evidence.database_role.audit.observed_sha256:
        raise ExecutionGateError("live role audit differs from the approved preflight")
    identities["role_audit_sha256"] = audit_hash
    systems = set(request.system_keys)
    if systems & {"S1", "S4"}:
        release = PublishedReleaseGate(engine).authorize(evidence.dataset_release.release_key)
        observed = (release.release_key, release.manifest_sha256, release.validation_receipt_sha256)
        expected = (
            evidence.dataset_release.release_key,
            evidence.dataset_release.manifest.observed_sha256,
            evidence.dataset_release.validation_receipt.observed_sha256,
        )
        if observed != expected:
            raise ExecutionGateError("live DatasetRelease differs from approved evidence")
        identities["dataset"] = observed
    if systems & {"S1", "S2", "S3"}:
        corpus = PublishedCorpusGate(engine).authorize(evidence.corpus_release.release_key)
        observed = (
            corpus.corpus_release_key,
            corpus.manifest_sha256,
            corpus.validation_receipt_sha256,
        )
        expected = (
            evidence.corpus_release.release_key,
            evidence.corpus_release.manifest.observed_sha256,
            evidence.corpus_release.validation_receipt.observed_sha256,
        )
        if observed != expected:
            raise ExecutionGateError("live CorpusRelease differs from approved evidence")
        if "S3" in systems and (
            corpus.model_artifact_manifest_sha256 != identities["bge_artifact_sha256"]
        ):
            raise ExecutionGateError("live corpus BGE model differs from verified local assets")
        identities["corpus"] = observed
    return canonical_json_sha256(identities)


def _artifact_fingerprint(
    request: Phase3ExecutionRequest,
    evidence: Phase3PreflightInput,
) -> dict[str, object]:
    """Rehash actual pinned bytes on both issuance and consumption, before database I/O."""
    identities: dict[str, object] = {}
    if request.raw_context_files is not None:
        raw = evidence.raw_context
        if any(
            value is None
            for value in (
                raw.material_manifest.approved_sha256,
                raw.construction_policy.approved_sha256,
                raw.tokenizer_artifact.approved_sha256,
            )
        ):
            raise ExecutionGateError("approved S1 artifact identities are missing")
        manifest, policy, segments = load_raw_context(
            request.raw_context_files,
            approved_material_sha256=str(raw.material_manifest.approved_sha256),
            approved_policy_sha256=str(raw.construction_policy.approved_sha256),
            approved_tokenizer_sha256=str(raw.tokenizer_artifact.approved_sha256),
        )
        if (
            manifest.dataset_release_key,
            manifest.dataset_manifest_sha256,
            manifest.corpus_release_key,
            manifest.corpus_manifest_sha256,
            policy.tokenizer_id,
            policy.tokenizer_revision,
            policy.tokenizer_artifact_manifest_sha256,
            policy.model_context_limit_tokens,
            policy.reserved_output_tokens,
        ) != (
            raw.dataset_release_key,
            raw.dataset_manifest_sha256,
            raw.corpus_release_key,
            raw.corpus_manifest_sha256,
            raw.tokenizer_id,
            raw.tokenizer_revision,
            raw.tokenizer_artifact_manifest_sha256,
            raw.model_context_limit_tokens,
            raw.reserved_output_tokens,
        ):
            raise ExecutionGateError("S1 artifacts differ from the exact preflight bindings")
        identities["raw_context"] = (
            manifest.manifest_sha256,
            policy.policy_sha256,
            tuple(segment.text_sha256 for segment in segments),
        )
    if request.bge_asset_files is not None:
        expected = evidence.retrieval.bge_artifact.approved_sha256
        if expected is None:
            raise ExecutionGateError("approved BGE artifact identity is missing")
        identities["bge_artifact_sha256"] = verify_bge_assets(request.bge_asset_files, expected)
    return identities


def issue_phase3_execution_authority(
    *,
    engine: Engine,
    evidence: Phase3PreflightInput,
    decision: Phase3PreflightDecision,
    request: Phase3ExecutionRequest,
    approved_request_file_sha256: str,
) -> Phase3ExecutionAuthority:
    """The operator pins canonical request bytes outside the executor process."""

    if type(request) is not Phase3ExecutionRequest or type(evidence) is not Phase3PreflightInput:
        raise ExecutionGateError("exact runtime request and preflight types are required")
    request = Phase3ExecutionRequest.model_validate_json(canonical_json_bytes(request))
    evidence = Phase3PreflightInput.model_validate_json(canonical_json_bytes(evidence))
    request_bytes = canonical_json_bytes(request) + b"\n"
    if hashlib.sha256(request_bytes).hexdigest() != approved_request_file_sha256:
        raise ExecutionGateError("runtime request differs from the externally approved bytes")
    if not is_issued_phase3_preflight_decision(decision):
        raise ExecutionGateError("an issued preflight decision is required")
    replay = run_phase3_preflight(evidence)
    if (
        replay.report != decision.report
        or request.preflight_input_sha256 != evidence.input_sha256
        or any(
            not item.ready
            for item in replay.report.systems
            if item.system_key in request.system_keys
        )
    ):
        raise ExecutionGateError("runtime request does not match passing preflight evidence")
    try:
        fingerprint = _live_fingerprint(engine, request, evidence)
    except Exception:
        raise ExecutionGateError("live isolated runtime verification failed") from None
    authority = Phase3ExecutionAuthority(
        request=request,
        preflight_report_sha256=decision.report.report_sha256,
        live_fingerprint=fingerprint,
        _engine=engine,
        _evidence=evidence,
        _deadline=time.monotonic() + 60,
        _issuer=_ISSUER,
    )
    identity = id(authority)

    def discard(reference: weakref.ReferenceType[Phase3ExecutionAuthority]) -> None:
        if _ISSUED.get(identity) is reference:
            _ISSUED.pop(identity, None)

    _ISSUED[identity] = weakref.ref(authority, discard)
    return authority


def consume_phase3_execution_authority[T](
    authority: Phase3ExecutionAuthority,
    decision: Phase3PreflightDecision,
    factory: Callable[[], T],
) -> T:
    """Consume once, recheck live state, then invoke the internal dependency factory."""

    if type(authority) is not Phase3ExecutionAuthority:
        raise ExecutionGateError("an issued runtime authority is required")
    reference = _ISSUED.pop(id(authority), None)
    if reference is None or reference() is not authority or authority._issuer is not _ISSUER:
        raise ExecutionGateError("runtime authority is forged, copied or already consumed")
    if (
        not is_issued_phase3_preflight_decision(decision)
        or decision.report.report_sha256 != authority.preflight_report_sha256
        or time.monotonic() >= authority._deadline
    ):
        raise ExecutionGateError("runtime authority is expired or bound to another decision")
    try:
        fingerprint = _live_fingerprint(authority._engine, authority.request, authority._evidence)
    except Exception:
        raise ExecutionGateError("runtime changed before dependency construction") from None
    if fingerprint != authority.live_fingerprint:
        raise ExecutionGateError("runtime fingerprint changed before dependency construction")
    return factory()
