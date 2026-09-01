"""Verified, local-only Transformers adapters used only by retrieval ablations."""

from __future__ import annotations

import importlib
import json
import math
from collections.abc import Sequence
from typing import Any

from eve_relation_rag.experiments.embedding_ablation.artifacts import (
    VerifiedModelArtifact,
    is_verified_artifact,
)
from eve_relation_rag.experiments.embedding_ablation.offline import offline_model_call
from eve_relation_rag.experiments.embedding_ablation.providers import (
    EmbeddingPassageBatchTelemetry,
    EmbeddingQueryTelemetry,
    RerankerBatchTelemetry,
)

MEDCPT_QUERY_MODEL_ID = "ncbi/MedCPT-Query-Encoder"
MEDCPT_ARTICLE_MODEL_ID = "ncbi/MedCPT-Article-Encoder"
MEDCPT_CROSS_ENCODER_MODEL_ID = "ncbi/MedCPT-Cross-Encoder"
QWEN3_EMBEDDING_MODEL_ID = "Qwen/Qwen3-Embedding-0.6B"
QWEN3_RERANKER_MODEL_ID = "Qwen/Qwen3-Reranker-0.6B"
QWEN3_RETRIEVAL_INSTRUCTION = (
    "Given a web search query, retrieve relevant passages that answer the query"
)


class LocalModelAdapterError(RuntimeError):
    """Raised before or during a local model call when an exact contract is violated."""


