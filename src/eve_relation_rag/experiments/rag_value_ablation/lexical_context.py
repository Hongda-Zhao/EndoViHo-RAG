"""Question-local retrieval objects and soft context, without inferred aliases.

Context terms are ranking hints only. Group keys retain the original lexical
plan's entity, accession and DOI identities; this module does not rewrite them.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

from eve_relation_rag.experiments.rag_value_ablation.lexical_query import (
    _INSTRUCTION_WORDS,
    _METADATA,
    LEXICAL_QUERY_POLICY_KEY,
    MAX_QUERY_GROUPS,
    MAX_QUESTION_CHARACTERS,
    LexicalQueryGroup,
    LexicalQueryPlan,
    SourceSpan,
)
from eve_relation_rag.literature.hashing import canonical_json_sha256

LEXICAL_CONTEXT_POLICY_KEY = "rag-value-lexical-context-v1"
MAX_RETRIEVAL_OBJECTS = 8
MAX_CONTEXT_TERMS = 64

type ContextStatus = Literal["ready", "no_objects", "rejected"]

_TERM = re.compile(r"[A-Za-z0-9]+(?:[._][A-Za-z0-9]+)*")
_SEPARATOR = re.compile(r"[,;，；、]|(?<!\w)(?:and|or)(?!\w)", re.IGNORECASE)
_OPEN_BRACKETS = {"(": ")", "[": "]", "{": "}", "（": "）", "【": "】"}
_CLOSE_BRACKETS = frozenset(_OPEN_BRACKETS.values())


@dataclass(frozen=True, slots=True)
class LexicalRetrievalObject:
    object_key: str
    group_keys: tuple[str, ...]
    context_terms: tuple[str, ...]
    source_spans: tuple[SourceSpan, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "object_key": self.object_key,
            "group_keys": list(self.group_keys),
            "context_terms": list(self.context_terms),
            "source_spans": [span.to_dict() for span in self.source_spans],
        }


@dataclass(frozen=True, slots=True)
class LexicalContextPlan:
    policy_key: str
    question_text_sha256: str
    lexical_plan_sha: str
    status: ContextStatus
    objects: tuple[LexicalRetrievalObject, ...]
    warnings: tuple[str, ...]
    context_sha: str

    def to_dict(self) -> dict[str, object]:
        return {
            "policy_key": self.policy_key,
            "question_text_sha256": self.question_text_sha256,
            "lexical_plan_sha": self.lexical_plan_sha,
            "status": self.status,
            "objects": [obj.to_dict() for obj in self.objects],
            "warnings": list(self.warnings),
            "context_sha": self.context_sha,
        }


def _finish(
    question_sha: str,
    lexical_plan_sha: str,
    status: ContextStatus,
    objects: tuple[LexicalRetrievalObject, ...] = (),
    warnings: tuple[str, ...] = (),
) -> LexicalContextPlan:
    warning_tuple = tuple(sorted(set(warnings)))
    payload = {
        "policy_key": LEXICAL_CONTEXT_POLICY_KEY,
        "question_text_sha256": question_sha,
        "lexical_plan_sha": lexical_plan_sha,
        "status": status,
        "objects": [obj.to_dict() for obj in objects],
        "warnings": list(warning_tuple),
    }
    return LexicalContextPlan(
        LEXICAL_CONTEXT_POLICY_KEY, question_sha, lexical_plan_sha, status, objects,
        warning_tuple, canonical_json_sha256(payload),
    )


def _terms(text: str) -> set[str]:
    # Decimal measurements and cytogenetic labels stay intact (33.3, 7q21.2).
    return set(_TERM.findall(unicodedata.normalize("NFKC", text).casefold()))


def _context_terms(text: str, groups: tuple[LexicalQueryGroup, ...]) -> tuple[str, ...]:
    terms = _terms(_METADATA.sub(" ", text)) - _INSTRUCTION_WORDS
    terms = {term for term in terms if not re.fullmatch(r"v\d+", term)}
    anchors = set().union(*(_terms(term) for group in groups for term in group.terms))
    return tuple(sorted(terms - anchors))


def _trim_span(question: str, start: int, end: int) -> SourceSpan | None:
    while start < end and question[start].isspace():
        start += 1
    while start < end and question[end - 1].isspace():
        end -= 1
    return SourceSpan(start, end, question[start:end]) if start < end else None


def _clauses(
    question: str, protected_spans: tuple[SourceSpan, ...],
) -> tuple[tuple[SourceSpan, ...], bool]:
    """Split top-level conjunctions and punctuation, preserving bracketed metadata."""
    clauses: list[SourceSpan] = []
    stack: list[str] = []
    start = index = 0
    while index < len(question):
        char = question[index]
        if char in _OPEN_BRACKETS:
            stack.append(_OPEN_BRACKETS[char])
        elif char in _CLOSE_BRACKETS:
            if not stack or stack.pop() != char:
                return (), False
        elif not stack:
            separator = _SEPARATOR.match(question, index)
            if separator and not any(
                span.start <= index < span.end for span in protected_spans
            ):
                clause = _trim_span(question, start, index)
                if clause is not None:
                    clauses.append(clause)
                index = separator.end()
                start = index
                continue
        index += 1
    if stack:
        return (), False
    clause = _trim_span(question, start, len(question))
    if clause is not None:
        clauses.append(clause)
    return tuple(clauses), True


def build_lexical_context(question_text: str, plan: LexicalQueryPlan) -> LexicalContextPlan:
    """Associate nearby anchors and derive optional context from their question clauses.

    Only clauses containing entity/identifier groups become objects. A single
    topic fallback group also forms an object, retaining its original search terms.
    One object receives whole-question context; multiple objects keep local context.
    Bounds reject the whole context plan, never silently discard objects or terms.
    """
    if not isinstance(question_text, str) or not isinstance(plan, LexicalQueryPlan):
        raise TypeError("question_text and plan must be a string and LexicalQueryPlan")
    question_sha = hashlib.sha256(question_text.encode("utf-8")).hexdigest()

    def reject(warning: str) -> LexicalContextPlan:
        return _finish(question_sha, plan.plan_sha, "rejected", warnings=(warning,))

    if not question_text.strip():
        return reject("empty_question")
    if len(question_text) > MAX_QUESTION_CHARACTERS:
        return reject("question_too_long")
    if any(ord(char) < 32 and char not in "\t\n\r" for char in question_text):
        return reject("unsupported_control_character")
    if question_sha != plan.question_text_sha256:
        return reject("question_sha_mismatch")
    if plan.policy_key != LEXICAL_QUERY_POLICY_KEY:
        return reject("unsupported_lexical_query_policy")
    serialized = plan.to_dict()
    serialized.pop("plan_sha")
    if canonical_json_sha256(serialized) != plan.plan_sha:
        return reject("lexical_plan_sha_mismatch")
    if plan.status != "ready":
        return reject("lexical_plan_not_ready")
    if len(plan.groups) > MAX_QUERY_GROUPS:
        return reject("too_many_query_groups")
    if len({group.group_key for group in plan.groups}) != len(plan.groups):
        return reject("duplicate_group_keys")
    for group in plan.groups:
        if not group.source_spans:
            return reject("missing_group_source_spans")
        for span in group.source_spans:
            if not (
                0 <= span.start < span.end <= len(question_text)
                and question_text[span.start:span.end] == span.text
            ):
                return reject("invalid_group_source_span")
    groups = tuple(group for group in plan.groups if group.kind != "topic")
    clauses: tuple[SourceSpan, ...]
    if not groups:
        if not plan.groups:
            return _finish(question_sha, plan.plan_sha, "no_objects", warnings=("no_query_groups",))
        if len(plan.groups) != 1 or plan.groups[0].kind != "topic":
            return reject("invalid_topic_fallback_groups")
        groups = plan.groups
        clauses = (SourceSpan(0, len(question_text), question_text),)
        balanced = True
    else:
        # Do not interpret a comma or /and/ inside an accession/DOI as a separator.
        protected = tuple(
            span for group in groups if group.identifier for span in group.source_spans
        )
        clauses, balanced = _clauses(question_text, protected)
    if not balanced:
        return reject("unbalanced_question_brackets")
    associations: list[tuple[SourceSpan, tuple[LexicalQueryGroup, ...]]] = []
    assigned_group_keys: set[str] = set()
    for clause in clauses:
        local_groups = tuple(sorted((
            group for group in groups if any(
                clause.start <= span.start and span.end <= clause.end
                for span in group.source_spans
            )
        ), key=lambda group: group.group_key))
        if local_groups:
            associations.append((clause, local_groups))
            assigned_group_keys.update(group.group_key for group in local_groups)
    if assigned_group_keys != {group.group_key for group in groups}:
        return reject("group_span_crosses_clause_boundary")
    if len(associations) > MAX_RETRIEVAL_OBJECTS:
        return reject("too_many_retrieval_objects")
    if len(associations) == 1:
        associations = [(SourceSpan(0, len(question_text), question_text), groups)]
    objects: list[LexicalRetrievalObject] = []
    for span, local_groups in associations:
        terms = _context_terms(span.text, local_groups)
        if len(terms) > MAX_CONTEXT_TERMS:
            return reject("too_many_context_terms")
        keys = tuple(sorted(group.group_key for group in local_groups))
        identity = {
            "policy_key": LEXICAL_CONTEXT_POLICY_KEY,
            "question_text_sha256": question_sha,
            "lexical_plan_sha": plan.plan_sha,
            "group_keys": list(keys),
            "context_terms": list(terms),
            "source_spans": [span.to_dict()],
        }
        objects.append(LexicalRetrievalObject(
            "lexical-object:sha256:" + canonical_json_sha256(identity), keys, terms, (span,),
        ))
    return _finish(question_sha, plan.plan_sha, "ready", tuple(objects))
