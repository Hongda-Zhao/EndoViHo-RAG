from __future__ import annotations

import subprocess
import sys


def test_production_bootstrap_does_not_import_rag_value_experiment() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import eve_relation_rag.bootstrap; "
            "assert not any(name.startswith("
            "'eve_relation_rag.experiments.rag_value_ablation') "
            "for name in sys.modules); print('isolated')",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert completed.stdout.strip() == "isolated"
    assert completed.stderr == ""
