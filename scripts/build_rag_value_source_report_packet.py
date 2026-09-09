#!/usr/bin/env python
"""Materialize the approved source-report inclusion policy without publishing a release."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

from eve_relation_rag.experiments.rag_value_ablation.source_reports import (
    RawSourceField,
    ReportTaxon,
    seal_literature_region,
    seal_source_row,
    select_source_reports,
)
from eve_relation_rag.literature.hashing import canonical_json_bytes, canonical_json_sha256


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--decision-directory", type=Path, required=True)
    parser.add_argument("--materials-directory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    policy = json.loads((args.decision_directory / "admission_policy.json").read_bytes())
    sha = policy.pop("policy_sha256")
    if sha != canonical_json_sha256(policy) or policy["decision"] != "retain_all_source_reports":
        raise ValueError("source-report admission policy mismatch")
    raw = Path(policy["source_selection_file"]).read_bytes()
    if hashlib.sha256(raw).hexdigest() != policy["source_selection_file_sha256"]:
        raise ValueError("selected source rows differ from the admission decision")
    taxonomy = json.loads((args.decision_directory / "taxonomy_name_bindings.json").read_bytes())
    if taxonomy["missing_names"]:
        raise ValueError("taxonomy names remain unresolved")
    rows = []
    for line in raw.splitlines():
        item = json.loads(line)
        cells = item["raw_cells"]
        binding = taxonomy["taxa"][cells["Q"]]
        if binding["exact_tax_id"] is None:
            raise ValueError("ambiguous source taxon")
        rows.append(seal_source_row(
            source_snapshot_key="study-defined:10.1101/2025.04.19.649669:v4:data-s1",
            source_artifact_sha256="79b5d99c095b359d93c834014863fffbbd5968a1dbadafe6a77133a1d690f800",
            worksheet=item["worksheet"], excel_row=item["excel_row"],
            fields=tuple(RawSourceField(column=c, value=cells[c]) for c in "ABCDEFGHIJKLMNOPQRSTU"),
            source_taxon=ReportTaxon(
                source_name=cells["Q"], tax_id=binding["exact_tax_id"],
                taxonomy_source_sha256=taxonomy["source_sha256"],
                ancestor_tax_ids=tuple(n["tax_id"] for n in binding["lineage_to_root"]),
            ),
        ))
    if len(rows) != policy["source_row_count"]:
        raise ValueError("retained row count differs from the inclusion decision")
    if len({r.source_occurrence_locus_key for r in rows}) != len(rows):
        raise ValueError("multiple records require an explicit occurrence merge policy")
    drafts = json.loads((args.materials_directory / "paper_claims.draft.json").read_bytes())
    region_specs = (
        ("EVE_LOCUS_A", "GEVE-01", "Chlamydomonas incerta", "C. incerta GEVE",
         "Imitervirales family 12 (classification used in the 2022 paper)",
         "approximately 475 kb within a 592-kb contig; approximate 95-kb and 22-kb flanks", 475000),
        ("EVE_LOCUS_B", "ERV-01", "Homo sapiens", "ERVWE1 proviral locus",
         "HERV-W", "chromosomal band 7q21.2; whole provirus, not only the env gene", None),
        ("EVE_LOCUS_C", "VPH-01", "Bigelowiella natans", "scaffold 2 virophage-like element",
         "virophage-like (putative)", "scaffold 2; source positions 1,655,224–1,688,550", 33300),
    )
    regions = {}
    for slot, reference, taxon, name, lineage, location, length in region_specs:
        source = drafts[reference]
        artifact = args.materials_directory / source["source_file"]
        region = seal_literature_region(
            source_doi=source["doi"], source_artifact_sha256=hashlib.sha256(
                artifact.read_bytes()).hexdigest(), source_locator=source["locator"],
            taxon_name=taxon, region_name=name, lineage_as_reported=lineage,
            location_as_reported=location, approximate_length_bp=length,
            reported_claim=source["claim_zh"], reported_uncertainty=source["uncertainty_zh"],
        )
        regions[slot] = region
    groups = {
        "all_source_reports": tuple(rows),
        "Carabidae_descendants": select_source_reports(
            rows, source_taxon_id=41073, include_descendants=True),
        "Megaviricetes_source_label": select_source_reports(rows, viral_label="Megaviricetes"),
        "Carabidae_Megaviricetes": select_source_reports(
            rows, source_taxon_id=41073, include_descendants=True, viral_label="Megaviricetes"),
        "Carabidae_Retroviridae_source_label": select_source_reports(
            rows, source_taxon_id=41073, include_descendants=True, viral_label="Retroviridae"),
        "selected_assembly": select_source_reports(rows, assembly="GCA_963924615.1"),
        "selected_assembly_Megaviricetes": select_source_reports(
            rows, assembly="GCA_963924615.1", viral_label="Megaviricetes"),
    }
    args.output.mkdir(parents=True, exist_ok=False)
    def write(name: str, value: object) -> None:
        (args.output / name).write_bytes(canonical_json_bytes(value) + b"\n")
    for name, values in (
        ("source_reports.jsonl", rows),
        ("literature_regions.jsonl", regions.values()),
    ):
        with (args.output / name).open("xb") as stream:
            for value in values:
                stream.write(canonical_json_bytes(value) + b"\n")
    summaries = {}
    for name, records in groups.items():
        summary = {
            "source_record_keys": sorted(r.record_key for r in records),
            "source_occurrence_locus_keys": sorted(r.source_occurrence_locus_key for r in records),
            "source_report_count": len(records),
            "source_assembly_accessions": sorted({r.cell("A") for r in records}),
            "source_taxa": sorted({r.cell("Q") for r in records}),
            "source_viral_labels": dict(Counter(r.cell("J") for r in records)),
            "source_hcvr_labels": dict(Counter(r.cell("D") for r in records)),
            "source_conserved_og_labels": dict(Counter(r.cell("T") for r in records)),
            "complete_for": "selected_source_report_snapshot_only",
            "biological_absence_claimed": False,
        }
        write(name + ".json", summary)
        summaries[name] = {k: v for k, v in summary.items()
                           if k not in {"source_record_keys", "source_occurrence_locus_keys"}}
    write("entity_region_bindings.json", {
        **{slot: r.record_key for slot, r in regions.items()},
        "REPORTED_REGION_A": regions["EVE_LOCUS_A"].record_key,
        "note": (
            "Shared source identity does not approve a cross-source alignment or exact placement."
        ),
    })
    write("scope_summaries.json", summaries)
    manifest = {
        "schema_version": "rag-value-source-report-packet-v1",
        "purpose": "source_materials_and_reference_preparation",
        "admission_policy_sha256": sha,
        "source_row_count": len(rows), "literature_region_count": len(regions),
        "public_validated_release": False, "gold_approval": None, "oracle_approval": None,
        "files": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in sorted(args.output.iterdir()) if p.is_file()},
    }
    write("packet_manifest.json", {**manifest, "manifest_sha256": canonical_json_sha256(manifest)})
    print(json.dumps({"source_rows": len(rows), "literature_regions": len(regions),
                      "scope_counts": {k: len(v) for k, v in groups.items()}}))


if __name__ == "__main__":
    main()
