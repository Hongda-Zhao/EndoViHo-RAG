# EndoViHo-RAG

An auditable hybrid RAG system for finding known endogenous viral elements across structured
data and literature.

## Current state: Milestone 2 structured retrieval complete

Milestone 2 provides a deterministic controlled-English parser, release-scoped resolver,
published-release capability gate, fixed SQLAlchemy compiler, membership-rooted repository,
HMAC-authenticated keyset pagination, typed results, FastAPI routes, and a Typer CLI. Both public
adapters use the same question-first application service; clients cannot submit SQL or an
arbitrary QueryPlan.

The current Zhao et al. pilot is still a **candidate**, not a public EVE release. Real requests
for `release:endoviho-rag:v0:20260826:001` therefore fail closed with
`release_not_published` and execute no public fact query. Synthetic success capabilities exist
only under `tests/` and cannot be supplied through the API or CLI.

## Milestone 1 verified staging retained

The Milestone 1 truth layer implements the user-approved Draft B contract. The frozen Zhao et
al. v4 pilot covers ten assembly accession.versions and all 39,495 selected VR rows matching
`Viral Major Taxon = Orthopolintovirales` and `Class = Bivalvia`.

| Staging result | Verified count |
|---|---:|
| Source VR calls and coordinate-free locus keys | 39,495 |
| `HCVR = Yes` → `source_high` | 71 |
| Other HCVR values → `source_low` | 39,424 |
| `Integration` rows with exact placements | 38,968 |
| `Viral contig` rows retained in quarantine | 527 |
| Exact assemblies / distinct source contigs | 10 / 12,233 |

`source_high` and `source_low` are source-relative assessments only. Neither confidence label
creates an inclusion decision or public release membership. The repository currently contains
no flank assessments, inclusion decisions, or public locus memberships.

The PostgreSQL schema contains 32 domain tables and Alembic head
`0005_m1_fail_closed_publication`. It separates physical source records from method-specific
detection calls, as well as loci, placements, assertions, evidence, decisions, and release
membership, so neither repeated analyses nor records sharing an interval are silently merged.

## Frozen provenance

- Canonical workbook: bioRxiv `649669_file12.xlsx`, physical sheet `S3`, 83,851,778 bytes,
  SHA-256 `79b5d99c095b359d93c834014863fffbbd5968a1dbadafe6a77133a1d690f800`.
- Assembly/sequence authority: NCBI Datasets v2 CLI `18.36.0`; both original JSONL reports,
  byte sizes, SHA-256 values, retrieval time, commands, and usage basis are frozen in the
  Milestone 1 manifest.
- Full-import audit: 39,495/39,495 exact assembly and contig resolutions, zero length
  mismatches, zero duplicate keys, and frozen order-independent call/locus key digests.

Large source artifacts remain outside Git. See `data/README.md` for the committed manifest and
audit boundary.

## Public-release boundary

Verified staging is not a published EVE release. Migration `0005_m1_fail_closed_publication`
hard-disables database status promotion to `validated` or `published` until a trusted, immutable
validation-receipt workflow is implemented. Publication also remains fail-closed until all
candidate memberships have independent supported left and right flank assessments, an explicit
authorized inclusion decision, complete frozen NCBI Taxonomy history, and the required ICTV
snapshot/release binding. Corrections to a published release must create a new immutable release.

## Local development

The project is developed on the local Apple Silicon Mac; gds2 remains a source/archive host.
Project-local tools and container state are ignored by Git.

```sh
. scripts/local-dev-env.sh
colima start --runtime docker --vm-type vz --arch aarch64 --cpus 2 --memory 2 --disk 20 --mount none --ssh-config=false --activate=false --binfmt=false
uv sync --locked --dev
docker compose up -d db
uv run alembic upgrade head
uv run python scripts/stage_milestone1.py
uv run pytest
uv run ruff check .
uv run mypy src
uv run alembic check
uv run uvicorn eve_relation_rag.api.app:app --reload
```

Set `EVE_RAG_CURSOR_HMAC_SECRET` to at least 32 random bytes before a structured fact query; no
default cursor key or bypass exists. The liveness endpoint is `GET /health`; the structured
question-first endpoints are `POST /v0/structured/plan` and `POST /v0/structured/query`.

```sh
uv run eve-relation-rag structured plan \
  --release-key release:endoviho-rag:v0:20260826:001 \
  --question "List all loci in this release."

uv run eve-relation-rag structured query \
  --release-key release:endoviho-rag:v0:20260826:001 \
  --question "Count distinct included loci in this release."
```

These examples intentionally demonstrate the real publication refusal until a trusted immutable
receipt and all biological inclusion gates exist. Stop services with `docker compose down` and
`colima stop`; the PostgreSQL named volume is preserved unless explicitly removed.

Detailed implementation status and scientific semantics are recorded in
`docs/development_status.md` and `docs/data_semantics.md`.
