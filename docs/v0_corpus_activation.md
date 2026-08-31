# V0 corpus and hybrid-anchor activation

This lane constructs candidate artifacts only. It does not modify the published
`corpus:endoviho-rag:v0:20260828:001`, import a candidate, issue a validation receipt,
publish a corpus, authorize a hybrid pair, or assign a human semantic-review label.

## Frozen corpus transition

The builder accepts only the approved M3 v2 baseline:

- corpus key: `corpus:endoviho-rag:v0:20260828:001`
- semantic manifest SHA-256:
  `1497ea3383bea64d2bc4f17d2376dceb537b4f6c6f57ccb6eaf667b6589732f0`
- anchor manifest SHA-256:
  `75a523bc6408f13b07ba283e6539734ec3b694f3dab59994a464d40d98b01fca`
- source set: the same 11 checksum-pinned Europe PMC JATS documents
- embedding model and parser, chunking, FTS, retrieval, and anchor policies: unchanged

It emits the distinct candidate key
`corpus:endoviho-rag:v0:20260829:001`. The locally constructed candidate semantic
manifest SHA-256 is
`a96fe244fa82ddbba0c24f7cee16753a5f1194b91c37af9cf27380c6368be929`.
That identity is a candidate for the Activation Manifest Packet, not an approval.

## Exact lineage anchors

The V0 anchor manifest retains the 22 previously approved document/keyword anchors and
adds eight structured lineage anchors: one study-defined and one current formal target
on each of four exact JATS evidence blocks.

The locally reconstructed 30-anchor candidate has semantic anchor-manifest SHA-256
`43e5010c1cd8af747b451f602099da659a31daeb1ea9d8514ffb6de251617ef7`
and serialized-file SHA-256
`3e41a4a8fcf37061f169f146df2e07a794948197cc2a8df82634a0740e1928ff`.
It binds the evidence-bearing taxonomy mapping manifest SHA-256
`12585a856a55fc7c97195f2b9ceae52e546e38dada6b230bd7a6abd7147b6e7c`.
These remain candidate identities until the Activation Manifest Packet is approved.

| PMCID | Exact JATS locator | Literature label |
|---|---|---|
| `PMC4028283` | `/article/front[1]/article-meta[1]/abstract[1]/sec[2]/p[1]` | Polintoviruses |
| `PMC4642659` | `/article/front[1]/article-meta[1]/abstract[1]/sec[2]/p[1]` | Polintons (polintoviruses) |
| `PMC7805220` | `/article/body[1]/sec[1]/p[1]` | Polintoviruses |
| `PMC8097293` | `/article/front[1]/article-meta[1]/abstract[1]/p[1]` | Mavericks |

Every new anchor key covers the source-artifact SHA-256, typed locator, parser-resolved
text SHA-256, an exact evidence quote and quote SHA-256, and the taxonomy bridge identity.
The builder reparses the frozen bytes and requires the quote at exactly that locator.

The study target remains
`study-viral-major-taxon:orthopolintovirales` in the frozen Zhao study namespace. The
formal MSL41 target is the current `Amphintovirales` term
`lineage-term:ictv-msl41:sha256:352b600f9fea40f27ba62cf424b81e2f9360210190822bb6732aee52c28bc200`.
The relationship is always the explicit ICTV proposal 2024.010D `renamed_to` mapping,
never string similarity. `Orthopolintovirales` is not written into the MSL41 namespace.
No `Adintoviridae` mapping is created because the study snapshot has no exact old-family
endpoint.

## Deliberately absent anchor types

None of the approved 11 documents names any of the ten exact GCA accession.version
assemblies or the project-specific
`method-definition:data-s1:sha256:9b1c8813db930e20a24ef959404db2a5c6f47b617a40bfe04e1269e37e5fc0e4`.
The candidate therefore contains no assembly, locus, or method anchor. Adding one would
fabricate a document-to-structured-target relationship.

The ten assembly-scoped hybrid cases must be phrased so their real locus results expose
the retained study lineage and the distinct formal `Amphintovirales` assertion. The
benchmark must preregister the exact identity targets that cannot be anchored from this
source set and must fail on any unregistered unmatched target. An unmatched diagnostic
must never trigger a broad literature fallback.

## Candidate commands

Build the corpus candidate:

```bash
python -m eve_relation_rag.activation.corpus corpus \
  --base-manifest .artifacts/milestone3/corpus-proposal/m3_real_corpus_manifest_v2.json \
  --output .artifacts/v0_activation/manifests/v0_corpus_manifest.json
```

After the evidence-bearing taxonomy mapping manifest is frozen, build and reconstruct
the anchor candidate:

```bash
python -m eve_relation_rag.activation.corpus anchors \
  --base-anchor-manifest .artifacts/milestone3/corpus-proposal/m3_real_anchor_manifest_v2.json \
  --corpus-manifest .artifacts/v0_activation/manifests/v0_corpus_manifest.json \
  --taxonomy-mapping-manifest .artifacts/v0_activation/manifests/study_formal_mapping.manifest.json \
  --corpus-root .artifacts/milestone3/corpus-proposal/corpus \
  --output .artifacts/v0_activation/manifests/v0_anchor_manifest.json
```

Only after the final structured release manifest digest exists, build the one-pair
binding candidate:

```bash
python -m eve_relation_rag.activation.corpus binding \
  --release-manifest-sha256 <final-structured-manifest-sha256> \
  --corpus-manifest-sha256 a96fe244fa82ddbba0c24f7cee16753a5f1194b91c37af9cf27380c6368be929 \
  --output .artifacts/v0_activation/manifests/v0_hybrid_binding_manifest.json
```

All three commands refuse to overwrite an existing artifact and report
`candidate_only=true` and `database_writes=false`.

## Staging provenance for reused immutable policies

The new corpus reuses the already approved v2 parser, chunking, FTS, retrieval, and
anchor policy rows. Those immutable rows retain the exact code identity recorded when
they were created. A later importer execution must not claim that historical identity
as its own.

`literature corpus-stage` therefore accepts a distinct `--policy-code-sha256` in
addition to `--importer-code-sha256`. The import run key and row bind the current
importer code; the import parameters separately checksum-bind the code identity of the
reused policies. For `corpus:endoviho-rag:v0:20260829:001`, both values are mandatory
and are carried into the typed validation export. Older exact replays that did not need
this distinction retain the backward-compatible single-code behavior.
