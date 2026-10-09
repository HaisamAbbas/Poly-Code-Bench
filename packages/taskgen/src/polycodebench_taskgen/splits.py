"""Family clustering, keyed split assignment and validation-to-test agreement.

Three rules keep related items together and keep the held-out split unpredictable:

1. Items whose texts overlap above a threshold form one cluster (union-find over pairwise
   containment). A cluster is the unit of splitting, so variants of one problem never straddle a
   public/private boundary.
2. A cluster's split comes from an HMAC keyed with a private secret, so an outsider cannot compute
   which split a new cluster will land in and cannot pick items to steer that outcome.
3. Public validation scores are only useful if they rank models like the private test does. The
   agreement check reports the Spearman rank correlation between the two sets in basis points, and
   reports nothing when fewer than three models are shared.
"""

from __future__ import annotations

import hashlib
import hmac
import math
from collections.abc import Mapping
from fractions import Fraction

from pydantic import model_validator

from polycodebench_taskgen.contracts import SplitName, TaskgenModel
from polycodebench_taskgen.overlap import ngram_hashes, surface_tokens


class SplitRatios(TaskgenModel):
    public_development_bp: int
    public_validation_bp: int
    private_heldout_bp: int

    @model_validator(mode="after")
    def _sums_to_whole(self) -> SplitRatios:
        values = (self.public_development_bp, self.public_validation_bp, self.private_heldout_bp)
        if min(values) < 0 or sum(values) != 10000:
            raise ValueError("split ratios must be nonnegative and sum to 10000 basis points")
        return self


def assign_split(cluster_id: str, *, secret: bytes, ratios: SplitRatios) -> SplitName:
    if len(secret) < 32:
        raise ValueError("split secret must be at least 32 bytes")
    digest = hmac.new(secret, f"split-v1\x00{cluster_id}".encode(), hashlib.sha256).digest()
    bucket = int.from_bytes(digest[:8], "big") % 10000
    if bucket < ratios.public_development_bp:
        return "public_development"
    if bucket < ratios.public_development_bp + ratios.public_validation_bp:
        return "public_validation"
    return "private_heldout"


def _containment_bp(grams_a: frozenset[int], grams_b: frozenset[int]) -> int:
    if not grams_a or not grams_b:
        return 0
    shared = len(grams_a & grams_b)
    return (10000 * shared + min(len(grams_a), len(grams_b)) // 2) // min(
        len(grams_a), len(grams_b)
    )


def cluster_by_containment(
    texts: Mapping[str, str], *, ngram: int, link_threshold_bp: int
) -> dict[str, str]:
    """Map each item id to its cluster id: the lexicographically smallest member's id."""
    if not 0 <= link_threshold_bp <= 10000:
        raise ValueError("link threshold must be between 0 and 10000 basis points")
    ids = sorted(texts)
    grams = {item: ngram_hashes(surface_tokens(texts[item]), ngram) for item in ids}
    parent = {item: item for item in ids}

    def root(item: str) -> str:
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    for position, left in enumerate(ids):
        for right in ids[position + 1 :]:
            if _containment_bp(grams[left], grams[right]) >= link_threshold_bp:
                low, high = sorted((root(left), root(right)))
                parent[high] = low
    return {item: root(item) for item in ids}


def rank_agreement_bp(
    validation_scores: Mapping[str, int], test_scores: Mapping[str, int]
) -> int | None:
    """Spearman rank correlation over shared models, in basis points, or None below three models.

    Scores are integers (for example scaled pass counts) so the inputs stay canonical. Ties share
    their average rank.
    """
    shared = sorted(set(validation_scores) & set(test_scores))
    if len(shared) < 3:
        return None
    ranks_a = _average_ranks([validation_scores[model] for model in shared])
    ranks_b = _average_ranks([test_scores[model] for model in shared])
    mean_a = sum(ranks_a) / len(ranks_a)
    mean_b = sum(ranks_b) / len(ranks_b)
    covariance = sum((a - mean_a) * (b - mean_b) for a, b in zip(ranks_a, ranks_b, strict=True))
    spread_a = sum((a - mean_a) ** 2 for a in ranks_a)
    spread_b = sum((b - mean_b) ** 2 for b in ranks_b)
    if spread_a == 0 or spread_b == 0:
        return None
    rho = float(covariance) / math.sqrt(float(spread_a) * float(spread_b))
    return round(10000 * rho)


def _average_ranks(values: list[int]) -> list[Fraction]:
    order = sorted(range(len(values)), key=lambda index: values[index])
    ranks = [Fraction(0)] * len(values)
    position = 0
    while position < len(order):
        end = position
        while end + 1 < len(order) and values[order[end + 1]] == values[order[position]]:
            end += 1
        average = Fraction(position + end + 2, 2)
        for index in order[position : end + 1]:
            ranks[index] = average
        position = end + 1
    return ranks
