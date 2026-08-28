# EVE Relation V0 — data and answer semantics

> Truth-layer contract: Milestone 1 approved Draft B
>
> Routed-answer contract: Milestone 4 approved Draft A
>
> Status: verified structured staging; no public EVE release; M4 engineering mechanism
> **FULFILLED** and real generation activation blocked
>
> Frozen pilot source: Zhao et al. v4 Data S1

## Scientific definition

An endogenous viral element (EVE) is a continuous virus-like gene fragment embedded in a
eukaryotic host genome and flanked on both sides by host genomic sequence. Milestone 1 stages
source-reported candidates; it does not yet claim that this definition has been demonstrated for
any staged row.

## Frozen pilot scope

The importer selects every VR reported for the ten approved bivalve assemblies in the Zhao et al.
v4 Data S1 scope. The verified result is:

| Outcome | Count | Meaning |
| --- | ---: | --- |
| Source-reported VR calls | 39,495 | Every selected native VR occurrence is retained. |
| `source_high` | 71 | The source workbook's HCVR value is exactly `Yes`. |
| `source_low` | 39,424 | Every other explicit source HCVR value. |
| `Integration` exact placements | 38,968 | Assembly, contig, length, and 0-based half-open interval passed validation. |
| `Viral contig` quarantine outcomes | 527 | Retained for audit, but not promoted to an exact integration placement. |
| Assemblies / selected contigs | 10 / 12,233 | All resolved exactly against the frozen NCBI reports. |

`source_high` and `source_low` are source-assessment labels. They are not validation verdicts,
proof of endogenization, inclusion decisions, or release membership.

## Object separation

Milestone 1 keeps these concepts separate:

```text
frozen source artifact
  -> immutable physical SourceRecord
  -> method-specific DetectionCall for the native VR occurrence
  -> coordinate-free locus identity
  -> optional exact placement
  -> independent left and right flank assessments
  -> explicit inclusion decision
  -> public release membership
```

Moving to a later object is never automatic. A source label or a valid coordinate cannot create
public membership by itself.

## Source, call, locus, and coordinate identity

- Each of the 39,495 selected rows has an immutable physical `SourceRecord` key derived from the
  frozen artifact, source snapshot, worksheet, and Excel row; this identity is independent of any
  import/detection method.
- Each selected native VR occurrence has a separate deterministic `DetectionCall` key derived
  from the frozen artifact/snapshot/worksheet, exact assembly and contig accession.versions,
  native VR token, and method/run identity.
- A source row and a detection call are therefore not the same identity; replay preserves the
  source row while a different approved method/run may create a distinct call.
- The locus identity binds the source snapshot, assembly accession/version, contig
  accession/version, native VR token, and identity policy.
- Coordinates are deliberately excluded from the locus key, so correcting a placement does not
  silently create a new biological identity.
- Placements use 0-based, half-open intervals and remain separate evidence attached to a locus.
- A missing or invalid placement can therefore be quarantined without losing the auditable source
  call or its coordinate-free identity.

## Authority and provenance

The study-defined `Orthopolintovirales` selection is a source claim, not a formal ICTV assignment.
Assembly and contig resolution is bound to the frozen NCBI Datasets v2 reports and exact artifact
checksums recorded in the Milestone 1 manifest. Public release additionally requires a complete,
versioned NCBI Taxonomy snapshot including merged and deleted taxon history, plus an explicit ICTV
release/snapshot binding for viral lineage assertions.

## Public-membership gates

A locus can enter a public release only when the release validator can verify all required
evidence and provenance, including:

1. one exact placement on the frozen assembly/contig authority;
2. independently supported left and right host-genomic flanks under the required policy;
3. an explicit `include` decision tied to that placement;
4. complete frozen NCBI taxonomy history and an ICTV release/snapshot binding; and
5. a clean, checksum-bound, conflict-free validation result.

The current pilot has no flank assessments, no inclusion decisions, and no public locus
memberships. Consequently, the 38,968 `Integration` placements are staged candidates—not a
published EVE catalogue—and the 527 `Viral contig` rows remain quarantined. Migration
`0005_m1_fail_closed_publication` additionally rejects database status promotion to `validated`
or `published` until a trusted, immutable validation-receipt workflow is implemented.

## Milestone 4 routed-answer object separation

Milestone 4 adds a read-only composition mechanism; it does not create a new truth layer and does
not promote a candidate locus, assertion, release, corpus, or generated sentence. The approved
flow preserves these objects separately:

```text
client-authored strict question and exact release selectors
  -> deterministic outer route
  -> unchanged, gate-authorized M2 QuerySuccess when structured facts are requested
  -> exact M3 RetrievedChunks when literature evidence is requested
  -> immutable checksum-bound ContextPack
  -> constrained provider draft in test-only generation paths
  -> all-or-nothing mechanical validation
  -> deterministic application-rendered answer
  -> separate human semantic-support review before real activation
```

