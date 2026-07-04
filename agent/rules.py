from __future__ import annotations

import logging
import operator
from dataclasses import dataclass
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

_COMPARATORS = {
    ">": operator.gt,
    ">=": operator.ge,
    "<": operator.lt,
    "<=": operator.le,
    "==": operator.eq,
    "!=": operator.ne,
}


@dataclass
class RemediationTarget:
    kind: str
    namespace: str
    name: str


@dataclass
class Rule:
    name: str
    query: str
    comparator: str
    threshold: float
    description: str = ""
    target: RemediationTarget | None = None
    consecutive_breaches_required: int = 1
    cooldown_seconds: int = 300
    action_hint: str = ""

    def is_breached(self, value: float) -> bool:
        try:
            comparator_fn = _COMPARATORS[self.comparator]
        except KeyError as exc:
            raise ValueError(f"Unknown comparator '{self.comparator}' in rule '{self.name}'") from exc
        return comparator_fn(value, self.threshold)


def load_rules(path: str) -> list[Rule]:
    rules_path = Path(path)
    if not rules_path.exists():
        logger.warning("Rules file %s not found; no monitoring rules loaded", path)
        return []

    with rules_path.open() as fh:
        raw = yaml.safe_load(fh) or {}

    rules: list[Rule] = []
    for entry in raw.get("rules", []):
        target_raw = entry.get("target")
        target = (
            RemediationTarget(
                kind=target_raw.get("kind", "Deployment"),
                namespace=target_raw["namespace"],
                name=target_raw["name"],
            )
            if target_raw
            else None
        )
        rules.append(
            Rule(
                name=entry["name"],
                query=entry["query"],
                comparator=entry["comparator"],
                threshold=float(entry["threshold"]),
                description=entry.get("description", ""),
                target=target,
                consecutive_breaches_required=int(entry.get("consecutive_breaches_required", 1)),
                cooldown_seconds=int(entry.get("cooldown_seconds", 300)),
                action_hint=entry.get("action_hint", ""),
            )
        )

    logger.info("Loaded %d monitoring rule(s) from %s", len(rules), path)
    return rules
