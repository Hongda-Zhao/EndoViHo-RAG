# V0 Activation and Publication Contract — Draft A

> Status: **APPROVED**
>
> Drafted: 2026-08-29
>
> Approved by the project owner: 2026-08-29
>
> Baseline: `main@d689c5391321807c8ddbbfd14495b4822cbcd06c`
>
> Scope: convert the completed M1–M5 engineering preview into one scientifically
> auditable, reproducible, locally generated V0 product and then publish only the
> explicitly approved software artifacts.

## 1. Current evidence and non-negotiable boundary

The M1–M5 implementation and latest `main` CI are complete, but the product is not yet an
activated V0. The current release checklist contains 22 passing items and 12 blocking items.
Those blocks are real data, scientific-review, provider, rebuild, and publication gates; they
must not be converted to passing by editing status files.

The live local database currently records:

| Object | Observed state |
|---|---|
| Structured release | `release:endoviho-rag:v0:20260826:001`, `candidate` |
| Structured source records / loci | 39,495 / 39,495 |
| Exact placements | 38,968 |
| Quarantined viral-contig records | 527 |
| Flank assessments | 0 |
| Inclusion decisions | 0 |
| Public locus / assertion memberships | 0 / 0 |
| Formal ICTV release binding | absent |
| Published corpus | `corpus:endoviho-rag:v0:20260828:001` |
| Published corpus contents | 11 documents, 1,464 chunks, 1,464 embeddings, 22 anchors |
| Published corpus receipt | trusted and passing |
| Structured-target corpus anchors | 0 |
| Production generation | disabled |
| Human-reviewed claims | 0 |
| Git tag / GitHub Release / registry image | absent / absent / absent |

The existing 527 `Viral contig` records remain fully accounted quarantine records. They do not
need invented placements and are not eligible for public membership. Their quarantine state does
not prevent V0 if the public-release manifest excludes them and preserves their audit trail.

## 2. Approval model

Approval of this Draft A authorizes implementation, public-source retrieval, candidate artifact
construction, local validation, and a V0 pull request. It does **not** by itself authorize an
unsupported scientific verdict, impersonate a human reviewer, disclose a ContextPack to a remote
provider, promote a release with a failing receipt, or publish an external tag/release/image.

The work has three explicit checkpoints:

1. **Contract approval** — authorizes the implementation described here.
2. **Activation Manifest Packet approval** — approves the exact checksums and contents of the
   structured adjudication, taxonomy, corpus-anchor, binding, local-provider, prompt, and benchmark
   manifests produced by the implementation.
3. **Human review and final publication approval** — a named human signs the semantic-review
   artifact; after all gates pass, the exact commit, tag, GitHub Release, and OCI digest are approved.

No checkpoint may be inferred from the existence of ignored local files or from a database row
whose status was edited outside the approved workflow.

## 3. ACT-D01 — structured V0 scope

The existing candidate key `release:endoviho-rag:v0:20260826:001` is retained. It continues to
audit all 39,495 selected Zhao et al. Data S1 records from the approved ten assemblies. Public V0
membership is a smaller evidence-qualified subset and is never equivalent to successful import.

The preregistered adjudication cohort is:

1. all 71 `source_high` records, ordered by physical source row;
2. if an assembly has no passing locus after step 1, exact-placement `source_low` records from that
   assembly are assessed in ascending physical source-row order until the first passing locus is
   found or the assembly is exhausted.

Selection into the adjudication cohort does not itself authorize membership. Every assessed record,
including failures, remains in the evidence manifest. V0 activation requires at least one passing
public locus in each of the ten approved assemblies; otherwise the structured release remains a
candidate and the failure is reported without changing the cohort after seeing results.

The final structured release manifest is a new self-checksummed object. It binds the existing M1
source manifest and audit digests, all dependency snapshots, every adjudication record, the exact
public membership set, and all terminal counts. Before validation its digest replaces the temporary
candidate `dataset_release.manifest_sha256`; the M1 source-manifest digest remains independently
bound as a source dependency.

