from __future__ import annotations

import subprocess
import sys


def test_structured_gate_cold_import_has_no_cycle() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "from eve_relation_rag.retrieval.structured.gate import PublishedReleaseGate; "
            "print(PublishedReleaseGate.__name__)",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert completed.stdout.strip() == "PublishedReleaseGate"
    assert completed.stderr == ""


def test_production_bootstrap_cold_import_never_loads_tests_only_capabilities() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import eve_relation_rag.bootstrap; "
            "assert not any(name == 'tests' or name.startswith('tests.') for name in sys.modules); "
            "print('clean')",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert completed.stdout.strip() == "clean"
    assert completed.stderr == ""
