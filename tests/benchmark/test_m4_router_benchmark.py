from __future__ import annotations

import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from eve_relation_rag.domain.keys import canonical_json_sha256
from eve_relation_rag.hybrid.contracts import RagQueryRequest
from eve_relation_rag.planning.router import DeterministicRouter

FIXTURE_PATH = Path(__file__).parents[1] / "fixtures" / "m4" / "router_cases.json"


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class RouterBenchmarkCase(_FrozenModel):
    case_id: str = Field(pattern=r"^(?:structured|literature|hybrid|unsupported)-[0-9]{2}$")
    request: dict[str, Any]
    expected_route: Literal["structured", "literature", "hybrid", "unsupported"]
    expected_structured_question: str | None
    expected_literature_question: str | None
    expected_effective_literature_top_k: int | None
    expected_refusal_code: Literal["unsupported_request", "route_request_mismatch"] | None


class RouterBenchmark(_FrozenModel):
    benchmark_schema_version: Literal["m4-router-benchmark-v1"]
    case_count: int = Field(ge=25)
    route_counts: dict[str, int]
    cases: tuple[RouterBenchmarkCase, ...]
    benchmark_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_identity_and_counts(self) -> RouterBenchmark:
        if self.case_count != len(self.cases):
            raise ValueError("case_count does not match cases")
        case_ids = tuple(case.case_id for case in self.cases)
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("router benchmark case IDs must be unique")
        observed_counts = dict(Counter(case.expected_route for case in self.cases))
        if observed_counts != self.route_counts:
            raise ValueError("route_counts do not match cases")
        required_minimums = {
            "structured": 5,
            "literature": 5,
            "hybrid": 10,
            "unsupported": 5,
        }
        if any(
            observed_counts.get(route, 0) < minimum for route, minimum in required_minimums.items()
        ):
            raise ValueError("router benchmark does not meet the approved route minimums")
        payload = self.model_dump(mode="python")
        del payload["benchmark_sha256"]
        if canonical_json_sha256(payload) != self.benchmark_sha256:
            raise ValueError("benchmark_sha256 does not match canonical benchmark payload")
        return self


def _load_benchmark() -> RouterBenchmark:
    return RouterBenchmark.model_validate_json(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_checksum_bound_router_benchmark_has_exact_expected_decisions() -> None:
    benchmark = _load_benchmark()
    router = DeterministicRouter()

    for case in benchmark.cases:
        request = RagQueryRequest.model_validate(case.request)
        decision = router.route(request)

        assert decision.route == case.expected_route, case.case_id
        assert decision.original_question == request.question, case.case_id
        assert decision.structured_question == case.expected_structured_question, case.case_id
        assert decision.literature_question == case.expected_literature_question, case.case_id
        assert decision.effective_literature_top_k == case.expected_effective_literature_top_k, (
            case.case_id
        )
        assert decision.refusal_code == case.expected_refusal_code, case.case_id


def test_router_cold_import_has_no_database_embedding_or_llm_runtime() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; "
                "from eve_relation_rag.planning.router import DeterministicRouter; "
                "forbidden = {'sqlalchemy', 'psycopg', 'pgvector', "
                "'sentence_transformers', 'transformers', 'openai', 'anthropic'}; "
                "assert forbidden.isdisjoint(sys.modules); "
                "print(DeterministicRouter.__name__)"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert completed.stdout.strip() == "DeterministicRouter"
    assert completed.stderr == ""
