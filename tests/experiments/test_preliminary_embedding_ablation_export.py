from __future__ import annotations

import runpy
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import cast

import pytest

CsvBytes = Callable[[Sequence[str], Sequence[dict[str, object]]], bytes]


def _load_csv_bytes() -> CsvBytes:
    repository_root = Path(__file__).resolve().parents[2]
    namespace = runpy.run_path(
        str(repository_root / "scripts" / "run_preliminary_embedding_ablation.py"),
        run_name="embedding_ablation_preliminary_export_test",
    )
    return cast(CsvBytes, namespace["_csv_bytes"])


def test_csv_export_projects_declared_fields_without_rejecting_summary_extras() -> None:
    csv_bytes = _load_csv_bytes()

    assert csv_bytes(
        ("system_key", "recall_at_5"),
        (
            {
                "system_key": "system-a",
                "recall_at_5": 0.75,
                "end_to_end_latency_p50_ms": 12.5,
            },
        ),
    ) == b"system_key,recall_at_5\nsystem-a,0.75\n"


def test_csv_export_rejects_missing_declared_fields() -> None:
    csv_bytes = _load_csv_bytes()

    with pytest.raises(RuntimeError, match="missing declared fields"):
        csv_bytes(("system_key", "recall_at_5"), ({"system_key": "system-a"},))
