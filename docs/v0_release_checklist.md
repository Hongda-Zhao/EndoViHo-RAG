# V0 release checklist

> Deterministic projection of `release/v0_release_checklist.json`.

## Status

- M5 engineering: `fulfilled`
- Software distribution: `preview_ready_not_published`
- V0 Definition of Done: `blocked`
- Real hybrid activation qualified: `false`

## Gates

| ID | Category | Status | Evidence |
|---|---|---|---|
| `M5-DEMO` | `m5_packaging` | **PASS** | HTTP-only Streamlit workbench and AppTest/client boundary tests |
| `M5-DOCKER` | `m5_packaging` | **PASS** | fresh-volume build, migration, health, Demo-to-API, fail-closed HTTP, non-root, and cleanup smoke |
| `M5-DOCS` | `m5_packaging` | **PASS** | README, milestone contract/status, semantic boundary, benchmark, and checklist projections |
| `M5-METADATA` | `m5_packaging` | **PASS** | MIT, DATA_LICENSE, CITATION.cff, and Unreleased changelog |
| `M5-QUALITY` | `m5_packaging` | **PASS** | 724 full tests, 72 frozen benchmark cases, Ruff, strict mypy over 84 source files, and 114-package lock check |
| `M5-MIGRATIONS` | `m5_packaging` | **PASS** | single 0010 head, current/check parity, and clean 0001-to-0010 replay |
| `M5-PACKAGE` | `m5_packaging` | **PASS** | audited wheel and sdist contain required metadata/resources and exclude restricted artifacts |
| `M1-TRUTH-SCHEMA` | `engineering` | **PASS** | normalized PostgreSQL schema and frozen M1 audit |
| `M2-STRUCTURED-MECHANISM` | `engineering` | **PASS** | 31-case controlled-English contract benchmark |
| `M3-LITERATURE-MECHANISM` | `engineering` | **PASS** | published exact corpus, trusted receipt, and pinned-model benchmark |
| `M4-HYBRID-MECHANISM` | `engineering` | **PASS** | router/orchestration/mechanical-generation gates |
| `V0-ENGLISH-ONLY` | `v0_definition_of_done` | **PASS** | strict ASCII English contracts and English-only demo |
| `V0-POSTGRES-TRUTH` | `v0_definition_of_done` | **PASS** | PostgreSQL is the only structured truth source |
| `V0-LAYER-SEPARATION` | `v0_definition_of_done` | **PASS** | facts, chunks, ContextPack, and generated presentation remain typed layers |
| `V0-LOCUS-VERSION-COORDINATES` | `v0_definition_of_done` | **BLOCK** | mechanism exists, but no published structured membership is available for per-locus audit and 527 quarantined viral-contig calls lack exact placement |
| `V0-AUDIT-LAYER-SEPARATION` | `v0_definition_of_done` | **PASS** | source calls, loci, assertions, evidence, and release memberships are distinct audited objects |
| `V0-LINEAGE-SCHEME-SNAPSHOT` | `v0_definition_of_done` | **BLOCK** | scheme/snapshot fields exist, but the required formal ICTV snapshot is not loaded and bound for the real release |
| `V0-AMBIGUITY-FAIL-CLOSED` | `v0_definition_of_done` | **PASS** | unresolved and ambiguous entities return typed refusals without broad-query fallback |
| `V0-LLM-NO-SQL-NO-MUTATION` | `v0_definition_of_done` | **PASS** | provider receives immutable ContextPack JSON and cannot generate SQL or mutate StructuredResult |
| `V0-FIXED-LITERATURE-RETRIEVAL` | `v0_definition_of_done` | **PASS** | exact published corpus uses fixed English FTS, pgvector branches, and RRF60 fusion |
| `V0-DOCUMENT-CLAIM-CITATIONS` | `v0_definition_of_done` | **PASS** | mechanical validators require every accepted document-derived claim to cite a current locatable chunk; semantic review remains separate |
| `V0-DOCKER-COLD-START` | `v0_definition_of_done` | **PASS** | fresh-volume db, migration, API, and Demo startup passed with canonical empty-state refusals |
| `V0-REPOSITORY-RELEASE-ASSETS` | `v0_definition_of_done` | **PASS** | Git contains tests, benchmark records, software/data notices, citation metadata, and Unreleased changelog |
| `V0-README-COVERAGE-LIMITS` | `v0_definition_of_done` | **PASS** | README states scientific, data, activation, and local quick-start limitations |
| `V0-THREE-REAL-ROUTES` | `v0_definition_of_done` | **BLOCK** | fresh clone has no real structured/hybrid success and production generation is disabled |
| `V0-PUBLISHED-STRUCTURED-RELEASE` | `real_activation` | **BLOCK** | Zhao release:endoviho-rag:v0:20260826:001 remains candidate-only |
| `V0-REAL-BINDING-ANCHORS` | `real_activation` | **BLOCK** | no approved binding manifest or structured-target anchors |
| `V0-PRODUCTION-GENERATION` | `real_activation` | **BLOCK** | LLM provider, prompt policy, credentials, and egress are not approved |
| `V0-HUMAN-SEMANTIC-BENCHMARK` | `real_activation` | **BLOCK** | not approved, not run, zero reviewed claims |
| `V0-BENCHMARK-THRESHOLDS` | `v0_definition_of_done` | **BLOCK** | engineering suites pass, but the required human semantic-support benchmark is not approved or run |
| `V0-FROZEN-INPUT-REBUILD` | `v0_definition_of_done` | **BLOCK** | an approved end-to-end rebuild from frozen structured inputs through published binding is not available; restricted bytes require separate licensed access |
| `PUB-GIT-TAG` | `external_publication` | **BLOCK** | no tag creation approved |
| `PUB-GITHUB-RELEASE` | `external_publication` | **BLOCK** | no GitHub Release approved |
| `PUB-PYPI-OR-REGISTRY` | `external_publication` | **BLOCK** | no package or image publication approved |

A packaging pass does not override a real-activation or external-publication block.

Checklist SHA-256: `16ae125624406b8bb9b528b63c6ff26046819be9e4a2957bf7d4ce65d9f96551`
