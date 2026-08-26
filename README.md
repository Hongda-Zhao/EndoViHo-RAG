# EndoViHo-RAG

An auditable hybrid RAG system for finding known endogenous viral elements across structured
data and literature.

## Milestone 0 local development

Milestone 0 is developed and verified on the local Apple Silicon Mac. The project-local tool
binaries and container state live in ignored directories, so they do not change global Docker or
Python installations.

```sh
. scripts/local-dev-env.sh

colima start \
  --runtime docker \
  --vm-type vz \
  --arch aarch64 \
  --cpus 2 \
  --memory 2 \
  --disk 20 \
  --mount none \
  --ssh-config=false \
  --activate=false \
  --binfmt=false

uv sync --locked --dev
docker compose up -d db
uv run alembic upgrade head
uv run pytest
uv run ruff check .
uv run mypy src
uv run uvicorn eve_relation_rag.api.app:app --reload
```

The API health check is available at `http://127.0.0.1:8000/health`. Stop the local services with
`docker compose down` and `colima stop`; the PostgreSQL named volume is preserved unless it is
explicitly removed.

The current implementation and exact verified versions are recorded in
`docs/development_status.md`.
