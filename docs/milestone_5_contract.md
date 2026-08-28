# Milestone 5 demo and release contract — Draft A; locally fulfilled

> Status: **APPROVED AND LOCALLY FULFILLED**
>
> Product version: V0
>
> Project: EndoViHo-RAG
>
> Approval and implementation date: 2026-08-28 (Asia/Tokyo)
>
> Target completion: **DEMO AND RELEASE PACKAGING MECHANISM FULFILLED**
>
> Real activation: **BLOCKED — SEPARATE APPROVALS REQUIRED**

## 1. Authority and completion classification

The user authorized completion of the remaining Milestone 5 work. This contract freezes the
scope before implementation. M5 may complete an application-dependency-locked and
container-version-constrained local demo, container packaging, benchmark record, release
metadata, and engineering gates. It does not publish scientific data, activate a production LLM,
create a release tag, publish a package or image, or qualify the real V0 product as scientifically
activated.

The product version remains `V0`; the Python distribution version remains `0`. No minor, patch,
or prerelease identifier is introduced.

## 2. M5-D01 — HTTP-only evidence workbench

The Streamlit demo calls exactly `POST /v0/query` through a bounded server-side HTTP client. The
API origin is configured only by `EVE_RAG_DEMO_API_BASE_URL`; it is not browser input. The demo
does not import the database bootstrap, repository, SQLAlchemy engine, command-line adapter,
provider implementation, or any tests-only capability.

The UI is English-only and exposes no route, SQL, plan, anchor, model, prompt, sampling, secret,
or external-search control. It presents four immutable examples: structured, literature, hybrid,
and unsupported. Release selectors remain visible and fixed while the user may edit only the
controlled-English question. The server still owns route selection.

The client performs one request with no redirect, retry, or fallback. It strictly revalidates the
outbound request, enforces a fixed timeout and response-byte limit, validates `RagResponse`, binds
the returned request/selectors to the submission, and sanitizes errors. The UI displays the
canonical refusal or answer, route, actual execution flags, provenance, limitations, and the
validated canonical envelope. It has no fake-success mode, chat memory, persistence, or live web
search.

## 3. M5-D02 — container quick start

Compose implements this dependency chain:

```text
db (healthy) -> migrate (successful one-shot) -> api (healthy) -> demo
```

`docker compose up --build` starts an empty PostgreSQL/pgvector schema, API, and demo. It never
stages or publishes Zhao data, ingests the M3 corpus, downloads a model, creates a hybrid binding,
adds anchors, or enables an LLM. All host ports bind to loopback. API and demo run as a fixed
non-root user with read-only filesystems, dropped capabilities, no-new-privileges, and temporary
filesystems. Demo has no database network or database/provider environment.

`/health` is process liveness only, not data or provider readiness. Streamlit uses
`/_stcore/health`. The build context excludes credentials, Git history, external source/model
artifacts, caches, tests, local environments, and build outputs.

## 4. M5-D03 — documentation and examples

README records the system boundary, architecture, exact local quick start, API/demo addresses,
four example questions, expected current refusals, benchmark links, license links, and shutdown
procedure. It must state that a fresh clone contains neither real pilot bytes nor model artifacts.

The three real data-dependent routes are not required to fabricate success. In the unseeded quick
start they must return canonical fail-closed envelopes. Synthetic success remains confined to
pytest and is labelled as mechanism validation.

## 5. M5-D04 — benchmark and release record

`benchmark/v0_benchmark_report.json` uses `v0-benchmark-report-v1` and is the canonical source for
`docs/benchmark_report.md`. It records the frozen M2, M3, and M4 suite sizes, thresholds, results,
manifests, and immutable checksums. `release/v0_release_checklist.json` uses
`v0-release-checklist-v1` and projects to `docs/v0_release_checklist.md`. Both JSON identities are
self-excluding canonical SHA-256 values and both Markdown projections are drift-checked.
The validator rejects extra or missing top-level fields, suite/item-set drift, duplicate IDs,
version disagreement with `pyproject.toml`, changed suite metrics or thresholds, invalid source
paths/checksums, incomplete Guide section 15 gates, and altered local tool/evidence snapshots.

