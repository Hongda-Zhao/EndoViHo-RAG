#!/usr/bin/env python3
"""Render the beginner-facing README comparison from machine benchmark CSVs."""

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
HEIGHT_MM = 108.0
PNG_DPI = 300
QA_DPI = 600
OUTPUT_STEM = "retrieval_ablation_overview"
EXPECTED_SYSTEM_CODES = ("A", "B", "C", "C+", "D", "D+")
DISPLAY_LABELS = {
    "A": "A  Current baseline (BGE)",
    "B": "B  Baseline + MedCPT reranking",
    "C": "C  MedCPT retrieval",
    "C+": "C+  MedCPT retrieval + reranking",
    "D": "D  Qwen3 retrieval",
    "D+": "D+  Qwen3 retrieval + reranking",
}
SYSTEM_COLORS = {
    "A": "#235A7A",
    "B": "#A7BAC6",
    "C": "#718894",
    "C+": "#D28A3E",
    "D": "#5D8E83",
    "D+": "#B5C2BF",
}
SYSTEM_TEXT_COLORS = {
    "A": "#235A7A",
    "B": "#617883",
    "C": "#586F79",
    "C+": "#B96820",
    "D": "#46766D",
    "D+": "#687B77",
}


class FigureInputError(ValueError):
    """Raised when the machine results cannot safely support the README figure."""


@dataclass(frozen=True)
class SystemResult:
    """Validated values displayed in the README figure."""

    system_key: str
    system_code: str
    source_label: str
    display_label: str
    question_count: int
    recall_at_5: float
    hit_count_at_5: int
    mrr_at_10: float
    end_to_end_p50_ms: float


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


def _index_by_key(
    rows: list[dict[str, str]], *, source_name: str
) -> dict[str, dict[str, str]]:
    indexed: dict[str, dict[str, str]] = {}
    for row in rows:
        key = row.get("system_key", "").strip()
        if not key or key in indexed:
            raise FigureInputError(f"{source_name} has a missing or duplicate system_key")
        indexed[key] = row
    return indexed


def _finite_float(
    row: dict[str, str], field: str, *, minimum: float = 0.0
) -> float:
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


def _positive_integer(row: dict[str, str], field: str) -> int:
    raw = row.get(field)
    try:
        value = int(raw) if raw is not None else 0
    except ValueError as exc:
        raise FigureInputError(f"{field} must be an integer") from exc
    if value <= 0:
        raise FigureInputError(f"{field} must be positive")
    return value


def _system_code(label: str) -> str:
    code = label.split("·", maxsplit=1)[0].strip()
    if code not in EXPECTED_SYSTEM_CODES:
        raise FigureInputError(f"unsupported system label: {label!r}")
    return code


def _load_results(input_directory: Path) -> tuple[SystemResult, ...]:
    quality_path = input_directory / "retrieval_quality.csv"
    latency_path = input_directory / "latency_comparison.csv"
    quality_rows = _read_rows(quality_path)
    quality_by_key = _index_by_key(quality_rows, source_name="quality CSV")
    latency_by_key = _index_by_key(
        _read_rows(latency_path), source_name="latency CSV"
    )
    if not quality_rows or set(quality_by_key) != set(latency_by_key):
        raise FigureInputError("quality and latency system sets disagree")

    results: list[SystemResult] = []
    seen_codes: set[str] = set()
    for quality in quality_rows:
        key = quality["system_key"]
        latency = latency_by_key[key]
        quality_label = quality.get("system_label", "").strip()
        latency_label = latency.get("system_label", "").strip()
        if not quality_label or quality_label != latency_label:
            raise FigureInputError(f"system label mismatch for {key}")
        code = _system_code(quality_label)
        if code in seen_codes:
            raise FigureInputError(f"duplicate system code: {code}")
        seen_codes.add(code)

        question_count = _positive_integer(quality, "question_count")
        recall_at_5 = _probability(quality, "recall_at_5")
        raw_hit_count = recall_at_5 * question_count
        hit_count = round(raw_hit_count)
        if not math.isclose(raw_hit_count, hit_count, rel_tol=0.0, abs_tol=1e-8):
            raise FigureInputError(f"Recall@5 does not map to whole questions for {key}")
        results.append(
            SystemResult(
                system_key=key,
                system_code=code,
                source_label=quality_label,
                display_label=DISPLAY_LABELS[code],
                question_count=question_count,
                recall_at_5=recall_at_5,
                hit_count_at_5=hit_count,
                mrr_at_10=_probability(quality, "mrr_at_10"),
                end_to_end_p50_ms=_finite_float(
                    latency, "end_to_end_latency_p50_ms", minimum=0.001
                ),
            )
        )

    if tuple(row.system_code for row in results) != EXPECTED_SYSTEM_CODES:
        raise FigureInputError("system order or membership differs from the experiment protocol")
    if {row.question_count for row in results} != {13}:
        raise FigureInputError("the preliminary README figure requires exactly 13 questions")
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

    mpl.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": [
                "Arial",
                "Helvetica",
                "DejaVu Sans",
                "sans-serif",
            ],
            "font.size": 8,
            "axes.labelsize": 8,
            "axes.titlesize": 9,
            "axes.titleweight": "bold",
            "axes.linewidth": 0.8,
            "axes.spines.right": False,
            "axes.spines.top": False,
            "xtick.labelsize": 7,
            "ytick.labelsize": 8,
            "svg.fonttype": "none",
            "pdf.fonttype": 42,
            "savefig.facecolor": "white",
        }
    )
    return mpl, np, plt


