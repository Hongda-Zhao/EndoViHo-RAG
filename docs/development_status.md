# Development status

> Product version: V0
> Current milestone: Milestone 0 — Repository scaffold
> Status: complete and verified in the local development environment
> Last verified: 2026-08-25 (Asia/Tokyo)

## Implemented scope

- Installable Python package under `src/eve_relation_rag`.
- Environment-backed Pydantic settings.
- FastAPI application with `GET /health`.
- Alembic configuration with one empty baseline migration.
- Docker Compose definition for PostgreSQL 16 with pgvector available in the image.
- Deterministic unit tests that do not call paid or external model providers.
- Ruff, mypy, and pytest configuration.
- GitHub Actions checks for the migration, tests, linting, and type checking.

## Explicitly not implemented

No domain tables, scientific rules, pilot importer, document ingestion, embedding or LLM
provider, retrieval route, query planner, demo, or release logic has been implemented.
The scientific decisions listed as unresolved in the build guide remain unresolved.

## Key parameters

| Parameter | Value |
|---|---|
| Product version | `V0` |
| Source repository | `https://github.com/Hongda-Zhao/EndoViHo-RAG` |
| Package metadata version | `0` (PEP 440 representation of V0) |
| Python constraint | `>=3.12,<3.13` |
| Health endpoint | `GET /health` |
| Default environment | `development` |
| Settings prefix | `EVE_RAG_` |
| Default database host/port | `localhost:5432` |
| Default database name | `eve_relation_rag` |
| Compose service | `db` |
| PostgreSQL image | `pgvector/pgvector:pg16` |
| Baseline revision | `0001_empty_baseline` |
| Active development host | Local Apple Silicon Mac |
| gds2 role | Remote source/archive copy; not the active Docker Compose host |

The credentials in `.env.example` and Compose defaults are development-only values.
Production credentials are intentionally not defined in the repository.

## Tools and pinned automation

| Tool | Recorded version or project choice |
|---|---|
| Build host | macOS `26.5.1`, arm64 |
| Host system Python | `3.10.4` (not used by the project) |
| Project-managed Python | CPython `3.12.14` |
| Environment and lock manager | `uv 0.12.5` |
| Git | `2.37.2`; branch `main`; `origin` tracks the GitHub repository above |
| API | FastAPI |
| Configuration | Pydantic Settings v2 |
| Database migration | Alembic |
| Database access dependencies | SQLAlchemy 2.x and psycopg 3 |
| Unit tests | pytest |
| Linting and import sorting | Ruff |
| Static typing | mypy strict mode |
| Database service definition | Docker Compose with PostgreSQL 16 + pgvector |
| Local VM/runtime | Colima `0.10.3`, Lima `2.2.0`, Docker Engine `29.5.2` |
| Local container clients | Docker CLI `29.7.2`, Docker Compose `5.5.0` |
| Verified database image contents | PostgreSQL `16.15`, pgvector `0.8.6` available |
| Colima VM parameters | Apple Virtualization Framework (`vz`), arm64, 2 CPU, 2 GiB RAM, 20 GiB disk, no project bind mount |
| CI | GitHub Actions |
| CI uv version | `0.12.5` |
| CI setup-uv action | `v9.0.0`, pinned by commit SHA |
| CI checkout action | `v7.0.1`, pinned by commit SHA |

Project-local tool binaries, Colima configuration, and the managed interpreter are under the
ignored `.tools/` directory. The virtual environment is under ignored `.venv/`; the uv cache is
under ignored `.uv-cache/`. Exact Python dependency versions and artifact hashes are recorded in
`uv.lock`. Source `scripts/local-dev-env.sh` before using the local commands so Docker, Colima,
and uv use only these project-local paths.

## Locked core dependencies

| Package | Locked version |
|---|---|
| FastAPI | `0.141.1` |
| Pydantic | `2.13.4` |
| pydantic-settings | `2.15.0` |
| SQLAlchemy | `2.0.52` |
| psycopg / psycopg-binary | `3.3.4` |
| Alembic | `1.19.1` |
| pytest | `8.4.2` |
| Ruff | `0.16.4` |
| mypy | `1.20.2` |

## Milestone 0 exit-command verification

| Command | Result | Evidence |
|---|---|---|
| `docker compose up -d db` | **PASS** | `eve-relation-rag-db-1` is healthy and exposes local port 5432. |
| `uv run alembic upgrade head` | **PASS** | Applied `0001_empty_baseline` against the local PostgreSQL container. |
| `uv run pytest` | **PASS** | 4 tests passed; one upstream TestClient deprecation warning. |
| `uv run ruff check .` | **PASS** | `All checks passed!` |
| `uv run mypy src` | **PASS** | No issues in 5 source files. |

### Additional smoke checks

- A live Uvicorn process returned
  `{"status":"ok","service":"EVE Relation RAG","version":"V0"}` from `GET /health`.
- Docker Compose reports the database container healthy; pgvector `0.8.6` is available in the
  running image.
- After the migration, the only table in the `public` schema was `alembic_version`; no domain
  tables were created.
- `uv sync --locked --dev` completed successfully with 43 resolved packages.

## Local operating commands

```sh
. scripts/local-dev-env.sh
colima start --runtime docker --vm-type vz --arch aarch64 --cpus 2 --memory 2 --disk 20 --mount none --ssh-config=false --activate=false --binfmt=false
docker compose up -d db
uv run alembic upgrade head
```

Use `docker compose down` to stop the database container and network while preserving the named
database volume. Use `colima stop` when the local VM is no longer needed.

## Why development moved off gds2

gds2 does not provide the Docker CLI or Docker Compose. Its rootless Podman cannot unpack the
image because the account has no subordinate UID/GID ranges and the default container store is
on Lustre, which rejects required extended-attribute operations. Apptainer was used only to
validate the image during diagnosis. The active local Colima/Docker environment now satisfies
the exact Docker Compose exit command, so Milestone 0 is no longer blocked.
