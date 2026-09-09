#!/usr/bin/env python
"""Extract user-selected source rows for review, without creating release membership."""

from __future__ import annotations

import argparse
import hashlib
import json
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from typing import cast
from zipfile import ZipFile

from eve_relation_rag.importers.data_s1 import (
    DATA_S1_ARTIFACT_SHA256,
    _decode_row,
    _find_worksheet_path,
    _load_shared_strings,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workbook", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with args.workbook.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != DATA_S1_ARTIFACT_SHA256:
        raise ValueError("source workbook differs from the recorded canonical artifact")
    args.output.mkdir(parents=True, exist_ok=False)
    counts: Counter[str] = Counter()
    selected: list[dict[str, object]] = []
    with ZipFile(args.workbook) as archive:
        strings = _load_shared_strings(archive)
        sheet, member = _find_worksheet_path(archive, ("S3",))
        stack = []
        header = None
        with archive.open(member) as stream:
            for event, element in ET.iterparse(stream, events=("start", "end")):
                if event == "start":
                    stack.append(element)
                    continue
                if element.tag.endswith("}row"):
                    row, cells = _decode_row(element, strings)
                    if row == 1:
                        header = cells
                    else:
                        counts["source_rows_scanned"] += 1
                        reasons = []
                        if cells.get("O") == "Carabidae":
                            reasons.append("source_reported_family_Carabidae")
                        if cells.get("A") == "GCA_963924615.1":
                            reasons.append("selected_assembly")
                        if cells.get("J") == "Megaviricetes":
                            counts["all_source_Megaviricetes_rows"] += 1
                        if reasons:
                            for reason in reasons:
                                counts[reason] += 1
                            counts["selected_viral_label:" + cells.get("J", "")] += 1
                            selected.append({"worksheet": sheet, "excel_row": row,
                                             "selection_reasons": reasons, "raw_cells": cells})
                    stack[-2].remove(element)
                    element.clear()
                stack.pop()
    with (args.output / "source_rows.jsonl").open("x") as stream:
        for item in selected:
            stream.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")
    receipt = {
        "artifact_kind": "source_evidence_for_review", "source_sha256": digest,
        "source_path": str(args.workbook.resolve()), "source_header": header,
        "counts": dict(counts), "selected_rows": len(selected),
        "selected_assemblies": sorted({cast(dict[str, str], r["raw_cells"])["A"]
                                       for r in selected}),
        "release_membership_created": False, "gold_approval_created": False,
        "coordinates_normalized": False,
        "note": "Family labels are original source annotations, not NCBI descendant closure. "
                "All matching rows retained, including non-HCVR and non-integration records.",
    }
    (args.output / "receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    print(json.dumps({"selected_rows": len(selected), "counts": counts}, ensure_ascii=False))


if __name__ == "__main__":
    main()
