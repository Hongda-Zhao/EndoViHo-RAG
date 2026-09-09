#!/usr/bin/env python3
# ruff: noqa: E501 - generated prose is kept as exact line-oriented Markdown input.
"""Regenerate the checksum-bound RAG-value v1 authoring artifacts and docs."""

from __future__ import annotations

import hashlib
from collections import Counter
from pathlib import Path

from eve_relation_rag.experiments.rag_value_ablation.associations import (
    association_contract_v1_bytes,
)
from eve_relation_rag.experiments.rag_value_ablation.contracts import EvaluationQuestion
from eve_relation_rag.experiments.rag_value_ablation.scientific_questions import (
    ScientificQuestionTemplate,
    build_scientific_entity_bindings_template,
    build_scientific_question_templates,
    scientific_entity_bindings_template_bytes,
    scientific_questions_template_bytes,
)
from eve_relation_rag.literature.hashing import canonical_json_bytes

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BENCHMARK_DIRECTORY = REPOSITORY_ROOT / "benchmark" / "rag_value_ablation"
DOCS_DIRECTORY = REPOSITORY_ROOT / "docs"


def main() -> None:
    templates = build_scientific_question_templates()
    BENCHMARK_DIRECTORY.mkdir(parents=True, exist_ok=True)
    (BENCHMARK_DIRECTORY / "association_contract_v1.json").write_bytes(
        association_contract_v1_bytes()
    )
    (BENCHMARK_DIRECTORY / "scientific_questions_template.jsonl").write_bytes(
        scientific_questions_template_bytes()
    )
    (BENCHMARK_DIRECTORY / "scientific_entity_bindings_template.json").write_bytes(
        scientific_entity_bindings_template_bytes()
    )
    (BENCHMARK_DIRECTORY / "question_schema.json").write_bytes(
        canonical_json_bytes(EvaluationQuestion.model_json_schema()) + b"\n"
    )
    (DOCS_DIRECTORY / "scientific_question_redesign.md").write_text(
        _redesign_document(templates), encoding="utf-8"
    )
    (DOCS_DIRECTORY / "scientific_question_capability_gap.md").write_text(
        _capability_document(templates), encoding="utf-8"
    )


