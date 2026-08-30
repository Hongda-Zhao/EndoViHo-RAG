# EndoViHo-RAG V0

EndoViHo-RAG V0 maps product version `V0` to Python distribution version `0.1.0`, annotated Git
tag `v0.1.0`, and OCI image `ghcr.io/hongda-zhao/endoviho-rag:v0.1.0`. The publication workflow
adds the exact audited commit and the immutable OCI digest to the released evidence.

## Highlights

- Release-scoped structured EVE evidence with immutable manifests and independently replayable
  validation receipts.
- A fixed, receipted literature corpus with structured-target anchors and exact dataset/corpus
  binding.
- Strict structured, literature, and hybrid routes with typed refusals and claim-level citations.
- A no-egress, loopback-only local generation provider whose output remains downstream of immutable
  structured and retrieval results.
- Human-reviewed real hybrid benchmark evidence plus deterministic mechanical, migration, rebuild,
  and clean-volume gates.

## Distribution and verification

The GitHub Release contains the exact source archive, wheel, sdist, `SHA256SUMS`, SPDX 2.3 SBOM,
and signed Sigstore provenance bundle. The GHCR digest and image provenance are attached after the
multi-platform image is published. Consumers should verify checksums and GitHub attestations, then
pull the image by digest rather than relying on the mutable display tag.

V0 is not published to PyPI. Ignored source workbooks, retrieved full text, model weights,
credentials, database volumes, and artifacts without an approved redistribution basis are not part
of the software release.
