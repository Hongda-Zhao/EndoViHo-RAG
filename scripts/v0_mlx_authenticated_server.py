#!/usr/bin/env python3
"""Run the fixed V0 MLX engine behind a mandatory inherited-FD credential."""

from __future__ import annotations

import argparse
import hmac
import json
import logging
import os
import sys
from collections.abc import Sequence
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar, Final

import mlx.core as mx
from mlx_lm import server as mlx_server

_HOST: Final = "127.0.0.1"
_PORT: Final = 8124
_INNER_KEY_BYTES: Final = 32
_MAX_REQUEST_BYTES: Final = 4 * 1024 * 1024
_HEADER_BODY_TIMEOUT_SECONDS: Final = 5.0


class EngineWrapperError(RuntimeError):
    """The authenticated MLX wrapper cannot establish its fixed runtime."""


def _read_inner_key(file_descriptor: int) -> bytes:
    if file_descriptor <= 2:
        raise EngineWrapperError("the inherited engine credential descriptor is invalid")
    payload = bytearray()
    try:
        while len(payload) <= _INNER_KEY_BYTES:
            chunk = os.read(file_descriptor, _INNER_KEY_BYTES + 1 - len(payload))
            if not chunk:
                break
            payload.extend(chunk)
    except OSError as exc:
        raise EngineWrapperError("the inherited engine credential is unavailable") from exc
    finally:
        try:
            os.close(file_descriptor)
        except OSError:
            pass
    if len(payload) != _INNER_KEY_BYTES:
        raise EngineWrapperError("the inherited engine credential has an invalid length")
    return bytes(payload)


class AuthenticatedAPIHandler(mlx_server.APIHandler):
    """Require the per-process inner credential before any upstream handler runs."""

    inner_api_key: ClassVar[bytes] = b""

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(_HEADER_BODY_TIMEOUT_SECONDS)

    def log_message(self, _format: str, *args: object) -> None:
        return

    def _authorized(self) -> bool:
        expected = b"Bearer " + self.inner_api_key.hex().encode("ascii")
        values = self.headers.get_all("authorization", [])
        if len(values) != 1:
            return False
        try:
            observed = values[0].encode("ascii")
        except UnicodeEncodeError:
            return False
        return hmac.compare_digest(observed, expected)

    def _safe_framing(self, *, expect_body: bool) -> bool:
        if (
            self.headers.get_all("transfer-encoding", [])
            or self.headers.get_all("expect", [])
            or self.headers.get_all("content-encoding", [])
        ):
            return False
        lengths = self.headers.get_all("content-length", [])
        if not expect_body:
            return not lengths
        if len(lengths) != 1 or not lengths[0].isdigit():
            return False
        length = int(lengths[0])
        return 1 <= length <= _MAX_REQUEST_BYTES

    def _reject(self) -> None:
        payload = json.dumps(
            {"error": "inner_engine_request_rejected"},
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
        self.close_connection = True
        self.send_response(HTTPStatus.UNAUTHORIZED)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.send_header("cache-control", "no-store")
        self.send_header("connection", "close")
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self.close_connection = True
        if not self._authorized() or not self._safe_framing(expect_body=False):
            self._reject()
            return
        super().do_GET()

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self.close_connection = True
        if not self._authorized() or not self._safe_framing(expect_body=True):
            self._reject()
            return
        super().do_POST()

    def do_OPTIONS(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self.close_connection = True
        if not self._authorized() or not self._safe_framing(expect_body=False):
            self._reject()
            return
        super().do_OPTIONS()


def _engine_arguments(model_root: Path) -> argparse.Namespace:
    return argparse.Namespace(
        adapter_path=None,
        allowed_origins=[],
        chat_template="",
        chat_template_args={},
        decode_concurrency=1,
        draft_model=None,
        max_tokens=256,
        min_p=0.0,
        model=str(model_root),
        num_draft_tokens=3,
        pipeline=False,
        prefill_step_size=2048,
        prompt_cache_bytes=None,
        prompt_cache_size=1,
        prompt_concurrency=1,
        temp=0.0,
        top_k=0,
        top_p=1.0,
        trust_remote_code=False,
        use_default_chat_template=False,
    )


def _parse_arguments(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--inner-key-fd", type=int, required=True)
    return parser.parse_args(argv)


def _run_authenticated_engine(provider: mlx_server.ModelProvider) -> None:
    """Run the pinned upstream engine while explicitly supplying our handler.

    mlx-lm 0.31.3 exposes ``handler_class`` on ``run`` but does not forward it
    to its private HTTP-server helper.  The module checksum is policy-bound, so
    reproduce that small dispatcher here and pass the mandatory handler at the
    point where the listening server is constructed.
    """

    group = mx.distributed.init()
    prompt_cache = mlx_server.LRUPromptCache(provider.cli_args.prompt_cache_size)
    response_generator = mlx_server.ResponseGenerator(provider, prompt_cache)
    if group.rank() == 0:
        mlx_server._run_http_server(  # noqa: SLF001 - checksum-bound pinned API
            _HOST,
            _PORT,
            response_generator,
            server_class=ThreadingHTTPServer,
            handler_class=AuthenticatedAPIHandler,
        )
    else:
        response_generator.join()


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parse_arguments(argv)
    model_dir = arguments.model_dir
    if model_dir.is_symlink() or not model_dir.is_dir():
        raise EngineWrapperError("the approved model directory is unavailable")
    model_root = model_dir.resolve(strict=True)
    inner_key = _read_inner_key(arguments.inner_key_fd)
    AuthenticatedAPIHandler.inner_api_key = inner_key
    if mx.metal.is_available():
        mx.set_wired_limit(mx.device_info()["max_recommended_working_set_size"])
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )
    provider = mlx_server.ModelProvider(_engine_arguments(model_root))
    _run_authenticated_engine(provider)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except EngineWrapperError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(4) from None
