"""Deterministic identity, sampling, partitioning, and blinding utilities."""

from __future__ import annotations

import copy
import hashlib
import math
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence


SELECTION_NAMESPACE = "agent-defect-corpus-v1"
PARTITION_NAMESPACE = "agent-defect-partition-v1"


def canonical_candidate_key(candidate: Mapping[str, Any]) -> str:
    """Return the stable repository-plus-fixing-change candidate identity."""

    return (
        f"{str(candidate['repository']).lower()}:"
        f"{candidate['canonical_locator']}"
    )


def _rank(namespace: str, candidate: Mapping[str, Any]) -> str:
    value = f"{namespace}|{canonical_candidate_key(candidate)}"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _stable_defect_id(candidate: Mapping[str, Any]) -> str:
    repository = re.sub(
        r"[^A-Z0-9]+",
        "-",
        str(candidate["repository"]).upper(),
    ).strip("-")
    identity = hashlib.sha256(
        canonical_candidate_key(candidate).encode("utf-8")
    ).hexdigest()[:12].upper()
    return f"{repository}-{identity}"


def select_and_partition(
    candidates: Sequence[Mapping[str, Any]],
    *,
    cap_per_repository: int = 25,
) -> list[dict[str, Any]]:
    """Select by repository and assign a hidden deterministic 60/40 split."""

    if cap_per_repository < 1:
        raise ValueError("cap_per_repository must be positive")
    keys = [canonical_candidate_key(candidate) for candidate in candidates]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate canonical candidate keys")

    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        grouped[str(candidate["repository"]).lower()].append(candidate)

    selected: list[dict[str, Any]] = []
    for repository_key in sorted(grouped):
        ranked = sorted(
            grouped[repository_key],
            key=lambda item: _rank(SELECTION_NAMESPACE, item),
        )[:cap_per_repository]
        partition_ranked = sorted(
            ranked,
            key=lambda item: _rank(PARTITION_NAMESPACE, item),
        )
        derivation_count = math.ceil(0.60 * len(partition_ranked))
        for index, candidate in enumerate(partition_ranked):
            item = copy.deepcopy(dict(candidate))
            item["defect_id"] = _stable_defect_id(candidate)
            item["selection_rank"] = _rank(SELECTION_NAMESPACE, candidate)
            item["partition"] = (
                "derivation" if index < derivation_count else "validation"
            )
            selected.append(item)
    return sorted(selected, key=lambda item: item["defect_id"])


def blind_packet(
    selected: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Remove fields that could reveal partitions or prior judgments."""

    hidden = {
        "partition",
        "selection_rank",
        "machine_precode",
        "operator_ids",
    }
    return [
        {
            key: copy.deepcopy(value)
            for key, value in item.items()
            if key not in hidden
        }
        for item in selected
    ]


def sha256_file(path: Path) -> str:
    """Hash the exact retained bytes of an artifact."""

    return hashlib.sha256(path.read_bytes()).hexdigest()
