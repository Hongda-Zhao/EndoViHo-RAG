# Development status

> Product version: V0
>
> Current milestone: Milestone 2 — Draft B M2.0-M2.5 complete
>
> Status: full M2 mechanics implemented; real public-release activation intentionally blocked
>
> Last verified: 2026-08-27 (Asia/Tokyo)

## Milestone 2 outcome

The approved merged Draft B is implemented through M2.5. M2.0 maps the existing 32-table
Milestone 1 truth layer to public retrieval semantics; M2.1 provides the strict plan/result/error
schemas. M2.2 adds deterministic controlled-English parsing and exact release-scoped resolution.
M2.3 adds the published-release capability gate, semantic validator, fixed SQLAlchemy compiler,
membership-rooted repository, and all five metrics. M2.4 adds HMAC-SHA-256 keyset cursors and
deterministic presentation. M2.5 composes the same application through FastAPI and Typer, freezes
the 31-case controlled-English planning/contract benchmark, and verifies facts separately through
a PostgreSQL production-path matrix.

No M2 migration or scientific data mutation was required. The current candidate still cannot
produce a public success response: the production gate requires `published` plus a trusted,
immutable validation receipt and rejects the real pilot before resolver or fact retrieval.

Key artifacts:

- `docs/milestone_2_contract.md`: approved merged Draft B and staged M2.0-M2.5 boundary.
- `docs/milestone_2_schema_mapping.md`: read-only M1-to-M2 authority and projection map.
- `src/eve_relation_rag/planning/query_plans.py`: strict plans, audits, canonical JSON, and hash.
- `src/eve_relation_rag/planning/parser.py` and `resolver.py`: controlled grammar and exact
  release-scoped entity resolution.
- `src/eve_relation_rag/retrieval/structured/results.py`: strict result and error envelopes.
- `src/eve_relation_rag/retrieval/structured/`: capability gate, validator, compiler, repository,
  cursor, pagination, service, serialization, and presentation.
- `src/eve_relation_rag/api/app.py` and `src/eve_relation_rag/cli.py`: question-first public
  adapters over one application service.
- `tests/benchmark/`: 31-case controlled-English planning and contract oracle.
- `tests/retrieval/structured/test_m23_postgres.py`: production compiler/repository fact matrix;
  `tests/planning/` and the remaining retrieval tests cover schema and boundary behavior.

### Frozen Milestone 2 parameters

| Parameter | Approved value |
|---|---|
| Plan version / route | `endoviho-query-plan-v0.1` / `structured` |
| Response / result schema | `structured-query-response-v1` / `structured-result-v1` |
| Plan intents / result data kinds | 6 / 6 |
| Exact aggregate metrics | 5, including `distinct_contig_count` |
| Filter combination | AND only; at most 3 distinct filter types |
| Page limit | default 50; strict range 1..100 |
| Canonical plan hash | SHA-256 of sorted canonical JSON; only cursor is normalized to null |
| Release syntax | exact `release:endoviho-rag:v0:YYYYMMDD:NNN`; status remains gate-owned |
| Release authorization | exact key, `published`, valid manifest, trusted immutable receipt |
| Resolver order | assembly, locus, snapshot-qualified term, canonical name, curated alias |
| Cursor | forward keyset; HMAC-SHA-256; runtime secret at least 32 bytes |
| Public adapters | `POST /v0/structured/plan`, `POST /v0/structured/query`, Typer CLI |
| Query technology excluded | arbitrary SQL, LLMs, embeddings, and literature retrieval |

## Milestone 1 retained outcome

Milestone 1 implements the approved Draft B pilot as an auditable staging truth layer. It does
not claim that the staged rows are publishable EVEs. All selected source rows have deterministic
calls and terminal outcomes; source confidence, placement, scientific assertions, evidence,
curation decisions, and public release membership remain separate objects.

## Implemented scope

- PostgreSQL/SQLAlchemy truth schema with 32 domain tables.
- Alembic revisions `0002_milestone_1_truth_layer`, `0003_m1_assertion_evidence`,
  `0004_m1_shared_intervals`, and `0005_m1_fail_closed_publication`; current head is
  `0005_m1_fail_closed_publication`.
- Streaming XLSX importer for the canonical physical worksheet `S3`.
- Streaming byte-size/SHA-256 verification for the workbook and NCBI JSONL inputs.
- Exact ten-assembly and 12,233-contig resolution against frozen NCBI Datasets v2 reports.
- Independent deterministic identities for the immutable physical source row and each
  method-specific detection call; the latter includes the native assembly/contig/VR occurrence
  plus method/run identity.
