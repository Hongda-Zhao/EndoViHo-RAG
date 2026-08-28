"""Shared fail-closed vocabulary for topics excluded from Milestone 4."""

from __future__ import annotations

import re
from typing import Final

_COMPARISON = r"(?:compar(?:e|ed|es|ing|ison|isons)|differ(?:s|ed|ing|ence|ences)?)"
_EVE = r"(?:EVEs?|endogenous +viral +elements?)"

FORBIDDEN_TOPIC_PATTERNS: Final = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\b(?:prevalence|percentage|biological +frequency)\b",
        r"\b(?:screened[- ]negative|biological +absence)\b",
        r"\babsence +(?:of|among|in)\b",
        rf"\bno +(?:known +)?{_EVE}\b",
        rf"\b{_EVE} +(?:are|were|do|does) +not +(?:present|exist|occur)\b",
        r"\binfect(?:ion|ed|s|ing)?\b",
        r"\b(?:co[- ]?divergence|codivergence)\b",
        r"\bindependent +integration +events?\b",
        rf"\b{_COMPARISON}\b.{{0,80}}\bhost[- ]lineages?\b",
        rf"\bhost[- ]lineages?\b.{{0,80}}\b{_COMPARISON}\b",
        rf"\b(?:new|de +novo) +{_EVE}\b",
        rf"\b(?:detect(?:ed|ing|ion|s)?|identif(?:y|ied|ies|ying|ication)|"
        rf"discover(?:ed|ing|y|ies)?|find(?:ing|s)?)\b.{{0,40}}\b{_EVE}\b",
        rf"\b{_EVE}\b.{{0,40}}\b(?:detect(?:ed|ing|ion|s)?|"
        rf"identif(?:y|ied|ies|ying|ication)|discover(?:ed|ing|y|ies)?|"
        rf"find(?:ing|s)?)\b",
        r"\b(?:sequence +upload|upload(?:ed|ing)? +(?:a +)?sequence)\b",
        r"\b(?:blast|hmmer|foldseek)\b",
        r"\b(?:phylogeny|phylogenetic|phylogenetically|jplace)\b",
        r"\b(?:live +(?:web +)?search|search +the +web)\b",
        r"\bexternal +knowledge\b",
        r"\b(?:ignor(?:e|ed|es|ing)|overrid(?:e|den|es|ing)|"
        r"disregard(?:ed|ing|s)?)(?: +the)? +"
        r"(?:(?:prior|previous|system|developer|answer) +)?instructions?\b",
        r"\b(?:call|invoke|execute|use) +(?:an? +)?(?:external +)?(?:tool|function)s?\b",
        r"\b(?:arbitrary +sql|text[- ]to[- ]sql|sql +query|"
        r"(?:generate|execute|write|run) +sql)\b",
        r"\bmultilingual +(?:output|quer(?:y|ies))\b",
        r"\bmulti[- ]turn +memory\b",
    )
)


def contains_forbidden_topic(text: str) -> bool:
    """Return true when fixed excluded-scope vocabulary occurs in text."""

    return any(pattern.search(text) is not None for pattern in FORBIDDEN_TOPIC_PATTERNS)


__all__ = ["FORBIDDEN_TOPIC_PATTERNS", "contains_forbidden_topic"]
