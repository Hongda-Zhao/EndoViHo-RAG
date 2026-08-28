from __future__ import annotations

import subprocess
import sys

from eve_relation_rag.literature.providers import EmbeddingProvider


class DeterministicFakeProvider:
    @property
    def model_key(self) -> str:
        return "embedding:test:deterministic-3d-v1"

    @property
    def dimension(self) -> int:
        return 3

    @property
    def artifact_manifest_sha256(self) -> str:
        return "a" * 64

    def embed_documents(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return tuple((float(len(text)), 0.0, 1.0) for text in texts)

    def embed_query(self, text: str) -> tuple[float, ...]:
        return (float(len(text)), 1.0, 0.0)


class MissingQueryMethod:
    @property
    def model_key(self) -> str:
        return "embedding:test:incomplete"

    @property
    def dimension(self) -> int:
        return 3

    def embed_documents(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return ()


class MissingArtifactIdentity:
    @property
    def model_key(self) -> str:
        return "embedding:test:incomplete"

    @property
    def dimension(self) -> int:
        return 3

    def embed_documents(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return ()

    def embed_query(self, text: str) -> tuple[float, ...]:
        return (float(len(text)), 1.0, 0.0)


def test_embedding_provider_is_a_runtime_checkable_structural_protocol() -> None:
    provider = DeterministicFakeProvider()

    assert isinstance(provider, EmbeddingProvider)
    assert not isinstance(MissingQueryMethod(), EmbeddingProvider)
    assert not isinstance(MissingArtifactIdentity(), EmbeddingProvider)
    assert provider.artifact_manifest_sha256 == "a" * 64
    assert provider.embed_documents(("a", "bb"))[1][0] == 2.0
    assert provider.embed_query("abc")[0] == 3.0


def test_provider_contract_cold_import_has_no_model_or_network_dependency() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from eve_relation_rag.literature.providers import EmbeddingProvider; "
            "assert 'sentence_transformers' not in sys.modules; "
            "assert 'transformers' not in sys.modules; print(EmbeddingProvider.__name__)",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert completed.stdout.strip() == "EmbeddingProvider"
    assert completed.stderr == ""
