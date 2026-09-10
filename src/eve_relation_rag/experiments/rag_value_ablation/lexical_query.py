"""Bounded lexical plans derived only from question text and visible entity labels.

The groups are plain text for ``plainto_tsquery``. They are not websearch/tsquery
expressions, model prompts, Gold annotations, or inferred biological aliases.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from eve_relation_rag.literature.hashing import canonical_json_sha256

LEXICAL_QUERY_POLICY_KEY = "rag-value-lexical-query-v1"
MAX_QUESTION_CHARACTERS = 8192
MAX_ENTITY_LABELS = 32
MAX_ENTITY_LABEL_CHARACTERS = 256
MAX_QUERY_GROUPS = 8
MAX_GROUP_TERMS = 8
MAX_TOPIC_TERMS = 6

type GroupKind = Literal["entity", "accession", "doi", "topic"]
type PlanStatus = Literal["ready", "no_usable_terms", "rejected"]

_HYPHENS = r"\-\u2010\u2011\u2012\u2013\u2014\u2212"
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9]*")
_ACCESSION = re.compile(
    r"(?<![A-Za-z0-9_])(?:GC[AF]_\d{6,}|[A-Z]{1,6}_?\d{5,})(?:\.\d+)?"
    r"(?![A-Za-z0-9_]|\.\d)", re.IGNORECASE,
)
_DOI = re.compile(r"(?<![A-Za-z0-9])10\.\d{4,9}/[^\s<>\"，。；（）]+", re.IGNORECASE)
_BINOMIAL = re.compile(r"(?<!\w)[A-Z][a-z]{2,}\s+[a-z][a-z-]{2,}(?!\w)")
_ABBREVIATED_BINOMIAL = re.compile(r"(?<!\w)[A-Z]\.\s+[a-z][a-z-]{2,}(?!\w)")
_TAXON = re.compile(
    r"(?<!\w)[A-Za-z]+(?:idae|inae|aceae|ales|viricetes|viricota|virinae|"
    r"phyceae|mycetes|phyta|zoa|karyota)(?!\w)", re.IGNORECASE,
)
_ACRONYM = re.compile(
    rf"(?<![A-Za-z0-9_])[A-Z][A-Z0-9]{{1,}}(?:[{_HYPHENS}][A-Za-z0-9]+)*"
    r"(?![A-Za-z0-9_])"
)
_SMALL_GENE = re.compile(r"(?<![A-Za-z0-9_])[A-Za-z]{1,6}\d+[A-Za-z0-9]*(?![A-Za-z0-9_])")

# These describe the question's requested presentation or snapshot, not its entities.
_INSTRUCTION_WORDS = frozenset("""
a an the and or not no which what when where why how who whose is are was were be been
being do does did has have had can could should would will may might for from to of in on
at by with within without across into between among each every all any this that these
those their its it they them we you as than then because while though even both either
neither also only same other another some such per through about over under versus vs
show give tell list report reports reported reporting record records recorded recording
describe explain present assign classify execute run merge treat compare determine identify
identifies identified name names named find retrieve display displays displaying use using
used include includes included including retain retained retaining preserve preserving
preserved group groups grouped grouping source sources evidence evidences publication
publications published author authors claim claims confidence uncertainty explicitly
directly relevant permitted permit selected release releases corpus corpora snapshot
snapshots frozen taxdump ncbi taxid taxonomy version versions versioned unversioned
unapproved approved documented documentation normal normalized normalization mapping
mappings available unmapped scope scopes output outputs result results format formats
require required requirement requirements exact tuple tuples table tables row rows
first page pages complete completeness incomplete truncated truncation represented
represents occurring occur occurs occurring occurence occurrences supported supports
support supporting align aligned aligns relation contract dimension dimensional
et al doi pmid pmcid sql json xml html blast hmmer true false yes failed
""".split())
_GENERIC_ACRONYMS = frozenset(
    {"eve", "geve", "vle", "vr", "hcvr", "dna", "rna"} | _INSTRUCTION_WORDS
)
_NON_TAXON_WORDS = frozenset({"male", "males", "female", "females", "sales", "scales"})
_ENTITY_CATEGORY_WORDS = frozenset({
    "gene", "genes", "virus", "viruses", "locus", "loci", "region", "regions",
    "taxon", "taxa", "lineage", "lineages", "protein", "proteins", "element", "elements",
})
_METADATA = re.compile(
    r"\b(?:taxid|tax_id|ncbi\s+taxid|version)\s*[:=]?\s*\d+(?:\.\d+)*\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class SourceSpan:
    start: int
    end: int
    text: str

    def to_dict(self) -> dict[str, object]:
        return {"start": self.start, "end": self.end, "text": self.text}


@dataclass(frozen=True, slots=True)
class LexicalQueryGroup:
    group_key: str
    kind: GroupKind
    terms: tuple[str, ...]
    query_text: str
    source_spans: tuple[SourceSpan, ...]
    identifier: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "group_key": self.group_key, "kind": self.kind, "terms": list(self.terms),
            "query_text": self.query_text,
            "source_spans": [span.to_dict() for span in self.source_spans],
            "identifier": self.identifier,
        }


@dataclass(frozen=True, slots=True)
class LexicalQueryPlan:
    policy_key: str
    question_text_sha256: str
    status: PlanStatus
    groups: tuple[LexicalQueryGroup, ...]
    warnings: tuple[str, ...]
    plan_sha: str

    @property
    def plan_sha256(self) -> str:
        return self.plan_sha

    def to_dict(self) -> dict[str, object]:
        return {
            "policy_key": self.policy_key, "question_text_sha256": self.question_text_sha256,
            "status": self.status, "groups": [group.to_dict() for group in self.groups],
            "warnings": list(self.warnings), "plan_sha": self.plan_sha,
        }


@dataclass(frozen=True, slots=True)
class _Candidate:
    kind: GroupKind
    span: SourceSpan
    terms: tuple[str, ...]
    priority: int
    identifier: str | None = None


def _finish(
    question_sha: str, status: PlanStatus, groups: tuple[LexicalQueryGroup, ...] = (),
    warnings: Sequence[str] = (),
) -> LexicalQueryPlan:
    warning_tuple = tuple(sorted(set(warnings)))
    payload = {
        "policy_key": LEXICAL_QUERY_POLICY_KEY, "question_text_sha256": question_sha,
        "status": status, "groups": [group.to_dict() for group in groups],
        "warnings": list(warning_tuple),
    }
    return LexicalQueryPlan(
        LEXICAL_QUERY_POLICY_KEY, question_sha, status, groups, warning_tuple,
        canonical_json_sha256(payload),
    )


def _terms(text: str, *, remove_instructions: bool = False) -> tuple[str, ...]:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    normalized = re.sub(rf"[{_HYPHENS}]", " ", normalized)
    terms = {match.group() for match in _WORD.finditer(normalized)}
    if remove_instructions:
        terms -= _INSTRUCTION_WORDS
        terms = {term for term in terms if not re.fullmatch(r"v\d+", term)}
    return tuple(sorted(terms))


def _overlap(left: SourceSpan, right: SourceSpan) -> bool:
    return left.start < right.end and right.start < left.end


def _candidates(text: str, *, offset: int = 0) -> list[_Candidate]:
    found: list[_Candidate] = []
    patterns = (
        (_DOI, "doi", 0), (_ACCESSION, "accession", 0),
        (_ABBREVIATED_BINOMIAL, "abbreviated", 1), (_BINOMIAL, "binomial", 1),
        (_TAXON, "taxon", 2), (_ACRONYM, "acronym", 3), (_SMALL_GENE, "gene", 3),
    )
    for pattern, category, priority in patterns:
        for match in pattern.finditer(text):
            raw = match.group()
            start, end = match.span()
            if category == "doi":
                raw = raw.rstrip(".,;:!?)]}")
                end = start + len(raw)
            span = SourceSpan(start + offset, end + offset, raw)
            identifier: str | None = None
            kind: GroupKind = "entity"
            terms: tuple[str, ...]
            if category in {"doi", "accession"}:
                kind = "doi" if category == "doi" else "accession"
                identifier = raw.casefold() if kind == "doi" else raw.upper()
                terms = (identifier,)
            else:
                terms = _terms(raw)
                if category == "abbreviated":
                    # The original genus initial remains in provenance; never expand it.
                    terms = tuple(term for term in terms if len(term) > 1)
                if not terms or any(term in _INSTRUCTION_WORDS for term in terms):
                    continue
                if category in {"binomial", "abbreviated"} and (
                    set(terms) & _ENTITY_CATEGORY_WORDS
                ):
                    continue
                if category in {"acronym", "gene"} and (
                    set(terms) <= (_GENERIC_ACRONYMS | _ENTITY_CATEGORY_WORDS)
                    or re.fullmatch(r"v\d+", raw, re.I)
                ):
                    continue
                if category == "taxon" and raw.casefold() in _NON_TAXON_WORDS:
                    continue
            found.append(_Candidate(kind, span, terms, priority, identifier))
    # Identifiers and full biological names take precedence over overlapping abbreviations.
    selected: list[_Candidate] = []
    for candidate in sorted(found, key=lambda c: (
        c.priority, -(c.span.end - c.span.start), c.span.start, c.kind, c.terms,
    )):
        if not any(_overlap(candidate.span, prior.span) for prior in selected):
            selected.append(candidate)
    return sorted(selected, key=lambda c: (c.span.start, c.span.end, c.kind, c.terms))


def _label_spans(question: str, label: str) -> tuple[SourceSpan, ...]:
    pattern = re.escape(label)
    pattern = re.sub(r"(?:\\ )+", r"\\s+", pattern)
    return tuple(SourceSpan(m.start(), m.end(), m.group()) for m in re.finditer(
        rf"(?<!\w){pattern}(?!\w)", question, flags=re.IGNORECASE,
    ))


def _group_candidates(candidates: Sequence[_Candidate]) -> tuple[LexicalQueryGroup, ...]:
    merged: dict[tuple[GroupKind, tuple[str, ...], str | None], set[SourceSpan]] = {}
    for candidate in candidates:
        merged.setdefault((candidate.kind, candidate.terms, candidate.identifier), set()).add(
            candidate.span)
    groups = []
    for (kind, terms, identifier), spans in sorted(merged.items(), key=lambda item: item[0]):
        source_spans = tuple(sorted(spans, key=lambda span: (span.start, span.end, span.text)))
        identity = {"policy_key": LEXICAL_QUERY_POLICY_KEY, "kind": kind,
                    "terms": list(terms), "identifier": identifier}
        groups.append(LexicalQueryGroup(
            "lexical-group:sha256:" + canonical_json_sha256(identity), kind, terms,
            " ".join(terms), source_spans, identifier,
        ))
    return tuple(groups)


def plan_lexical_query(
    question_text: str, *, entity_labels: Sequence[str] = (),
) -> LexicalQueryPlan:
    """Build independent, conjunctive search groups without reading any other evidence.

    Labels must occur in the question. Bounds apply to the complete proposed plan:
    rejection returns no groups, never a silently shortened query or entity list.
    """
    if not isinstance(question_text, str):
        raise TypeError("question_text must be a string")
    question_sha = hashlib.sha256(question_text.encode("utf-8")).hexdigest()
    if not question_text.strip():
        return _finish(question_sha, "rejected", warnings=("empty_question",))
    if len(question_text) > MAX_QUESTION_CHARACTERS:
        return _finish(question_sha, "rejected", warnings=("question_too_long",))
    if any(ord(c) < 32 and c not in "\t\n\r" for c in question_text):
        return _finish(question_sha, "rejected", warnings=("unsupported_control_character",))
    if isinstance(entity_labels, (str, bytes)) or not isinstance(entity_labels, Sequence):
        return _finish(question_sha, "rejected", warnings=("invalid_entity_labels",))
    if len(entity_labels) > MAX_ENTITY_LABELS:
        return _finish(question_sha, "rejected", warnings=("too_many_entity_labels",))
    labels: list[tuple[str, tuple[SourceSpan, ...]]] = []
    for label in entity_labels:
        if not isinstance(label, str) or not label.strip():
            return _finish(question_sha, "rejected", warnings=("invalid_entity_label",))
        if len(label) > MAX_ENTITY_LABEL_CHARACTERS:
            return _finish(question_sha, "rejected", warnings=("entity_label_too_long",))
        spans = _label_spans(question_text, label.strip())
        if not spans:
            return _finish(question_sha, "rejected", warnings=("entity_label_not_in_question",))
        labels.append((label.strip(), spans))
    candidates = _candidates(question_text)
    warnings = []
    for _label, spans in sorted(labels):
        for span in spans:
            recognized = _candidates(span.text, offset=span.start)
            if recognized:
                candidates.extend(recognized)
                continue
            terms = _terms(_METADATA.sub(" ", span.text), remove_instructions=True)
            if not terms or set(terms) <= _GENERIC_ACRONYMS:
                warnings.append("entity_label_has_no_specific_entity")
                continue
            candidates.append(_Candidate("entity", span, terms, 4))
    if not candidates:
        terms = _terms(_METADATA.sub(" ", question_text), remove_instructions=True)
        if not terms:
            return _finish(question_sha, "no_usable_terms", warnings=(*warnings, "no_usable_terms"))
        if len(terms) > MAX_TOPIC_TERMS:
            return _finish(question_sha, "rejected", warnings=(*warnings, "too_many_topic_terms"))
        candidates.append(_Candidate(
            "topic", SourceSpan(0, len(question_text), question_text), terms, 5))
        warnings.append("topic_keyword_fallback")
    if any(len(candidate.terms) > MAX_GROUP_TERMS for candidate in candidates):
        return _finish(question_sha, "rejected", warnings=(*warnings, "too_many_group_terms"))
    groups = _group_candidates(candidates)
    if len(groups) > MAX_QUERY_GROUPS:
        return _finish(question_sha, "rejected", warnings=(*warnings, "too_many_query_groups"))
    return _finish(question_sha, "ready", groups, warnings)
