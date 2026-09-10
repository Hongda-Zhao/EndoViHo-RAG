"""Machine journals cannot fabricate review or overwrite an existing observation."""

from types import SimpleNamespace

import pytest

from eve_relation_rag.experiments.rag_value_ablation.source_experiment import (
    SourceExperiment,
    atomic_json,
)


def test_formal_run_requires_input_review_before_provider_or_output(tmp_path):
    runtime = SourceExperiment.__new__(SourceExperiment)
    output = tmp_path / "formal"
    with pytest.raises(ValueError, match="Gold and Oracle approval"):
        runtime.run(output)
    assert not output.exists()


def test_new_lexical_policy_cannot_reuse_an_incomplete_implementation_freeze():
    runtime = SourceExperiment.__new__(SourceExperiment)
    runtime.config = {
        "lexical_query_policy": "rag-value-lexical-query-v1",
        "implementation_files": [], "files": {},
    }
    with pytest.raises(ValueError, match="active implementation pins"):
        runtime.verify_files()


def test_machine_record_is_atomic_and_cannot_replace_an_observation(tmp_path):
    path = tmp_path / "cell.json"
    atomic_json(path, {"status": "invalid_answer", "raw_answer": "original failure"})
    before = path.read_bytes()
    with pytest.raises(FileExistsError):
        atomic_json(path, {"status": "completed"})
    assert path.read_bytes() == before
    assert not path.with_suffix(".json.tmp").exists()


def test_source_route_scope_and_applicability_do_not_touch_providers():
    runtime = SourceExperiment.__new__(SourceExperiment)
    provider = SimpleNamespace()
    question = {
        "question_id": "development-policy",
        "family": "unsupported",
        "question_text_draft": "Run HMMER on a new sequence.",
    }
    assert runtime.execute_cell(provider, question, "S0")["status"] == "scope_refused"
    question.update(family="literature", question_text_draft="What does the paper report?")
    assert runtime.execute_cell(provider, question, "S4")["status"] == "not_applicable"
    question["family"] = "unsupported"
    assert runtime.execute_cell(provider, question, "S5")["status"] == "route_refused"


def test_fixed_generation_timeout_is_an_observation_without_retry(monkeypatch):
    from eve_relation_rag.experiments.rag_value_ablation import source_experiment as module
    from eve_relation_rag.experiments.rag_value_ablation.local_generation import (
        LocalGenerationTimeout,
    )
    from tests.experiments.test_rag_value_systems import _generation_identity

    runtime = SourceExperiment.__new__(SourceExperiment)
    runtime.config_sha256 = "a" * 64
    calls = []

    def measured(provider, **values):
        return module.build_evidence_pack(
            **values,
            tokenizer_key=module.TOKENIZER_KEY,
            model_context_limit_tokens=32768,
            reserved_output_tokens=8192,
            input_token_count=100,
            context_token_count=20,
        )

    def timeout(evidence):
        calls.append(evidence)
        raise LocalGenerationTimeout("generate")

    monkeypatch.setattr(module, "build_measured_evidence", measured)
    provider = SimpleNamespace(identity=_generation_identity(), generate=timeout)
    result = runtime.execute_cell(
        provider,
        {
            "question_id": "development-timeout",
            "family": "hybrid",
            "question_text_draft": "Describe the supplied source evidence.",
        },
        "S0",
    )
    assert result["status"] == "model_timeout"
    assert result["generation_attempted"] is True
    assert result["generation_executed"] is None
    assert result["automatic_retry"] is False
    assert len(calls) == 1


