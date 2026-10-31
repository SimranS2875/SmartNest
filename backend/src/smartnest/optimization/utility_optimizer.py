"""Deterministic utility optimizer (Assignment 3.2).

Algorithm — exhaustive evaluation, which is exact and cheap for catalogue
sizes in the tens or hundreds:

    1. Inspect every candidate (input order is preserved in the report).
    2. Validate its mandatory fields       → "invalid" if malformed.
    3. Check the hard constraints          → "rejected" with reasons.
    4. Compute the normalised utility U of every eligible candidate.
    5. Rank eligible candidates by the deterministic key
           (higher U, higher quality Q, lower price, product_id A→Z,
            source agent A→Z).
    6. Select rank 1, and verify it against every eligible candidate.

Because every candidate is evaluated and the ranking is a total order,
the selected candidate is provably the highest-utility feasible option
in the evaluated set.  If no candidate is feasible, there is no
recommendation (``selected`` is ``None``) — never a "best of the
infeasible".

The optimizer is pure computation: no sockets, no CNP state, no database,
no LLM.  :meth:`UtilityOptimizer.optimize_async` runs it in a worker
thread so an async web backend (Assignment 5.2) is never blocked.
"""

from __future__ import annotations

import asyncio
from typing import Any, Iterable

from pydantic import BaseModel, ConfigDict

from smartnest.optimization.constraint_validator import (
    ConstraintValidator,
    ProductCandidate,
    Status,
    UserRequirements,
)
from smartnest.optimization.scoring_config import COMPONENTS
from smartnest.optimization.utility_model import SCORE_PRECISION, SYMBOLS, UtilityBreakdown, UtilityModel


class CandidateEvaluation(BaseModel):
    """Everything known about one candidate after evaluation."""

    model_config = ConfigDict(frozen=True)

    product_id: str
    source_agent_id: str | None = None
    name: str | None = None
    status: Status
    reasons: list[str] = []
    price: float | None = None
    breakdown: UtilityBreakdown | None = None
    rank: int | None = None

    @property
    def eligible(self) -> bool:
        return self.status == "eligible"

    @property
    def utility(self) -> float | None:
        """U on the 0–100 scale (``None`` if not eligible)."""
        return self.breakdown.total_utility if self.breakdown else None


class OptimizationResult(BaseModel):
    """Serialisable outcome of one optimisation run."""

    requirements: UserRequirements
    weights: dict[str, float]
    total_inspected: int
    eligible_count: int
    rejected_count: int  # every non-eligible candidate (constraint + invalid)
    invalid_count: int   # subset of rejected_count with malformed records
    evaluations: list[CandidateEvaluation]  # input order
    ranking: list[CandidateEvaluation]      # eligible only, best first
    selected: CandidateEvaluation | None
    verified_optimal: bool
    explanation: str

    @property
    def has_recommendation(self) -> bool:
        return self.selected is not None

    @property
    def rejected(self) -> list[CandidateEvaluation]:
        return [e for e in self.evaluations if not e.eligible]


def ranking_key(evaluation: CandidateEvaluation) -> tuple:
    """Deterministic total order: U ↓, Q ↓, price ↑, product_id ↑, source ↑."""
    b = evaluation.breakdown
    return (
        -round(b.total_utility, SCORE_PRECISION),
        -round(b.components["quality"], SCORE_PRECISION),
        evaluation.price,
        evaluation.product_id,
        evaluation.source_agent_id or "",
    )


