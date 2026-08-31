# V0 local provider operations

V0 generation uses the checksum-approved local model only. The server launch surface is
[`scripts/run_v0_local_provider.sh`](../scripts/run_v0_local_provider.sh); it binds numeric
loopback `127.0.0.1:8123`, forces Hugging Face offline mode, places both Hugging Face cache
variables below the workspace, disables remote-code trust, and pins prompt/decode concurrency
to one. The script must be launched from a normal local macOS session with Metal available.
It is expected to fail in a headless or sandboxed session without a Metal device.

The model manifest must be built only after the exact quantized-repository commit, base-model
commit, base license source, engine lock digest, prompt-policy digest, and complete artifact
file list are independently approved. No `main` reference is admissible. The construction API
is:

```python
from pathlib import Path

from eve_relation_rag.generation.policy import (
    build_local_model_policy_manifest,
    inventory_model_artifacts,
)

model_root = Path(".artifacts/v0_activation/model/Qwen3-4B-Instruct-2507-4bit")
artifacts = inventory_model_artifacts(
    model_root,
    relative_paths=(
        ".gitattributes",
        "LICENSE.base-apache-2.0",
        "README.md",
        "added_tokens.json",
        "chat_template.jinja",
        "config.json",
        "generation_config.json",
        "merges.txt",
        "model.safetensors",
        "model.safetensors.index.json",
        "special_tokens_map.json",
        "tokenizer.json",
        "tokenizer_config.json",
        "vocab.json",
    ),
)

# Supply every keyword required by LocalModelPolicyManifest. The builder computes
# manifest_sha256; callers must then approve that digest independently before loading it.
manifest = build_local_model_policy_manifest(
    provider_key="provider:local-openai-compatible:v1",
    model_key="model:hf:mlx-community:Qwen3-4B-Instruct-2507-4bit",
    api_model_name="default_model",
    model_revision=APPROVED_QUANTIZED_COMMIT,
    repository_uri="https://huggingface.co/mlx-community/Qwen3-4B-Instruct-2507-4bit",
    repository_revision=APPROVED_QUANTIZED_COMMIT,
    base_model_repository_uri="https://huggingface.co/Qwen/Qwen3-4B-Instruct-2507",
    base_model_key="model:hf:Qwen:Qwen3-4B-Instruct-2507",
    base_model_revision=APPROVED_BASE_COMMIT,
    artifacts=artifacts,
    license_key="Apache-2.0",
    license_artifact_relative_path="LICENSE.base-apache-2.0",
    license_artifact_sha256=next(
        item.sha256
        for item in artifacts
        if item.relative_path == "LICENSE.base-apache-2.0"
    ),
    license_source_uri=(
        "https://huggingface.co/Qwen/Qwen3-4B-Instruct-2507/resolve/"
        f"{APPROVED_BASE_COMMIT}/LICENSE?download=true"
    ),
    inference_engine_key="engine:mlx-lm",
    inference_engine_version="0.31.3+mlx-0.32.2+mlx-metal-0.32.2",
    inference_engine_lock_sha256=APPROVED_ENGINE_LOCK_SHA256,
    quantization="mlx:4bit",
    tokenizer_key="tokenizer:hf:Qwen3-4B-Instruct-2507",
    tokenizer_revision=APPROVED_QUANTIZED_COMMIT,
    context_length_tokens=APPROVED_CONTEXT_LENGTH,
    seed_supported=True,
    seed=0,
    generation_policy_key="generation:v0:json-temp0-seed0-single-request",
    prompt_policy_manifest_sha256=APPROVED_PROMPT_MANIFEST_SHA256,
    max_output_tokens=256,
    timeout_seconds=300,
)
```

