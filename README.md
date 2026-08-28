# EndoViHo-RAG

An auditable hybrid RAG system for finding known endogenous viral elements across structured
data and literature.

## Current state: Milestone 4 mechanism FULFILLED; real activation blocked

The approved Milestone 4 Draft A mechanism is present on
`codex/milestone-4-hybrid-rag`: strict routed contracts, a deterministic four-route grammar,
checksum-pinned exact release binding, trusted same-corpus anchor resolution, immutable
`ContextPack`, a dependency-free LLM provider boundary, constrained claims, all-or-none
mechanical validation, deterministic rendering, one shared application service, `POST /v0/query`,
and `eve-relation-rag rag query`. The final repository-wide test, benchmark, lock, migration,
lint/type, documentation, and diff gates passed on the final local working tree. This is an
engineering-mechanism fulfillment record, not a claim that real hybrid generation is activated
or that remote CI has completed.

| Final local gate | Result |
|---|---|
| Full PostgreSQL pytest suite | `682 passed, 1 warning` |
| Frozen M2/M3/M4 benchmark selection | `72 passed` |
| Ruff | passed |
| strict mypy | passed for `78 source files` |
| Lock verification | `uv lock --check` passed with `92 packages` |
| Alembic | sole head `0010_m3_lock_hardening`; current database and a temporary empty-database upgrade from `0001_empty_baseline` through `0010_m3_lock_hardening` both reported no model drift; the temporary database was deleted |
| Patch hygiene | Markdown checks and `git diff --check` passed |

M4 adds no Alembic revision, schema change, production data mutation, or generated-answer write
path. Its local PR exit gate is fulfilled; pull-request and remote-CI state are tracked
separately from mechanism fulfillment.

Real generation remains intentionally disabled. Production accepts only
`EVE_RAG_LLM_PROVIDER=disabled` and composes no provider. No real dataset/corpus binding manifest
is approved, the Zhao structured release is still candidate-only, and the published corpus has
document/keyword anchors but no locus, assembly, lineage, or method anchors derivable from a
structured result. Human semantic-support review is also still required before activation.

The M4 outer grammar is deliberately narrow:

| Route | Question shape | Exact selectors |
|---|---|---|
| Structured | unchanged M2 `show`, `list`, or `count` family | structured `release_key` only |
| Literature | `Explain the literature evidence for <topic>`, `Explain the literature methods for <topic>`, or `Explain the literature limitations for <topic>` | `corpus_release_key` only |
| Hybrid | one M2 clause plus exactly one terminal `and explain the literature evidence`, `and explain the literature methods`, or `and explain the literature limitations` suffix | both exact release keys |
| Unsupported | anything else, a selector mismatch, duplicate suffix, or prohibited topic | no downstream calls |

M4 limits context to 131,072 UTF-8 bytes, literature `top_k` to 8, generated claims to 16,
trusted anchors to 64, and provider output to 32,768 UTF-8 bytes. Citation IDs, exact evidence
spans, identifiers, numeric tokens, hashes, and forbidden-inference patterns are validated
mechanically. `validation_scope="mechanical"` is traceability, not proof of scientific
entailment; real activation requires a separate checksum-bound human claim review.

### Milestone 3 published corpus retained

Milestone 3 implements the fixed-corpus literature path: checksum-bound local Markdown,
plain-text, and safe JATS ingestion; stable locators; BGE-tokenizer chunking; PostgreSQL English
FTS; local 384D embeddings; pgvector HNSW cosine search; deterministic RRF; typed curated
anchors; strict `RetrievedChunks`; independent rebuild validation; benchmark receipts; and
fail-closed publication.

The approved 11-document Europe PMC pilot, `corpus:endoviho-rag:v0:20260828:001`, is published
with 1,464 chunks, 1,464 embeddings, and 22 curated anchors. Its v2 retrieval policy fuses three
equal RRF60 branches: English weighted FTS, full-chunk dense retrieval, and title/abstract dense
retrieval, each at depth 100. The 13-question pinned-model pilot passed with Recall@5
`0.846153846154`, Recall@10 `1.000000000000`, citation validity `1.000000000000`, and locator
validity `1.000000000000`; responses use `retrieved-chunks-v2`.

The release is bound to corpus manifest
`1497ea3383bea64d2bc4f17d2376dceb537b4f6c6f57ccb6eaf667b6589732f0`, anchor manifest
`75a523bc6408f13b07ba283e6539734ec3b694f3dab59994a464d40d98b01fca`, and model artifact
manifest `0dc66d301fc8305bae93aa197200a176a61be13a302c3fee430cd2efc744241a`. Direct literature
retrieval remains available as a developer CLI command. The M4 routed endpoint can authorize the
same exact corpus, but production answer generation stops with `llm_provider_unavailable` after
successful non-empty retrieval because no provider is approved.

