"""Record validation and hard-constraint checking (Assignment 3.2).

Two separate questions are answered for every candidate, in this order:

1. **Is the record valid?**  Every field needed to compute utility must be
   present, of the right type, finite and within its domain.  A missing
   or malformed field produces a controlled *validation failure*
   (status ``"invalid"``) — it is never silently replaced by a default.

2. **Does it satisfy the hard constraints?**  Budget, width, depth,
   seating and confirmed availability.  Every violated constraint is
   recorded as an explicit reason (status ``"rejected"``).  Boundaries are
   inclusive: a price equal to the budget or a width equal to the room
   limit is allowed.

Only candidates that pass both are ``"eligible"`` for utility scoring.
This module never computes utility, so no score can override a hard
constraint.

The models here are plain, JSON-serialisable Pydantic models with no
dependency on sockets, CNP, databases or LLMs.
"""

from __future__ import annotations

import math
from typing import Any, Iterable, Literal

from pydantic import BaseModel, ConfigDict

from smartnest.optimization.scoring_config import ScoringConfig


class InvalidRequirementsError(ValueError):
    """Raised when user requirements are malformed (caller error)."""


# ── Numeric helpers ──────────────────────────────────────────────────────────

def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _numeric_error(name: str, value: Any, *, positive: bool = False,
                   non_negative: bool = False, integer: bool = False) -> str:
    """Describe why ``value`` is not an acceptable number, or return ``""``."""
    if value is None:
        return f"{name} is missing"
    if not _is_number(value):
        return f"{name} must be a number, got {value!r}"
    if not math.isfinite(value):
        return f"{name} must be finite, got {value!r}"
    if integer and float(value) != int(value):
        return f"{name} must be a whole number, got {value!r}"
    if positive and value <= 0:
        return f"{name} must be greater than 0, got {value!r}"
    if non_negative and value < 0:
        return f"{name} must not be negative, got {value!r}"
    return ""


