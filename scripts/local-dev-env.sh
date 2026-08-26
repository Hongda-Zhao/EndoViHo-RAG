#!/bin/sh
# Source this file from any directory inside the repository:
#   . scripts/local-dev-env.sh

if ! EVE_RAG_PROJECT_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)"; then
    printf '%s\n' "Run this command from inside the EndoViHo-RAG repository." >&2
    return 1 2>/dev/null || exit 1
fi

export EVE_RAG_PROJECT_ROOT
export PATH="${EVE_RAG_PROJECT_ROOT}/.tools/lima/bin:${EVE_RAG_PROJECT_ROOT}/.tools/bin:${EVE_RAG_PROJECT_ROOT}/.tools:${PATH}"
export XDG_CONFIG_HOME="${EVE_RAG_PROJECT_ROOT}/.tools/x"
export XDG_CACHE_HOME="${EVE_RAG_PROJECT_ROOT}/.tools/c"
export DOCKER_CONFIG="${EVE_RAG_PROJECT_ROOT}/.tools/docker-config"
export DOCKER_HOST="unix://${EVE_RAG_PROJECT_ROOT}/.tools/x/colima/default/docker.sock"
export UV_PYTHON_INSTALL_DIR="${EVE_RAG_PROJECT_ROOT}/.tools/python"
export UV_CACHE_DIR="${EVE_RAG_PROJECT_ROOT}/.uv-cache"
