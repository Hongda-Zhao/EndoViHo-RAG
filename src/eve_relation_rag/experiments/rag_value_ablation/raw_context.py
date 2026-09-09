"""Checksum-bound, whole-file S1 context in manifest order, with no retrieval or truncation."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, model_validator

from eve_relation_rag.experiments.rag_value_ablation.contracts import (
    RawContextPolicy,
    RawContextSegment,
)
from eve_relation_rag.experiments.rag_value_ablation.local_generation import (
    CONTEXT_LIMIT,
    MODEL_ID,
    MODEL_REVISION,
    OUTPUT_LIMIT,
    TOKENIZER_KEY,
    LocalGenerationConfig,
    generation_identity,
    verify_generation_assets,
)
from eve_relation_rag.literature.contracts import (
    CorpusManifest,
    RelativePath,
    Sha256,
    StableToken,
    StrictFrozenSchema,
)
from eve_relation_rag.literature.hashing import canonical_json_sha256


class RawContextError(ValueError):
    """A raw source or construction policy differs from the explicit approved bundle."""


class RawMaterialSource(StrictFrozenSchema):
    source_kind: Literal["structured_export", "document"]
    source_key: StableToken
    relative_path: RelativePath
    source_sha256: Sha256
    byte_size: int = Field(gt=0, le=52_428_800)


class RawMaterialManifest(StrictFrozenSchema):
    schema_version: Literal["rag-value-raw-material-manifest-v1"]
    dataset_release_key: StableToken
    dataset_manifest_sha256: Sha256
    corpus_release_key: StableToken
    corpus_manifest_sha256: Sha256
    sources: tuple[RawMaterialSource, ...] = Field(min_length=2)
    manifest_sha256: Sha256

    @model_validator(mode="after")
    def validate_manifest(self) -> Self:
        if tuple(s.source_kind for s in self.sources) != (
            "structured_export",
            *("document" for _ in self.sources[1:]),
        ):
            raise ValueError("raw sources require one structured export followed by documents")
        for attribute in ("relative_path", "source_key"):
            values = tuple(getattr(s, attribute) for s in self.sources)
            if len(values) != len(set(values)):
                raise ValueError("raw source paths and identities must be unique")
        if self.manifest_sha256 != canonical_json_sha256(
            self.model_dump(mode="json", exclude={"manifest_sha256"})
        ):
            raise ValueError("raw material manifest checksum mismatch")
        return self


class RawContextFiles(StrictFrozenSchema):
    """Explicit paths; approved identities live in the independently pinned request/preflight."""

    material_root: str
    material_manifest_path: str
    corpus_manifest_path: str
    construction_policy_path: str
    model_root: str
    model_policy_path: str
    model_policy_file_sha256: Sha256


def load_raw_context(
    files: RawContextFiles,
    *,
    approved_material_sha256: str,
    approved_policy_sha256: str,
    approved_tokenizer_sha256: str,
) -> tuple[RawMaterialManifest, RawContextPolicy, tuple[RawContextSegment, ...]]:
    """Verify all sources before returning any context; retain original bytes and source order."""
    manifest = RawMaterialManifest.model_validate_json(
        _read_regular(Path(files.material_manifest_path))
    )
    policy = RawContextPolicy.model_validate_json(
        _read_regular(Path(files.construction_policy_path))
    )
    corpus = CorpusManifest.model_validate_json(_read_regular(Path(files.corpus_manifest_path)))
    if (
        manifest.manifest_sha256 != approved_material_sha256
        or policy.policy_sha256 != approved_policy_sha256
        or policy.source_manifest_sha256 != manifest.manifest_sha256
        or policy.structured_export_sha256 != manifest.sources[0].source_sha256
        or policy.document_manifest_sha256 != corpus.manifest_sha256
        or (manifest.corpus_release_key, manifest.corpus_manifest_sha256)
        != (corpus.corpus_release_key, corpus.manifest_sha256)
    ):
        raise RawContextError("raw bundle differs from approved manifest or policy")
    expected = tuple(
        (d.expected_document_key, d.source_sha256, d.byte_size) for d in corpus.documents
    )
    observed = tuple((s.source_key, s.source_sha256, s.byte_size) for s in manifest.sources[1:])
    if observed != expected:
        raise RawContextError("raw documents differ from the complete corpus manifest order")
    if any(
        not d.retrieval_text_allowed or d.license_review_status != "approved"
        for d in corpus.documents
    ):
        raise RawContextError("raw document lacks approved text-use permission")
    # Reuse full model/tokenizer file verification; no model is loaded here.
    model = verify_generation_assets(
        LocalGenerationConfig(
            model_root=Path(files.model_root),
            model_policy_path=Path(files.model_policy_path),
            model_policy_file_sha256=files.model_policy_file_sha256,
            python_executable=Path("unused"),
            worker_script=Path("unused"),
        )
    )
    identity = generation_identity(model)
    if (
        policy.final_partial_segment_allowed
        or policy.model_context_limit_tokens != CONTEXT_LIMIT
        or policy.reserved_output_tokens != OUTPUT_LIMIT
        or policy.tokenizer_key != TOKENIZER_KEY
        or policy.tokenizer_id != MODEL_ID
        or policy.tokenizer_revision != MODEL_REVISION
        or policy.tokenizer_artifact_manifest_sha256 != approved_tokenizer_sha256
        or approved_tokenizer_sha256 != identity.tokenizer_artifact_manifest_sha256
        # Segments remain objects in a canonical JSON array, separated by comma bytes.
        or policy.separator_sha256 != hashlib.sha256(b",").hexdigest()
    ):
        raise RawContextError("S1 construction must use the fixed whole-material tokenizer policy")
    root = Path(files.material_root)
    if root.is_symlink() or not root.is_dir():
        raise RawContextError("raw material root is invalid")
    segments = []
    for index, source in enumerate(manifest.sources, 1):
        path = root
        for part in Path(source.relative_path).parts:
            path = path / part
            if path.is_symlink():
                raise RawContextError("raw source contains a symlink component")
        if not path.resolve().is_relative_to(root.resolve()):
            raise RawContextError("raw source escapes its material root")
        raw = _read_regular(path)
        if len(raw) != source.byte_size or hashlib.sha256(raw).hexdigest() != source.source_sha256:
            raise RawContextError("raw source bytes differ from the pinned manifest")
        segments.append(
            RawContextSegment(
                segment_id=f"R{index}",
                source_kind=source.source_kind,
                source_key=source.source_key,
                source_sha256=source.source_sha256,
                byte_start=0,
                byte_end=len(raw),
                text=raw.decode("utf-8"),
                text_sha256=source.source_sha256,
            )
        )
    return manifest, policy, tuple(segments)


def _read_regular(path: Path) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise RawContextError("explicit artifact is not a regular file")
    return path.read_bytes()
