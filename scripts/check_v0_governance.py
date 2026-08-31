"""Verify ACT-D10 GitHub governance before V0 publication.

The check is read-only.  An unavailable or incomplete API response is a hard failure because the
release workflow must never infer that repository protections exist.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Sequence
from typing import Any

API_VERSION = "2026-03-10"
RELEASE_ENVIRONMENT = "v0-production"
RELEASE_TAG = "v0.1.0"
REQUIRED_CHECKS = frozenset({"quality", "container-smoke"})


class GovernanceError(RuntimeError):
    """The repository does not satisfy the approved publication governance."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise GovernanceError(message)


def _applies_to_ref(ruleset: dict[str, Any], ref: str, symbolic: str) -> bool:
    conditions = ruleset.get("conditions")
    if not isinstance(conditions, dict):
        return False
    ref_name = conditions.get("ref_name")
    if not isinstance(ref_name, dict):
        return False
    includes = ref_name.get("include")
    excludes = ref_name.get("exclude")
    if not isinstance(includes, list) or not all(isinstance(item, str) for item in includes):
        return False
    if not isinstance(excludes, list) or not all(isinstance(item, str) for item in excludes):
        return False

    def matches(pattern: str) -> bool:
        return pattern in {symbolic, "~ALL"} or fnmatch.fnmatchcase(ref, pattern)

    if any(matches(pattern) for pattern in excludes):
        return False
    return any(matches(pattern) for pattern in includes)


def _rules_by_type(rulesets: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for ruleset in rulesets:
        rules = ruleset.get("rules")
        _require(isinstance(rules, list), "ruleset detail is missing rules")
        for rule in rules:
            _require(isinstance(rule, dict), "ruleset rule must be an object")
            rule_type = rule.get("type")
            _require(isinstance(rule_type, str), "ruleset rule type is missing")
            result.setdefault(rule_type, []).append(rule)
    return result


def validate_governance(
    rulesets: list[dict[str, Any]],
    environment: dict[str, Any],
) -> None:
    active = [item for item in rulesets if item.get("enforcement") == "active"]
    main_sets = [
        item
        for item in active
        if item.get("target") == "branch"
        and _applies_to_ref(item, "refs/heads/main", "~DEFAULT_BRANCH")
    ]
    _require(main_sets, "no active ruleset protects main")
    main_rules = _rules_by_type(main_sets)
    for rule_type in ("pull_request", "deletion", "non_fast_forward"):
        _require(rule_type in main_rules, f"main protection lacks rule: {rule_type}")

    status_rules = main_rules.get("required_status_checks", [])
    status_checks_satisfied = False
    for rule in status_rules:
        parameters = rule.get("parameters")
        if not isinstance(parameters, dict):
            continue
        checks = parameters.get("required_status_checks")
        if not isinstance(checks, list):
            continue
        contexts: set[str] = set()
        for check in checks:
            if isinstance(check, dict) and isinstance(check.get("context"), str):
                contexts.add(check["context"])
        status_checks_satisfied = status_checks_satisfied or (
            parameters.get("strict_required_status_checks_policy") is True
            and REQUIRED_CHECKS.issubset(contexts)
        )
    _require(
        status_checks_satisfied,
        "main does not strictly require quality and container-smoke",
    )

    tag_ref = f"refs/tags/{RELEASE_TAG}"
    tag_sets = [
        item
        for item in active
        if item.get("target") == "tag" and _applies_to_ref(item, tag_ref, "~ALL")
    ]
    _require(tag_sets, "no active ruleset protects v0.1.0")
    tag_rules = _rules_by_type(tag_sets)
    for rule_type in ("deletion", "non_fast_forward"):
        _require(rule_type in tag_rules, f"v* tag protection lacks rule: {rule_type}")

    _require(environment.get("name") == RELEASE_ENVIRONMENT, "release environment name drifted")
    protection_rules = environment.get("protection_rules")
    _require(isinstance(protection_rules, list), "release environment protections are unavailable")
    reviewer_rules = [
        rule
        for rule in protection_rules
        if isinstance(rule, dict) and rule.get("type") == "required_reviewers"
    ]
    _require(reviewer_rules, "v0-production lacks required human reviewers")
    _require(
        any(
            isinstance(rule.get("reviewers"), list) and rule["reviewers"] for rule in reviewer_rules
        ),
        "v0-production reviewer list is empty",
    )
    deployment_policy = environment.get("deployment_branch_policy")
    _require(isinstance(deployment_policy, dict), "release environment branch policy is missing")
    _require(
        deployment_policy.get("protected_branches") is True,
        "v0-production does not restrict deployments to protected branches",
    )


class GitHubApi:
    """Minimal read-only GitHub REST client."""

    def __init__(self, repository: str, token: str) -> None:
        _require(
            re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) is not None,
            "invalid repository",
        )
        _require(bool(token), "GITHUB_TOKEN is required for governance verification")
        self._repository = repository
        self._token = token

    def get(self, path: str) -> Any:
        url = f"https://api.github.com/repos/{self._repository}/{path.lstrip('/')}"
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self._token}",
                "User-Agent": "EndoViHo-RAG-V0-preflight",
                "X-GitHub-Api-Version": API_VERSION,
            },
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                _require(response.status == 200, f"GitHub API returned HTTP {response.status}")
                return json.loads(response.read().decode("utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError, urllib.error.HTTPError) as exc:
            raise GovernanceError(f"GitHub governance API request failed: {url}") from exc

    def rulesets(self) -> list[dict[str, Any]]:
        summaries = self.get("rulesets?per_page=100")
        _require(isinstance(summaries, list), "GitHub ruleset list is invalid")
        details: list[dict[str, Any]] = []
        for summary in summaries:
            _require(isinstance(summary, dict), "GitHub ruleset summary is invalid")
            ruleset_id = summary.get("id")
            _require(isinstance(ruleset_id, int), "GitHub ruleset id is missing")
            detail = self.get(f"rulesets/{ruleset_id}")
            _require(isinstance(detail, dict), "GitHub ruleset detail is invalid")
            details.append(detail)
        return details

    def environment(self) -> dict[str, Any]:
        name = urllib.parse.quote(RELEASE_ENVIRONMENT, safe="")
        value = self.get(f"environments/{name}")
        _require(isinstance(value, dict), "GitHub environment response is invalid")
        return value


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY"))
    parser.add_argument("--token", default=os.environ.get("GITHUB_TOKEN"))
    args = parser.parse_args(argv)
    _require(isinstance(args.repository, str), "--repository is required")
    _require(isinstance(args.token, str), "--token is required")
    api = GitHubApi(args.repository, args.token)
    validate_governance(api.rulesets(), api.environment())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
