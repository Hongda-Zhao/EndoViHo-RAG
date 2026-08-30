# V0 publication runbook

This runbook implements ACT-D09 and ACT-D10 from the approved V0 Activation Contract. Adding the
workflow does not publish V0: the current activation candidate must continue to fail its release
preflight until every non-publication gate has evidence and the versioned metadata is finalized.

## One-time repository governance

Before dispatch, configure repository rules that are readable through the GitHub API:

1. An active branch ruleset applies to `main`, requires pull requests, requires the strict/up-to-date
   `quality` and `container-smoke` checks, and prohibits deletion and non-fast-forward updates.
2. An active tag ruleset applies to `refs/tags/v*` and prohibits deletion and non-fast-forward
   updates. Tag creation remains confined to the reviewed publication workflow.
3. The `v0-production` environment is restricted to protected branches and has at least one required
   human reviewer.

The read-only governance check is run before preflight and again after environment approval. A
missing rule, an unreadable API response, or governance drift stops publication.

## PostgreSQL trust boundary and runtime role

The PostgreSQL database owner and administrators are the trusted V0 control plane. SHA-256 receipt
identities and lifecycle triggers are integrity and concurrency controls; they do not authenticate a
malicious database owner. Only the owner-side administrative commands may stage candidates, insert
receipts, or change release state. Owner credentials must never be supplied to the API, Demo, model
provider, retrieval clients, or a public endpoint.

The public API must use a distinct, membership-free, non-owner role with SELECT-only access. Role
creation is deliberately a deployment operation rather than an Alembic migration because roles are
cluster-global and their credentials belong in the deployment secret store. As the database owner,
provision an environment-specific role along these lines, replacing the example database and role
names and setting its login credential outside the repository:

```sql
CREATE ROLE eve_rag_runtime
    NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
GRANT CONNECT ON DATABASE eve_relation_rag TO eve_rag_runtime;
REVOKE CREATE ON DATABASE eve_relation_rag FROM eve_rag_runtime;
GRANT USAGE ON SCHEMA public TO eve_rag_runtime;
REVOKE CREATE ON SCHEMA public FROM eve_rag_runtime;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO eve_rag_runtime;
REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON ALL TABLES IN SCHEMA public
    FROM eve_rag_runtime;
REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM eve_rag_runtime;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT ON TABLES TO eve_rag_runtime;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON TABLES FROM eve_rag_runtime;
ALTER ROLE eve_rag_runtime SET default_transaction_read_only = on;
```

Do not grant this role membership in an owner, migration, receipt-writer, or publication role. Once
its externally managed login is enabled, set `EVE_RAG_DATABASE_URL` to that runtime identity and
run:

```sh
python scripts/check_v0_database_role.py
```

The audit must exit zero and report `"runtime_readonly":true`. It checks every application table,
the receipt INSERT privilege, the release-status UPDATE privilege, ownership, role memberships,
schema/database creation, superuser, role/database creation, replication, and RLS-bypass powers.
Failure is a Checkpoint 2/deployment blocker; do not substitute the local `eve` development owner.

## Final metadata state

The exact `main` commit selected for publication must contain all of the following:

- Python distribution, benchmark, and checklist package version `0.1.0`;
- a dated `## [0.1.0] - YYYY-MM-DD` changelog entry;
- `CITATION.cff` fields `version: 0.1.0` and `date-released: YYYY-MM-DD`;
- a checksum-valid benchmark report with at least ten passing real hybrid questions, an approved
  non-blocking human semantic-support review, and all recorded verification gates passing;
- a checksum-valid checklist with every non-publication item passing, status
  `publication_pending`, distribution status `release_candidate`, and only the three external
  publication items still blocked;
- no tracked `.artifacts`, workbooks, sequence dumps, databases, model weights, private keys,
  credentials, or other restricted bytes.

The three external checklist items remain blocked before the workflow because the tag, GitHub
Release, and registry image must not be claimed before they exist. They are updated with evidence in
a post-publication pull request.

## Dispatch and approval

Run **V0 publication** manually from the workflow on `main`. Supply:

- `release_commit`: the full 40-character lowercase commit ID currently at `origin/main`;
- `confirmation`: `publish-v0.1.0:` followed immediately by the same commit ID.

The unprivileged preflight checks that identity against `origin/main`, refuses any existing
`v0.1.0` tag, checks governance and restricted bytes, replays migrations on a fresh PostgreSQL
database, runs all tests and the frozen benchmark selection, runs Ruff and mypy, audits the built
wheel/sdist, builds an exact Git source archive, generates an SPDX 2.3 SBOM, creates SHA-256 sums,
tests a fresh wheel install, and runs the fresh-volume container smoke. Its exact assets are retained
for one day as an immutable workflow artifact.

Only after preflight succeeds does the `publish` job request approval in the protected
`v0-production` environment. The reviewer must approve the named commit, not a branch name or a
moving reference. The job rechecks `origin/main`, governance, repository metadata, and every staged
artifact after approval and before its first external write.

## Publication order

The protected job performs the approved order without a PyPI step:

1. create and push the annotated `v0.1.0` tag;
2. sign provenance for the source, wheel, sdist, and SBOM;
3. create GitHub Release `v0.1.0` with those artifacts and `SHA256SUMS`;
4. publish the multi-platform GHCR image with exact version, source, and commit labels;
5. sign registry provenance and attach the immutable digest and provenance bundle to the release;
6. redownload and checksum the released files, verify their GitHub attestations, fresh-install the
   wheel, pull the image by digest, and verify its version/revision labels and package import.

The real structured, literature, and hybrid route verification against frozen external activation
inputs remains an evidence-producing operational check because licensed inputs, local model weights,
credentials, and database volumes are intentionally excluded from GitHub artifacts. Complete that
check using the approved activation manifest packet, then update the three external checklist items
with the release URL, annotated tag object, artifact attestation URLs, and GHCR digest.

## Failure and recovery

Every check is fail closed. Do not delete failed cases, replace the requested commit, force-update a
tag, or recreate `v0.1.0`. A failure before tag creation is corrected through a new pull request and
a fresh exact-commit dispatch. If a step fails after the protected tag has been created, stop: do not
retag. Audit the partial external state and obtain an explicit recovery amendment before resuming or
publishing a superseding version.

Verify downloaded subjects with:

```sh
sha256sum --check SHA256SUMS
gh attestation verify eve_relation_rag-0.1.0-py3-none-any.whl \
  --repo Hongda-Zhao/EndoViHo-RAG
```

Pull the recorded image identity from `ghcr-image-digest.txt`, for example:

```sh
docker pull ghcr.io/hongda-zhao/endoviho-rag@sha256:<recorded-digest>
```
