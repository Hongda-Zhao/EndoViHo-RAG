"""Fail-closed controlled-English parser and planner for Milestone 2.

This module turns one strict request into a server-authored :class:`StructuredPlan`.
It never opens a database session and never executes scientific facts.  Callers must
first gate the exact release and then inject a resolver bound to that same release.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict

from eve_relation_rag.planning.query_plans import (
    PLAN_VERSION,
    AggregatePlan,
    AssemblyDetailPlan,
    AssemblyFilter,
    EntireReleaseScope,
    ExtractedCondition,
    FilteredScope,
    ListAssembliesPlan,
    ListLociPlan,
    ListSourceTaxaPlan,
    LocusDetailPlan,
    LocusFilter,
    MetricKey,
    PageSpec,
    PlanningAudit,
    PublishedReleaseKey,
    QueryFilter,
    QuestionText,
    SemanticSpan,
    SourceLineageFilter,
    StructuredPlan,
    ViralLineageFilter,
)
from eve_relation_rag.planning.resolver import (
    LineageReference,
    LineageRole,
    ReleaseScopedEntityResolver,
    ResolutionFailure,
)
from eve_relation_rag.retrieval.structured.results import (
    EntitySuggestion,
    ErrorResponse,
    PlanSuccess,
    ResolvedEntity,
    StructuredError,
)

type Intent = Literal[
    "assembly_detail",
    "locus_detail",
    "list_loci",
    "list_assemblies",
    "list_source_taxa",
    "aggregate",
]
type FilterType = Literal["assembly", "locus", "source_lineage", "viral_lineage"]
type PlanningErrorCode = Literal[
    "request_schema_invalid",
    "unsupported_question",
    "condition_unmapped",
    "full_release_scope_not_explicit",
    "intent_filter_incompatible",
    "pagination_not_allowed",
    "release_dependencies_incomplete",
    "assembly_accession_version_required",
    "entity_unresolved",
    "entity_ambiguous",
    "entity_not_in_release",
    "lineage_snapshot_mismatch",
    "lineage_role_ambiguous",
    "lineage_scope_ambiguous",
]

_DETAIL_RE = re.compile(
    r"^(?P<intent>show) +(?P<kind>assembly|locus) +(?P<entity>\S+)$",
    re.IGNORECASE,
)
_LIST_RE = re.compile(
    r"^(?P<intent>list) +(?P<object>(?:all +)?loci|(?:all +)?assemblies|"
    r"(?:all +)?source +taxa) +(?P<tail>.+)$",
    re.IGNORECASE,
)
_AGGREGATE_RE = re.compile(
    r"^(?P<intent>count) +(?P<metric>distinct +included +loci|distinct +contigs|"
    r"distinct +assemblies|distinct +source +taxa|detection +calls) +(?P<tail>.+)$",
    re.IGNORECASE,
)
_CLAUSE_MARKER_RE = re.compile(
    r"(?:^|(?P<separator> +and +))(?P<marker>in +assembly|"
    r"assigned +exactly +to +source +lineage|assigned +to +source +lineage|"
    r"with +formal +viral +lineage|with +study +viral +lineage|"
    r"with +viral +lineage) +",
    re.IGNORECASE,
)
_EXACT_LINEAGE_RE = re.compile(
    r"^term +(?P<term>\S+) +in +snapshot +(?P<snapshot>\S+)$",
    re.IGNORECASE,
)
_UNSUPPORTED_PATTERNS: tuple[
    tuple[re.Pattern[str], Literal["negation", "logical_operator", "comparator"]], ...
] = (
    (
        re.compile(r"\b(?:not|no|without|except|excluding|exclude|other +than)\b", re.I),
        "negation",
    ),
    (re.compile(r"\bor\b", re.I), "logical_operator"),
    (
        re.compile(
            r"\b(?:between|from +\d+ +to|at +least|at +most|more +than|less +than)\b|[<>]",
            re.I,
        ),
        "comparator",
    ),
)
_METRICS: dict[str, MetricKey] = {
    "distinct included loci": "distinct_included_locus_count",
    "distinct contigs": "distinct_contig_count",
    "distinct assemblies": "distinct_assembly_count",
    "distinct source taxa": "distinct_source_taxon_count",
    "detection calls": "detection_call_count",
}
_LIST_INTENTS: dict[str, Intent] = {
    "loci": "list_loci",
    "assemblies": "list_assemblies",
    "source taxa": "list_source_taxa",
}
_ALLOWED_FILTERS: dict[Intent, frozenset[FilterType]] = {
    "assembly_detail": frozenset({"assembly"}),
    "locus_detail": frozenset({"locus"}),
    "list_loci": frozenset({"assembly", "source_lineage", "viral_lineage"}),
    "list_assemblies": frozenset({"source_lineage", "viral_lineage"}),
    "list_source_taxa": frozenset({"viral_lineage"}),
    "aggregate": frozenset({"assembly", "source_lineage", "viral_lineage"}),
}


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class StructuredQueryRequest(_FrozenModel):
    """Strict question-first request accepted by the future API and CLI adapters."""

    request_schema_version: Literal["structured-query-request-v1"] = "structured-query-request-v1"
    release_key: PublishedReleaseKey
    question: QuestionText
    page: PageSpec | None = None


@dataclass(slots=True)
class _ConditionDraft:
    key: int
    source_text: str
    source_start: int
    source_end: int
    condition_kind: Literal[
        "intent",
        "entity",
        "negation",
        "logical_operator",
        "comparator",
        "metric",
        "scope",
        "pagination",
    ]
    mapped_target: str | None


@dataclass(frozen=True, slots=True)
class _Mention:
    filter_type: FilterType
    original_input: str
    condition_key: int
    include_descendants: bool | None = None
    lineage_role: LineageRole | None = None
    lineage_term_key: str | None = None
    lineage_snapshot_key: str | None = None
    lineage_name: str | None = None


@dataclass(slots=True)
class _ParseState:
    question: str
    conditions: list[_ConditionDraft] = field(default_factory=list)

    def add_condition(
        self,
        *,
        start: int,
        end: int,
        kind: Literal[
            "intent",
            "entity",
            "negation",
            "logical_operator",
            "comparator",
            "metric",
            "scope",
            "pagination",
        ],
        target: str | None,
    ) -> int:
        key = len(self.conditions)
        self.conditions.append(
            _ConditionDraft(
                key=key,
                source_text=self.question[start:end],
                source_start=start,
                source_end=end,
                condition_kind=kind,
                mapped_target=target,
            )
        )
        return key


@dataclass(frozen=True, slots=True)
class _ParsedQuery:
    intent: Intent
    metric_key: MetricKey | None
    entire_release: bool
    mentions: tuple[_Mention, ...]
    state: _ParseState


class _PlanningRefusal(Exception):
    def __init__(
        self,
        code: PlanningErrorCode,
        message: str,
        audit: PlanningAudit,
        *,
        suggestions: tuple[EntitySuggestion, ...] = (),
        resolved_entities: tuple[ResolvedEntity, ...] = (),
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.audit = audit
        self.suggestions = suggestions
        self.resolved_entities = resolved_entities


def _condition_id(index: int, condition: _ConditionDraft) -> str:
    return f"condition:{index:03d}:{condition.condition_kind}"


def _build_audit(
    state: _ParseState,
    *,
    unresolved_keys: frozenset[int] = frozenset(),
    unconsumed: tuple[tuple[int, int], ...] = (),
) -> PlanningAudit:
    ordered = tuple(
        sorted(state.conditions, key=lambda item: (item.source_start, item.source_end, item.key))
    )
    ids_by_key = {
        item.key: _condition_id(index, item) for index, item in enumerate(ordered, start=1)
    }
    extracted = tuple(
        ExtractedCondition(
            condition_id=ids_by_key[item.key],
            source_text=item.source_text,
            source_start=item.source_start,
            source_end=item.source_end,
            condition_kind=item.condition_kind,
            mapped_target=None if item.key in unresolved_keys else item.mapped_target,
        )
        for item in ordered
    )
    mapped = tuple(item.condition_id for item in extracted if item.mapped_target is not None)
    unresolved = tuple(item.condition_id for item in extracted if item.mapped_target is None)
    spans = tuple(
        SemanticSpan(
            source_text=state.question[start:end],
            source_start=start,
            source_end=end,
        )
        for start, end in unconsumed
    )
    return PlanningAudit(
        extracted_conditions=extracted,
        mapped_condition_ids=mapped,
        unresolved_condition_ids=unresolved,
        unconsumed_semantic_spans=spans,
    )


def _normalize_syntax(value: str) -> str:
    return " ".join(value.split()).casefold()


def _body_and_offset(question: str) -> tuple[str, int]:
    leading = len(question) - len(question.lstrip())
    body = question.strip()
    if body.endswith((".", "?")):
        body = body[:-1].rstrip()
    return body, leading


def _span(match: re.Match[str], group: str, offset: int) -> tuple[int, int]:
    start, end = match.span(group)
    return start + offset, end + offset


def _refuse_unknown(question: str) -> _PlanningRefusal:
    state = _ParseState(question)
    start = len(question) - len(question.lstrip())
    end = len(question.rstrip())
    return _PlanningRefusal(
        "unsupported_question",
        "The question is outside the controlled-English grammar.",
        _build_audit(state, unconsumed=((start, end),)),
    )


def _preflight_unsupported(question: str, body: str, offset: int) -> None:
    for pattern, kind in _UNSUPPORTED_PATTERNS:
        match = pattern.search(body)
        if match is None:
            continue
        start, end = match.span()
        state = _ParseState(question)
        key = state.add_condition(
            start=start + offset,
            end=end + offset,
            kind=kind,
            target=None,
        )
        raise _PlanningRefusal(
            "unsupported_question",
            "Negation, OR, exclusion, comparators, and ranges are not supported.",
            _build_audit(
                state,
                unresolved_keys=frozenset({key}),
                unconsumed=((start + offset, end + offset),),
            ),
        )


def _parse_lineage_reference(
    raw_reference: str,
    *,
    state: _ParseState,
    entity_kind: Literal["source_lineage", "viral_lineage"],
    role: LineageRole,
    filter_type: Literal["source_lineage", "viral_lineage"],
    condition_key: int,
    include_descendants: bool,
) -> _Mention:
    exact = _EXACT_LINEAGE_RE.fullmatch(raw_reference)
    if exact is not None:
        if len(exact.group("term")) > 255 or len(exact.group("snapshot")) > 255:
            state.conditions[condition_key].mapped_target = None
            raise _PlanningRefusal(
                "request_schema_invalid",
                "Lineage term and snapshot keys must contain at most 255 characters.",
                _build_audit(
                    state,
                    unresolved_keys=frozenset({condition_key}),
                ),
            )
        return _Mention(
            filter_type=filter_type,
            original_input=raw_reference,
            condition_key=condition_key,
            include_descendants=include_descendants,
            lineage_role=role,
            lineage_term_key=exact.group("term"),
            lineage_snapshot_key=exact.group("snapshot"),
        )
    return _Mention(
        filter_type=filter_type,
        original_input=raw_reference,
        condition_key=condition_key,
        include_descendants=include_descendants,
        lineage_role=role,
        lineage_name=raw_reference,
    )


def _lineage_ref_and_scope(
    *,
    state: _ParseState,
    raw_value: str,
    raw_start: int,
    marker_normalized: str,
    filter_type: Literal["source_lineage", "viral_lineage"],
) -> tuple[str, int, bool]:
    if marker_normalized == "assigned exactly to source lineage":
        if re.search(r" +(exactly|including +descendants)$", raw_value, re.I):
            key = state.add_condition(
                start=raw_start,
                end=raw_start + len(raw_value),
                kind="scope",
                target=None,
            )
            raise _PlanningRefusal(
                "unsupported_question",
                "A lineage scope qualifier may be stated only once.",
                _build_audit(state, unresolved_keys=frozenset({key})),
            )
        return raw_value, raw_start + len(raw_value), False

    suffix_match = re.search(r" +(?P<scope>exactly|including +descendants)$", raw_value, re.I)
    if suffix_match is None:
        key = state.add_condition(
            start=raw_start,
            end=raw_start + len(raw_value),
            kind="scope",
            target=None,
        )
        raise _PlanningRefusal(
            "lineage_scope_ambiguous",
            "A lineage filter must say exactly or including descendants.",
            _build_audit(state, unresolved_keys=frozenset({key})),
        )
    reference = raw_value[: suffix_match.start()].rstrip()
    if not reference:
        key = state.add_condition(
            start=raw_start,
            end=raw_start + len(raw_value),
            kind="entity",
            target=None,
        )
        raise _PlanningRefusal(
            "entity_unresolved",
            "A lineage entity is required.",
            _build_audit(state, unresolved_keys=frozenset({key})),
        )
    scope_start = raw_start + suffix_match.start("scope")
    state.add_condition(
        start=scope_start,
        end=raw_start + suffix_match.end("scope"),
        kind="scope",
        target=f"scope.filter:{filter_type}.include_descendants",
    )
    return (
        reference,
        raw_start + len(reference),
        _normalize_syntax(suffix_match.group("scope")) == "including descendants",
    )


def _parse_filter_tail(
    *,
    question: str,
    state: _ParseState,
    tail: str,
    tail_start: int,
) -> tuple[_Mention, ...]:
    matches = tuple(_CLAUSE_MARKER_RE.finditer(tail))
    if not matches or matches[0].start() != 0:
        raise _refuse_unknown(question)

    mentions: list[_Mention] = []
    seen: set[FilterType] = set()
    for index, match in enumerate(matches):
        if index > 0:
            separator_start, separator_end = match.span("separator")
            separator_text = match.group("separator")
            and_offset = separator_text.casefold().index("and")
            state.add_condition(
                start=tail_start + separator_start + and_offset,
                end=tail_start + separator_start + and_offset + 3,
                kind="logical_operator",
                target=f"scope.and:{index}",
            )
        value_end = matches[index + 1].start() if index + 1 < len(matches) else len(tail)
        raw_value = tail[match.end() : value_end].rstrip()
        raw_start = tail_start + match.end()
        marker_start, _ = match.span("marker")
        marker_text = match.group("marker")
        marker = _normalize_syntax(marker_text)
        entity_start = tail_start + marker_start
        if not raw_value:
            key = state.add_condition(
                start=entity_start,
                end=tail_start + value_end,
                kind="entity",
                target=None,
            )
            raise _PlanningRefusal(
                "entity_unresolved",
                "An entity value is required after the filter phrase.",
                _build_audit(state, unresolved_keys=frozenset({key})),
            )

        if marker == "in assembly":
            if " " in raw_value:
                key = state.add_condition(
                    start=entity_start,
                    end=tail_start + value_end,
                    kind="entity",
                    target=None,
                )
                raise _PlanningRefusal(
                    "unsupported_question",
                    "An assembly filter accepts one exact identifier.",
                    _build_audit(state, unresolved_keys=frozenset({key})),
                )
            condition_key = state.add_condition(
                start=entity_start,
                end=tail_start + value_end,
                kind="entity",
                target="scope.filter:assembly",
            )
            mention = _Mention(
                filter_type="assembly",
                original_input=raw_value,
                condition_key=condition_key,
            )
        else:
            if marker == "with viral lineage":
                key = state.add_condition(
                    start=entity_start,
                    end=tail_start + value_end,
                    kind="entity",
                    target=None,
                )
                raise _PlanningRefusal(
                    "lineage_role_ambiguous",
                    "A viral lineage must be qualified as formal or study.",
                    _build_audit(state, unresolved_keys=frozenset({key})),
                )
            if "source lineage" in marker:
                filter_type: Literal["source_lineage", "viral_lineage"] = "source_lineage"
                entity_kind: Literal["source_lineage", "viral_lineage"] = "source_lineage"
                role: LineageRole = "assembly_source_taxonomy"
            else:
                filter_type = "viral_lineage"
                entity_kind = "viral_lineage"
                role = (
                    "formal_viral_taxonomy"
                    if marker == "with formal viral lineage"
                    else "study_viral_lineage"
                )
            role_phrase = (
                "source lineage"
                if filter_type == "source_lineage"
                else (
                    "formal viral lineage"
                    if role == "formal_viral_taxonomy"
                    else "study viral lineage"
                )
            )
            role_match = re.search(role_phrase.replace(" ", r" +"), marker_text, re.I)
            if role_match is not None:
                entity_start = tail_start + marker_start + role_match.start()
            if marker == "assigned exactly to source lineage":
                exact_match = re.search(r"\bexactly\b", marker_text, re.I)
                assert exact_match is not None
                state.add_condition(
                    start=tail_start + marker_start + exact_match.start(),
                    end=tail_start + marker_start + exact_match.end(),
                    kind="scope",
                    target="scope.filter:source_lineage.include_descendants",
                )
            reference, reference_end, include_descendants = _lineage_ref_and_scope(
                state=state,
                raw_value=raw_value,
                raw_start=raw_start,
                marker_normalized=marker,
                filter_type=filter_type,
            )
            condition_key = state.add_condition(
                start=entity_start,
                end=reference_end,
                kind="entity",
                target=f"scope.filter:{filter_type}.term",
            )
            mention = _parse_lineage_reference(
                reference,
                state=state,
                entity_kind=entity_kind,
                role=role,
                filter_type=filter_type,
                condition_key=condition_key,
                include_descendants=include_descendants,
            )

        if mention.filter_type in seen:
            state.conditions[mention.condition_key].mapped_target = None
            raise _PlanningRefusal(
                "unsupported_question",
                "Each filter type may be mentioned at most once.",
                _build_audit(
                    state,
                    unresolved_keys=frozenset({mention.condition_key}),
                ),
            )
        seen.add(mention.filter_type)
        mentions.append(mention)
    return tuple(mentions)


def _parse_question(question: str) -> _ParsedQuery:
    body, offset = _body_and_offset(question)
    _preflight_unsupported(question, body, offset)
    state = _ParseState(question)

    detail = _DETAIL_RE.fullmatch(body)
    if detail is not None:
        kind = detail.group("kind").casefold()
        intent: Intent = "assembly_detail" if kind == "assembly" else "locus_detail"
        intent_start, _ = _span(detail, "intent", offset)
        _, kind_end = _span(detail, "kind", offset)
        state.add_condition(
            start=intent_start,
            end=kind_end,
            kind="intent",
            target=f"intent:{intent}",
        )
        entity_start, entity_end = _span(detail, "entity", offset)
        condition_key = state.add_condition(
            start=entity_start,
            end=entity_end,
            kind="entity",
            target=f"scope.filter:{kind}",
        )
        return _ParsedQuery(
            intent=intent,
            metric_key=None,
            entire_release=False,
            mentions=(
                _Mention(
                    filter_type="assembly" if kind == "assembly" else "locus",
                    original_input=detail.group("entity"),
                    condition_key=condition_key,
                ),
            ),
            state=state,
        )

    listed = _LIST_RE.fullmatch(body)
    if listed is not None:
        object_normalized = _normalize_syntax(listed.group("object"))
        all_requested = object_normalized.startswith("all ")
        object_name = object_normalized.removeprefix("all ")
        intent = _LIST_INTENTS[object_name]
        intent_start, _ = _span(listed, "intent", offset)
        _, object_end = _span(listed, "object", offset)
        state.add_condition(
            start=intent_start,
            end=object_end,
            kind="intent",
            target=f"intent:{intent}",
        )
        tail_start, tail_end = _span(listed, "tail", offset)
        tail = listed.group("tail")
        normalized_tail = _normalize_syntax(tail)
        if normalized_tail in {"in this release", "represented in this release"}:
            scope_key = state.add_condition(
                start=tail_start,
                end=tail_end,
                kind="scope",
                target="scope:entire_release",
            )
            explicit = all_requested or normalized_tail.startswith("represented ")
            if not explicit:
                state.conditions[scope_key].mapped_target = None
                raise _PlanningRefusal(
                    "full_release_scope_not_explicit",
                    "A full-release list must explicitly say all or represented.",
                    _build_audit(state, unresolved_keys=frozenset({scope_key})),
                )
            return _ParsedQuery(intent, None, True, (), state)
        mentions = _parse_filter_tail(
            question=question,
            state=state,
            tail=tail,
            tail_start=tail_start,
        )
        return _ParsedQuery(intent, None, False, mentions, state)

    aggregate = _AGGREGATE_RE.fullmatch(body)
    if aggregate is not None:
        intent_start, intent_end = _span(aggregate, "intent", offset)
        state.add_condition(
            start=intent_start,
            end=intent_end,
            kind="intent",
            target="intent:aggregate",
        )
        metric_start, metric_end = _span(aggregate, "metric", offset)
        metric_key = _METRICS[_normalize_syntax(aggregate.group("metric"))]
        state.add_condition(
            start=metric_start,
            end=metric_end,
            kind="metric",
            target=f"metric_key:{metric_key}",
        )
        tail_start, tail_end = _span(aggregate, "tail", offset)
        tail = aggregate.group("tail")
        if _normalize_syntax(tail) == "in this release":
            state.add_condition(
                start=tail_start,
                end=tail_end,
                kind="scope",
                target="scope:entire_release",
            )
            return _ParsedQuery("aggregate", metric_key, True, (), state)
        mentions = _parse_filter_tail(
            question=question,
            state=state,
            tail=tail,
            tail_start=tail_start,
        )
        return _ParsedQuery("aggregate", metric_key, False, mentions, state)

    if re.search(r"\blineage\b", body, re.I) and not re.search(
        r"\b(?:source|formal +viral|study +viral) +lineage\b", body, re.I
    ):
        match = re.search(r"\b(?:viral +)?lineage\b", body, re.I)
        assert match is not None
        key = state.add_condition(
            start=match.start() + offset,
            end=match.end() + offset,
            kind="entity",
            target=None,
        )
        raise _PlanningRefusal(
            "lineage_role_ambiguous",
            "A lineage mention must state source, formal viral, or study viral lineage.",
            _build_audit(state, unresolved_keys=frozenset({key})),
        )
    raise _refuse_unknown(question)


def _deduplicate_suggestions(
    suggestions: list[EntitySuggestion],
) -> tuple[EntitySuggestion, ...]:
    unique = {(item.entity_kind, item.stable_key): item for item in suggestions}
    return tuple(sorted(unique.values(), key=lambda item: (item.entity_kind, item.stable_key))[:5])


def _resolve_mentions(
    parsed: _ParsedQuery,
    resolver: ReleaseScopedEntityResolver,
) -> tuple[tuple[QueryFilter, ...], tuple[ResolvedEntity, ...]]:
    filters: list[QueryFilter] = []
    resolved: list[ResolvedEntity] = []
    failures: list[tuple[_Mention, ResolutionFailure]] = []
    suggestions: list[EntitySuggestion] = []

    for mention in parsed.mentions:
        try:
            if mention.filter_type == "assembly":
                entity = resolver.resolve_assembly(mention.original_input)
                query_filter: QueryFilter = AssemblyFilter(
                    filter_type="assembly",
                    assembly_key=entity.stable_key,
                )
            elif mention.filter_type == "locus":
                entity = resolver.resolve_locus(mention.original_input)
                query_filter = LocusFilter(filter_type="locus", locus_key=entity.stable_key)
            else:
                assert mention.lineage_role is not None
                reference = LineageReference(
                    original_input=mention.original_input,
                    entity_kind=mention.filter_type,
                    role=mention.lineage_role,
                    term_key=mention.lineage_term_key,
                    snapshot_key=mention.lineage_snapshot_key,
                    name=mention.lineage_name,
                )
                entity = resolver.resolve_lineage(reference)
                assert entity.snapshot_key is not None
                assert entity.role is not None
                assert mention.include_descendants is not None
                if mention.filter_type == "source_lineage":
                    query_filter = SourceLineageFilter(
                        filter_type="source_lineage",
                        snapshot_key=entity.snapshot_key,
                        term_key=entity.stable_key,
                        role="assembly_source_taxonomy",
                        include_descendants=mention.include_descendants,
                    )
                else:
                    if entity.role == "assembly_source_taxonomy":
                        raise RuntimeError("viral resolution returned a source lineage role")
                    query_filter = ViralLineageFilter(
                        filter_type="viral_lineage",
                        snapshot_key=entity.snapshot_key,
                        term_key=entity.stable_key,
                        role=entity.role,
                        include_descendants=mention.include_descendants,
                    )
            filters.append(query_filter)
            resolved.append(entity)
        except ResolutionFailure as failure:
            failures.append((mention, failure))
            suggestions.extend(failure.suggestions)

    if failures:
        unresolved = frozenset(mention.condition_key for mention, _ in failures)
        first_failure = failures[0][1]
        raise _PlanningRefusal(
            first_failure.code,
            first_failure.message,
            _build_audit(parsed.state, unresolved_keys=unresolved),
            suggestions=_deduplicate_suggestions(suggestions),
            resolved_entities=tuple(
                sorted(resolved, key=lambda item: (item.entity_kind, item.stable_key))
            ),
        )
    return (
        tuple(filters),
        tuple(sorted(resolved, key=lambda item: (item.entity_kind, item.stable_key))),
    )


def _make_plan(
    *,
    request: StructuredQueryRequest,
    parsed: _ParsedQuery,
    filters: tuple[QueryFilter, ...],
) -> StructuredPlan:
    scope = (
        EntireReleaseScope(scope_type="entire_release", explicitly_requested=True)
        if parsed.entire_release
        else FilteredScope(scope_type="filtered", filters=filters)
    )
    if parsed.intent == "assembly_detail":
        return AssemblyDetailPlan(
            plan_version=PLAN_VERSION,
            route="structured",
            release_key=request.release_key,
            original_question=request.question,
            scope=scope,
            intent="assembly_detail",
        )
    if parsed.intent == "locus_detail":
        return LocusDetailPlan(
            plan_version=PLAN_VERSION,
            route="structured",
            release_key=request.release_key,
            original_question=request.question,
            scope=scope,
            intent="locus_detail",
        )
    if parsed.intent == "aggregate":
        assert parsed.metric_key is not None
        return AggregatePlan(
            plan_version=PLAN_VERSION,
            route="structured",
            release_key=request.release_key,
            original_question=request.question,
            scope=scope,
            intent="aggregate",
            metric_key=parsed.metric_key,
        )
    page = request.page or PageSpec()
    if parsed.intent == "list_loci":
        return ListLociPlan(
            plan_version=PLAN_VERSION,
            route="structured",
            release_key=request.release_key,
            original_question=request.question,
            scope=scope,
            intent="list_loci",
            page=page,
        )
    if parsed.intent == "list_assemblies":
        return ListAssembliesPlan(
            plan_version=PLAN_VERSION,
            route="structured",
            release_key=request.release_key,
            original_question=request.question,
            scope=scope,
            intent="list_assemblies",
            page=page,
        )
    return ListSourceTaxaPlan(
        plan_version=PLAN_VERSION,
        route="structured",
        release_key=request.release_key,
        original_question=request.question,
        scope=scope,
        intent="list_source_taxa",
        page=page,
    )


class ControlledEnglishPlanner:
    """Deterministic planner with no fact-retrieval or release-bypass capability."""

    def plan(
        self,
        request: StructuredQueryRequest,
        resolver: ReleaseScopedEntityResolver,
    ) -> PlanSuccess | ErrorResponse:
        if resolver.release_key != request.release_key:
            return ErrorResponse(
                error=StructuredError(
                    code="release_dependencies_incomplete",
                    message="The resolver is not scoped to the requested release.",
                )
            )
        try:
            parsed = _parse_question(request.question)
            if request.page is not None and parsed.intent in {
                "assembly_detail",
                "locus_detail",
                "aggregate",
            }:
                raise _PlanningRefusal(
                    "pagination_not_allowed",
                    "Pagination is allowed only for list intents.",
                    _build_audit(parsed.state),
                )
            incompatible = tuple(
                mention
                for mention in parsed.mentions
                if mention.filter_type not in _ALLOWED_FILTERS[parsed.intent]
            )
            if incompatible:
                unresolved = frozenset(item.condition_key for item in incompatible)
                raise _PlanningRefusal(
                    "intent_filter_incompatible",
                    "One or more filters are incompatible with the requested intent.",
                    _build_audit(parsed.state, unresolved_keys=unresolved),
                )
            filters, resolved = _resolve_mentions(parsed, resolver)
            plan = _make_plan(request=request, parsed=parsed, filters=filters)
            return PlanSuccess(
                query_plan=plan,
                planning_audit=_build_audit(parsed.state),
                resolved_entities=resolved,
            )
        except _PlanningRefusal as failure:
            return ErrorResponse(
                planning_audit=failure.audit,
                resolved_entities=failure.resolved_entities,
                error=StructuredError(
                    code=failure.code,
                    message=failure.message,
                    suggestions=failure.suggestions,
                ),
            )


__all__ = ["ControlledEnglishPlanner", "StructuredQueryRequest"]
