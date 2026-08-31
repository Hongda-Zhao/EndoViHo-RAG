# V0 Activation Contract factual errata

> Status: **PENDING ACTIVATION MANIFEST PACKET APPROVAL**
>
> Applies to: `docs/v0_activation_contract.md`, approved Draft A
>
> Recorded: 2026-08-29

## E1 — MSL41 polinton order name

Draft A section ACT-D05 refers to the exact formal MSL41 term
`Orthopolintovirales`. That is not the current order name in MSL41. The frozen
MSL41 workbook contains `Amphintovirales`; approved ICTV proposal 2024.010D
records the rename from `Orthopolintovirales` to `Amphintovirales`. The same
proposal records `Adintoviridae` to `Eupolintoviridae`, but the Zhao study
snapshot has no exact old-family endpoint, so V0 must not fabricate that second
study-to-formal mapping.

The operational correction proposed for checkpoint 2 is therefore:

- retain `study-viral-major-taxon:orthopolintovirales` in the distinct Zhao
  study namespace;
- map it to the exact MSL41 `Amphintovirales` term using relation
  `renamed_to` and evidence from proposal 2024.010D;
- never treat the two names as fuzzy matches, synonyms, or one namespace;
- create no `Adintoviridae` study endpoint or family mapping unless a future
  separately approved study snapshot contains that exact endpoint.

Authority captures proposed for the Activation Manifest Packet:

| Artifact | Source | SHA-256 | Bytes | Retrieved at |
|---|---|---:|---:|---|
| NCBI website/data usage policy | `https://www.ncbi.nlm.nih.gov/home/about/policies/` | `8ad8f6f186ca51ec73a5fb8935ecfa17b8cbaad300b7025b381898ab72621869` | 38,936 | `2026-08-29T06:41:28Z` |
| ICTV taxonomy page with CC BY 4.0 statement | `https://ictv.global/taxonomy` | `4c8bc175029519fe34003254cc2c01fbac9ba00bb2086cf08a96f03a54efc4df` | 62,480 | `2026-08-29T06:41:28Z` |
| ICTV proposal `2024.010D.Varidnaviria_reorg.xlsx` | `https://ictv.global/system/files/proposals/approved/Animal_DNA_viruses_and_Retroviruses/2024.010D.Varidnaviria_reorg.xlsx` | `c11d6f496ff610a33862e1993b6f27d967563478e8c24b80b882037ba16bfd62` | 26,852 | `2026-08-29T06:41:28Z` |

This erratum does not authorize publication. It becomes effective only when the
project owner approves the exact Activation Manifest Packet that references it.

## E2 — ICTV files have no publisher checksum

Draft A section ACT-D03 says the ICTV artifacts use exact upstream checksums verified
after download. The ICTV download pages used for MSL41 and the corrected VMR do not
publish checksums for those files. V0 must not relabel a project-computed digest as a
publisher-provided checksum.

The checkpoint-2 packet therefore records, for both ICTV workbooks:

- the exact HTTPS source URI and retrieval time;
- the retrieved byte size and project-computed SHA-256;
- `upstream_checksum=null`, `upstream_checksum_algorithm=null`, and
  `upstream_checksum_verified=false`;
- the captured ICTV taxonomy page and its CC BY 4.0 statement as separate policy
  evidence.

NCBI Taxonomy remains different: its retrieved archive is checked against the
publisher-provided MD5 before the project-computed SHA-256 is accepted. This correction
preserves the audit trail while preventing an unsupported checksum claim. It does not
authorize publication and becomes effective only through approval of the exact
Activation Manifest Packet.
