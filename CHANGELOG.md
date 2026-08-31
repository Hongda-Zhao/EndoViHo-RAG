# Changelog

All notable project changes are recorded here. The product remains `V0`; no external software
release or scientific-data publication is implied by this file.

## V0 — Unreleased

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