- Coordinate-free locus identity using source snapshot, assembly, contig, native VR token, and
  identity-policy version; coordinates live in a separate placement object.
- Complete all-VR audit with frozen counts and order-independent call/locus key digests.
- Atomic, idempotent PostgreSQL staging with source records, calls, assessments, assertions,
  evidence, placements, and quarantine ledger rows.
- Release-membership schema and fail-closed validator gates without automatic membership;
  database triggers hard-disable promotion to `validated` or `published` until a trusted,
  immutable validation-receipt workflow exists.
- Immutable published-release guards and support for distinct source occurrences sharing an
  exact interval.
- Complete M2 deterministic structured-query stack with six intents, five metrics, AND-only
  filters, exact membership projections, and tests-only synthetic success capabilities.
- FastAPI liveness/structured endpoints, Typer CLI, deterministic tests, Ruff, mypy, and
  PostgreSQL-backed CI.

## Frozen pilot results

Selection is exactly the approved ten assembly accession.versions with source column
`J = Orthopolintovirales` and column `M = Bivalvia`; every VR value is included.

| Result | Count |
|---|---:|
| Selected source records / `DetectionCall`s | 39,495 |
| Unique coordinate-free locus keys | 39,495 |
| `source_high` (`HCVR = Yes`) | 71 |
| `source_low` (all other selected HCVR values) | 39,424 |
| `Integration` normalized candidates with exact placements | 38,968 |
| `Viral contig` terminal policy quarantines | 527 |
| Exact assemblies | 10 |
| Distinct source contigs resolved exactly | 12,233 |
| Source organism names | 9 |
| NCBI/source contig-length mismatches | 0 |
| Duplicate call, locus, or row-locator keys | 0 |

The two confidence branches sum to all 39,495 calls. The two VR-type branches also sum to all
39,495 terminal outcomes. No row is silently removed.

## Scientific and publication boundary

The following objects currently have no staged or public rows:

- independent left-flank assessments;
- independent right-flank assessments;
- explicit inclusion decisions;
- public `ReleaseLocusMembership` rows;
- public assertion memberships.

`source_high` is not evidence that both host flanks exist, and `source_low` is not an automatic
exclusion. Likewise, source `VR Type = Integration` is a Zhao et al. assertion, not a substitute
for independent flank evidence.

Public release remains blocked until:

1. both left and right flank assessments are independently recorded as `supported` under the
   same approved policy;
2. an explicit human- or policy-authorized `include` decision exists;
3. complete frozen NCBI Taxonomy assignments and merged/deleted TaxId history are loaded;
4. the required ICTV taxonomy snapshot is loaded and bound to the candidate release;
5. the release validator passes integrity, count, provenance, license, checksum, deterministic
   key, and reproducibility checks.

## Frozen source and authority provenance

### Zhao et al. Data S1

| Parameter | Frozen value |
|---|---|
| Source snapshot | `study-defined:10.1101/2025.04.19.649669:v4:data-s1` |
| Canonical filename | `649669_file12.xlsx` |
| Physical worksheet | `S3` |
| Byte size | `83,851,778` |
| SHA-256 | `79b5d99c095b359d93c834014863fffbbd5968a1dbadafe6a77133a1d690f800` |
| Conservative artifact license basis | `CC-BY-NC-ND-4.0` |
| Remote verification | byte-exact bioRxiv DC6/media-6 artifact verified |

The user's `Data S1.xlsx` is a semantically equivalent metadata-edited working copy, not the
canonical release artifact. Its different bytes and worksheet name are recorded separately.

### NCBI resolution package

| Parameter | Frozen value |
|---|---|
| Authority snapshot | `authority:ncbi-datasets-v2:18.36.0:20260826:pilot-resolution` |
| NCBI Datasets CLI | `18.36.0` |
| Assembly JSONL | 10 records; 39,377 bytes; SHA-256 `adcbef683cbc1ad592464e6a7ec64bd3d5612b91e4d44fb531d5d4cfdf4d81d4` |
| Sequence JSONL | 220,512 records; 59,941,556 bytes; SHA-256 `c96695fc44481f4b08c6bd4e56a439efb9baaf9332c8337d048fe5dab345e425` |
| Resolution result | 10/10 assemblies and 12,233/12,233 selected contigs exact; zero length mismatches |
| Usage basis | `NCBI-MOLECULAR-DATA-USAGE-POLICY` |

The raw reports, retrieval time, exact commands, binary checksum, and usage-basis URL are frozen
in `data/manifests/milestone1_zhao_v4_data_s1.json`.

### Frozen execution parameters