The frozen evidence is exact: M2 has 31 tests-only cases (26 accepted, 5 fail-closed); M3 has a
5-question deterministic synthetic tier and a 13-question real-corpus/pinned-model tier; M4 has
30 router cases and 14 tests-only mechanical-generation cases. M3 thresholds are Recall@5
`>=0.80`, Recall@10 `>=0.90`, citation validity `1.00`, and locator validity `1.00`. The M4 router
and generation identities are `ad4142226ec986efec6dc26ee8125e679b12489d5322ec797e0acfd7fd66e356`
and `538294e55050d9f1d2a56949849878d94cf5383e1c1049785f219c49c8e20cfa`. The M3
benchmark-report and trusted-receipt identities are
`894dc74002c27e3f2cdf6a47970041d88cb91a8625ec8fad8f00f6c87d7c2565` and
`28f436d57630edd8403b71a503d23528fb7a1640432d8f623eca256b68858e7e`.

The report separates `engineering_benchmarks_passed` from
`real_hybrid_activation_qualified`. Human semantic-support review is not approved, not run, and
blocking, with zero reviewed claims.

The checklist represents every V0 Definition-of-Done statement from Build Guide section 15 as an
explicit gate. Locus placement and formal lineage-snapshot gates remain blocked until a published
structured membership can be audited and the required ICTV snapshot is loaded and bound.
Engineering thresholds pass, but the aggregate V0 benchmark gate remains blocked by the absent
human semantic-support review. Frozen-input rebuild remains blocked because no approved
end-to-end structured-input-through-binding rebuild package is available.

Final evidence includes the full pytest result, frozen benchmark selection, Ruff, mypy, lock,
migration, package-build, metadata, Docker configuration/build, and fresh-volume smoke gates.
Mechanical validation is never described as semantic entailment. Human semantic-support review
remains `NOT APPROVED / NOT RUN / BLOCKING`.

## 6. M5-D05 — legal and citation metadata

MIT continues to cover project software only. `DATA_LICENSE` records dataset-level ownership and
license boundaries without relicensing third-party inputs. `CITATION.cff` identifies the V0
software and repository without inventing a DOI, ORCID, release date, or published tag.
`CHANGELOG.md` remains under `Unreleased` until an external publication is separately approved.

## 7. M5-D06 — CI and exit gates

Required local gates are:

```text
uv sync --locked --dev --extra demo
uv lock --check
uv run pytest
uv run pytest <frozen M2/M3/M4 benchmark selection>
uv run ruff check .
uv run mypy src app
uv run alembic heads/current/check
uv build
docker compose config --quiet
fresh-volume db/migrate/api/demo smoke
release metadata, local links, package contents, and git diff checks
```

The smoke gate verifies migration exit zero, both health endpoints, a canonical unsupported
response with zero execution flags, a canonical binding refusal, non-root API/demo users, absence
of automatic scientific data, and cleanup of only the isolated smoke project and volume.

M5 introduces no migration or schema change. The unique Alembic head remains
`0010_m3_lock_hardening`. Frozen M1 provenance-bearing code is not mechanically reformatted.
The PEP 517 build backend is exactly `hatchling==1.32.0`; dependency package archives remain
locked by `uv.lock`. Base container tags are version-constrained inputs, not claimed registry
digest reproducibility.

## 8. Final local engineering evidence

The final M5 tree passed 724 PostgreSQL-backed tests, the 72-case frozen benchmark selection, Ruff,
strict mypy, the 114-package lock check, unique-head/current/model-drift checks, a clean
`0001_empty_baseline` through `0010_m3_lock_hardening` replay, audited wheel/sdist builds, and
the documentation/artifact drift gates. The cold-start container gate used a new database volume,
rendered the unsupported UI result through Streamlit AppTest, exercised Demo-to-API structured
validation for unsupported and hybrid refusals, and removed its unique image, containers, volume,
and networks.

This is local mechanism-fulfillment evidence. Pull-request CI is recorded separately after the
branch is pushed and does not retroactively authorize a tag, release, package/image publication,
real data activation, or provider/egress configuration.

## 9. Explicitly blocked after packaging completion

The following remain outside this approval:

- publishing the candidate Zhao `DatasetRelease` or public memberships;
- approving a real dataset/corpus binding or structured-target anchor manifest;
- distributing restricted source, corpus, model, or workbook bytes;
- configuring a production LLM, prompt policy, credentials, or data egress;
- claiming a human semantic-support benchmark passed;
- claiming the three real routes succeed from an empty clone;
- production hardening such as authentication, rate limiting, a read-only DB role, readiness,
  backup/restore, or public hosting;
- creating a Git tag, GitHub Release, PyPI release, or container-registry publication.

The M5 pull request may be opened after every engineering gate passes. Because it is based on the
unmerged M4 branch, it is stacked on that branch until M4 is merged; no merge is authorized here.
