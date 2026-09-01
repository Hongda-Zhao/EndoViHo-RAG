#!/usr/bin/env python3
"""Render the preliminary single-system retrieval-quality figure from a real report."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

WIDTH_MM = 180.0
HEIGHT_MM = 84.0
RASTER_DPI = 600
OUTPUT_STEM = "preliminary_bge_retrieval_quality"


class FigureInputError(ValueError):
    """Raised when the machine report cannot safely support this figure."""


@dataclass(frozen=True)
class QuestionResult:
    """One fixed benchmark question and its binary recall outcomes."""

    question_key: str
    recall_at_5: float
    recall_at_10: float


@dataclass(frozen=True)
class BenchmarkReport:
    """Validated inputs needed for the preliminary retrieval figure."""

    source_sha256: str
    embedding_model_key: str
    retrieval_policy_key: str
    recall_at_5: float
    recall_at_10: float
    question_results: tuple[QuestionResult, ...]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _probability(value: object, *, field: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise FigureInputError(f"{field} must be numeric") from exc
    if not math.isfinite(parsed):
        raise FigureInputError(f"{field} must be finite")
    if not 0.0 <= parsed <= 1.0:
        raise FigureInputError(f"{field} must be between zero and one")
    return parsed


def _required_text(payload: dict[str, object], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise FigureInputError(f"{field} must be a non-empty string")
    return value.strip()


def _load_report(path: Path) -> BenchmarkReport:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise FigureInputError(f"cannot read report: {path}") from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise FigureInputError("report is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise FigureInputError("report root must be a JSON object")

    question_count = payload.get("question_count")
    raw_results = payload.get("question_results")
    if not isinstance(question_count, int) or question_count <= 0:
        raise FigureInputError("question_count must be a positive integer")
    if not isinstance(raw_results, list) or len(raw_results) != question_count:
        raise FigureInputError("question_results length must equal question_count")

    results: list[QuestionResult] = []
    seen_keys: set[str] = set()
    for index, item in enumerate(raw_results, start=1):
        if not isinstance(item, dict):
            raise FigureInputError(f"question_results[{index}] must be an object")
        status = item.get("status")
        if status != "ok":
            raise FigureInputError(
                f"question_results[{index}] has non-ok status: {status!r}"
            )
        question_key = _required_text(item, "question_key")
        if question_key in seen_keys:
            raise FigureInputError(f"duplicate question_key: {question_key}")
        seen_keys.add(question_key)
        recall_at_5 = _probability(
            item.get("recall_at_5"), field=f"{question_key}.recall_at_5"
        )
        recall_at_10 = _probability(
            item.get("recall_at_10"), field=f"{question_key}.recall_at_10"
        )
        if recall_at_10 < recall_at_5:
            raise FigureInputError(f"{question_key} violates monotonic recall")
        if recall_at_5 not in {0.0, 1.0} or recall_at_10 not in {0.0, 1.0}:
            raise FigureInputError(
                f"{question_key} is not binary; this diagnostic requires hit/miss recall"
            )
        results.append(
            QuestionResult(
                question_key=question_key,
                recall_at_5=recall_at_5,
                recall_at_10=recall_at_10,
            )
        )

    recall_at_5 = _probability(payload.get("recall_at_5"), field="recall_at_5")
    recall_at_10 = _probability(payload.get("recall_at_10"), field="recall_at_10")
    calculated_at_5 = sum(result.recall_at_5 for result in results) / question_count
    calculated_at_10 = sum(result.recall_at_10 for result in results) / question_count
    if not math.isclose(recall_at_5, calculated_at_5, rel_tol=0.0, abs_tol=5e-12):
        raise FigureInputError("aggregate Recall@5 disagrees with per-question results")
    if not math.isclose(recall_at_10, calculated_at_10, rel_tol=0.0, abs_tol=5e-12):
        raise FigureInputError("aggregate Recall@10 disagrees with per-question results")

    return BenchmarkReport(
        source_sha256=hashlib.sha256(raw).hexdigest(),
        embedding_model_key=_required_text(payload, "embedding_model_key"),
        retrieval_policy_key=_required_text(payload, "retrieval_policy_key"),
        recall_at_5=recall_at_5,
        recall_at_10=recall_at_10,
        question_results=tuple(results),
    )


def _load_matplotlib() -> tuple[Any, Any, Any]:
    try:
        import matplotlib as mpl

        mpl.use("Agg")
        import matplotlib.lines as mlines
        import matplotlib.pyplot as plt
    except ModuleNotFoundError as exc:
        raise FigureInputError(
            "matplotlib is required; use a fixed, isolated plotting environment"
        ) from exc
    return mpl, mlines, plt


def _configure_matplotlib(mpl: Any) -> None:
    mpl.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": ["Arial", "Helvetica", "DejaVu Sans", "sans-serif"],
            "font.size": 7,
            "axes.labelsize": 7,
            "axes.titlesize": 8,
            "axes.titleweight": "bold",
            "axes.linewidth": 0.8,
            "axes.spines.right": False,
            "axes.spines.top": False,
            "legend.frameon": False,
            "legend.fontsize": 6.5,
            "xtick.labelsize": 6.5,
            "ytick.labelsize": 6.5,
            "svg.fonttype": "none",
            "pdf.fonttype": 42,
            "savefig.facecolor": "white",
        }
    )


def _write_source_data(report: BenchmarkReport, output_dir: Path) -> Path:
    path = output_dir / f"{OUTPUT_STEM}_source_data.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=(
                "question_index",
                "question_key",
                "recall_at_5",
                "recall_at_10",
            ),
        )
        writer.writeheader()
        for index, result in enumerate(report.question_results, start=1):
            writer.writerow(
                {
                    "question_index": index,
                    "question_key": result.question_key,
                    "recall_at_5": f"{result.recall_at_5:.12f}",
                    "recall_at_10": f"{result.recall_at_10:.12f}",
                }
            )
    return path


def _draw_figure(report: BenchmarkReport, output_dir: Path) -> tuple[str, list[Path]]:
    mpl, mlines, plt = _load_matplotlib()
    _configure_matplotlib(mpl)

    blue = "#0F4D92"
    red = "#B64342"
    green = "#2E7D32"
    neutral = "#767676"
    pale = "#E8EDF3"

    figure = plt.figure(figsize=(WIDTH_MM / 25.4, HEIGHT_MM / 25.4))
    grid = figure.add_gridspec(
        1,
        2,
        width_ratios=(0.85, 1.75),
        left=0.075,
        right=0.985,
        bottom=0.22,
        top=0.70,
        wspace=0.28,
    )
    quality_axis = figure.add_subplot(grid[0, 0])
    matrix_axis = figure.add_subplot(grid[0, 1])

    n_questions = len(report.question_results)
    count_at_5 = round(report.recall_at_5 * n_questions)
    count_at_10 = round(report.recall_at_10 * n_questions)
    recall_percent = (report.recall_at_5 * 100.0, report.recall_at_10 * 100.0)
    y_positions = (1, 0)

    quality_axis.hlines(y_positions, 80.0, recall_percent, color=pale, linewidth=3.2)
    quality_axis.scatter(
        recall_percent,
        y_positions,
        s=76,
        color=(blue, green),
        edgecolors="white",
        linewidths=0.8,
        zorder=3,
    )
    for value, y_position, count, x_offset, alignment in zip(
        recall_percent,
        y_positions,
        (count_at_5, count_at_10),
        (0.8, -0.8),
        ("left", "right"),
        strict=True,
    ):
        quality_axis.text(
            value + x_offset,
            y_position,
            f"{value:.1f}%  ({count}/{n_questions})",
            ha=alignment,
            va="center",
            color="#272727",
            fontsize=6.8,
            fontweight="bold",
        )
    quality_axis.annotate(
        f"+{(report.recall_at_10 - report.recall_at_5) * 100.0:.1f} pp",
        xy=(99.7, 0.02),
        xytext=(91.0, 0.48),
        ha="center",
        va="center",
        color=green,
        fontsize=6.5,
        fontweight="bold",
        arrowprops={"arrowstyle": "->", "color": green, "linewidth": 0.8},
    )
    quality_axis.set_xlim(79.0, 103.0)
    quality_axis.set_ylim(-0.55, 1.55)
    quality_axis.set_xticks((80, 90, 100))
    quality_axis.set_yticks(y_positions, ("Recall@5", "Recall@10"))
    quality_axis.set_xlabel("Questions with a gold hit (%)")
    quality_axis.set_title("Aggregate retrieval quality", loc="left", pad=7)
    quality_axis.tick_params(axis="y", length=0)
    quality_axis.spines["left"].set_visible(False)
    quality_axis.spines["bottom"].set_color("#A9A9A9")

    x_positions = list(range(n_questions))
    for row, cutoff in ((1, 5), (0, 10)):
        for x_position, result in zip(x_positions, report.question_results, strict=True):
            is_hit = result.recall_at_5 == 1.0 if cutoff == 5 else result.recall_at_10 == 1.0
            matrix_axis.scatter(
                x_position,
                row,
                s=90 if is_hit else 84,
                marker="s" if is_hit else "X",
                facecolor=blue if is_hit else red,
                edgecolor="white" if is_hit else red,
                linewidth=0.65,
                zorder=3,
            )
    matrix_axis.set_xlim(-0.65, n_questions - 0.35)
    matrix_axis.set_ylim(-0.55, 1.55)
    matrix_axis.set_xticks(
        x_positions, tuple(f"Q{index:02d}" for index in range(1, n_questions + 1))
    )
    matrix_axis.set_yticks((1, 0), ("Top 5", "Top 10"))
    matrix_axis.set_xlabel("Fixed gold question")
    matrix_axis.set_title("Per-question retrieval outcome", loc="left", pad=7)
    matrix_axis.tick_params(axis="both", length=0)
    for spine in matrix_axis.spines.values():
        spine.set_visible(False)
    for tick_index, tick_label in enumerate(matrix_axis.get_xticklabels()):
        result = report.question_results[tick_index]
        if result.recall_at_5 == 0.0:
            tick_label.set_color(red)
            tick_label.set_fontweight("bold")

    hit_handle = mlines.Line2D(
        [],
        [],
        color=blue,
        marker="s",
        linestyle="None",
        markersize=5.5,
        markeredgecolor="white",
        label="Gold hit",
    )
    miss_handle = mlines.Line2D(
        [],
        [],
        color=red,
        marker="X",
        linestyle="None",
        markersize=5.5,
        label="Miss",
    )
    matrix_axis.legend(
        handles=(hit_handle, miss_handle),
        loc="upper right",
        bbox_to_anchor=(1.0, 1.18),
        ncols=2,
        columnspacing=1.1,
        handletextpad=0.4,
    )

    figure.text(
        0.025,
        0.95,
        "Preliminary retrieval benchmark: Top 10 recovers two Top-5 misses",
        ha="left",
        va="top",
        fontsize=10,
        fontweight="bold",
        color="#272727",
    )
    figure.text(
        0.025,
        0.855,
        "BGE-small + PostgreSQL FTS + dense retrieval + RRF · fixed EVE benchmark",
        ha="left",
        va="top",
        fontsize=7,
        color=neutral,
    )
    figure.text(0.018, 0.715, "a", fontsize=8, fontweight="bold", va="bottom")
    figure.text(0.376, 0.715, "b", fontsize=8, fontweight="bold", va="bottom")
    figure.text(
        0.025,
        0.065,
        (
            f"n = {n_questions} fixed gold questions; deterministic point estimates, "
            "no confidence interval. Single-system diagnostic—not a cross-model comparison."
        ),
        ha="left",
        va="bottom",
        fontsize=5.8,
        color=neutral,
    )

    svg_path = output_dir / f"{OUTPUT_STEM}.svg"
    pdf_path = output_dir / f"{OUTPUT_STEM}.pdf"
    tiff_path = output_dir / f"{OUTPUT_STEM}.tiff"
    png_path = output_dir / f"{OUTPUT_STEM}.png"
    figure.savefig(svg_path, format="svg")
    figure.savefig(pdf_path, format="pdf")
    figure.savefig(tiff_path, format="tiff", dpi=RASTER_DPI)
    figure.savefig(png_path, format="png", dpi=RASTER_DPI)
    plt.close(figure)
    return str(mpl.__version__), [svg_path, pdf_path, tiff_path, png_path]


def _write_manifest(
    report: BenchmarkReport,
    output_dir: Path,
    source_data_path: Path,
    matplotlib_version: str,
    figure_paths: list[Path],
) -> Path:
    misses_at_5 = [
        result.question_key for result in report.question_results if result.recall_at_5 == 0.0
    ]
    manifest = {
        "figure_key": OUTPUT_STEM,
        "status": "preliminary_single_system_diagnostic",
        "source_report_sha256": report.source_sha256,
        "source_data": {
            "filename": source_data_path.name,
            "sha256": _sha256(source_data_path),
        },
        "embedding_model_key": report.embedding_model_key,
        "retrieval_policy_key": report.retrieval_policy_key,
        "question_count": len(report.question_results),
        "recall_at_5": f"{report.recall_at_5:.12f}",
        "recall_at_10": f"{report.recall_at_10:.12f}",
        "top_5_miss_question_keys": misses_at_5,
        "width_mm": WIDTH_MM,
        "height_mm": HEIGHT_MM,
        "raster_dpi": RASTER_DPI,
        "matplotlib_version": matplotlib_version,
        "outputs": [
            {"filename": path.name, "sha256": _sha256(path)} for path in figure_paths
        ],
        "caveat": "No cross-model conclusion is supported until trusted Phase 1 runs exist.",
    }
    path = output_dir / f"{OUTPUT_STEM}_manifest.json"
    path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True, type=Path, help="trusted benchmark report JSON")
    parser.add_argument(
        "--output-dir", required=True, type=Path, help="new figure bundle directory"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _build_parser().parse_args(argv)
    output_dir: Path = arguments.output_dir
    if output_dir.exists() and any(output_dir.iterdir()):
        print(f"refusing to overwrite non-empty output directory: {output_dir}", file=sys.stderr)
        return 2
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        report = _load_report(arguments.report)
        source_data_path = _write_source_data(report, output_dir)
        matplotlib_version, figure_paths = _draw_figure(report, output_dir)
        manifest_path = _write_manifest(
            report,
            output_dir,
            source_data_path,
            matplotlib_version,
            figure_paths,
        )
    except FigureInputError as exc:
        print(f"figure generation failed: {exc}", file=sys.stderr)
        return 2
    print(manifest_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
