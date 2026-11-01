"""The four worker agents.  Each one is a small, single-purpose component reachable only through the signed bus.

    SpacePlanningAgent    "2BHK" -> rooms -> the items to buy (and the assumptions made)        task: plan
    ProductResearchAgent  fetches a category's listings, removes accessories and says why        task: research
    ComparisonAgent       runs the UtilityOptimizer for one item and returns ranked options     task: compare
    BudgetAgent           splits the budget, re-splits it, and produces the analytics           task: allocate | reallocate | summarise

None of them calls Gemini or the network.  The ComparisonAgent is the only place that ranks products, and it does so
with the existing :class:`UtilityOptimizer`, so every hard constraint (price ceiling, stock, size, seats) still holds.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from smartnest.furnishing import budget as budget_mod
from smartnest.furnishing.catalog import ProductCatalog
from smartnest.furnishing.categories import CATEGORIES
from smartnest.furnishing.rooms import Slot, parse_home_type, rooms_for, slots_for
from smartnest.optimization import UtilityOptimizer
from smartnest.optimization.utility_optimizer import CandidateEvaluation


class SpacePlanningAgent:
    agent_id = "space_planning"

    def handle(self, task: str, data: dict[str, Any]) -> dict[str, Any]:
        if task != "plan":
            raise ValueError(f"unknown task {task!r}")
        bedrooms = data.get("bedrooms")
        assumptions: list[str] = []
        if bedrooms is None:
            bedrooms = parse_home_type(str(data.get("home_text") or ""))
        if bedrooms is None:
            raise ValueError("the number of bedrooms is not known (say e.g. '2BHK')")
        rooms = rooms_for(int(bedrooms), data.get("room_dimensions"))
        slots = slots_for(rooms)
        assumptions.append(f"A {bedrooms}BHK has a living room, {bedrooms} bedroom(s) and a kitchen.")
        assumptions.append("Living room: sofa (3 seats or more) and TV unit. Each bedroom: bed, mattress, wardrobe. "
                           "Kitchen: refrigerator and a dining table that seats 4 or more.")
        if not any(r.width_cm or r.depth_cm for r in rooms):
            assumptions.append("No room dimensions were given, so no size limits were applied; products whose size "
                               "is stated are not checked against a room.")
        return {"rooms": [asdict(r) for r in rooms], "slots": [s.to_dict() for s in slots],
                "assumptions": assumptions}


class ProductResearchAgent:
    agent_id = "product_research"

    def __init__(self, catalog: ProductCatalog) -> None:
        self._catalog = catalog

    def handle(self, task: str, data: dict[str, Any]) -> dict[str, Any]:
        if task != "research":
            raise ValueError(f"unknown task {task!r}")
        profile = CATEGORIES[data["category"]]
        listings = self._catalog.listings(profile.id)
        kept, dropped = [], []
        for record in listings.records:
            reason = profile.accessory_reason(str(record.get("name") or ""), float(record.get("price") or 0))
            if reason:
                dropped.append({"name": record.get("name"), "price": record.get("price"), "reason": reason})
            else:
                kept.append(record)
        return {"category": profile.id, "records": kept, "dropped": dropped, "origin": listings.origin,
                "retrieved_at": listings.retrieved_at, "query": listings.query,
                "rejected_at_parse": listings.rejected, "notes": listings.notes,
                "found": len(listings.records)}


def _option(evaluation: CandidateEvaluation, record: dict[str, Any], state: str) -> dict[str, Any]:
    b = evaluation.breakdown
    return {
        "state": state, "rank": evaluation.rank, "product_id": evaluation.product_id, "name": evaluation.name,
        "price": evaluation.price, "utility": b.total_utility if b else None,
        "completeness": b.completeness if b else None, "unavailable": evaluation.unavailable,
        "components": b.components if b else {}, "weights": b.weights if b else {},
        "contributions": b.contributions if b else {}, "excluded": b.excluded if b else {},
        "availability": evaluation.availability, "gaps": list(evaluation.gaps), "fit": evaluation.fit_label,
        "reasons": list(evaluation.reasons), "record": record,
    }


class ComparisonAgent:
    agent_id = "comparison"

    def __init__(self) -> None:
        self._optimizer = UtilityOptimizer()

    def handle(self, task: str, data: dict[str, Any]) -> dict[str, Any]:
        if task != "compare":
            raise ValueError(f"unknown task {task!r}")
        slot = Slot.from_dict(data["slot"])
        ceiling = float(data["ceiling"])
        records = [dict(r) for r in data["records"]]
        overrides: dict[str, dict[str, Any]] = data.get("overrides") or {}
        for record in records:                       # events (out of stock, price change) override the snapshot
            record.update(overrides.get(record["product_id"], {}))
        if ceiling <= 0:
            return {"slot_id": slot.slot_id, "selected": None, "options": [], "inspected": len(records),
                    "rejected": len(records), "note": "no budget is left for this item"}
        requirements = {"budget": ceiling, "min_seating": slot.min_seating if slot.profile.needs_seating else 1,
                        "max_width_cm": slot.max_width_cm, "max_depth_cm": slot.max_depth_cm,
                        "preferred_style": data.get("style"), "preferred_colour": data.get("colour")}
        result = self._optimizer.optimize(records, requirements)
        by_id = {r["product_id"]: r for r in records}
        options = [_option(e, by_id.get(e.product_id, {}), "confirmed") for e in result.ranking[:5]]
        options += [_option(e, by_id.get(e.product_id, {}), "unverified") for e in result.unverified[:5]]
        selected = options[0] if result.ranking else (_option(result.unverified[0], by_id.get(
            result.unverified[0].product_id, {}), "unverified") if result.unverified else None)
        rejected = [{"name": e.name, "price": e.price, "reasons": e.reasons[:2]}
                    for e in result.evaluations if e.status in ("rejected", "invalid")][:8]
        return {"slot_id": slot.slot_id, "selected": selected, "options": options, "inspected": result.total_inspected,
                "eligible": result.eligible_count, "unverified": result.unverified_count,
                "rejected": result.rejected_count, "rejected_examples": rejected, "explanation": result.explanation,
                "ceiling": ceiling}


class BudgetAgent:
    agent_id = "budget"

    def handle(self, task: str, data: dict[str, Any]) -> dict[str, Any]:
        slots = [Slot.from_dict(s) for s in data.get("slots", [])]
        if task == "allocate":
            return {"ceilings": budget_mod.allocate(float(data["budget"]), slots)}
        if task == "reallocate":
            return {"ceilings": budget_mod.reallocate(float(data["budget"]), slots, data.get("settled") or {},
                                                      set(data.get("open") or []))}
        if task == "summarise":
            return asdict(budget_mod.summarise(float(data["budget"]), slots, data.get("prices") or {}))
        raise ValueError(f"unknown task {task!r}")
