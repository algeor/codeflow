from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any


@dataclass(frozen=True)
class ReviewRoleDecision:
    role: str
    files: list[str]
    matched_patterns: list[str]
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "files": self.files,
            "matched_patterns": self.matched_patterns,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ReviewPlan:
    changed_files: list[str]
    roles: list[ReviewRoleDecision]
    review_tasks: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "changed_files": self.changed_files,
            "required_roles": [role.role for role in self.roles],
            "roles": [role.to_dict() for role in self.roles],
            "review_tasks": self.review_tasks,
        }


DEFAULT_REVIEW_ROLE_PATTERNS: dict[str, list[str]] = {
    "backend": ["**/*.py", "**/*.go", "**/*.java", "**/*.ts"],
    "frontend": ["**/*.tsx", "**/*.jsx", "**/*.css", "**/*.scss"],
    "security": ["**/*auth*", "**/*secret*", "**/*token*", "**/*permission*"],
    "infra": ["Dockerfile", "docker-compose*.yml", ".github/**", "k8s/**", "helm/**"],
    "docs": ["**/*.md", "docs/**"],
    "test_quality": ["tests/**", "**/*test*", "**/*spec*"],
}


def detect_review_plan(changed_files: list[str], config: dict[str, Any] | None) -> ReviewPlan:
    normalized_files = _normalized_changed_files(changed_files)
    role_patterns = _configured_review_role_patterns(config)
    roles: list[ReviewRoleDecision] = []

    for role in sorted(role_patterns):
        if role == "test_quality" and not _require_test_quality_review(config):
            continue
        matched_files, matched_patterns = _match_role(normalized_files, role_patterns[role])
        if matched_files:
            roles.append(
                ReviewRoleDecision(
                    role=role,
                    files=matched_files,
                    matched_patterns=matched_patterns,
                    reason=f"matched configured {role} review patterns",
                )
            )

    return ReviewPlan(
        changed_files=normalized_files,
        roles=roles,
        review_tasks=_review_tasks_for_roles(normalized_files, [role.role for role in roles]),
    )


def _normalized_changed_files(changed_files: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw_path in changed_files:
        path = str(raw_path).strip().replace("\\", "/")
        if not path or path in seen:
            continue
        seen.add(path)
        normalized.append(path)
    return normalized


def _configured_review_role_patterns(config: dict[str, Any] | None) -> dict[str, list[str]]:
    review_config = config.get("review", {}) if isinstance(config, dict) else {}
    if not isinstance(review_config, dict):
        return dict(DEFAULT_REVIEW_ROLE_PATTERNS)
    roles_config = review_config.get("roles")
    if not isinstance(roles_config, dict):
        return dict(DEFAULT_REVIEW_ROLE_PATTERNS)

    patterns_by_role: dict[str, list[str]] = {}
    for role, raw_config in roles_config.items():
        if not isinstance(raw_config, dict):
            continue
        patterns = raw_config.get("patterns")
        if not isinstance(patterns, list):
            continue
        normalized_patterns = [str(pattern).strip() for pattern in patterns if str(pattern).strip()]
        if normalized_patterns:
            patterns_by_role[str(role)] = normalized_patterns
    return patterns_by_role or dict(DEFAULT_REVIEW_ROLE_PATTERNS)


def _require_test_quality_review(config: dict[str, Any] | None) -> bool:
    review_config = config.get("review", {}) if isinstance(config, dict) else {}
    if not isinstance(review_config, dict):
        return True
    value = review_config.get("require_test_quality_review", True)
    return bool(value)


def _match_role(files: list[str], patterns: list[str]) -> tuple[list[str], list[str]]:
    matched_files: list[str] = []
    matched_patterns: list[str] = []
    for file_path in files:
        file_matched = False
        for pattern in patterns:
            if _matches_pattern(file_path, pattern):
                file_matched = True
                if pattern not in matched_patterns:
                    matched_patterns.append(pattern)
        if file_matched:
            matched_files.append(file_path)
    return matched_files, matched_patterns


def _matches_pattern(file_path: str, pattern: str) -> bool:
    normalized_pattern = pattern.replace("\\", "/")
    if fnmatch.fnmatchcase(file_path, normalized_pattern):
        return True
    if normalized_pattern.startswith("**/") and fnmatch.fnmatchcase(file_path, normalized_pattern[3:]):
        return True
    return PurePosixPath(file_path).match(normalized_pattern)


def _review_tasks_for_roles(changed_files: list[str], roles: list[str]) -> list[str]:
    if not changed_files:
        return []
    tasks = ["code_review"]
    if "security" in roles:
        tasks.append("security_review")
    if "test_quality" in roles:
        tasks.append("test_quality_review")
    tasks.append("final_blocking_review")
    return tasks
