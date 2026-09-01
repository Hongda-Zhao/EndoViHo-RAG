"""Model-call-scoped offline environment and Python socket denial."""

from __future__ import annotations

import os
import socket
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Never
from unittest.mock import patch

_OFFLINE_ENVIRONMENT = {
    "HF_DATASETS_OFFLINE": "1",
    "HF_HUB_OFFLINE": "1",
    "TRANSFORMERS_OFFLINE": "1",
}
_MODEL_CALL_LOCK = threading.RLock()


class OfflineModelRuntimeError(RuntimeError):
    """Raised for every Python-level networking attempt during a model call."""


class _OfflineSocket(socket.socket):
    def connect(self, address: object) -> Never:
        raise OfflineModelRuntimeError(f"model runtime network access denied: {address!r}")

    def connect_ex(self, address: object) -> Never:
        raise OfflineModelRuntimeError(f"model runtime network access denied: {address!r}")


def _deny_network(*args: object, **kwargs: object) -> Never:
    del args, kwargs
    raise OfflineModelRuntimeError("model runtime network access denied")


@contextmanager
def offline_model_call() -> Iterator[None]:
    """Set offline runtime flags and deny sockets only for one provider call."""

    with _MODEL_CALL_LOCK:
        previous = {key: os.environ.get(key) for key in _OFFLINE_ENVIRONMENT}
        os.environ.update(_OFFLINE_ENVIRONMENT)
        try:
            with (
                patch.object(socket, "socket", _OfflineSocket),
                patch.object(socket, "create_connection", _deny_network),
                patch.object(socket, "getaddrinfo", _deny_network),
            ):
                yield
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
