"""Explicit mathematical utility model (Assignment 3.2).

    U(p) = 100 × (0.30·P + 0.25·Q + 0.20·F + 0.15·S + 0.10·C)

with every component normalised to [0, 1]:

    P  price affordability   clamp(1 − price / budget, 0, 1)
    Q  quality               0.80·(rating / 5) + 0.20·min(reviews / 100, 1)
    F  spatial utilisation   0.50·(width / max_width) + 0.50·(depth / max_depth)
    S  style compatibility   1.0 exact · 0.5 recognised-compatible · 0.0 otherwise
    C  colour compatibility  1.0 exact · 0.5 same colour family  · 0.0 otherwise

The weights, sub-weights, review saturation and compatibility tables come
from :class:`ScoringConfig`, so the coefficients above are the defaults,
not constants baked into the code.

Spatial-utilisation assumptions (F):
    - The limits are the free space available for the sofa (the wall
      length and the floor depth in front of it), so a product that uses
      more of that space — without exceeding it — fills the area better
      and scores higher.  A product at exactly the limit scores 1.0.
    - Width and depth matter equally by default (0.5 / 0.5).
    - The score is only defined for products within the limits; hard
      constraints are checked first, and calling the spatial scorer with
      an oversized product raises an error instead of returning > 1.
    - Leaving clearance for walkways is a user decision: the user should
      enter the clearance-adjusted space as the limit.

The model is pure arithmetic: no I/O, no randomness, no network, no LLM,
so identical inputs always give identical outputs.
"""

from __future__ import annotations

import math

from pydantic import BaseModel, ConfigDict

from smartnest.optimization.constraint_validator import ProductCandidate, UserRequirements
from smartnest.optimization.scoring_config import COMPONENTS, ScoringConfig

#: Decimal places kept in scores; also the resolution at which ties are detected.
SCORE_PRECISION = 6

#: Short symbols used in reports, keyed by component name.
SYMBOLS: dict[str, str] = {
    "price": "P", "quality": "Q", "spatial": "F", "style": "S", "colour": "C",
}


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    if math.isnan(value):
        raise ValueError("cannot clamp NaN")
    return max(low, min(high, value))


class UtilityBreakdown(BaseModel):
    """Full, serialisable explanation of one product's utility.

    Attributes:
        components: Normalised component scores in [0, 1].
        weights: Weights used.
        contributions: Points each component adds to U (100 × w × score).
        total_utility: U on the 0–100 scale.
    """

    model_config = ConfigDict(frozen=True)

    components: dict[str, float]
    weights: dict[str, float]
    contributions: dict[str, float]
    total_utility: float

    @property
    def normalised_utility(self) -> float:
        """U / 100, in [0, 1]."""
        return round(self.total_utility / 100.0, SCORE_PRECISION)


class UtilityModel:
    """Computes the normalised components and total utility of a product.

    Args:
        config: Scoring configuration (validated).  Defaults to
            :class:`ScoringConfig` with the assignment's weights.
    """

    def __init__(self, config: ScoringConfig | None = None) -> None:
        self._config = config or ScoringConfig()

    @property
    def config(self) -> ScoringConfig:
        return self._config

    @property
    def weights(self) -> dict[str, float]:
        return dict(self._config.weights)

    # ── Components ───────────────────────────────────────────────────────

    def price_score(self, price: float, budget: float) -> float:
        """P = clamp(1 − price / budget, 0, 1)."""
        if not (math.isfinite(budget) and budget > 0):
            raise ValueError(f"budget must be finite and > 0, got {budget!r}")
        if not math.isfinite(price):
            raise ValueError(f"price must be finite, got {price!r}")
        return clamp(1.0 - price / budget)

    def quality_score(self, rating: float, review_count: int) -> float:
        """Q = a·(rating / 5) + b·min(reviews / saturation, 1)."""
        if not (math.isfinite(rating) and 0.0 <= rating <= 5.0):
            raise ValueError(f"rating must be within [0, 5], got {rating!r}")
        if review_count < 0:
            raise ValueError(f"review_count must be non-negative, got {review_count!r}")
        cfg = self._config
        review_term = min(review_count / cfg.review_saturation, 1.0)
        return clamp(cfg.quality_rating_weight * (rating / 5.0) + cfg.quality_review_weight * review_term)

    def spatial_score(self, width: float, depth: float, max_width: float, max_depth: float) -> float:
        """F = a·(width / max_width) + b·(depth / max_depth), within the limits only."""
        for name, v in (("max_width", max_width), ("max_depth", max_depth)):
            if not (math.isfinite(v) and v > 0):
                raise ValueError(f"{name} must be finite and > 0, got {v!r}")
        for name, v in (("width", width), ("depth", depth)):
            if not (math.isfinite(v) and v > 0):
                raise ValueError(f"{name} must be finite and > 0, got {v!r}")
        if width > max_width or depth > max_depth:
            raise ValueError(
                "spatial score is only defined for products within the room limits "
                f"({width}×{depth} cm vs {max_width}×{max_depth} cm)"
            )
        cfg = self._config
        return clamp(cfg.spatial_width_weight * (width / max_width)
                     + cfg.spatial_depth_weight * (depth / max_depth))

    def _match_score(self, value: str, preferred: str | None, table: dict[str, list[str]]) -> float:
        cfg = self._config
        if preferred is None:
            return cfg.exact_match_score  # no preference → nothing to penalise
        value = value.strip().lower()
        if not value:
            raise ValueError("value must be a non-empty string")
        if value == preferred:
            return cfg.exact_match_score
        if value in table.get(preferred, []):
            return cfg.compatible_score
        return cfg.incompatible_score

    def style_score(self, style: str, preferred_style: str | None) -> float:
        """S via the configured style-compatibility table."""
        return self._match_score(style, preferred_style, self._config.style_compatibility)

    def colour_score(self, colour: str, preferred_colour: str | None) -> float:
        """C via the configured colour-family table."""
        return self._match_score(colour, preferred_colour, self._config.colour_families)

    # ── Total ─────────────────────────────────────────────────────────────

    def score(self, product: ProductCandidate, req: UserRequirements) -> UtilityBreakdown:
        """Compute U for a *feasible* product and return the full breakdown."""
        components = {
            "price": self.price_score(product.price, req.budget),
            "quality": self.quality_score(product.rating, product.review_count),
            "spatial": self.spatial_score(product.width_cm, product.depth_cm,
                                          req.max_width_cm, req.max_depth_cm),
            "style": self.style_score(product.style, req.preferred_style),
            "colour": self.colour_score(product.colour, req.preferred_colour),
        }
        weights = self._config.weights
        contributions = {k: 100.0 * weights[k] * components[k] for k in COMPONENTS}
        total = sum(contributions.values())
        if not math.isfinite(total) or not -1e-9 <= total <= 100.0 + 1e-9:
            raise ArithmeticError(f"utility out of range: {total!r}")  # defensive; should be impossible
        return UtilityBreakdown(
            components={k: round(components[k], SCORE_PRECISION) for k in COMPONENTS},
            weights=dict(weights),
            contributions={k: round(contributions[k], SCORE_PRECISION) for k in COMPONENTS},
            total_utility=round(clamp(total, 0.0, 100.0), SCORE_PRECISION),
        )
