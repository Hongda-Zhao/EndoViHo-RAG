#!/usr/bin/env python3
"""Export system-blinded answers or validate two independent submissions; no auto-labels."""

from __future__ import annotations

import argparse
from pathlib import Path

from eve_relation_rag.experiments.rag_value_ablation.human_review import (
    HumanReviewPacket,
    HumanReviewSubmission,
    ReviewSourceAnswer,
    build_blinded_review_packet,
    calculate_reviewer_agreement,
    validate_review_target,
)
from eve_relation_rag.literature.hashing import canonical_json_bytes


def write_new(path: Path, value: object) -> None:
    with path.open("xb") as handle:
        handle.write(canonical_json_bytes(value) + b"\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    export = sub.add_parser("export")
    export.add_argument("--sources-jsonl", type=Path, required=True)
    export.add_argument("--shuffle-seed", required=True)
    export.add_argument("--output", type=Path, required=True)
    review = sub.add_parser("import")
    review.add_argument("--packet", type=Path, required=True)
    review.add_argument("--first", type=Path, required=True)
    review.add_argument("--second", type=Path, required=True)
    review.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.mode == "export":
        sources = tuple(
            ReviewSourceAnswer.model_validate_json(line)
            for line in args.sources_jsonl.read_bytes().splitlines()
            if line.strip()
        )
        packet, mapping = build_blinded_review_packet(sources, shuffle_seed=args.shuffle_seed)
        args.output.mkdir(exist_ok=False)
        (args.output / "reviewers").mkdir()
        (args.output / "withheld").mkdir(mode=0o700)
        write_new(args.output / "reviewers" / "packet.json", packet)
        write_new(args.output / "withheld" / "unblinding_map.json", mapping)
        write_new(
            args.output / "status.json",
            {
                "labels_present": False,
                "agreement": None,
                "answer_count": len(packet.answers),
                "claim_count": sum(len(a.claims) for a in packet.answers),
                "trust_level": "review_preparation_only",
            },
        )
    else:
        packet = HumanReviewPacket.model_validate_json(args.packet.read_bytes())
        first = HumanReviewSubmission.model_validate_json(args.first.read_bytes())
        second = HumanReviewSubmission.model_validate_json(args.second.read_bytes())
        validate_review_target(packet)
        agreement = calculate_reviewer_agreement(packet, first, second)
        write_new(args.output, agreement)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