The current Activation Manifest Packet candidate binds prompt-manifest SHA-256
`5d456d6083d6b4101f9877327c432a61a9d9a6dfee54986ed2e0a0ef02315a2b` and
model-policy-manifest SHA-256
`43a819d8532b3b267d8426c94134f287cd01152edd6657c28a522c13a2fead94`.
They are retained in the non-overwriting `.v2.json` manifest slots; the original
unversioned files remain the historical Checkpoint 2A inputs.
The model policy fixes `temperature=0`, `seed=0`, one request, no retry, 256 output
tokens, and a 300-second hard deadline. These are candidates until the packet is
explicitly approved; changing any value changes the self-checksum and requires a new
approval.

The prompt manifest separately binds the unchanged M4 AnswerInstructions, the exact
generated-draft schema, and serialization-template SHA-256
`d66794a9eaf88b1e2cc7b32ba37097e7687c1464fced687782144def9dadf2fb`.
The serialization template is non-factual system metadata. The canonical ContextPack
remains the only factual payload supplied to the model.

## Checkpoint 2 qualification evidence

The Checkpoint 2 candidate uses exactly one Darwin/arm64 qualification candidate. Build the
typed, self-hashed definition first, approve or freeze those bytes, and then run the definition
without rebuilding it. The runner replays the exact client runtime before starting the provider:
28 decision-source files and their actual import origins, resolved CPython executable bytes and
runtime identity, complete Pydantic and pydantic-core `RECORD` identities, and the runtime-semantic
dependency projection from `pyproject.toml` and `uv.lock`.

The replacement Packet candidate evidence is immutable under
`.artifacts/v0_activation/candidates/provider-qualification-20260830T104559Z/`:

| Evidence | Semantic SHA-256 | Physical file SHA-256 |
| --- | --- | --- |
| Qualification definition | `37f5fcfa59baab28296d1592cd10e82264ca2c84aac11e035ec63b55cb2c114c` | `a5ea823d7cbd3b92366c68d517a6fcd160638425eaee357d68a72f6b49eb0ea3` |
| Qualification report | `5ead3008e44e2aa73c594a276e4c00509a1fe2d01633410f6d09abe59c27630f` | `266824fb5d3051ea36d9c302cd7553562cb9cf39ff439c6c9856b65eda4aeee2` |

The exact client-runtime manifest SHA-256 is
`4351867792547ced0f95998f3533b797c9ef59d22e56bf57af546a32df23eb69`.
The mechanical qualification result is `passed` with the following fixed observations:

| Parameter or observation | Accepted value |
| --- | --- |
| Candidate selection | `only_passing_candidate` (1 candidate) |
| Provider/model | `provider:local-openai-compatible:v1` / `model:hf:mlx-community:Qwen3-4B-Instruct-2507-4bit` |
| Model revision | `50d427756c6b1b2fe0c0a10f67fbda1fc8e82c1b` |
| Decode/request policy | temperature 0, seed 0, 1 request, 0 retries, 256 output tokens, 300-second deadline |
| Network/authentication | macOS sandbox ports-only v2, HMAC runtime attestation verified, unauthenticated inner request returned 401 |
| Startup/generation time | 11,833,509,584 ns / 30,453,264,958 ns |
| Grounded output | 1 claim, 1 citation; provider output SHA-256 `571ab6e273f965e77c855b18394d9d1001c1d2dc9a7bf3c986536daefa5f4ee8` |
| Shutdown | process exit 0; clean shutdown; inner and outer ports closed |

The first attempt to launch this already-frozen definition from inside the workspace's outer
sandbox failed before provider readiness because the nested macOS sandbox could not establish
the required local runtime. It produced no qualification report. The same definition bytes and
semantic hash were then run from the approved host context while the provider remained inside
the project's no-egress ports-only sandbox. The accepted report records zero model retries; the
failed outer launch is infrastructure audit history, not a model-generation retry.

`GET /v1/models` in `mlx-lm 0.31.3` identifies the loaded model by its absolute local model
path and omits `owned_by`. Readiness therefore requires exactly one model entry matching the
resolved configured artifact root. Chat requests use `model=default_model`; those two identities
are intentionally separate and both are checked. The application provider disables environment
proxies and redirects, accepts only identity-encoded bounded JSON, and re-hashes all model
artifacts before every readiness or generation call.