def _clean_text(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip().lower()
    return None


# ── User requirements ────────────────────────────────────────────────────────

class UserRequirements(BaseModel):
    """What the user asked for.  Validated on construction.

    ``preferred_style`` / ``preferred_colour`` may be ``None`` meaning
    "no preference" (every product then scores the exact-match value for
    that component).  All numeric limits must be finite and positive —
    in particular a zero budget is rejected, because affordability
    (1 − price/budget) would divide by zero.
    """

    model_config = ConfigDict(frozen=True)

    budget: float
    max_width_cm: float
    max_depth_cm: float
    min_seating: int = 1
    preferred_style: str | None = None
    preferred_colour: str | None = None

    def __init__(self, **data: Any) -> None:
        errors = [
            _numeric_error("budget", data.get("budget"), positive=True),
            _numeric_error("max_width_cm", data.get("max_width_cm"), positive=True),
            _numeric_error("max_depth_cm", data.get("max_depth_cm"), positive=True),
            _numeric_error("min_seating", data.get("min_seating", 1), positive=True, integer=True),
        ]
        for key in ("preferred_style", "preferred_colour"):
            value = data.get(key)
            if value is not None and not isinstance(value, str):
                errors.append(f"{key} must be a string or None, got {value!r}")
        errors = [e for e in errors if e]
        if errors:
            raise InvalidRequirementsError("; ".join(errors))
        data["min_seating"] = int(data.get("min_seating", 1))
        data["preferred_style"] = _clean_text(data.get("preferred_style"))
        data["preferred_colour"] = _clean_text(data.get("preferred_colour"))
        super().__init__(**data)

    @classmethod
    def from_cnp(cls, requirements: dict[str, Any]) -> "UserRequirements":
        """Build from a CNP coordinator/bidder requirements dict.

        Accepts the 3.1 key names (``max_budget``, ``room_width`` /
        ``max_width``, ``room_depth`` / ``max_depth``, ``seating_capacity``,
        ``preferred_style``, ``preferred_colour`` / ``preferred_color``).
        """
        def first(*keys: str) -> Any:
            for k in keys:
                if requirements.get(k) is not None:
                    return requirements[k]
            return None

        return cls(
            budget=first("budget", "max_budget"),
            max_width_cm=first("max_width_cm", "max_width", "room_width"),
            max_depth_cm=first("max_depth_cm", "max_depth", "room_depth"),
            min_seating=first("min_seating", "seating_capacity") or 1,
            preferred_style=first("preferred_style") or None,
            preferred_colour=first("preferred_colour", "preferred_color") or None,
        )


# ── Product candidate ────────────────────────────────────────────────────────

class ProductCandidate(BaseModel):
    """A validated product record, normalised for scoring."""

    model_config = ConfigDict(frozen=True)

    product_id: str
    name: str
    price: float
    width_cm: float
    depth_cm: float
    seating_capacity: int
    material: str
    colour: str
    style: str
    availability: str
    rating: float
    review_count: int
    source_agent_id: str

    def with_price(self, price: float) -> "ProductCandidate":
        """Copy with a different (e.g. negotiated) price."""
        return self.model_copy(update={"price": float(price)})


# Required text fields: record key(s) → candidate field.
_TEXT_FIELDS: dict[str, tuple[str, ...]] = {
    "name": ("name",),
    "material": ("material",),
    "colour": ("colour", "color"),
    "style": ("style",),
    "availability": ("availability",),
    "source_agent_id": ("source_agent_id",),
}


def validate_record(record: Any) -> tuple[ProductCandidate | None, list[str]]:
    """Validate a raw product record.

    Returns:
        ``(candidate, [])`` when valid, ``(None, errors)`` otherwise.
        ``errors`` lists every problem found, not just the first.
    """
    if isinstance(record, ProductCandidate):
        return record, []
    if not isinstance(record, dict):
        return None, [f"record must be a mapping, got {type(record).__name__}"]

    errors: list[str] = []
    values: dict[str, Any] = {}

    pid = record.get("product_id")
    if not isinstance(pid, str) or not pid.strip():
        errors.append(f"product_id is missing or invalid ({pid!r})")
    else:
        values["product_id"] = pid.strip()

    for field_name, keys in _TEXT_FIELDS.items():
        raw = next((record[k] for k in keys if record.get(k) is not None), None)
        if raw is None:
            errors.append(f"{field_name} is missing")
        elif not isinstance(raw, str) or not raw.strip():
            errors.append(f"{field_name} must be a non-empty string, got {raw!r}")
        else:
            # Names and IDs keep their case; matching fields are normalised.
            keep_case = field_name in ("name", "source_agent_id")
            values[field_name] = raw.strip() if keep_case else raw.strip().lower()

    numeric_rules = {
        "price": dict(positive=True),
        "width_cm": dict(positive=True),
        "depth_cm": dict(positive=True),
        "seating_capacity": dict(positive=True, integer=True),
        "rating": dict(non_negative=True),
        "review_count": dict(non_negative=True, integer=True),
    }
    for key, rule in numeric_rules.items():
        err = _numeric_error(key, record.get(key), **rule)
        if err:
            errors.append(err)
        else:
            values[key] = record[key]

    if "rating" in values and values["rating"] > 5:
        errors.append(f"rating must be between 0 and 5, got {values['rating']!r}")

    if errors:
        return None, errors

    values["seating_capacity"] = int(values["seating_capacity"])
    values["review_count"] = int(values["review_count"])
    return ProductCandidate(**values), []


# ── Eligibility ──────────────────────────────────────────────────────────────

Status = Literal["eligible", "rejected", "invalid"]


class EligibilityResult(BaseModel):
    """Outcome of validating one record and checking its hard constraints."""

    product_id: str
    source_agent_id: str | None = None
    status: Status
    reasons: list[str] = []
    candidate: ProductCandidate | None = None

    @property
    def eligible(self) -> bool:
        return self.status == "eligible"


class ConstraintValidator:
    """Checks records against the mandatory (hard) constraints.

    Args:
        config: Supplies ``available_states`` (which availability values
            count as confirmed).  Defaults to :class:`ScoringConfig`.
    """

    def __init__(self, config: ScoringConfig | None = None) -> None:
        self._available = frozenset((config or ScoringConfig()).available_states)

    def check_constraints(self, product: ProductCandidate, req: UserRequirements) -> list[str]:
        """Return every hard constraint ``product`` violates (empty if none)."""
        reasons: list[str] = []
        if product.price > req.budget:
            reasons.append(
                f"price ₹{product.price:,.0f} exceeds budget ₹{req.budget:,.0f} "
                f"by ₹{product.price - req.budget:,.0f}"
            )
        if product.width_cm > req.max_width_cm:
            reasons.append(
                f"width {product.width_cm:g} cm exceeds maximum width {req.max_width_cm:g} cm "
                f"by {product.width_cm - req.max_width_cm:g} cm"
            )
        if product.depth_cm > req.max_depth_cm:
            reasons.append(
                f"depth {product.depth_cm:g} cm exceeds maximum depth {req.max_depth_cm:g} cm "
                f"by {product.depth_cm - req.max_depth_cm:g} cm"
            )
        if product.seating_capacity < req.min_seating:
            reasons.append(
                f"seating capacity {product.seating_capacity} is below the minimum {req.min_seating}"
            )
        if product.availability not in self._available:
            reasons.append(f"availability not confirmed ({product.availability})")
        return reasons

    def evaluate(self, record: Any, req: UserRequirements) -> EligibilityResult:
        """Validate ``record`` and check its hard constraints."""
        candidate, errors = validate_record(record)
        if candidate is None:
            raw = record if isinstance(record, dict) else {}
            pid = raw.get("product_id")
            return EligibilityResult(
                product_id=pid if isinstance(pid, str) and pid.strip() else "<unknown>",
                source_agent_id=raw.get("source_agent_id") if isinstance(raw.get("source_agent_id"), str) else None,
                status="invalid",
                reasons=[f"invalid record: {e}" for e in errors],
            )
        violations = self.check_constraints(candidate, req)
        return EligibilityResult(
            product_id=candidate.product_id,
            source_agent_id=candidate.source_agent_id,
            status="rejected" if violations else "eligible",
            reasons=violations,
            candidate=candidate,
        )

    def evaluate_all(self, records: Iterable[Any], req: UserRequirements) -> list[EligibilityResult]:
        return [self.evaluate(r, req) for r in records]
