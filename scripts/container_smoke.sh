#!/bin/sh
set -eu

script_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
repo_root="$(CDPATH= cd -- "${script_dir}/.." && pwd)"
smoke_project="${M5_SMOKE_PROJECT:-eve-rag-m5-smoke-local}"
case "${smoke_project}" in
    eve-rag-m5-smoke-*[!a-zA-Z0-9_-]*|eve-rag-m5-smoke-)
        printf '%s\n' "M5_SMOKE_PROJECT must use the dedicated eve-rag-m5-smoke-* namespace." >&2
        exit 2
        ;;
    eve-rag-m5-smoke-*) ;;
    *)
        printf '%s\n' "M5_SMOKE_PROJECT must use the dedicated eve-rag-m5-smoke-* namespace." >&2
        exit 2
        ;;
esac

compose() {
    docker compose \
        --file "${repo_root}/compose.yaml" \
        --project-directory "${repo_root}" \
        --project-name "${smoke_project}" \
        "$@"
}

export POSTGRES_PORT="${M5_SMOKE_POSTGRES_PORT:-55432}"
export API_PORT="${M5_SMOKE_API_PORT:-58000}"
export DEMO_PORT="${M5_SMOKE_DEMO_PORT:-58501}"
export POSTGRES_DB=eve_relation_rag
export POSTGRES_USER=eve
export POSTGRES_PASSWORD=eve_m5_smoke_password
export EVE_RAG_CURSOR_HMAC_SECRET=m5-smoke-cursor-secret-0123456789abcdef0123456789abcdef
export PYTHON_IMAGE=python:3.12.13-slim-bookworm
export UV_IMAGE=ghcr.io/astral-sh/uv:0.12.5
export POSTGRES_IMAGE=pgvector/pgvector:pg16
export EVE_RAG_IMAGE="${M5_SMOKE_IMAGE:-eve-relation-rag:${smoke_project}}"

for smoke_port in "${POSTGRES_PORT}" "${API_PORT}" "${DEMO_PORT}"; do
    case "${smoke_port}" in
        *[!0-9]*|'')
            printf '%s\n' "Smoke ports must be decimal integers." >&2
            exit 2
            ;;
    esac
    if test "${smoke_port}" -lt 1 || test "${smoke_port}" -gt 65535; then
        printf '%s\n' "Smoke ports must be between 1 and 65535." >&2
        exit 2
    fi
done

created_scope=0
cleanup() {
    smoke_status=$?
    cleanup_status=0
    trap - EXIT HUP INT TERM
    if test "${created_scope}" = "1"; then
        if ! compose down --volumes --remove-orphans; then
            cleanup_status=1
        fi
        if ! remaining_containers="$(docker ps --all --quiet --filter "label=com.docker.compose.project=${smoke_project}")"; then
            cleanup_status=1
            remaining_containers=unknown
        fi
        if ! remaining_volumes="$(docker volume ls --quiet --filter "label=com.docker.compose.project=${smoke_project}")"; then
            cleanup_status=1
            remaining_volumes=unknown
        fi
        if ! remaining_networks="$(docker network ls --quiet --filter "label=com.docker.compose.project=${smoke_project}")"; then
            cleanup_status=1
            remaining_networks=unknown
        fi
        if test -n "${remaining_containers}${remaining_volumes}${remaining_networks}"; then
            cleanup_status=1
        fi
        if docker volume inspect "${smoke_project}_postgres_data" >/dev/null 2>&1; then
            cleanup_status=1
        fi
        if docker network inspect "${smoke_project}_backend" >/dev/null 2>&1 \
            || docker network inspect "${smoke_project}_frontend" >/dev/null 2>&1; then
            cleanup_status=1
        fi
        if docker image inspect "${EVE_RAG_IMAGE}" >/dev/null 2>&1; then
            if ! docker image rm "${EVE_RAG_IMAGE}"; then
                cleanup_status=1
            fi
        fi
        if docker image inspect "${EVE_RAG_IMAGE}" >/dev/null 2>&1; then
            cleanup_status=1
        fi
        if test "${cleanup_status}" != "0"; then
            printf '%s\n' "Smoke cleanup did not remove its isolated resources." >&2
        fi
    fi
    if test "${smoke_status}" = "0" && test "${cleanup_status}" != "0"; then
        smoke_status=1
    fi
    exit "${smoke_status}"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

existing_containers="$(docker ps --all --quiet --filter "label=com.docker.compose.project=${smoke_project}")"
existing_volumes="$(docker volume ls --quiet --filter "label=com.docker.compose.project=${smoke_project}")"
existing_networks="$(docker network ls --quiet --filter "label=com.docker.compose.project=${smoke_project}")"
test -z "${existing_containers}"
test -z "${existing_volumes}"
test -z "${existing_networks}"
if docker image inspect "${EVE_RAG_IMAGE}" >/dev/null 2>&1; then
    printf '%s\n' "Refusing to overwrite existing smoke image: ${EVE_RAG_IMAGE}" >&2
    exit 2
fi
for exact_volume in "${smoke_project}_postgres_data"; do
    if docker volume inspect "${exact_volume}" >/dev/null 2>&1; then
        printf '%s\n' "Refusing to reuse existing smoke volume: ${exact_volume}" >&2
        exit 2
    fi
done
for exact_network in "${smoke_project}_backend" "${smoke_project}_frontend"; do
    if docker network inspect "${exact_network}" >/dev/null 2>&1; then
        printf '%s\n' "Refusing to reuse existing smoke network: ${exact_network}" >&2
        exit 2
    fi
done
created_scope=1
compose up --detach --build --wait --wait-timeout 240

migrate_id="$(compose ps --all --quiet migrate)"
test -n "${migrate_id}"
test "$(docker inspect --format '{{.State.ExitCode}}' "${migrate_id}")" = "0"

test "$(compose exec -T api id -u)" = "10001"
test "$(compose exec -T demo id -u)" = "10001"
console_shebang="$(compose exec -T api head -n 1 /opt/eve-rag/bin/uvicorn)"
case "${console_shebang}" in
    '#!/opt/eve-rag/bin/python'|'#!/opt/eve-rag/bin/python3') ;;
    *)
        printf '%s\n' "Runtime console script points outside the final virtual environment." >&2
        exit 1
        ;;