def _format_latency(milliseconds: float) -> str:
    seconds = milliseconds / 1000.0
    if seconds < 10.0:
        return f"{seconds:.2f} s"
    if seconds < 60.0:
        return f"{seconds:.1f} s"
    minutes = int(seconds // 60)
    remaining_seconds = round(seconds - minutes * 60)
    return f"{minutes} min {remaining_seconds:02d} s"


def _draw_figure(
    results: tuple[SystemResult, ...],
    output_directory: Path,
    qa_directory: Path,
) -> list[Path]:
    _, np, plt = _load_matplotlib()
    colors = [SYSTEM_COLORS[row.system_code] for row in results]
    text_colors = [SYSTEM_TEXT_COLORS[row.system_code] for row in results]
    y_positions = np.arange(len(results))[::-1]
    hit_counts = np.asarray([row.hit_count_at_5 for row in results])
    latencies_s = np.asarray([row.end_to_end_p50_ms / 1000.0 for row in results])
    if np.any(latencies_s <= 0):
        raise FigureInputError("log latency axis requires strictly positive values")

    figure = plt.figure(figsize=(WIDTH_MM / 25.4, HEIGHT_MM / 25.4))
    grid = figure.add_gridspec(
        1,
        2,
        width_ratios=(1.05, 1.15),
        left=0.29,
        right=0.975,
        bottom=0.235,
        top=0.70,
        wspace=0.28,
    )
    hit_axis = figure.add_subplot(grid[0, 0])
    latency_axis = figure.add_subplot(grid[0, 1])

    hit_axis.barh(
        y_positions,
        [13] * len(results),
        height=0.52,
        color="#EDF1F3",
        edgecolor="none",
        zorder=1,
    )
    hit_axis.barh(
        y_positions,
        hit_counts,
        height=0.52,
        color=colors,
        edgecolor="none",
        zorder=2,
    )
    for index, y_position in enumerate(y_positions):
        count = hit_counts[index]
        text_color = text_colors[index]
        hit_axis.text(
            count + 0.18,
            y_position,
            f"{count}/13",
            ha="left",
            va="center",
            fontsize=8,
            fontweight="bold",
            color=text_color,
        )
    hit_axis.set_xlim(0, 14.5)
    hit_axis.set_xticks((0, 5, 10, 13))
    hit_axis.set_yticks(y_positions, [row.display_label for row in results])
    hit_axis.set_xlabel("Questions with a gold-evidence hit")
    hit_axis.set_title("1  Correct evidence found in the top 5", loc="left", pad=8)
    hit_axis.grid(axis="x", color="#DCE2E5", linewidth=0.6, zorder=0)
    hit_axis.tick_params(axis="y", length=0, pad=8)
    hit_axis.spines["left"].set_visible(False)
    hit_axis.spines["bottom"].set_color("#AAB4B9")

    latency_axis.hlines(
        y_positions,
        0.04,
        latencies_s,
        color=colors,
        linewidth=2.2,
        zorder=1,
    )
    latency_axis.scatter(
        latencies_s,
        y_positions,
        s=48,
        color=colors,
        edgecolors="white",
        linewidths=0.7,
        zorder=3,
    )
    for index, y_position in enumerate(y_positions):
        latency = latencies_s[index]
        row = results[index]
        text_color = text_colors[index]
        latency_axis.annotate(
            _format_latency(row.end_to_end_p50_ms),
            (latency, y_position),
            xytext=(5, 0),
            textcoords="offset points",
            ha="left",
            va="center",
            fontsize=7.6,
            fontweight="bold",
            color=text_color,
        )
    latency_axis.set_xscale("log")
    latency_axis.set_xlim(0.035, 600.0)
    latency_axis.set_xticks((0.05, 1.0, 10.0, 60.0, 300.0))
    latency_axis.set_xticklabels(("0.05 s", "1 s", "10 s", "1 min", "5 min"))
    latency_axis.minorticks_off()
    latency_axis.set_yticks(y_positions, [""] * len(results))
    latency_axis.set_xlabel("Median end-to-end latency (lower is better)")
    latency_axis.set_title("2  Typical wait per question (log scale)", loc="left", pad=8)
    latency_axis.grid(axis="x", color="#DCE2E5", linewidth=0.6, zorder=0)
    latency_axis.tick_params(axis="y", length=0)
    latency_axis.spines["left"].set_visible(False)
    latency_axis.spines["bottom"].set_color("#AAB4B9")

    figure.suptitle(
        "Which literature retrieval system fits this repository?",
        x=0.075,
        y=0.955,
        ha="left",
        va="top",
        fontsize=13,
        fontweight="bold",
        color="#173B4F",
    )
    figure.text(
        0.075,
        0.86,
        (
            "Same 11 documents, 1,464 chunks, and 13 real questions.\n"
            "More hits do not automatically mean a better deployment choice; "
            "latency matters too."
        ),
        ha="left",
        va="top",
        fontsize=8.5,
        color="#4F626C",
    )
    figure.text(
        0.075,
        0.105,
        (
            "Preliminary: 0/13 legacy-gold questions have completed expert approval; "
            "a hit means gold evidence appears in the top 5."
        ),
        ha="left",
        va="bottom",
        fontsize=7,
        color="#55666E",
    )
    figure.text(
        0.075,
        0.052,
        (
            "Latency is the median on Apple M2, 16 GiB, CPU-only hardware; "
            "model loading is excluded."
        ),
        ha="left",
        va="bottom",
        fontsize=7,
        color="#55666E",
    )

    output_directory.mkdir(parents=True, exist_ok=True)
    qa_directory.mkdir(parents=True, exist_ok=True)
    svg_path = output_directory / f"{OUTPUT_STEM}.svg"
    png_path = output_directory / f"{OUTPUT_STEM}.png"
    pdf_path = qa_directory / f"{OUTPUT_STEM}.pdf"
    tiff_path = qa_directory / f"{OUTPUT_STEM}.tiff"
    figure.savefig(svg_path, bbox_inches="tight")
    svg_text = svg_path.read_text(encoding="utf-8")
    svg_path.write_text(
        "\n".join(line.rstrip() for line in svg_text.splitlines()) + "\n",
        encoding="utf-8",
    )
    figure.savefig(pdf_path, bbox_inches="tight")
    figure.savefig(tiff_path, dpi=QA_DPI, bbox_inches="tight")
    figure.savefig(png_path, dpi=PNG_DPI, bbox_inches="tight")
    plt.close(figure)
    return [svg_path, png_path, pdf_path, tiff_path]


def _write_source_data(
    results: tuple[SystemResult, ...], qa_directory: Path
) -> Path:
    path = qa_directory / f"{OUTPUT_STEM}_source_data.csv"
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
    qa_directory: Path,
    outputs: list[Path],
    source_data: Path,
) -> Path:
    manifest_path = qa_directory / f"{OUTPUT_STEM}_manifest.json"
    payload = {
        "figure_schema_version": "embedding-ablation-readme-figure-v1",
        "result_status": "preliminary_unreviewed_legacy_gold",
        "formal_benchmark_eligible": False,
        "figure_contract": {
            "core_conclusion": (
                "The current BGE baseline is the best measured quality-latency balance; "
                "MedCPT retrieval plus reranking finds one additional question but is "
                "about 29 times slower at p50."
            ),
            "archetype": "quantitative grid",
            "backend": "Python/matplotlib",
            "final_size_mm": [WIDTH_MM, HEIGHT_MM],
            "panel_map": {
                "left": "Recall@5 expressed as whole questions out of 13",
                "right": "end-to-end p50 latency on a logarithmic scale",
            },
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
            for name in ("retrieval_quality.csv", "latency_comparison.csv")
        },
        "source_data": {"path": str(source_data), "sha256": _sha256(source_data)},
        "outputs": [
            {
                "path": str(path),
                "sha256": _sha256(path),
                "byte_size": path.stat().st_size,
            }
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
        "--output-directory", type=Path, default=Path("docs/assets")
    )
    parser.add_argument(
        "--qa-directory",
        type=Path,
        default=Path(".artifacts/embedding_ablation/figures/readme_summary"),
    )
    arguments = parser.parse_args()
    results = _load_results(arguments.input_directory)
    outputs = _draw_figure(
        results, arguments.output_directory, arguments.qa_directory
    )
    source_data = _write_source_data(results, arguments.qa_directory)
    manifest = _write_manifest(
        input_directory=arguments.input_directory,
        qa_directory=arguments.qa_directory,
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
