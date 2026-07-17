"""Shared value objects for the semantic model graph feature."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class GraphIssue:
    """A deterministic graph compilation or planning diagnostic."""

    level: str
    code: str
    path: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {
            "level": self.level,
            "code": self.code,
            "path": self.path,
            "message": self.message,
        }

    def __str__(self) -> str:
        return f"[{self.level.upper()}] {self.code} at {self.path}: {self.message}"


class GraphCompilationError(ValueError):
    """Raised when source metadata cannot produce a trustworthy graph."""

    def __init__(self, issues: list[GraphIssue]):
        self.issues = tuple(issue for issue in issues if issue.level == "error")
        details = "\n".join(f"- {issue}" for issue in self.issues)
        super().__init__(f"Semantic graph compilation failed:\n{details}")


class GraphPlanningError(ValueError):
    """Raised when a requested virtual Cube has no safe single-fact plan."""

    def __init__(self, code: str, message: str, *, details: Any = None):
        self.code = code
        self.details = details
        super().__init__(message)


@dataclass(frozen=True)
class GraphConfig:
    """Graph-only configuration loaded from ``relationships.yml``."""

    max_hops: int
    master_attributes: dict[str, str]
    relationship_roles: dict[str, str]
    relationship_entities: dict[str, str]
    bridge_policies: dict[str, dict[str, str]]
    metric_policies: dict[str, dict[str, Any]]

    def as_dict(self) -> dict[str, Any]:
        return {
            "maxHops": self.max_hops,
            "masterData": {"attributes": dict(self.master_attributes)},
            "relationshipRoles": dict(self.relationship_roles),
            "relationshipEntities": dict(self.relationship_entities),
            "bridges": {
                relationship: {
                    "model": policy["model"],
                    "sourceRelationship": policy["source_relationship"],
                    "targetRelationship": policy["target_relationship"],
                    "allocationExpression": policy["allocation_expression"],
                    "allocationMode": policy["allocation_mode"],
                }
                for relationship, policy in self.bridge_policies.items()
            },
            "metricPolicies": {
                metric: {
                    "additivity": policy["additivity"],
                    "blockedDimensions": list(policy["blocked_dimensions"]),
                }
                for metric, policy in self.metric_policies.items()
            },
        }


@dataclass(frozen=True)
class GraphBundle:
    """The graph artifact and its precomputed queryability index."""

    semantic_graph: dict[str, Any]
    queryability_index: dict[str, Any]
    issues: tuple[GraphIssue, ...]