esac
compose exec -T api python -c "from pathlib import Path; forbidden = [path for root in (Path('/app/app'), Path('/app/migrations')) for path in root.rglob('*') if path.name in {'__pycache__', '.DS_Store'} or path.suffix in {'.pyc', '.pyo'}]; assert not forbidden, forbidden"

test "$(compose exec -T db psql -U eve -d eve_relation_rag -Atqc 'SELECT (SELECT count(*) FROM dataset_release), (SELECT count(*) FROM corpus_release);')" = "0|0"

if compose exec -T demo python -c "import socket; socket.getaddrinfo('db', 5432)" >/dev/null 2>&1; then
    printf '%s\n' "Demo unexpectedly resolved the database service." >&2
    exit 1
fi

demo_id="$(compose ps --quiet demo)"
docker inspect --format '{{json .Config.Env}}' "${demo_id}" | python3 -c "import json,sys; values=json.load(sys.stdin); forbidden=('EVE_RAG_DATABASE_URL=', 'EVE_RAG_LLM_PROVIDER=', 'POSTGRES_PASSWORD='); assert not any(item.startswith(forbidden) for item in values)"

compose exec -T demo python -c "from eve_relation_rag.demo.examples import load_demo_examples; examples=load_demo_examples(); assert len(examples) == 4; assert {item.family for item in examples} == {'structured', 'literature', 'hybrid', 'unsupported'}"
compose exec -T demo python -c "from streamlit.testing.v1 import AppTest; app=AppTest.from_file('/app/app/streamlit_app.py', default_timeout=20).run(); app.selectbox[0].select('Out-of-scope biological inference').run(); app.button[0].click().run(); assert not app.exception; assert any(item.value == 'NOT COMPLETED / unsupported_request' for item in app.subheader); assert [item.value for item in app.metric[-3:]] == ['HELD', 'HELD', 'HELD']"
compose exec -T demo python -c "from eve_relation_rag.demo.client import submit_query; from eve_relation_rag.demo.examples import load_demo_examples; from eve_relation_rag.hybrid.contracts import RagErrorResponse; result=submit_query(load_demo_examples()[3].request); assert result.status_code == 422; assert isinstance(result.response, RagErrorResponse); assert result.response.code == 'unsupported_request'; assert not any(result.response.execution.model_dump().values())"
compose exec -T demo python -c "from eve_relation_rag.demo.client import submit_query; from eve_relation_rag.demo.examples import load_demo_examples; from eve_relation_rag.hybrid.contracts import RagErrorResponse; result=submit_query(load_demo_examples()[2].request); assert result.status_code == 409; assert isinstance(result.response, RagErrorResponse); assert result.response.code == 'hybrid_binding_unavailable'; assert not any(result.response.execution.model_dump().values())"

python3 "${repo_root}/scripts/smoke_m5_http.py" \
    --api-base "http://127.0.0.1:${API_PORT}" \
    --demo-base "http://127.0.0.1:${DEMO_PORT}"