@pytest.mark.parametrize("execution_systems", [None, ["S2", "S3", "S5"]])
def test_complete_journal_reloads_without_repeating_a_cell(
    tmp_path, monkeypatch, execution_systems,
):
    import contextlib
    import hashlib
    import json

    from eve_relation_rag.experiments.rag_value_ablation import source_experiment as module
    from eve_relation_rag.literature.hashing import canonical_json_bytes

    # Synthetic unit fixture only; no real review or provider authority is issued.
    runtime = SourceExperiment.__new__(SourceExperiment)
    runtime.config_sha256 = "a" * 64
    runtime.config = {
        "status": "frozen_for_formal_execution",
        "files": {"input_review": {"sha256": "b" * 64}},
    }
    if execution_systems is not None:
        runtime.config["execution_systems"] = execution_systems
    runtime.questions = tuple({"question_id": f"tests-only-{i}"} for i in range(53))
    runtime.provider_config = None
    oracles = [
        {"question_id": q["question_id"], "source_queries": [], "chunk_keys": []}
        for q in runtime.questions
    ]
    review = {
        "schema_version": "rag-value-source-input-approval-v1",
        "runtime_config_sha256": runtime.config_sha256,
        "gold_approved": True,
        "oracle_approved": True,
        "reviewer": "synthetic-test-fixture",
        "user_approval_message": "Synthetic fixture, not human approval",
        "reference_packet_sha256": "b" * 64,
        "oracle_entries": oracles,
    }
    path = tmp_path / "synthetic-review.json"
    path.write_bytes(canonical_json_bytes(review))
    monkeypatch.setattr(
        runtime,
        "read",
        lambda key: b"\n".join(canonical_json_bytes({"oracle_entry": e}) for e in oracles),
    )
    monkeypatch.setattr(runtime, "verify_files", lambda: None)
    monkeypatch.setattr(
        module, "OfflineMlxGenerationProvider", lambda config: contextlib.nullcontext(None)
    )
    calls = []

    def cell(provider, question, system, **kwargs):
        calls.append((question["question_id"], system))
        return {
            "status": "completed",
            "generation_executed": False,
            "scientific_score": None,
            "synthetic_test_only": True,
        }

    monkeypatch.setattr(runtime, "execute_cell", cell)
    kwargs = {
        "approved_review": path,
        "approved_review_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }
    output = tmp_path / "records"
    summary = runtime.run(output, **kwargs)
    selected = execution_systems or list(module.ALL_SYSTEM_KEYS)
    expected_cells = 53 * len(selected)
    assert summary["expected_cells"] == summary["recorded_cells"] == expected_cells
    assert len(calls) == len(set(calls)) == expected_cells
    assert {system for _, system in calls} == set(selected)
    assert summary["execution_complete"] is True
    if execution_systems is None:
        assert expected_cells == 371
        assert "execution_systems" not in summary  # Preserve old summary replay identity.
    else:
        assert expected_cells == 159
        assert summary["execution_systems"] == selected
        assert summary["execution_scope"] == "system_subset"
        assert summary["full_matrix_expected_cells"] == 371
        assert summary["full_matrix_execution_complete"] is False
        assert not list(output.glob("*.S0.json"))
    assert runtime.run(output, **kwargs) == summary
    assert len(calls) == expected_cells
    saved = output / f"tests-only-0.{selected[0]}.json"
    value = json.loads(saved.read_bytes())
    assert value["run_mode"] == "formal_machine_execution"
    assert value["runtime_config_sha256"] == runtime.config_sha256
    assert value["input_approval_file_sha256"] == kwargs["approved_review_sha256"]
    value["status"] = "tampered"
    saved.write_bytes(canonical_json_bytes(value))
    with pytest.raises(ValueError, match="existing record differs"):
        runtime.run(output, **kwargs)


@pytest.mark.parametrize("execution_systems", [
    [], None, "S2", ("S2",), ["S7"], ["S2", "S2"], ["S5", "S2"], [2], [True],
])
def test_invalid_execution_systems_reject_before_inputs_provider_or_output(
    tmp_path, execution_systems,
):
    import hashlib

    from eve_relation_rag.literature.hashing import canonical_json_bytes

    config = {
        "schema_version": "rag-value-source-runtime-v1",
        "variant": "source-reported-v1",
        "public_publication": False,
        "execution_systems": execution_systems,
        "status": "frozen_for_formal_execution",
    }
    # Tuples are not JSON values distinct from lists; test direct config validation below.
    if not isinstance(execution_systems, tuple):
        path = tmp_path / "config.json"
        path.write_bytes(canonical_json_bytes(config))
        with pytest.raises(ValueError, match="execution_systems"):
            SourceExperiment(path, hashlib.sha256(path.read_bytes()).hexdigest())
    runtime = SourceExperiment.__new__(SourceExperiment)
    runtime.config = config
    output = tmp_path / "records"
    with pytest.raises(ValueError, match="execution_systems"):
        runtime.run(
            output, approved_review=tmp_path / "absent-review.json",
            approved_review_sha256="a" * 64,
        )
    assert not output.exists()