def serialize_medcpt_article_passage(title: str, text: str) -> str:
    """Serialize title/chunk inputs canonically across the existing provider boundary."""

    if not title.strip() or not text.strip():
        raise LocalModelAdapterError("MedCPT article title and text must be non-empty")
    return json.dumps(
        {"text": text, "title": title},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


class _VerifiedAdapter:
    def __init__(self, artifact: VerifiedModelArtifact, expected_model_id: str) -> None:
        if not is_verified_artifact(artifact):
            raise LocalModelAdapterError("model adapter requires a verifier-issued artifact")
        if artifact.manifest.model_id != expected_model_id:
            raise LocalModelAdapterError("verified artifact model ID is incompatible")
        if not artifact.manifest.local_files_only or artifact.manifest.trust_remote_code:
            raise LocalModelAdapterError("verified artifact violates the local runtime policy")
        self._artifact = artifact

    @property
    def model_key(self) -> str:
        return self._artifact.manifest.model_key

    @property
    def artifact_manifest_sha256(self) -> str:
        return self._artifact.artifact_manifest_sha256


class _EmbeddingAdapter(_VerifiedAdapter):
    def __init__(self, artifact: VerifiedModelArtifact, expected_model_id: str) -> None:
        super().__init__(artifact, expected_model_id)
        dimension = artifact.manifest.representation.dimension
        if artifact.manifest.representation.task_kind != "embedding" or dimension is None:
            raise LocalModelAdapterError("embedding adapter requires embedding metadata")
        self._dimension = dimension
        self._last_query_telemetry: EmbeddingQueryTelemetry | None = None
        self._last_passage_telemetry: EmbeddingPassageBatchTelemetry | None = None

    @property
    def dimension(self) -> int:
        return self._dimension

    def consume_last_query_telemetry(self) -> EmbeddingQueryTelemetry:
        telemetry = self._last_query_telemetry
        self._last_query_telemetry = None
        if telemetry is None:
            raise LocalModelAdapterError("query telemetry is unavailable or already consumed")
        return telemetry

    def consume_last_passage_batch_telemetry(self) -> EmbeddingPassageBatchTelemetry:
        telemetry = self._last_passage_telemetry
        self._last_passage_telemetry = None
        if telemetry is None:
            raise LocalModelAdapterError("passage telemetry is unavailable or already consumed")
        return telemetry


class _RerankerAdapter(_VerifiedAdapter):
    def __init__(self, artifact: VerifiedModelArtifact, expected_model_id: str) -> None:
        super().__init__(artifact, expected_model_id)
        if artifact.manifest.representation.task_kind != "reranker":
            raise LocalModelAdapterError("reranker adapter requires reranker metadata")
        self._last_batch_telemetry: RerankerBatchTelemetry | None = None

    def consume_last_batch_telemetry(self) -> RerankerBatchTelemetry:
        telemetry = self._last_batch_telemetry
        self._last_batch_telemetry = None
        if telemetry is None:
            raise LocalModelAdapterError("reranker telemetry is unavailable or already consumed")
        return telemetry


class MedCptQueryEmbeddingProvider(_EmbeddingAdapter):
    """Official 768-dimensional MedCPT query encoder (CLS, no normalization)."""

    def __init__(self, artifact: VerifiedModelArtifact) -> None:
        super().__init__(artifact, MEDCPT_QUERY_MODEL_ID)
        representation = artifact.manifest.representation
        if (
            self.dimension != 768
            or representation.pooling != "cls"
            or representation.normalization != "none"
            or representation.similarity != "dot_product"
            or representation.max_sequence_length != 64
        ):
            raise LocalModelAdapterError("MedCPT query representation contract is incompatible")
        self._torch, self._tokenizer, self._model = _load_encoder(artifact)

    def embed_query(self, text: str) -> tuple[float, ...]:
        vectors, truncated = self._encode((text,))
        self._last_query_telemetry = EmbeddingQueryTelemetry(
            truncated_query_count=int(truncated[0] > 0),
            truncated_query_tokens=truncated[0],
        )
        return vectors[0]

    def embed_documents(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        vectors, truncated = self._encode(tuple(texts))
        self._last_passage_telemetry = EmbeddingPassageBatchTelemetry(
            passage_count=len(vectors),
            truncated_passage_count=sum(value > 0 for value in truncated),
            truncated_passage_tokens=sum(truncated),
        )
        return vectors

    def _encode(
        self, texts: tuple[str, ...]
    ) -> tuple[tuple[tuple[float, ...], ...], tuple[int, ...]]:
        if not texts or any(not text.strip() for text in texts):
            raise LocalModelAdapterError("MedCPT query inputs must be non-empty")
        maximum = self._artifact.manifest.representation.max_sequence_length
        truncated = _single_sequence_truncation(self._tokenizer, texts, maximum)
        encoded = self._tokenizer(
            list(texts),
            padding=True,
            truncation=True,
            max_length=maximum,
            return_tensors="pt",
        )
        with self._torch.inference_mode():
            vectors = self._model(**encoded).last_hidden_state[:, 0, :].float().cpu()
        return _tensor_rows(vectors), truncated


class MedCptArticleEmbeddingProvider(_EmbeddingAdapter):
    """Official 768-dimensional MedCPT article encoder over title/chunk pairs."""

    def __init__(self, artifact: VerifiedModelArtifact) -> None:
        super().__init__(artifact, MEDCPT_ARTICLE_MODEL_ID)
        representation = artifact.manifest.representation
        if (
            self.dimension != 768
            or representation.pooling != "cls"
            or representation.normalization != "none"
            or representation.similarity != "dot_product"
            or representation.max_sequence_length != 512
        ):
            raise LocalModelAdapterError("MedCPT article representation contract is incompatible")
        self._torch, self._tokenizer, self._model = _load_encoder(artifact)

    def embed_documents(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        pairs = tuple(_parse_medcpt_article_passage(value) for value in texts)
        if not pairs:
            raise LocalModelAdapterError("MedCPT article batch must be non-empty")
        maximum = self._artifact.manifest.representation.max_sequence_length
        titles = tuple(title for title, _text in pairs)
        passages = tuple(text for _title, text in pairs)
        truncated = _pair_total_truncation(self._tokenizer, titles, passages, maximum)
        encoded = self._tokenizer(
            list(titles),
            list(passages),
            padding=True,
            truncation=True,
            max_length=maximum,
            return_tensors="pt",
        )
        with self._torch.inference_mode():
            vectors = self._model(**encoded).last_hidden_state[:, 0, :].float().cpu()
        rows = _tensor_rows(vectors)
        self._last_passage_telemetry = EmbeddingPassageBatchTelemetry(
            passage_count=len(rows),
            truncated_passage_count=sum(value > 0 for value in truncated),
            truncated_passage_tokens=sum(truncated),
        )
        return rows

    def embed_query(self, text: str) -> tuple[float, ...]:
        raise LocalModelAdapterError("MedCPT article encoder cannot encode retrieval queries")


class Qwen3EmbeddingProvider(_EmbeddingAdapter):
    """Qwen3 last-token embeddings truncated by MRL to 384 dimensions and normalized."""

    def __init__(self, artifact: VerifiedModelArtifact) -> None:
        super().__init__(artifact, QWEN3_EMBEDDING_MODEL_ID)
        representation = artifact.manifest.representation
        if (
            self.dimension != 384
            or representation.pooling != "last_token_then_mrl_prefix_384"
            or representation.normalization != "l2"
            or representation.similarity != "cosine"
            or representation.max_sequence_length != 512
        ):
            raise LocalModelAdapterError("Qwen3 embedding representation contract is incompatible")
        self._torch, self._tokenizer, self._model = _load_encoder(
            artifact, padding_side="left", dtype="auto", attn_implementation="eager"
        )

    def embed_query(self, text: str) -> tuple[float, ...]:
        formatted = f"Instruct: {QWEN3_RETRIEVAL_INSTRUCTION}\nQuery:{text}"
        rows, truncated = self._encode((formatted,))
        self._last_query_telemetry = EmbeddingQueryTelemetry(
            truncated_query_count=int(truncated[0] > 0),
            truncated_query_tokens=truncated[0],
        )
        return rows[0]

    def embed_documents(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        rows, truncated = self._encode(tuple(texts))
        self._last_passage_telemetry = EmbeddingPassageBatchTelemetry(
            passage_count=len(rows),
            truncated_passage_count=sum(value > 0 for value in truncated),
            truncated_passage_tokens=sum(truncated),
        )
        return rows

    def _encode(
        self, texts: tuple[str, ...]
    ) -> tuple[tuple[tuple[float, ...], ...], tuple[int, ...]]:
        if not texts or any(not text.strip() for text in texts):
            raise LocalModelAdapterError("Qwen3 embedding inputs must be non-empty")
        maximum = self._artifact.manifest.representation.max_sequence_length
        truncated = _single_sequence_truncation(self._tokenizer, texts, maximum)
        encoded = self._tokenizer(
            list(texts),
            padding=True,
            truncation=True,
            max_length=maximum,
            return_tensors="pt",
        )
        with self._torch.inference_mode():
            hidden = self._model(
                **encoded, use_cache=False
            ).last_hidden_state[:, -1, : self.dimension]
            vectors = self._torch.nn.functional.normalize(hidden.float(), p=2, dim=1).cpu()
        return _tensor_rows(vectors), truncated


class MedCptCrossEncoderProvider(_RerankerAdapter):
    """Official MedCPT cross-encoder returning one raw relevance logit per passage."""

    def __init__(self, artifact: VerifiedModelArtifact) -> None:
        super().__init__(artifact, MEDCPT_CROSS_ENCODER_MODEL_ID)
        representation = artifact.manifest.representation
        if (
            representation.pooling != "sequence_classification_logit"
            or representation.max_sequence_length != 512
        ):
            raise LocalModelAdapterError("MedCPT cross-encoder contract is incompatible")
        self._torch, self._tokenizer, self._model = _load_sequence_classifier(artifact)

    def score(self, query: str, passages: Sequence[str]) -> tuple[float, ...]:
        materialized = tuple(passages)
        if not query.strip() or not materialized or any(not item.strip() for item in materialized):
            raise LocalModelAdapterError("MedCPT reranker inputs must be non-empty")
        maximum = self._artifact.manifest.representation.max_sequence_length
        query_removed, passage_removed = _longest_first_pair_truncation(
            self._tokenizer,
            tuple(query for _ in materialized),
            materialized,
            maximum,
        )
        encoded = self._tokenizer(
            [query] * len(materialized),
            list(materialized),
            padding=True,
            truncation="longest_first",
            max_length=maximum,
            return_tensors="pt",
        )
        with self._torch.inference_mode():
            logits = self._model(**encoded).logits.reshape(-1).float().cpu().tolist()
        scores = tuple(float(value) for value in logits)
        _require_finite(scores)
        self._last_batch_telemetry = RerankerBatchTelemetry(
            passage_count=len(materialized),
            truncated_query_count=int(any(value > 0 for value in query_removed)),
            truncated_passage_count=sum(value > 0 for value in passage_removed),
            truncated_query_tokens=max(query_removed, default=0),
            truncated_passage_tokens=sum(passage_removed),
        )
        return scores


class Qwen3RerankerProvider(_RerankerAdapter):
    """Qwen3 causal-LM reranker using the official yes/no probability contract."""

    _PREFIX = (
        '<|im_start|>system\nJudge whether the Document meets the requirements based on the '
        'Query and the Instruct provided. Note that the answer can only be "yes" or '
        '"no".<|im_end|>\n<|im_start|>user\n'
    )
    _SUFFIX = "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"

    def __init__(self, artifact: VerifiedModelArtifact) -> None:
        super().__init__(artifact, QWEN3_RERANKER_MODEL_ID)
        representation = artifact.manifest.representation
        if (
            representation.pooling != "causal_lm_yes_probability"
            or representation.max_sequence_length != 512
        ):
            raise LocalModelAdapterError("Qwen3 reranker contract is incompatible")
        self._torch, self._tokenizer, self._model = _load_causal_lm(
            artifact, padding_side="left", dtype="auto", attn_implementation="eager"
        )
        self._false_token_id = int(self._tokenizer.convert_tokens_to_ids("no"))
        self._true_token_id = int(self._tokenizer.convert_tokens_to_ids("yes"))
        if self._false_token_id == self._true_token_id or min(
            self._false_token_id, self._true_token_id
        ) < 0:
            raise LocalModelAdapterError("Qwen3 yes/no token identities are invalid")
        self._prefix_tokens = tuple(
            self._tokenizer.encode(self._PREFIX, add_special_tokens=False)
        )
        self._suffix_tokens = tuple(
            self._tokenizer.encode(self._SUFFIX, add_special_tokens=False)
        )

    def score(self, query: str, passages: Sequence[str]) -> tuple[float, ...]:
        materialized = tuple(passages)
        if not query.strip() or not materialized or any(not item.strip() for item in materialized):
            raise LocalModelAdapterError("Qwen3 reranker inputs must be non-empty")
        formatted = tuple(_qwen3_reranker_text(query, passage) for passage in materialized)
        maximum = self._artifact.manifest.representation.max_sequence_length
        content_limit = maximum - len(self._prefix_tokens) - len(self._suffix_tokens)
        if content_limit < 1:
            raise LocalModelAdapterError("Qwen3 reranker prompt leaves no content tokens")
        token_rows = self._tokenizer(
            list(formatted),
            padding=False,
            truncation=False,
            add_special_tokens=False,
        )["input_ids"]
        passage_token_counts = tuple(
            len(self._tokenizer(passage, add_special_tokens=False)["input_ids"])
            for passage in materialized
        )
        removed = tuple(max(0, len(row) - content_limit) for row in token_rows)
        if any(
            total > passage
            for total, passage in zip(removed, passage_token_counts, strict=True)
        ):
            raise LocalModelAdapterError("Qwen3 truncation would reach the query; refuse ambiguity")
        truncated_rows = tuple(
            list(self._prefix_tokens)
            + list(row[:content_limit])
            + list(self._suffix_tokens)
            for row in token_rows
        )
        encoded = self._tokenizer.pad(
            {"input_ids": truncated_rows},
            padding=True,
            return_attention_mask=True,
            return_tensors="pt",
        )
        with self._torch.inference_mode():
            logits = self._model(**encoded, use_cache=False).logits[:, -1, :]
            binary = self._torch.stack(
                (logits[:, self._false_token_id], logits[:, self._true_token_id]), dim=1
            )
            scores = tuple(
                float(value)
                for value in self._torch.nn.functional.softmax(
                    binary.float(), dim=1
                )[:, 1].cpu().tolist()
            )
        _require_finite(scores)
        self._last_batch_telemetry = RerankerBatchTelemetry(
            passage_count=len(materialized),
            truncated_query_count=0,
            truncated_passage_count=sum(value > 0 for value in removed),
            truncated_query_tokens=0,
            truncated_passage_tokens=sum(removed),
        )
        return scores


def _load_encoder(
    artifact: VerifiedModelArtifact,
    *,
    padding_side: str | None = None,
    dtype: str | None = None,
    attn_implementation: str | None = None,
) -> tuple[Any, Any, Any]:
    return _load_transformers_model(
        artifact,
        auto_model_name="AutoModel",
        padding_side=padding_side,
        dtype=dtype,
        attn_implementation=attn_implementation,
    )


def _load_sequence_classifier(
    artifact: VerifiedModelArtifact,
) -> tuple[Any, Any, Any]:
    return _load_transformers_model(
        artifact,
        auto_model_name="AutoModelForSequenceClassification",
    )


def _load_causal_lm(
    artifact: VerifiedModelArtifact,
    *,
    padding_side: str,
    dtype: str,
    attn_implementation: str,
) -> tuple[Any, Any, Any]:
    return _load_transformers_model(
        artifact,
        auto_model_name="AutoModelForCausalLM",
        padding_side=padding_side,
        dtype=dtype,
        attn_implementation=attn_implementation,
    )


def _load_transformers_model(
    artifact: VerifiedModelArtifact,
    *,
    auto_model_name: str,
    padding_side: str | None = None,
    dtype: str | None = None,
    attn_implementation: str | None = None,
) -> tuple[Any, Any, Any]:
    try:
        torch = importlib.import_module("torch")
        transformers = importlib.import_module("transformers")
        auto_tokenizer = transformers.AutoTokenizer
        auto_model = getattr(transformers, auto_model_name)
    except (AttributeError, ImportError) as exc:
        raise LocalModelAdapterError(
            "install the local-embeddings runtime before loading ablation models"
        ) from exc
    tokenizer_kwargs: dict[str, object] = {
        "local_files_only": True,
        "trust_remote_code": False,
    }
    if padding_side is not None:
        tokenizer_kwargs["padding_side"] = padding_side
    model_kwargs: dict[str, object] = {
        "local_files_only": True,
        "trust_remote_code": False,
    }
    if dtype is not None:
        model_kwargs["dtype"] = dtype
    if attn_implementation is not None:
        model_kwargs["attn_implementation"] = attn_implementation
    try:
        with offline_model_call():
            tokenizer = auto_tokenizer.from_pretrained(
                str(artifact.model_directory), **tokenizer_kwargs
            )
            model = auto_model.from_pretrained(
                str(artifact.model_directory), **model_kwargs
            ).eval()
        model.to("cpu")
    except Exception as exc:
        raise LocalModelAdapterError("failed to load the verified local model") from exc
    return torch, tokenizer, model


def _parse_medcpt_article_passage(value: str) -> tuple[str, str]:
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as exc:
        raise LocalModelAdapterError("MedCPT article passage is not canonical JSON") from exc
    if (
        not isinstance(payload, dict)
        or set(payload) != {"text", "title"}
        or not isinstance(payload["title"], str)
        or not isinstance(payload["text"], str)
        or serialize_medcpt_article_passage(payload["title"], payload["text"]) != value
    ):
        raise LocalModelAdapterError("MedCPT article passage contract is invalid")
    return payload["title"], payload["text"]


def _single_sequence_truncation(
    tokenizer: Any, texts: tuple[str, ...], maximum: int
) -> tuple[int, ...]:
    rows = tokenizer(
        list(texts), truncation=False, padding=False, add_special_tokens=True
    )["input_ids"]
    return tuple(max(0, len(row) - maximum) for row in rows)


def _pair_total_truncation(
    tokenizer: Any,
    first: tuple[str, ...],
    second: tuple[str, ...],
    maximum: int,
) -> tuple[int, ...]:
    rows = tokenizer(
        list(first),
        list(second),
        truncation=False,
        padding=False,
        add_special_tokens=True,
    )["input_ids"]
    return tuple(max(0, len(row) - maximum) for row in rows)


def _longest_first_pair_truncation(
    tokenizer: Any,
    queries: tuple[str, ...],
    passages: tuple[str, ...],
    maximum: int,
) -> tuple[tuple[int, ...], tuple[int, ...]]:
    special = int(tokenizer.num_special_tokens_to_add(pair=True))
    query_rows = tokenizer(
        list(queries), truncation=False, padding=False, add_special_tokens=False
    )["input_ids"]
    passage_rows = tokenizer(
        list(passages), truncation=False, padding=False, add_special_tokens=False
    )["input_ids"]
    query_removed: list[int] = []
    passage_removed: list[int] = []
    for query_row, passage_row in zip(query_rows, passage_rows, strict=True):
        query_length = len(query_row)
        passage_length = len(passage_row)
        removed_query = 0
        removed_passage = 0
        overflow = max(0, query_length + passage_length + special - maximum)
        for _ in range(overflow):
            if query_length > passage_length:
                query_length -= 1
                removed_query += 1
            else:
                passage_length -= 1
                removed_passage += 1
        query_removed.append(removed_query)
        passage_removed.append(removed_passage)
    return tuple(query_removed), tuple(passage_removed)


def _qwen3_reranker_text(query: str, passage: str) -> str:
    return (
        f"<Instruct>: {QWEN3_RETRIEVAL_INSTRUCTION}\n"
        f"<Query>: {query}\n<Document>: {passage}"
    )


def _tensor_rows(value: Any) -> tuple[tuple[float, ...], ...]:
    return tuple(tuple(float(component) for component in row.tolist()) for row in value)


def _require_finite(values: Sequence[float]) -> None:
    if any(not math.isfinite(value) for value in values):
        raise LocalModelAdapterError("model returned a non-finite score")
