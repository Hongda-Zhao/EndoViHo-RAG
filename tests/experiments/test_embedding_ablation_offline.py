from __future__ import annotations

import os
import socket

import pytest

from eve_relation_rag.experiments.embedding_ablation.offline import (
    OfflineModelRuntimeError,
    offline_model_call,
)


def test_model_call_sets_offline_flags_denies_socket_and_restores_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "previous")

    with offline_model_call():
        assert os.environ["HF_HUB_OFFLINE"] == "1"
        assert os.environ["HF_DATASETS_OFFLINE"] == "1"
        assert os.environ["TRANSFORMERS_OFFLINE"] == "1"
        with pytest.raises(OfflineModelRuntimeError, match="denied"):
            socket.create_connection(("example.invalid", 443))

    assert "HF_HUB_OFFLINE" not in os.environ
    assert "HF_DATASETS_OFFLINE" not in os.environ
    assert os.environ["TRANSFORMERS_OFFLINE"] == "previous"