The only structured facts in a hybrid answer are the unchanged M2 `QuerySuccess`, `QueryPlan`,
and `StructuredResult`. Generated prose cannot rewrite structured counts, coordinates, release
status, lineage identity, or other structured fields. The only literature facts admitted to a
provider are the exact M3 chunks and provenance inside a `ContextPack`; provider background
knowledge, live search, hidden documents, SQL, embeddings, credentials, and conversation memory
are outside that object and outside the approved path. See
[`hybrid/contracts.py`](../src/eve_relation_rag/hybrid/contracts.py),
[`generation/context.py`](../src/eve_relation_rag/generation/context.py), and
[`application/rag.py`](../src/eve_relation_rag/application/rag.py).

## Anchor and retrieval semantics

A curated document anchor is a corpus-scoped retrieval signal, not a scientific assertion or a
structured/literature release binding. For a hybrid request, M4 round-trip revalidates the
structured success and may derive only these exact targets:

- locus keys from returned loci or a validated locus filter;
- assembly keys from returned loci/assemblies or a validated assembly filter;
- exact snapshot-qualified lineage terms from returned lineage references or validated lineage
  filters; and
- method-definition keys from typed public assertion detail, never from a detection call's
  source-method field.

The question, client, and provider cannot author anchors. M4 does not infer a document or keyword
anchor from structured content, perform fuzzy substitution, or cross corpus releases. It queries
the exact capability-scoped corpus for existing curated locus, assembly, lineage, or method
anchors, then validates their persisted manifest row, typed shape, complete preimage, anchor key,
and checksum before passing the actual M3 anchors to retrieval. Targets are deduplicated in
`locus`, `assembly`, `lineage`, `method` order; more than 64 is refused rather than truncated.

`structured_anchor_unmatched` means only that no exact curated anchor exists for one or more
trusted targets, or that the structured result has no exact target. M3 may then use its explicit
same-corpus fill behavior. That diagnostic does not disprove a biological relation, and a
corpus-fill chunk does not establish one. The resolver is implemented in
[`retrieval/hybrid/anchors.py`](../src/eve_relation_rag/retrieval/hybrid/anchors.py).

## Generated claim and citation semantics

`ContextPack` is immutable, canonical-JSON hashed, and limited to 131,072 UTF-8 bytes and eight
retrieved chunks. A constrained draft contains at most 16 ordered atomic literature claims. Each
claim must cite one to four current-response `D#` identifiers and provide one exact contiguous
evidence span, at most 500 characters, for every cited chunk. Provider output is limited to
32,768 UTF-8 bytes. Structured and retrieved content is never silently truncated to meet those
limits.

The validator is deliberately mechanical. It verifies the context/provider/prompt identities,
current-response citations, exact span membership, identifier and numeric-token provenance,
forbidden-inference patterns, required limitations, and strict round-trip integrity. The final
record therefore declares `validation_scope = "mechanical"`.

Mechanical validation establishes traceability and contract conformance, not scientific
entailment. A quote occurring in a cited chunk does not by itself prove that the generated claim
is supported by the source, and a citation does not turn an explanatory statement into EVE
evidence. Real generation activation requires a separate checksum-bound human benchmark in which
every claim is reviewed as `supported`, `partially_supported`, or `unsupported`; unsupported
claims block activation, and partially supported claims must be narrowed and regenerated. The
mechanical validator is in
[`generation/validators.py`](../src/eve_relation_rag/generation/validators.py).

## Mechanism status and real-activation boundary

The M4 engineering mechanism is **FULFILLED**: the final local PostgreSQL suite, frozen
benchmarks, Ruff, strict mypy, lock verification, clean-migration/drift checks, documentation
checks, and diff check passed. This classification does not claim a remote CI result or real
provider activation. Production remains fail-closed for generation: `EVE_RAG_LLM_PROVIDER` has
only the value `disabled`, bootstrap supplies no composer, and no remote SDK, model revision,
credential, retry, or data-egress policy is approved. M4 introduced no schema or production-data
mutation.

Real Zhao hybrid activation is independently blocked by all of the following:

1. the Zhao structured release is still candidate-only and has no public locus memberships;
2. no checksum-approved real dataset-release/corpus-release binding manifest exists;
3. the published 11-document M3 corpus has document and keyword anchors but no curated locus,
   assembly, lineage, or method anchors derivable from a structured result;
4. no production LLM provider, pinned generation policy, or structured/document egress approval
   exists; and
5. no checksum-bound human semantic-support benchmark has been approved and passed.

Synthetic releases, bindings, structured-target anchors, and deterministic providers under
`tests/` demonstrate the engineering mechanism only. They are not selectable through production
settings, HTTP, or CLI and confer no scientific or release authorization.
