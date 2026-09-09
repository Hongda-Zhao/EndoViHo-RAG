# Changelog

All notable project changes are recorded here. The product remains `V0`; artifact status changes
only where an entry and a self-checksummed release manifest explicitly say so.

## V0 — Unreleased

### Source-reported RAG-value execution

- Added the `source-reported-v1` S4/S5 extension, complete multi-query evidence, local
  generation workers, answer validation, and checksum-bound execution and review tools.
- Completed the fixed 53-question × 7-condition run with 371 terminal records and 162
  confirmed generations; an independent machine audit verified record integrity. The
  repository includes aggregate results; full evidence and execution journals remain local.
- Preserved 73 context overflows, 30 model timeouts, and 10 answer-validation failures.
  All 48 eligible S2 queries returned no candidates, so this run cannot measure gains from
  successful keyword retrieval. Expert scientific scoring remains pending.

### Real mini DatasetRelease v1

- Published a portable, self-checksummed 11-locus release across three current Unionidae
  assemblies, with exact contig coordinates, physical source rows, and explicit `include`
  decisions.
- Required complete, unambiguous 20 kb left and right flank evidence for every member and fixed
  NCBI Taxonomy taxdump `2026-08-29T05:29:15Z`, ICTV MSL41 v1, and corrected VMR
  `MSL41.v1.20260729` artifact identities.
- Preserved `HCVR`, `VR Type`, and study `Orthopolintovirales` as source annotations, linked the
  latter to formal `Amphintovirales` through frozen rename evidence, and assigned no
  `Transferred gene` / `Integrated virus` class.
- Kept portable publication separate from live PostgreSQL activation; no running database was
  mutated by this release build.

### RAG-value association v1

- Simplified the scientific benchmark to the supported chain: assembly-source taxon → EVE locus
  or reported viral region → viral-lineage affinity → evidence and source.
- Removed mandatory `Transferred gene` / `Integrated virus` fields and readiness requirements;
  `HCVR`, `VR Type`, and `Viral Major Taxon` remain source-native annotations with explicit
  prohibitions against automatic class mapping.
- Regenerated the 64 pending scientific templates, ten-slot entity-binding worksheet, Gold/output
  schema, provenance-aware association metrics, preflight evidence contract, and documentation.
- Limited Phase 3 readiness to the specified S1-S4 retrieval-only scope, deferred S5 to Phase 4,
  and added a deterministic public-workspace audit that fails closed without constructing database,
  retrieval, embedding, or generation dependencies.

### Retrieval ablation framework

- Added an isolated, offline-first embedding/reranker ablation framework with checksum-bound model
  identities, read-only corpus snapshots, exact sidecar dense retrieval, production-equivalent FTS
  and RRF, optional tier-preserving reranking, exact metrics, telemetry, and deterministic outputs.
- Added pending-only legacy annotation migration and trust gates that prevent fake or merely
  structural providers from producing a formal report.
- Completed a checksum-verified, offline preliminary comparison of BGE, MedCPT, and Qwen3 across
  the existing 13-question legacy gold set, with machine-readable quality, latency, resource, and
  per-question outputs plus a deterministic report and README figure.
- Kept production retrieval, database schema/defaults, model dependencies, published corpus, and
  embeddings unchanged; formal model selection remains blocked until 30–50 expert-approved gold
  questions are available.

### Repository cleanup

- Removed generated benchmark/checklist reports, internal build guides, milestone records, and
  unpublished release automation from the tracked tree while retaining runtime features, scientific
  provenance, migrations, and functional regression tests.
- Simplified the README, CI, and source distribution so the public repository centers on the tool
  and its supported operating documentation.

### Extended viral-lineage query layer

- Added the release-bound `extended_viral_lineage` role for evidence-backed, non-ICTV affinity
  groups such as `asfa-like`, with a distinct controlled-English query qualifier.
- Added database constraints, migration, resolver/capability support, closure attestation, and
  tests that keep formal, study-defined, and extended namespaces separate.
- Advanced the clean-rebuild contract to migration head `0012_extended_viral_lineage`. Because
  structured validator code is checksum-bound, pre-change candidate validation inputs must be
  rebuilt and re-approved; no published structured release currently exists to migrate.
- Kept real asfa-like loci and a new published dataset/corpus out of the repository until their
  exact source artifacts, assertions, review, and immutable release receipts are approved.

### Demo and packaging

- Added an English-only Streamlit evidence workbench over the existing `/v0/query` API.
- Added bounded HTTP transport, fixed real-state examples, and a three-stage execution trace.
- Added a non-root, fail-closed Docker quick start with explicit one-shot migrations.
- Added data-license and citation metadata.
- Kept real Zhao structured publication, hybrid binding/anchors, production generation/egress,
  and human semantic-support review blocked behind separate approvals.

### Routed hybrid RAG

- Added deterministic structured, literature, hybrid, and unsupported routing.
- Added exact dual-release binding, structured-anchor resolution, immutable `ContextPack`, a
  disabled-by-default provider boundary, constrained composition, and mechanical validators.
- Added checksum-bound router and generation regression suites; no real LLM was activated.

### Literature retrieval

- Added manifest-first safe ingestion, section-aware deterministic chunking, pinned local BGE
  embeddings, PostgreSQL FTS/vector retrieval, reciprocal-rank fusion, curated anchors, stable
  citations, reproducibility validation, and publication gates.
- Published the explicitly approved eleven-document corpus with its trusted receipt; corpus
  and model bytes remain outside Git.

### Structured query

- Added strict controlled-English planning, typed immutable query plans, exact entity resolution,
  read-only fact retrieval, cursor binding, typed results, API/CLI adapters, and a 31-case gold
  regression suite.

### Structured truth foundation

- Added normalized evidence, assembly, locus, call, assertion, lineage, and immutable-release
  schemas plus deterministic Zhao staging and fail-closed publication validation.
- The Zhao pilot remains a candidate rather than a published structured release.

### Project foundation

- Established Python 3.12, FastAPI, PostgreSQL/pgvector, SQLAlchemy, Alembic, uv, pytest, Ruff,
  mypy, CI, stable health/version contracts, and the initial documentation boundary.