## Milestone 2 structured retrieval retained

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

The PostgreSQL schema contains 43 domain tables and Alembic head
`0010_m3_lock_hardening`: 32 structured truth tables plus 11 independent literature
tables. The structured layer still separates physical source records from method-specific
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

Verified structured staging is not a published EVE release. Migration
`0005_m1_fail_closed_publication`
hard-disables database status promotion to `validated` or `published` until a trusted, immutable
validation-receipt workflow is implemented. Publication also remains fail-closed until all
candidate memberships have independent supported left and right flank assessments, an explicit
authorized inclusion decision, complete frozen NCBI Taxonomy history, and the required ICTV
snapshot/release binding. Corrections to a published release must create a new immutable release.

The literature corpus has a separate, completed publication lifecycle. Database administration
is the trusted control plane for staging, receipt creation, and status transitions; these guards
do not attempt to defend against a malicious database administrator. A status value alone does
not authorize retrieval: the application gate also verifies the exact manifest, immutable
receipt evidence, policy identities and hashes, recomputed policy graph, approved model artifact,
license boundary, and complete embeddings before issuing a query capability.

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

The local BGE runtime is a separately locked optional dependency. Install it only on a host that
holds the exact approved, locally verified model artifacts:

```sh
uv sync --locked --dev --extra local-embeddings
```

Configure `EVE_RAG_EMBEDDING_MODEL_PATH`,
`EVE_RAG_EMBEDDING_ARTIFACT_MANIFEST_PATH`, and
`EVE_RAG_EMBEDDING_ARTIFACT_MANIFEST_SHA256` with that exact package. Mutating corpus operations
also require an explicit `--import-root`; there is no model, corpus, or checksum default.

Set `EVE_RAG_CURSOR_HMAC_SECRET` to at least 32 random bytes before a structured fact query; no
default cursor key or bypass exists. The liveness endpoint is `GET /health`; the structured
question-first endpoints are `POST /v0/structured/plan` and `POST /v0/structured/query`; and the
M4 routed endpoint is `POST /v0/query`.

```sh
uv run eve-relation-rag structured plan \
  --release-key release:endoviho-rag:v0:20260826:001 \
  --question "List all loci in this release."

uv run eve-relation-rag structured query \
  --release-key release:endoviho-rag:v0:20260826:001 \
  --question "Count distinct included loci in this release."
```

The equivalent M4 structured route uses the new shared surface and does not construct literature,
embedding, binding, or LLM dependencies:

```sh
uv run eve-relation-rag rag query \
  --release-key release:endoviho-rag:v0:20260826:001 \
  --question "Count distinct included loci in this release."
```

It currently returns canonical `rag-error-v1` with `code="structured_refused"` and upstream
`release_not_published`, because the Zhao release is intentionally candidate-only. A hybrid-form
question additionally supplies `--corpus-release-key` and ends in exactly one approved literature
suffix, but current production configuration refuses it at `hybrid_binding_unavailable` before
fact retrieval because no real binding manifest is approved.

The same request shape is available over HTTP:

```sh
curl -sS http://127.0.0.1:8000/v0/query \
  -H 'content-type: application/json' \
  -d '{"release_key":"release:endoviho-rag:v0:20260826:001","question":"Count distinct included loci in this release."}'
```

The M3 developer namespace contains these explicit, checksum-bound operations:

```text
eve-relation-rag literature manifest-validate
eve-relation-rag literature corpus-stage
eve-relation-rag literature benchmark
eve-relation-rag literature corpus-validate
eve-relation-rag literature corpus-publish
eve-relation-rag literature retrieve
```

All mutating and evaluation commands require the exact approved inputs they consume:
`corpus-stage` binds corpus, anchor, and model artifacts, while `benchmark` and
`corpus-validate` additionally bind the benchmark definition. They never download documents or a
model, infer `latest`, or publish implicitly. Publication requires the exact manifest and
immutable passing receipt checksums.

The structured examples intentionally demonstrate refusal for the candidate-only Zhao release;
that boundary is independent of the published literature corpus. Stop services with
`docker compose down` and `colima stop`; the PostgreSQL named volume is preserved unless
explicitly removed.

Detailed implementation status and scientific semantics are recorded in
`docs/development_status.md` and `docs/data_semantics.md`.
