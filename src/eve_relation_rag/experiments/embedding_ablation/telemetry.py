"""Dependency-free hardware identity and process resource measurements."""

from __future__ import annotations

import hashlib
import os
import platform
import resource
import subprocess
import sys
from pathlib import Path

from sqlalchemy import Engine, text

from eve_relation_rag.experiments.embedding_ablation.contracts import HardwareRecord

_THREAD_ENVIRONMENT_KEYS = (
    "BLIS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "TOKENIZERS_PARALLELISM",
    "VECLIB_MAXIMUM_THREADS",
)


class TelemetryError(RuntimeError):
    """Raised when required hardware/runtime measurements are unavailable."""


def collect_hardware_record(
    engine: Engine,
    *,
    uv_lock_path: Path,
    accelerator: str,
    accelerator_runtime: str,
    numerical_backend: str,
) -> HardwareRecord:
    """Collect one hardware record shared by all systems in a run."""

    if not accelerator.strip() or not accelerator_runtime.strip() or not numerical_backend.strip():
        raise TelemetryError("hardware backend descriptions must be explicit")
    try:
        lock_bytes = uv_lock_path.read_bytes()
    except OSError as exc:
        raise TelemetryError("uv.lock cannot be read") from exc
    logical_cores = os.cpu_count()
    if logical_cores is None or logical_cores < 1:
        raise TelemetryError("logical CPU count is unavailable")
    cpu_model, physical_cores, ram_bytes = _host_cpu_and_memory(logical_cores)
    try:
        with engine.connect().execution_options(postgresql_readonly=True) as connection:
            with connection.begin():
                if connection.scalar(text("SHOW transaction_read_only")) != "on":
                    raise TelemetryError(
                        "hardware fingerprint database transaction is not read-only"
                    )
                postgresql_version = str(connection.scalar(text("SELECT version()")))
                pgvector_version = str(
                    connection.scalar(
                        text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
                    )
                )
    except TelemetryError:
        raise
    except Exception as exc:
        raise TelemetryError("database runtime fingerprint is unavailable") from exc
    if not pgvector_version or pgvector_version == "None":
        raise TelemetryError("pgvector extension version is unavailable")
    return HardwareRecord(
        hardware_schema_version="embedding-ablation-hardware-v1",
        cpu_model=cpu_model,
        physical_core_count=physical_cores,
        logical_core_count=logical_cores,
        ram_bytes=ram_bytes,
        operating_system=platform.system(),
        kernel_release=platform.release(),
        machine_architecture=platform.machine(),
        accelerator=accelerator,
        accelerator_runtime=accelerator_runtime,
        numerical_backend=numerical_backend,
        python_version=platform.python_version(),
        uv_lock_sha256=hashlib.sha256(lock_bytes).hexdigest(),
        postgresql_version=postgresql_version,
        pgvector_version=pgvector_version,
        thread_settings={
            key: os.environ.get(key, "<unset>") for key in _THREAD_ENVIRONMENT_KEYS
        },
    )


def peak_process_rss_bytes() -> int:
    """Return ru_maxrss normalized to bytes on Darwin and other POSIX platforms."""

    observed = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if observed < 0:
        raise TelemetryError("peak process RSS is invalid")
    return int(observed if sys.platform == "darwin" else observed * 1024)


def _host_cpu_and_memory(logical_cores: int) -> tuple[str, int, int]:
    operating_system = platform.system()
    if operating_system == "Darwin":
        cpu_model = _sysctl("machdep.cpu.brand_string") or _sysctl("hw.model")
        physical = _parse_positive_int(_sysctl("hw.physicalcpu"), "physical CPU count")
        ram_bytes = _parse_positive_int(_sysctl("hw.memsize"), "RAM size")
        if not cpu_model:
            raise TelemetryError("CPU model is unavailable")
        return cpu_model, physical, ram_bytes
    if operating_system == "Linux":
        return _linux_cpu_and_memory(logical_cores)
    cpu_model = platform.processor() or platform.machine()
    ram_bytes = _portable_ram_bytes()
    return cpu_model, logical_cores, ram_bytes


def _linux_cpu_and_memory(logical_cores: int) -> tuple[str, int, int]:
    try:
        cpuinfo = Path("/proc/cpuinfo").read_text(encoding="utf-8")
    except OSError as exc:
        raise TelemetryError("Linux CPU identity is unavailable") from exc
    model = next(
        (
            line.split(":", 1)[1].strip()
            for line in cpuinfo.splitlines()
            if line.startswith(("model name", "Hardware")) and ":" in line
        ),
        platform.processor() or platform.machine(),
    )
    physical_pairs: set[tuple[str, str]] = set()
    current: dict[str, str] = {}
    for line in (*cpuinfo.splitlines(), ""):
        if not line.strip():
            if "physical id" in current and "core id" in current:
                physical_pairs.add((current["physical id"], current["core id"]))
            current = {}
        elif ":" in line:
            key, value = line.split(":", 1)
            current[key.strip()] = value.strip()
    physical = len(physical_pairs) or logical_cores
    return model, physical, _portable_ram_bytes()


def _portable_ram_bytes() -> int:
    try:
        page_size = int(os.sysconf("SC_PAGE_SIZE"))
        page_count = int(os.sysconf("SC_PHYS_PAGES"))
    except (OSError, ValueError) as exc:
        raise TelemetryError("RAM size is unavailable") from exc
    ram_bytes = page_size * page_count
    if ram_bytes < 1:
        raise TelemetryError("RAM size is invalid")
    return ram_bytes


def _sysctl(key: str) -> str:
    try:
        completed = subprocess.run(
            ("/usr/sbin/sysctl", "-n", key),
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return completed.stdout.strip()


def _parse_positive_int(value: str, description: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise TelemetryError(f"{description} is unavailable") from exc
    if parsed < 1:
        raise TelemetryError(f"{description} is invalid")
    return parsed
