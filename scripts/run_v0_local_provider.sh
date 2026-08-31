#!/bin/sh
set -eu

# V0 is intentionally fixed to one workspace model, one numeric-loopback port,
# offline Hugging Face state, an exact RECORD-verified Python environment, and
# single-request MLX prompt/decode concurrency.
V0_PROJECT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)
V0_MODEL_DIR="$V0_PROJECT_ROOT/.artifacts/v0_activation/model/Qwen3-4B-Instruct-2507-4bit"
V0_PROVIDER_ROOT="$V0_PROJECT_ROOT/.artifacts/v0_activation/provider-env"
V0_PROVIDER_MODULE="$V0_PROJECT_ROOT/.artifacts/v0_activation/provider-env/lib/python3.12/site-packages/mlx_lm/server.py"
V0_PROVIDER_PYTHON="$V0_PROVIDER_ROOT/bin/python"
V0_PROVIDER_PYTHON_CONFIG="$V0_PROVIDER_ROOT/pyvenv.cfg"
V0_PROVIDER_LOCK="$V0_PROJECT_ROOT/config/v0-provider-requirements.lock"
V0_PROVIDER_LAUNCHER="$V0_PROJECT_ROOT/scripts/run_v0_local_provider.sh"
V0_PROVIDER_PROXY="$V0_PROJECT_ROOT/scripts/v0_provider_proxy.py"
V0_PROVIDER_WRAPPER="$V0_PROJECT_ROOT/scripts/v0_mlx_authenticated_server.py"
V0_PROVIDER_ENV_VERIFIER="$V0_PROJECT_ROOT/scripts/v0_provider_environment.py"
V0_EGRESS_PROFILE="$V0_PROJECT_ROOT/scripts/v0_provider_loopback.sb"
V0_SANDBOX_EXECUTABLE=/usr/bin/sandbox-exec
V0_ENV_EXECUTABLE=/usr/bin/env
V0_MODEL_POLICY="$V0_PROJECT_ROOT/.artifacts/v0_activation/manifests/v0_local_model_policy_manifest.v2.json"
V0_PROMPT_POLICY="$V0_PROJECT_ROOT/.artifacts/v0_activation/manifests/v0_prompt_policy_manifest.v2.json"
V0_PROVIDER_ENV_MANIFEST="$V0_PROJECT_ROOT/.artifacts/v0_activation/manifests/v0_provider_environment_manifest.json"
V0_PROVIDER_HOME="$V0_PROJECT_ROOT/.artifacts/v0_activation/provider-home"
V0_PROVIDER_TMP="$V0_PROJECT_ROOT/.artifacts/v0_activation/provider-tmp"
V0_HF_HOME="$V0_PROJECT_ROOT/.artifacts/v0_activation/provider-hf-cache"
V0_HF_CACHE="$V0_HF_HOME/hub"
V0_PORT=8123
V0_INNER_PORT=8124

if [ ! -d "$V0_MODEL_DIR" ] || [ -L "$V0_MODEL_DIR" ]; then
    echo "The approved local model directory is unavailable." >&2
    exit 4
fi
if [ ! -d "$V0_PROVIDER_ROOT" ] || [ -L "$V0_PROVIDER_ROOT" ]; then
    echo "The approved local provider environment is unavailable." >&2
    exit 4
fi
if [ ! -x "$V0_PROVIDER_PYTHON" ] || [ ! -L "$V0_PROVIDER_PYTHON" ]; then
    echo "The approved local provider Python runtime is unavailable." >&2
    exit 4
fi
for V0_REQUIRED_FILE in \
    "$V0_PROVIDER_MODULE" \
    "$V0_PROVIDER_PYTHON_CONFIG" \
    "$V0_PROVIDER_LOCK" \
    "$V0_PROVIDER_LAUNCHER" \
    "$V0_PROVIDER_PROXY" \
    "$V0_PROVIDER_WRAPPER" \
    "$V0_PROVIDER_ENV_VERIFIER" \
    "$V0_EGRESS_PROFILE" \
    "$V0_SANDBOX_EXECUTABLE" \
    "$V0_ENV_EXECUTABLE" \
    "$V0_MODEL_POLICY" \
    "$V0_PROMPT_POLICY" \
    "$V0_PROVIDER_ENV_MANIFEST"
do
    if [ ! -f "$V0_REQUIRED_FILE" ] || [ -L "$V0_REQUIRED_FILE" ]; then
        echo "An approved local provider runtime artifact is unavailable." >&2
        exit 4
    fi
done
if [ -z "${EVE_RAG_LLM_API_KEY_FILE:-}" ] \
    || [ ! -f "$EVE_RAG_LLM_API_KEY_FILE" ] \
    || [ -L "$EVE_RAG_LLM_API_KEY_FILE" ]; then
    echo "A private provider API key file is required." >&2
    exit 4
fi
if [ ! -x "$V0_SANDBOX_EXECUTABLE" ]; then
    echo "The approved macOS network sandbox is unavailable." >&2
    exit 4
fi

umask 077
mkdir -p "$V0_PROVIDER_HOME" "$V0_PROVIDER_TMP" "$V0_HF_CACHE"

exec "$V0_ENV_EXECUTABLE" -i \
    HOME="$V0_PROVIDER_HOME" \
    TMPDIR="$V0_PROVIDER_TMP" \
    HF_HOME="$V0_HF_HOME" \
    HF_HUB_CACHE="$V0_HF_CACHE" \
    HF_HUB_DISABLE_TELEMETRY=1 \
    HF_HUB_OFFLINE=1 \
    LC_ALL=C \
    NO_PROXY=127.0.0.1 \
    no_proxy=127.0.0.1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONHASHSEED=0 \
    PYTHONNOUSERSITE=1 \
    PYTHONSAFEPATH=1 \
    TOKENIZERS_PARALLELISM=false \
    TRANSFORMERS_OFFLINE=1 \
    "$V0_SANDBOX_EXECUTABLE" -f "$V0_EGRESS_PROFILE" \
    "$V0_PROVIDER_PYTHON" -B -I "$V0_PROVIDER_PROXY" \
    --outer-port "$V0_PORT" \
    --inner-port "$V0_INNER_PORT" \
    --model-dir "$V0_MODEL_DIR" \
    --model-policy "$V0_MODEL_POLICY" \
    --prompt-policy "$V0_PROMPT_POLICY" \
    --provider-root "$V0_PROVIDER_ROOT" \
    --provider-environment-manifest "$V0_PROVIDER_ENV_MANIFEST" \
    --environment-verifier "$V0_PROVIDER_ENV_VERIFIER" \
    --engine-wrapper "$V0_PROVIDER_WRAPPER" \
    --engine-module "$V0_PROVIDER_MODULE" \
    --python-executable "$V0_PROVIDER_PYTHON" \
    --python-configuration "$V0_PROVIDER_PYTHON_CONFIG" \
    --engine-lock "$V0_PROVIDER_LOCK" \
    --runtime-launcher "$V0_PROVIDER_LAUNCHER" \
    --proxy-script "$V0_PROVIDER_PROXY" \
    --egress-profile "$V0_EGRESS_PROFILE" \
    --sandbox-executable "$V0_SANDBOX_EXECUTABLE" \
    --environment-executable "$V0_ENV_EXECUTABLE" \
    --api-key-file "$EVE_RAG_LLM_API_KEY_FILE"
