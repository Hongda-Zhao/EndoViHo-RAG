from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parents[2]


def test_docker_build_uses_an_allowlist_and_non_root_runtime() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()

    assert "COPY . ." not in dockerfile
    assert "USER ${APP_UID}:${APP_GID}" in dockerfile
    assert "uv sync --frozen --no-dev --extra demo --no-editable" in dockerfile
    assert dockerignore[0] == "*"
    assert {
        "!Dockerfile",
        "!LICENSE",
        "!README.md",
        "!alembic.ini",
        "!pyproject.toml",
        "!uv.lock",
        "!.streamlit/config.toml",
        "!app/**",
        "!migrations/**",
        "!src/**",
    } <= set(dockerignore)
    assert {
        "**/__pycache__/",
        "**/__pycache__/**",
        "**/*.py[cod]",
        "**/.DS_Store",
    } <= set(dockerignore)
    assert not any(item.startswith("!data") or item.startswith("!tests") for item in dockerignore)


def test_compose_orders_services_and_keeps_demo_off_the_database_network() -> None:
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    demo_section = compose.split("\n  demo:\n", maxsplit=1)[1].split("\nnetworks:\n", maxsplit=1)[0]

    assert all(f"\n  {service}:\n" in compose for service in ("db", "migrate", "api", "demo"))
    assert "condition: service_healthy" in compose
    assert "condition: service_completed_successfully" in compose
    assert "127.0.0.1:${API_PORT:-8000}:8000" in compose
    assert "127.0.0.1:${DEMO_PORT:-8501}:8501" in compose
    assert "EVE_RAG_DEMO_API_BASE_URL: http://api:8000" in demo_section
    assert "EVE_RAG_DATABASE_URL" not in demo_section
    assert "EVE_RAG_LLM_PROVIDER" not in demo_section
    assert "- backend" not in demo_section
    assert "seed" not in compose.casefold()


def test_container_smoke_pins_compose_scope_and_refuses_existing_resources() -> None:
    smoke = (ROOT / "scripts/container_smoke.sh").read_text(encoding="utf-8")

    assert '--file "${repo_root}/compose.yaml"' in smoke
    assert '--project-directory "${repo_root}"' in smoke
    assert '--project-name "${smoke_project}"' in smoke
    assert 'docker volume inspect "${exact_volume}"' in smoke
    assert 'docker network inspect "${exact_network}"' in smoke
    assert 'python3 "${repo_root}/scripts/smoke_m5_http.py"' in smoke
    assert 'EVE_RAG_IMAGE="${M5_SMOKE_IMAGE:-eve-relation-rag:${smoke_project}}"' in smoke
    assert "submit_query(load_demo_examples()[3].request)" in smoke
    assert "submit_query(load_demo_examples()[2].request)" in smoke
    assert "app.button[0].click().run()" in smoke
    assert "NOT COMPLETED / unsupported_request" in smoke
    assert "path.name in {'__pycache__', '.DS_Store'}" in smoke
    assert "trap cleanup EXIT" in smoke
    assert "trap 'exit 130' INT" in smoke
    assert "trap 'exit 143' TERM" in smoke