| Parameter | Approved value |
|---|---|
| Importer/method identity | `zhao-data-s1-import-v2` |
| Physical source-record key schema | `zhao-data-s1-source-record-v1` |
| Method-specific call-key schema | `zhao-data-s1-detection-call-v2` |
| Coordinate-free locus policy | `zhao-v4-contig-source-occurrence-v1` |
| Placement coordinate system | `0-based-half-open` |
| Default database batch size | `1,000` rows |
| Importer SHA-256 | `e9ff3cfcbcb3f20a6971b245ddd2d7fbbbe552a96021f33c8ac70a6f8c7be514` |
| Audit module SHA-256 | `93cbead58cdfca828a97c33bed1ab21a6d31c6115e52835a67e139f40f640b98` |
| Staging module SHA-256 | `d66113be75cd02dc5353fb2cce7784d0a7e6c7f6597cf89ed805913569a97ffb` |
| Combined execution-code SHA-256 | `6e112712651bbdfdf96848d857a381a6f7d4e6f618f6f4cbd1a1c6cc7aaf42d6` |
| Sorted call-key set SHA-256 | `0b204b937aa53bcb286f555e85817d360ba5288ad23e3ba865191179730debae` |
| Sorted locus-key set SHA-256 | `cfba1fa2f70f6ea7f297fbffa67ac6f76c67e11be23687bc688896a2830b4fcc` |

The committed audit artifact records these hashes with the tool versions and verified input
checksums; any execution-code change therefore produces a distinct deterministic import run.

## Database status

| Item | Current value |
|---|---|
| Database | PostgreSQL 16 with pgvector available |
| ORM / migration | SQLAlchemy 2.x / Alembic |
| Domain tables | 32 |
| Baseline | `0001_empty_baseline` |
| Migration head | `0005_m1_fail_closed_publication` |
| Published release | none |
| Public locus membership rows | none |

Revision 0004 ensures two distinct source-occurrence loci may share one exact interval without
coordinate-based deduplication. Revision 0005 binds each import-ledger outcome to the same
physical source record as its call and locus, permits method-specific calls without conflating
them with source-row identity, and hard-disables database promotion to `validated` or `published`
until a trusted, immutable validation receipt can authorize it. Published or deprecated
release-scoped rows remain immutable; corrections require a new release and explicit
supersession.

## API, CLI, and deferred scope

Implemented API surface is `GET /health`, `POST /v0/structured/plan`, and
`POST /v0/structured/query`. The CLI exposes `structured plan` and `structured query`. These
interfaces do not claim that the candidate is published: the real candidate returns
`release_not_published` with `fact_retrieval_executed = false`.

Deferred beyond Milestone 2:

- trusted immutable validation-receipt and publication workflow;
- literature ingestion, chunking, full-text/vector retrieval, and citations;
- embedding or LLM providers;
- complete global Zhao et al. data beyond the ten-assembly pilot;
- the Guinet adapter and additional source adapters;
- demo and evaluation layers.

## Development environment and tools

| Tool | Recorded version or project choice |
|---|---|
| Active build host | Local Apple Silicon Mac; gds2 is source/archive only |
| Project Python | CPython `3.12.14` |
| Environment/lock manager | `uv 0.12.5` |
| API | FastAPI |
| CLI | Typer `0.27.1` |
| Validation | Pydantic v2, database constraints, audit, release validator |
| Cursor authentication | HMAC-SHA-256; no default secret or bypass |
| Database | PostgreSQL 16 / pgvector image |
| Migration | Alembic head `0005_m1_fail_closed_publication` |
| Tests | pytest, including PostgreSQL integration tests |
| Lint / static typing | Ruff / mypy strict mode |
| CI | GitHub Actions on push and pull request |
| Local container runtime | Colima + Docker Compose |

Exact Python dependency versions and package hashes remain in `uv.lock`. Project-local binaries,
the virtual environment, caches, Colima state, and large source artifacts are ignored by Git.

## Verification commands

```sh
. scripts/local-dev-env.sh
docker compose up -d db
uv sync --locked --dev
uv run alembic upgrade head
uv run python scripts/stage_milestone1.py
uv run pytest
uv run ruff check .
uv run mypy src
uv run alembic check
```

CI runs the migration, Alembic model-drift check, complete pytest suite, Ruff, and mypy against
PostgreSQL. Branch
protection/required-check enforcement is a GitHub repository setting and is not asserted by the
local checkout.

Final local full-M2 verification on 2026-08-27 completed with `364 passed` and one upstream
FastAPI/Starlette TestClient deprecation warning. Ruff, mypy strict (`40` source files), lockfile
reproducibility, targeted formatting, and `alembic check` all passed; Alembic reported no new
upgrade operations. These checks remain mandatory in CI and do not change the candidate-only
publication state.
