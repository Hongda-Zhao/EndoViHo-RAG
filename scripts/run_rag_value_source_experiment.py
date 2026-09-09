#!/usr/bin/env python3
"""Run the separately accepted source-reported-v1 experiment from pinned files."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from eve_relation_rag.experiments.rag_value_ablation.source_experiment import SourceExperiment


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--config-sha256', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--input-approval', type=Path, required=True)
    parser.add_argument('--input-approval-sha256', required=True)
    args = parser.parse_args()
    experiment = SourceExperiment(args.config,args.config_sha256)
    summary = experiment.run(args.output,approved_review=args.input_approval,
                             approved_review_sha256=args.input_approval_sha256)
    print(json.dumps({k:v for k,v in summary.items() if k != 'records'},ensure_ascii=False))
    return 0 if summary['execution_complete'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