## 4. ACT-D02 — flank evidence and inclusion policy

Each cohort locus is evaluated against its exact INSDC accession.version and 0-based half-open
placement. One frozen sequence-evidence package is retrieved per locus and contains the interval
plus up to 20,000 bp immediately adjacent on each side, the exact request coordinates, retrieval
timestamp, source URI, response SHA-256, parser/tool versions, and normalized sequence SHA-256.

The 20,000 bp value is an inspection window, not a biological minimum. Left and right verdicts are
independent:

- `supported`: the exact accession.version resolves, the interval is in bounds, at least one base is
  available and inspected on that side, the base immediately adjacent to the locus is an unambiguous
  nucleotide, and the response and normalized coordinates pass checksum/integrity validation;
- `insufficient`: no adjacent base exists, the boundary is an assembly gap/unknown base, the exact
  sequence cannot be retrieved, or the evidence is incomplete;
- `contradicted`: the frozen sequence length/version conflicts with the placement or provenance;
- `not_assessed`: no completed assessment exists.

The evidence report also records inspected length, ambiguity fraction, longest ambiguity run, and
boundary bases. A `supported` verdict means only that independently inspectable assembly-context
sequence flanks the reported interval; it does not claim functional activity, ancestral host identity,
or independent experimental proof of endogenization.

The explicit policy-authorized inclusion decision is
`policy:v0-pilot-inclusion-v1`. It issues `include` only when all existing M1 gates pass, exactly one
placement is exact, both flank assessments are `supported`, formal dependency snapshots are bound,
and no unresolved issue, quarantine issue, or conflict remains. The decision records the approved
policy identity in `authorized_by`; `source_high` or `source_low` alone can never issue `include`.

## 5. ACT-D03 — frozen taxonomy authorities

The structured release binds three separate namespaces:

- host taxonomy: a retrieval-date-frozen NCBI Taxonomy package containing `nodes.dmp`, `names.dmp`,
  `merged.dmp`, and `delnodes.dmp`, plus the upstream archive checksum and usage-policy evidence;
- formal viral taxonomy: `ICTV_Master_Species_List_2025_MSL41.v1.xlsx`;
- exemplar metadata: corrected `VMR_MSL41.v1.20260729.xlsx`. The earlier 2026-07-21 VMR is forbidden
  because ICTV states that it contained errors and should be discarded.

The ICTV artifacts use the ICTV site's stated CC BY 4.0 terms and exact upstream checksums verified
after download. The full MSL hierarchy is imported with closure and exact release binding. The
study-defined Zhao lineage remains a separate namespace. A study label maps to a formal ICTV term
only when a curated mapping row names both exact snapshot terms and its evidence; exact string
similarity cannot silently upgrade a study assertion.

The exact artifact digests, retrieval timestamps, mapping rows, and generated snapshot digests are
part of the Activation Manifest Packet and require checksum-level approval before publication.

## 6. ACT-D04 — trusted structured validation and publication

V0 adds an immutable, release-bound `DatasetValidationReceipt` workflow analogous in safety to the
published corpus receipt, without reusing corpus-specific evidence.

The receipt binds at least:

- final structured release manifest;
- M1 whole-ledger audit and terminal counts;
- NCBI and ICTV dependency graph;
- flank and inclusion adjudication manifest;
- public locus and assertion membership manifests;
- independent clean-database rebuild report;
- structured and hybrid benchmark reports;
- receipt schema, validator code, policy, and receipt self-digests.

Database triggers permit `candidate -> validated` only when one exact trusted passing receipt exists,
and `validated -> published` only through the publication service naming that receipt. Published and
deprecated release-scoped content remains immutable. The production gate independently reconstructs
and verifies the receipt before issuing `ReleaseCapability`; a status value alone is never sufficient.

Administrative CLI commands must be idempotent on exact replay, reject checksum drift, and never
disable triggers or change `session_replication_role`.