class UtilityOptimizer:
    """Evaluates, filters, ranks and selects product candidates.

    Args:
        model: The utility model (default configuration if omitted).
        validator: The hard-constraint validator; defaults to one that
            shares the model's configuration.
    """

    def __init__(
        self,
        model: UtilityModel | None = None,
        validator: ConstraintValidator | None = None,
    ) -> None:
        self._model = model or UtilityModel()
        self._validator = validator or ConstraintValidator(self._model.config)

    @property
    def model(self) -> UtilityModel:
        return self._model

    @property
    def validator(self) -> ConstraintValidator:
        return self._validator

    # ── Single candidate ─────────────────────────────────────────────────

    def evaluate_candidate(self, record: Any, requirements: UserRequirements) -> CandidateEvaluation:
        """Validate, constraint-check and (if eligible) score one candidate."""
        result = self._validator.evaluate(record, requirements)
        cand = result.candidate
        if not result.eligible:
            return CandidateEvaluation(
                product_id=result.product_id,
                source_agent_id=result.source_agent_id,
                name=cand.name if cand else None,
                status=result.status,
                reasons=list(result.reasons),
                price=cand.price if cand else None,
            )
        return CandidateEvaluation(
            product_id=cand.product_id,
            source_agent_id=cand.source_agent_id,
            name=cand.name,
            status="eligible",
            price=cand.price,
            breakdown=self._model.score(cand, requirements),
        )

    # ── Whole set ─────────────────────────────────────────────────────────

    def optimize(
        self,
        candidates: Iterable[Any],
        requirements: UserRequirements | dict[str, Any],
    ) -> OptimizationResult:
        """Exhaustively evaluate ``candidates`` and select the best feasible one.

        Raises:
            InvalidRequirementsError: If ``requirements`` are malformed.
        """
        if not isinstance(requirements, UserRequirements):
            requirements = UserRequirements(**requirements)

        evaluations: list[CandidateEvaluation] = []
        seen: set[tuple[str, str]] = set()
        for record in list(candidates):
            evaluation = self.evaluate_candidate(record, requirements)
            key = (evaluation.source_agent_id or "", evaluation.product_id)
            if evaluation.status != "invalid" and key in seen:
                evaluation = evaluation.model_copy(update={
                    "status": "invalid",
                    "breakdown": None,
                    "reasons": [f"invalid record: duplicate product {evaluation.product_id} "
                                f"from {evaluation.source_agent_id}"],
                })
            seen.add(key)
            evaluations.append(evaluation)

        eligible = sorted((e for e in evaluations if e.eligible), key=ranking_key)
        ranking = [e.model_copy(update={"rank": i}) for i, e in enumerate(eligible, start=1)]
        rank_of = {(e.source_agent_id, e.product_id): e.rank for e in ranking}
        evaluations = [
            e.model_copy(update={"rank": rank_of.get((e.source_agent_id, e.product_id))})
            if e.eligible else e
            for e in evaluations
        ]

        selected = ranking[0] if ranking else None
        verified = selected is not None and all(
            selected.utility >= e.utility for e in ranking
        )

        invalid = sum(1 for e in evaluations if e.status == "invalid")
        return OptimizationResult(
            requirements=requirements,
            weights=self._model.weights,
            total_inspected=len(evaluations),
            eligible_count=len(ranking),
            rejected_count=len(evaluations) - len(ranking),
            invalid_count=invalid,
            evaluations=evaluations,
            ranking=ranking,
            selected=selected,
            verified_optimal=verified,
            explanation=self._explain(ranking, len(evaluations)),
        )

    async def optimize_async(
        self,
        candidates: Iterable[Any],
        requirements: UserRequirements | dict[str, Any],
    ) -> OptimizationResult:
        """Run :meth:`optimize` in a worker thread (non-blocking for async apps)."""
        return await asyncio.to_thread(self.optimize, list(candidates), requirements)

    # ── Explanation ──────────────────────────────────────────────────────

    @staticmethod
    def _explain(ranking: list[CandidateEvaluation], inspected: int) -> str:
        if not ranking:
            return (f"No feasible candidate among {inspected} inspected — "
                    f"no recommendation is made.")
        best = ranking[0]
        head = (f"{best.product_id} ({best.source_agent_id}) has the highest utility "
                f"U = {best.utility:.2f} of {len(ranking)} feasible candidate(s) "
                f"out of {inspected} inspected.")
        if len(ranking) == 1:
            return head + " It is the only candidate that satisfies every hard constraint."

        runner = ranking[1]
        margin = best.utility - runner.utility
        diffs = {
            k: best.breakdown.contributions[k] - runner.breakdown.contributions[k]
            for k in COMPONENTS
        }
        ahead = sorted(((v, k) for k, v in diffs.items() if v > 1e-9), reverse=True)
        behind = sorted((v, k) for k, v in diffs.items() if v < -1e-9)

        def fmt(items):
            return ", ".join(f"{k} ({SYMBOLS[k]}) {v:+.2f}" for v, k in items)

        text = (f"{head} It beats the runner-up {runner.product_id} "
                f"(U = {runner.utility:.2f}) by {margin:.2f} points.")
        if ahead:
            text += f" Gains over the runner-up: {fmt(ahead)}."
        if behind:
            text += f" Trails it on: {fmt(behind)}."
        if abs(margin) <= 10 ** -SCORE_PRECISION:
            q_best, q_run = best.breakdown.components["quality"], runner.breakdown.components["quality"]
            if q_best != q_run:
                rule = "higher quality score"
            elif best.price != runner.price:
                rule = "lower price"
            else:
                rule = "product ID order"
            text += f" Utilities are tied; it wins the tie-break on {rule}."
        return text


__all__ = [
    "CandidateEvaluation",
    "OptimizationResult",
    "ProductCandidate",
    "UtilityOptimizer",
    "ranking_key",
]
