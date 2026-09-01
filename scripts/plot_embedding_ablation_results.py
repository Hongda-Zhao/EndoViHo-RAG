#!/usr/bin/env python3
"""Render the preliminary embedding/reranker comparison from machine CSV outputs."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

WIDTH_MM = 180.0
HEIGHT_MM = 128.0
RASTER_DPI = 600
OUTPUT_STEM = "embedding_ablation_preliminary_comparison"
EXPECTED_SYSTEM_CODES = ("A", "B", "C", "C+", "D", "D+")
SYSTEM_COLORS = {
    "A": "#484878",
    "B": "#7884B4",
    "C": "#9A4D8E",
    "C+": "#D89CC6",
    "D": "#2F7F86",
    "D+": "#82C7C8",
}


class FigureInputError(ValueError):
    """Raised when machine results cannot safely support the figure."""


@dataclass(frozen=True)
class SystemResult:
    """Validated values used by the comparison figure."""

    system_key: str
    system_code: str
    system_label: str
    question_count: int
    recall_at_5: float
    mrr_at_10: float
    ndcg_at_10: float
    end_to_end_p50_ms: float
    end_to_end_p95_ms: float
    total_unique_model_size_bytes: int
    index_size_bytes: int
    peak_runtime_process_rss_bytes: int


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_rows(path: Path) -> list[dict[str, str]]:
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            return list(csv.DictReader(handle))
    except OSError as exc:
        raise FigureInputError(f"cannot read {path}") from exc


def _finite_float(row: dict[str, str], field: str, *, minimum: float = 0.0) -> float:
    raw = row.get(field)
    try:
        value = float(raw) if raw is not None else math.nan
    except ValueError as exc:
        raise FigureInputError(f"{field} must be numeric") from exc
    if not math.isfinite(value) or value < minimum:
        raise FigureInputError(f"{field} must be finite and >= {minimum}")
    return value


def _probability(row: dict[str, str], field: str) -> float:
    value = _finite_float(row, field)
    if value > 1.0:
        raise FigureInputError(f"{field} must be <= 1")
    return value


def _integer(row: dict[str, str], field: str, *, positive: bool = False) -> int:
    raw = row.get(field)
    try:
        value = int(raw) if raw is not None else -1
    except ValueError as exc:
        raise FigureInputError(f"{field} must be an integer") from exc
    minimum = 1 if positive else 0
    if value < minimum:
        raise FigureInputError(f"{field} must be >= {minimum}")
    return value


def _system_code(label: str) -> str:
    code = label.split("·", maxsplit=1)[0].strip()
    if code not in EXPECTED_SYSTEM_CODES:
        raise FigureInputError(f"unsupported system label: {label!r}")
    return code


def _index_by_key(
    rows: list[dict[str, str]], *, source_name: str
) -> dict[str, dict[str, str]]:
    indexed: dict[str, dict[str, str]] = {}
    for row in rows:
        key = row.get("system_key", "").strip()
        if not key or key in indexed:
            raise FigureInputError(f"{source_name} has missing or duplicate system_key")
        indexed[key] = row
    return indexed


def _load_results(input_directory: Path) -> tuple[SystemResult, ...]:
    quality_path = input_directory / "retrieval_quality.csv"
    latency_path = input_directory / "latency_comparison.csv"
    resource_path = input_directory / "resource_comparison.csv"
    quality_rows = _read_rows(quality_path)
    latency_by_key = _index_by_key(_read_rows(latency_path), source_name="latency CSV")
    resource_by_key = _index_by_key(
        _read_rows(resource_path), source_name="resource CSV"
    )
    quality_by_key = _index_by_key(quality_rows, source_name="quality CSV")
    if not quality_rows or not (
        set(quality_by_key) == set(latency_by_key) == set(resource_by_key)
    ):
        raise FigureInputError("quality, latency, and resource system sets disagree")

    results: list[SystemResult] = []
    seen_codes: set[str] = set()
    for quality in quality_rows:
        key = quality["system_key"]
        latency = latency_by_key[key]
        resource = resource_by_key[key]
        labels = {
            quality.get("system_label", ""),
            latency.get("system_label", ""),
            resource.get("system_label", ""),
        }
        if len(labels) != 1 or "" in labels:
            raise FigureInputError(f"system label mismatch for {key}")
        label = labels.pop()
        code = _system_code(label)
        if code in seen_codes:
            raise FigureInputError(f"duplicate system code: {code}")
        seen_codes.add(code)
        p50 = _finite_float(latency, "end_to_end_latency_p50_ms", minimum=0.001)
        p95 = _finite_float(latency, "end_to_end_latency_p95_ms", minimum=0.001)
        if p95 < p50:
            raise FigureInputError(f"p95 latency is lower than p50 for {key}")
        results.append(
            SystemResult(
                system_key=key,
                system_code=code,
                system_label=label,
                question_count=_integer(quality, "question_count", positive=True),
                recall_at_5=_probability(quality, "recall_at_5"),
                mrr_at_10=_probability(quality, "mrr_at_10"),
                ndcg_at_10=_probability(quality, "ndcg_at_10"),
                end_to_end_p50_ms=p50,
                end_to_end_p95_ms=p95,
                total_unique_model_size_bytes=_integer(
                    resource, "total_unique_model_size_bytes", positive=True
                ),
                index_size_bytes=_integer(resource, "index_size_bytes", positive=True),
                peak_runtime_process_rss_bytes=_integer(
                    resource, "peak_runtime_process_rss_bytes", positive=True
                ),
            )
        )
    if tuple(result.system_code for result in results) != EXPECTED_SYSTEM_CODES:
        raise FigureInputError("system order or membership differs from the approved protocol")
    question_counts = {result.question_count for result in results}
    if question_counts != {13}:
        raise FigureInputError("preliminary comparison requires exactly 13 questions")
    return tuple(results)


def _load_matplotlib() -> tuple[Any, Any, Any]:
    try:
        import matplotlib as mpl  # type: ignore[import-not-found]

        mpl.use("Agg")
        import matplotlib.pyplot as plt  # type: ignore[import-not-found]
        import numpy as np
    except ModuleNotFoundError as exc:
        raise FigureInputError(
            "matplotlib and numpy are required in the selected Python plotting runtime"
        ) from exc
    # Editable SVG text is part of the figure contract.
    plt.rcParams["font.family"] = "sans-serif"
    plt.rcParams["font.sans-serif"] = ["Arial", "DejaVu Sans", "Liberation Sans"]
    plt.rcParams["svg.fonttype"] = "none"
    return mpl, np, plt


def _configure_matplotlib(mpl: Any) -> None:
    mpl.rcParams.update(
        {
            "font.size": 7,
            "axes.labelsize": 7,
            "axes.titlesize": 8,
            "axes.titleweight": "bold",
            "axes.linewidth": 0.8,
            "axes.spines.right": False,
            "axes.spines.top": False,
            "xtick.labelsize": 6.5,
            "ytick.labelsize": 6.5,
            "legend.frameon": False,
            "legend.fontsize": 6.5,
            "svg.fonttype": "none",
            "pdf.fonttype": 42,
            "savefig.facecolor": "white",
        }
    )


def _padded_probability_limits(values: list[float]) -> tuple[float, float]:
    low = min(values)
    high = max(values)
    span = max(high - low, 0.05)
    return max(0.0, low - span * 0.15), min(1.0, high + span * 0.18)


def _draw_figure(
    results: tuple[SystemResult, ...], output_directory: Path
) -> list[Path]:
    mpl, np, plt = _load_matplotlib()
    _configure_matplotlib(mpl)
    codes = [row.system_code for row in results]
    colors = [SYSTEM_COLORS[code] for code in codes]
    recall = np.asarray([row.recall_at_5 for row in results])
    mrr = np.asarray([row.mrr_at_10 for row in results])
    p50 = np.asarray([row.end_to_end_p50_ms for row in results])
    p95 = np.asarray([row.end_to_end_p95_ms for row in results])
    if np.any(p50 <= 0) or np.any(p95 <= 0):
        raise FigureInputError("log latency axes require strictly positive values")

    figure = plt.figure(figsize=(WIDTH_MM / 25.4, HEIGHT_MM / 25.4))
    grid = figure.add_gridspec(
        2,
        2,
        height_ratios=(1.12, 1.0),
        left=0.095,
        right=0.975,
        bottom=0.20,
        top=0.88,
        hspace=0.55,
        wspace=0.34,
    )
    tradeoff_axis = figure.add_subplot(grid[0, :])
    quality_axis = figure.add_subplot(grid[1, 0])
    latency_axis = figure.add_subplot(grid[1, 1])

    # a | Hero evidence: retrieval quality against request latency.
    for start, end in ((0, 1), (2, 3), (4, 5)):
        tradeoff_axis.annotate(
            "",
            xy=(p50[end], recall[end]),
            xytext=(p50[start], recall[start]),
            arrowprops={
                "arrowstyle": "->",
                "color": "#A8A8A8",
                "linewidth": 0.9,
                "shrinkA": 6,
                "shrinkB": 6,
            },
            zorder=1,
        )
    tradeoff_axis.scatter(
        p50,
        recall,
        s=58,
        c=colors,
        edgecolors="#272727",
        linewidths=0.55,
        zorder=3,
    )
    for index, code in enumerate(codes):
        x_value = p50[index]
        y_value = recall[index]
        tradeoff_axis.annotate(
            code,
            (x_value, y_value),
            xytext=(5, 4),
            textcoords="offset points",
            color="#272727",
            fontsize=7,
            fontweight="bold",
        )
    tradeoff_axis.set_xscale("log")
    tradeoff_axis.set_xlabel("End-to-end latency p50 (ms, log scale)")
    tradeoff_axis.set_ylabel("Recall@5")
    tradeoff_axis.set_ylim(*_padded_probability_limits(recall.tolist()))
    tradeoff_axis.grid(axis="both", color="#D8D8D8", linewidth=0.45, alpha=0.65)
    tradeoff_axis.set_axisbelow(True)
    tradeoff_axis.set_title("a   Quality–latency trade-off", loc="left")

    # b | Quality metrics, on a common probability scale.
    y_positions = np.arange(len(results))[::-1]
    quality_axis.hlines(
        y_positions,
        np.minimum(recall, mrr),
        np.maximum(recall, mrr),
        color="#CFCECE",
        linewidth=1.6,
        zorder=1,
    )
    quality_axis.scatter(
        recall,
        y_positions,
        s=31,
        c=colors,
        marker="o",
        edgecolors="#272727",
        linewidths=0.45,
        label="Recall@5",
        zorder=3,
    )
    quality_axis.scatter(
        mrr,
        y_positions,
        s=31,
        c=colors,
        marker="s",
        edgecolors="#272727",
        linewidths=0.45,
        label="MRR@10",
        zorder=3,
    )
    quality_axis.set_yticks(y_positions, codes)
    quality_axis.set_xlim(*_padded_probability_limits([*recall, *mrr]))
    quality_axis.set_xlabel("Metric value")
    quality_axis.set_title("b   Retrieval quality (n = 13)", loc="left")
    quality_axis.grid(axis="x", color="#D8D8D8", linewidth=0.45, alpha=0.65)
    quality_axis.legend(loc="upper right", handletextpad=0.4)
    quality_axis.set_axisbelow(True)

    # c | Measured request latency, showing distribution summary without CI claims.
    latency_axis.hlines(
        y_positions,
        p50,
        p95,
        color=colors,
        linewidth=2.0,
        zorder=1,
    )
    latency_axis.scatter(
        p50,
        y_positions,
        s=31,
        c=colors,
        marker="o",
        edgecolors="#272727",
        linewidths=0.45,
        label="p50",
        zorder=3,
    )
    latency_axis.scatter(
        p95,
        y_positions,
        s=31,
        c=colors,
        marker="|",
        linewidths=1.4,
        label="p95",
        zorder=3,
    )
    latency_axis.set_xscale("log")
    latency_axis.set_yticks(y_positions, codes)
    latency_axis.set_xlabel("End-to-end latency (ms, log scale)")
    latency_axis.set_title("c   Request latency (13 measurements)", loc="left")
    latency_axis.grid(axis="x", color="#D8D8D8", linewidth=0.45, alpha=0.65)
    latency_axis.legend(loc="upper right", handletextpad=0.4)
    latency_axis.set_axisbelow(True)

    labels = (
        "A  BGE-small   ·   B  BGE + MedCPT CE   ·   C  MedCPT 768d\n"
        "C+  MedCPT 768d + CE   ·   D  Qwen3 384d   ·   D+  Qwen3 384d + reranker"
    )
    figure.suptitle(
        "Embedding and reranker ablation — preliminary, unreviewed legacy gold",
        x=0.095,
        y=0.965,
        ha="left",
        va="top",
        fontsize=9,
        fontweight="bold",
    )
    figure.text(
        0.095,
        0.025,
        labels,
        ha="left",
        va="bottom",
        fontsize=5.8,
        color="#4D4D4D",
        wrap=True,
    )

    output_directory.mkdir(parents=True, exist_ok=True)
    svg_path = output_directory / f"{OUTPUT_STEM}.svg"
    pdf_path = output_directory / f"{OUTPUT_STEM}.pdf"
    tiff_path = output_directory / f"{OUTPUT_STEM}.tiff"
    png_path = output_directory / f"{OUTPUT_STEM}.png"
    figure.savefig(svg_path, bbox_inches="tight")
    figure.savefig(pdf_path, bbox_inches="tight")
    figure.savefig(tiff_path, dpi=RASTER_DPI, bbox_inches="tight")
    figure.savefig(png_path, dpi=RASTER_DPI, bbox_inches="tight")
    plt.close(figure)
    return [svg_path, pdf_path, tiff_path, png_path]


def _write_source_data(results: tuple[SystemResult, ...], output_directory: Path) -> Path:
    path = output_directory / f"{OUTPUT_STEM}_source_data.csv"
    fields = tuple(SystemResult.__dataclass_fields__)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for row in results:
            writer.writerow({field: getattr(row, field) for field in fields})
    return path


def _write_manifest(
    *,
    input_directory: Path,
    output_directory: Path,
    outputs: list[Path],
    source_data: Path,
) -> Path:
    manifest_path = output_directory / f"{OUTPUT_STEM}_manifest.json"
    payload = {
        "figure_schema_version": "embedding-ablation-preliminary-figure-v1",
        "result_status": "preliminary_unreviewed_legacy_gold",
        "formal_benchmark_eligible": False,
        "figure_contract": {
            "core_conclusion": (
                "Expose the measured Recall@5 and MRR@10 versus end-to-end latency "
                "trade-off without presupposing a winning system."
            ),
            "archetype": "quantitative grid",
            "backend": "Python/matplotlib",
            "final_size_mm": [WIDTH_MM, HEIGHT_MM],
            "sample_size": 13,
            "statistics": "descriptive metrics; no confidence intervals",
            "reviewer_risks": [
                "legacy gold has zero approved questions",
                "latency uses one measured request per question after one warmup",
                "CPU-only measurements are hardware-specific",
            ],
        },
        "source_files": {
            name: {
                "path": str(input_directory / name),
                "sha256": _sha256(input_directory / name),
            }
            for name in (
                "retrieval_quality.csv",
                "latency_comparison.csv",
                "resource_comparison.csv",
            )
        },
        "source_data": {"path": str(source_data), "sha256": _sha256(source_data)},
        "outputs": [
            {"path": str(path), "sha256": _sha256(path), "byte_size": path.stat().st_size}
            for path in outputs
        ],
    }
    manifest_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input-directory", type=Path, default=Path("benchmark/embedding_ablation")
    )
    parser.add_argument(
        "--output-directory",
        type=Path,
        default=Path(
            ".artifacts/embedding_ablation/figures/preliminary_comparison"
        ),
    )
    arguments = parser.parse_args()
    results = _load_results(arguments.input_directory)
    outputs = _draw_figure(results, arguments.output_directory)
    source_data = _write_source_data(results, arguments.output_directory)
    manifest = _write_manifest(
        input_directory=arguments.input_directory,
        output_directory=arguments.output_directory,
        outputs=outputs,
        source_data=source_data,
    )
    print(
        json.dumps(
            {
                "manifest": str(manifest),
                "outputs": [str(path) for path in outputs],
                "source_data": str(source_data),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