## 7. ACT-D05 — corpus anchors and exact hybrid binding

The published corpus `corpus:endoviho-rag:v0:20260828:001` is immutable and is not modified.

A new corpus release `corpus:endoviho-rag:v0:20260829:001` reuses the same approved 11-document
source set and frozen BGE model, while adding only curator-authored structured-target anchors that
are directly supported by locatable text. The preferred minimum is formal viral-lineage anchors for
the exact MSL41 `Orthopolintovirales` term; method anchors may be added only where the cited document
actually describes the exact method semantics. No locus or assembly anchor may be fabricated from
topic similarity.

The new corpus is rebuilt, benchmarked, receipted, and published as a distinct immutable release.
An exact self-checksummed binding manifest then permits only:

```text
release:endoviho-rag:v0:20260826:001
    <-> corpus:endoviho-rag:v0:20260829:001
```

The anchor manifest, corpus manifest, corpus receipt, and binding manifest are all part of the
Activation Manifest Packet. Unexpected unmatched targets in preregistered hybrid cases block
activation; an unmatched diagnostic is not converted to a broad literature query.

## 8. ACT-D06 — production generation and egress

V0 uses a local-only, no-external-egress generation provider on the project host. The production
adapter speaks a strict OpenAI-compatible loopback HTTP contract but accepts only an approved
loopback endpoint, disables environment proxies and redirects, sends no tools, streams no output,
and enforces the existing one-call, temperature-zero, retry-zero, timeout, and byte limits.

The exact local model is selected only after a fixed offline candidate benchmark on the target
Apple-silicon host. The winning model manifest binds repository/model identity, immutable revision,
every artifact SHA-256, license, inference-engine version, quantization, tokenizer, context length,
seed support, and generation policy. If no local candidate passes, V0 remains blocked; this contract
does not silently authorize a remote provider.

Production settings reject default database credentials, missing cursor secret, a disabled provider,
non-loopback model endpoints, unapproved model/prompt hashes, or a missing readiness dependency.
`/ready` verifies database migrations, published release capabilities, exact binding, model readiness,
and policy checks without exposing credentials or provider payloads. CI uses only a loopback fake and
never invokes a paid or external model.

Any later remote-provider proposal requires a separate amendment naming the provider, endpoint,
region, model revision, retention/training/residency policy, allowed ContextPack fields, credential
source, and deployment egress control.

## 9. ACT-D07 — preregistered human semantic benchmark

Before generation, V0 freezes ten hybrid questions: exactly one assembly-scoped case per approved
assembly. Each case must resolve at least one structured locus and at least one exact structured-target
literature anchor. Structured-only and literature-only real-route smoke suites are frozen separately.

Each generated answer binds its exact question; structured/corpus release keys and manifests;
binding and anchor hashes; ContextPack hash; provider/model/prompt/generation-policy identities; and
answer hash. It must contain at least one factual document-derived claim, so an all-abstention run
cannot pass.

One named, accountable human domain reviewer is the minimum V0 signatory. The Agent may prepare the
review packet and mechanical checks but must not assign the human label or sign for the reviewer.
For every factual claim, the reviewer uses only the cited current chunk, locator, and evidence span
to assign `supported`, `partially_supported`, or `unsupported`.

Pass criteria are:

- all ten preregistered cases are retained in the report;
- every retained claim is `supported`;
- `unsupported = 0`, unresolved `partially_supported = 0`, and unreviewed claims = 0;
- mechanical citation existence, release match, locator validity, and citation coverage remain 100%;
- any narrowed or regenerated claim receives a new checksum and a new review;
- the final review artifact records rubric version, reviewer identity, review time, all decisions,
  and a self-checksum.

## 10. ACT-D08 — frozen-input rebuild and rollback

Tracked manifests contain checksums and legal/retrieval metadata; restricted or large source bytes
remain outside Git. The rebuild contract therefore distinguishes:

- redistributable public artifacts, which a restore command may retrieve and checksum;
- operator-supplied artifacts, especially the Zhao Data S1 workbook, which must be placed in a
  documented local escrow path and verified against the approved digest;
- credentials, which are never part of a frozen package or checksum manifest.

A clean-volume activation run must reproduce semantic manifests, terminal counts, database receipts,
corpus identities, binding identity, and benchmark identities from the exact frozen inputs. Remote
model prose is not relevant because the approved V0 provider is local. The run also proves real
structured, literature, and hybrid success through the public API and CLI.

Rollback is fail closed: removing the approved binding/provider configuration or selecting
`llm_provider=disabled` immediately disables generation/hybrid activation without mutating either
published release. Published releases are corrected only by a new superseding release.

## 11. ACT-D09 — version and publication targets

The proposed unified version mapping is:

| Surface | V0 value |
|---|---|
| Product name | `V0` |
| Python distribution version | `0.1.0` |
| Git tag | `v0.1.0` |
| GitHub Release | `v0.1.0` |
| OCI image | `ghcr.io/hongda-zhao/endoviho-rag:v0.1.0` plus immutable digest |
| PyPI | not published in V0 |

Before publication, `CHANGELOG.md`, `CITATION.cff`, package metadata, image labels, checklist, release
notes, and generated checksums must agree on this mapping and the exact audited commit.

The V0 release includes source, wheel, sdist, SHA-256 manifest, SBOM, provenance attestations, and
the OCI digest. It excludes ignored source workbooks, retrieved full text, model weights, credentials,
database volumes, and any artifact whose redistribution basis is not approved.

## 12. ACT-D10 — repository governance and release order

Before the V0 tag is created:

- `main` changes require a pull request;
- `quality` and `container-smoke` are required checks;
- force push and branch deletion are disabled;
- `v*` tags are protected;
- a protected `v0-production` environment requires human approval;
- release jobs use minimum token permissions and pin third-party actions;
- the final preflight reruns tests, migrations, distribution audit, checklist, benchmarks, restricted-
  byte audit, and fresh-volume smoke at the exact release commit.

The mandatory order is:

1. merge the activation PR after required checks pass;
2. run final preflight on the resulting exact `main` commit;
3. obtain final publication approval naming that commit;
4. create the protected annotated tag `v0.1.0`;
5. publish the GitHub Release and checksum/SBOM/provenance assets;
6. publish the GHCR image and record its immutable digest;
7. verify fresh install/pull and all three real routes;
8. update the release checklist with evidence URLs and immutable identities.

No tag is created early and no failed case is removed to make the release pass.

## 13. V0 completion criteria

V0 is established only when all current 12 blocking checklist entries become evidence-backed passes:

1. every public locus has exact versioned assembly/sequence/coordinate provenance;
2. the formal MSL41 and complete NCBI taxonomy snapshots are bound;
3. the trusted structured receipt and published structured release pass independent replay;
4. the new corpus has exact structured-target anchors and a published trusted receipt;
5. the exact dataset/corpus pair binding is approved and checksum-valid;
6. the approved local provider is ready and no external ContextPack egress occurs;
7. structured, literature, and hybrid real routes succeed from a clean activation;
8. all preregistered human-reviewed claims pass the semantic gate;
9. all existing mechanical, retrieval, security, and cold-start thresholds remain passing;
10. frozen-input rebuild reproduces the approved identities;
11. the exact audited commit has the protected `v0.1.0` tag and GitHub Release;
12. the GHCR image is published, pull-tested, and recorded by digest.

Until all twelve conditions hold, README and release status must continue to say engineering preview
or activation candidate, and the system must continue to fail closed.

## 14. Approval record

- [x] Draft A contract approved on 2026-08-29.
- [ ] Activation Manifest Packet approved by exact manifest identities.
- [ ] Human semantic-review artifact signed by a named reviewer.
- [ ] Final exact commit/tag/GitHub Release/GHCR publication approved.