def _redesign_document(templates: tuple[ScientificQuestionTemplate, ...]) -> str:
    question_bytes = scientific_questions_template_bytes()
    bindings = build_scientific_entity_bindings_template()
    lines = [
        "# Scientific question redesign for the RAG-value benchmark",
        "",
        "## Outcome",
        "",
        "The first benchmark version now asks only for associations represented by the available data:",
        "",
        "```text",
        "Assembly-source taxon",
        "  -> EVE locus / reported viral region",
        "  -> viral-lineage affinity",
        "  -> evidence and source",
        "```",
        "",
        "The 48 answerable templates do not require records to be classified as `Transferred gene`",
        "or `Integrated virus`. The remaining 16 templates test explicit scientific and operational",
        "refusal boundaries. All 64 records remain pending authoring templates without Gold or approval.",
        "",
        "## Source-label boundary",
        "",
        "`HCVR`, `VR Type`, and `Viral Major Taxon` remain source-native annotations. In particular:",
        "",
        "- `VR Type = Integration` does not imply `Integrated virus`;",
        "- `VR Type = Viral contig` does not imply `Transferred gene`; and",
        "- `HCVR` does not imply either relation class.",
        "",
        "The v1 association schema has no `relation_class` field. The two requested relation classes",
        "may be added only in a later, separately reviewed contract supported by explicit assertions.",
        "",
        "## Association output contract",
        "",
        "| Family | Required association output | Evidence boundary |",
        "| --- | --- | --- |",
        "| Structured | `exact_association_set` | Exact DatasetRelease and source-record keys |",
        "| Literature | `source_reported_association_set` | Permitted documents and evidence-group keys |",
        "| Hybrid | Both sets plus `cross_source_association_set` | Human-reviewed alignment; source values remain separate |",
        "",
        "Every exact tuple carries an assembly-source taxon binding, assembly accession, EVE-locus key,",
        "role- and snapshot-qualified viral-lineage affinity, release identity, and source-record keys.",
        "Every literature tuple carries source-reported taxon text, a named viral region, source-reported",
        "viral-lineage-affinity text, document keys, and evidence-group keys. Missing normalization is kept",
        "explicit and is never filled from lexical similarity or the other truth domain.",
        "",
        "## Template inventory",
        "",
        f"The canonical JSONL has SHA-256 `{hashlib.sha256(question_bytes).hexdigest()}`.",
        "Family counts are 16 structured, 16 literature, 16 Hybrid, and 16 unsupported.",
        "",
        "## Entity-binding worksheet",
        "",
        f"The worksheet contains {bindings.binding_count} empty pending slots:",
        "",
        "| Slot | Required entity type |",
        "| --- | --- |",
    ]
    lines.extend(
        f"| `{binding.entity_slot}` | `{binding.required_entity_type}` |"
        for binding in bindings.bindings
    )
    lines.extend(
        [
            "",
            "## Human-readable question set",
            "",
        ]
    )
    task_titles = {
        "source_taxon_association": "Assembly-source taxon association",
        "viral_lineage_association": "Viral-lineage affinity association",
        "source_viral_lineage_association": "Taxon × viral-lineage affinity association",
        "assembly_locus_association": "Assembly and locus/region association",
        "unsupported_scientific_or_operational_boundary": "Unsupported boundaries",
    }
    for task, title in task_titles.items():
        lines.extend([f"### {title}", ""])
        lines.extend(
            f"- `{template.template_id}` — {template.question_text_template}"
            for template in templates
            if template.scientific_task == task
        )
        lines.append("")
    lines.extend(
        [
            "## Trust transition",
            "",
            "A pending template can become an executable evaluation question only after entity binding,",
            "complete association projection, provenance verification, independent scientific wording review,",
            "human Gold and Oracle annotation, and explicit approval. Parser acceptance alone is not evidence.",
            "",
        ]
    )
    return "\n".join(lines)


def _capability_document(templates: tuple[ScientificQuestionTemplate, ...]) -> str:
    status_counts = Counter(template.capability_status for template in templates)
    lines = [
        "# Scientific question capability-gap analysis",
        "",
        "## Scope and result",
        "",
        "This static audit evaluates the 64 pending templates against the v1 association chain:",
        "",
        "```text",
        "assembly-source taxon -> EVE locus / reported viral region",
        "  -> viral-lineage affinity -> evidence and source",
        "```",
        "",
        f"All {status_counts['requires_v1_association_projection']} answerable templates require the v1",
        "association projection and provenance-preservation path. None requires a `Transferred gene` or",
        "`Integrated virus` field. The 16 unsupported templates remain fail-closed by design.",
        "",
        "## Shared gaps",
        "",
        "The current application has useful query primitives, but the natural-language benchmark still",
        "needs a complete paginated association projection, explicit evidence/source serialization,",
        "family-specific routing, and human-reviewed cross-source alignment. Source annotations must remain",
        "verbatim and cannot be promoted into relation classes.",
        "",
        "## Per-template capability status",
        "",
        "| Template | Family | Status | Required capabilities |",
        "| --- | --- | --- | --- |",
    ]
    lines.extend(
        "| {id} | {family} | `{status}` | {capabilities} |".format(
            id=template.template_id,
            family=template.family,
            status=template.capability_status,
            capabilities=", ".join(f"`{value}`" for value in template.required_capabilities),
        )
        for template in templates
    )
    lines.extend(
        [
            "",
            "## Activation boundary",
            "",
            "Before execution, the benchmark must bind approved question, Gold, entity, association-contract,",
            "release, corpus, anchor, and evidence-source manifests. Diversity checks apply to represented",
            "source taxa, assemblies, EVE loci, reported viral regions, role-qualified viral lineages, and",
            "evidence sources—not to unsupported relation classes.",
            "",
        ]
    )
    return "\n".join(lines)


if __name__ == "__main__":
    main()
