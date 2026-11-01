"""The Coordinator: one furnishing plan, built by the agents and kept up to date by BDI-style replanning.

Roles
-----
    Coordinator       owns the plan.  It never ranks a product or does arithmetic on money itself; it asks the
                      Space Planning, Product Research, Comparison and Budget agents over the signed bus.
    Beliefs           what the coordinator holds true (budget, style, which products are known out of stock...),
                      kept in the same ``BeliefBase`` as the single-item BDI agent, so every change reports whether
                      anything really changed.
    Desire            complete every item of every room, inside the budget, with the best-scoring product.
    Intentions        one committed purchase per item (``adopted``); when a belief changes, an intention is
                      ``retained`` if it is still the best choice, ``replaced`` if a better valid one exists, or
                      ``invalidated`` (and the item re-planned) if its product is no longer valid.

Honesty rules carried over from the rest of SmartNest
-----------------------------------------------------
    * A confirmed purchase needs stock evidence.  A listing whose stock is not stated is shown as ``unverified`` -
      it is still counted against the budget, but is never labelled confirmed.
    * Item budgets are HARD ceilings given to the optimizer, so the plan total cannot exceed the budget.
    * Unfilled items stay unfilled (and say why) rather than breaking a constraint.
    * Simulated listings are labelled and have no buy link.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from smartnest.bdi.beliefs import BeliefBase
from smartnest.furnishing.activity import ActivityLog, AgentBus, AgentCallError
from smartnest.furnishing.agents import BudgetAgent, ComparisonAgent, ProductResearchAgent, SpacePlanningAgent
from smartnest.furnishing.money import inr
from smartnest.furnishing.catalog import ORIGIN_REAL, ORIGIN_SIMULATED, ProductCatalog
from smartnest.furnishing.rooms import Slot

COORD = "coordinator"
MAX_ROUNDS = 3


class PlanError(ValueError):
    """The request cannot be planned (bad budget, unknown home type, ...)."""


@dataclass
class FurnishingRequest:
    total_budget: float
    bedrooms: int
    style: str | None = None
    colour: str | None = None
    room_dimensions: dict[str, dict[str, float]] | None = None

    def validate(self) -> None:
        if isinstance(self.total_budget, bool) or not isinstance(self.total_budget, (int, float)) \
                or self.total_budget != self.total_budget or self.total_budget <= 0 or self.total_budget > 1e9:
            raise PlanError("the budget must be a positive number of rupees")
        if not isinstance(self.bedrooms, int) or isinstance(self.bedrooms, bool) or not 1 <= self.bedrooms <= 5:
            raise PlanError("bedrooms must be a whole number from 1 to 5")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _product_view(option: dict[str, Any]) -> dict[str, Any]:
    """What the interface may show about a chosen option (links are withheld for simulated data)."""
    r = option.get("record") or {}
    origin = r.get("data_origin") or ORIGIN_REAL
    listing_url, url_type = r.get("listing_url"), r.get("listing_url_type")
    simulated = origin == ORIGIN_SIMULATED
    return {
        "product_id": option["product_id"], "name": option["name"], "price": option["price"],
        "retailer": r.get("retailer"), "image_url": None if simulated else r.get("image_url"),
        "data_origin": origin, "simulated": simulated,
        "buy_url": None if simulated else listing_url,
        "link_label": ("Sample listing - not a real product" if simulated else
                       "Retailer page" if url_type == "retailer" else
                       "Google Shopping page" if url_type else "No link available"),
        "url_type": url_type, "rating": r.get("rating"), "review_count": r.get("review_count"),
        "seating_capacity": r.get("seating_capacity"), "retrieved_at": r.get("verified_at"),
        "availability": option.get("availability"),
    }


def _alternative(option: dict[str, Any]) -> dict[str, Any]:
    view = _product_view(option)
    view.update({"state": option["state"], "utility": option["utility"], "completeness": option["completeness"],
                 "gaps": option["gaps"]})
    return view


@dataclass
class Plan:
    request: FurnishingRequest
    assumptions: list[str]
    rooms: list[dict]
    lines: dict[str, dict[str, Any]]
    summary: dict[str, Any]
    data_notes: list[str]
    version: int = 1
    updated_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "request": {"total_budget": self.request.total_budget, "bedrooms": self.request.bedrooms,
                        "style": self.request.style, "colour": self.request.colour},
            "version": self.version, "updated_at": self.updated_at, "assumptions": self.assumptions,
            "rooms": self.rooms, "items": list(self.lines.values()), "summary": self.summary,
            "data_notes": self.data_notes,
        }


class FurnishingCoordinator:
    """Builds and maintains one home's furnishing plan."""

    def __init__(self, catalog: ProductCatalog, *, log: ActivityLog | None = None) -> None:
        self.log = log or ActivityLog()
        self.bus = AgentBus(self.log)
        self._agents = {a.agent_id: a for a in (SpacePlanningAgent(), ProductResearchAgent(catalog),
                                                ComparisonAgent(), BudgetAgent())}
        for agent_id, agent in self._agents.items():
            self.bus.register(agent_id, agent.handle)
        self.beliefs = BeliefBase()
        self.plan: Plan | None = None
        self.transitions: list[dict[str, Any]] = []
        self.shortlist: list[str] = []
        self._request: FurnishingRequest | None = None
        self._slots: list[Slot] = []
        self._research: dict[str, dict[str, Any]] = {}
        self._overrides: dict[str, dict[str, Any]] = {}
        self._removed: set[str] = set()
        self._choices: dict[str, str] = {}            # slot id -> product id the USER picked
        self._refused_choice: dict[str, str] = {}     # slot id -> why the user's pick could not be honoured
        self._assumptions: list[str] = []
        self._rooms: list[dict] = []

    # ── Public API ───────────────────────────────────────────────────────

    def create_plan(self, request: FurnishingRequest) -> Plan:
        request.validate()
        self._request = request
        self.log.record(COORD, "info", f"new request: {request.bedrooms}BHK, budget Rs {request.total_budget:,.0f}"
                        + (f", style {request.style}" if request.style else ""))
        self._set_beliefs(request)
        try:
            planned = self.bus.call("space_planning", "plan", {
                "bedrooms": request.bedrooms, "room_dimensions": request.room_dimensions},
                summary="asked Space Planning for the rooms and items")
        except AgentCallError as exc:
            raise PlanError(exc.reason) from None
        self._slots = [Slot.from_dict(s) for s in planned["slots"]]
        self._assumptions, self._rooms = planned["assumptions"], planned["rooms"]
        self.log.record("space_planning", "decision",
                        f"{len(self._slots)} items across {len(self._rooms)} rooms", details={"slots": [
                            s.slot_id for s in self._slots]})
        self.plan = self._solve(previous=None)
        return self.plan

    def handle_event(self, event: dict[str, Any]) -> Plan:
        """Apply a change in the world or from the user and re-plan only what it affects."""
        if self.plan is None or self._request is None:
            raise PlanError("there is no plan yet")
        kind = event.get("type")
        previous = {k: copy.deepcopy(v) for k, v in self.plan.lines.items()}
        if kind == "out_of_stock":
            pid = self._require_product(event)
            self._overrides.setdefault(pid, {})["availability"] = "out_of_stock"
            self.beliefs.update(f"product:{pid}", "availability", "out_of_stock", source="event")
            self.log.record(COORD, "replan", f"event: {pid} is out of stock", details=event)
        elif kind == "price_change":
            pid = self._require_product(event)
            price = event.get("price")
            if isinstance(price, bool) or not isinstance(price, (int, float)) or price != price or price <= 0:
                raise PlanError("price must be a positive number")
            self._overrides.setdefault(pid, {})["price"] = float(price)
            self.beliefs.update(f"product:{pid}", "price", float(price), source="event")
            self.log.record(COORD, "replan", f"event: {pid} price is now Rs {price:,.0f}", details=event)
        elif kind == "budget_change":
            new = event.get("total_budget")
            candidate = FurnishingRequest(new, self._request.bedrooms, self._request.style, self._request.colour,
                                          self._request.room_dimensions)
            candidate.validate()
            self._request = candidate
            self.beliefs.update("user", "total_budget", float(new), source="user")
            self.log.record(COORD, "replan", f"user changed the budget to Rs {new:,.0f}", details=event)
        elif kind == "preference_change":
            style = event.get("style", self._request.style)
            colour = event.get("colour", self._request.colour)
            self._request = FurnishingRequest(self._request.total_budget, self._request.bedrooms, style, colour,
                                              self._request.room_dimensions)
            self.beliefs.update("user", "style", style, source="user")
            self.beliefs.update("user", "colour", colour, source="user")
            self.log.record(COORD, "replan", f"user changed preferences: style={style}, colour={colour}", details=event)
        elif kind == "remove_item":
            slot_id = event.get("slot_id")
            if slot_id not in {s.slot_id for s in self._slots}:
                raise PlanError(f"unknown item {slot_id!r}")
            self._removed.add(slot_id)
            self.log.record(COORD, "replan", f"user removed {slot_id} from the plan", details=event)
        elif kind in ("choose_product", "clear_choice"):
            return self._handle_choice(event, previous)
        else:
            raise PlanError(f"unknown event type {kind!r}")
        self.plan = self._solve(previous=previous)
        return self.plan

    def _handle_choice(self, event: dict[str, Any], previous: dict[str, dict[str, Any]]) -> Plan:
        slot_id = event.get("slot_id")
        slot = next((s for s in self._slots if s.slot_id == slot_id and s.slot_id not in self._removed), None)
        if slot is None:
            raise PlanError(f"unknown item {slot_id!r}")
        if event["type"] == "clear_choice":
            self._choices.pop(slot_id, None)
            self.log.record(COORD, "replan", f"user released their choice for {slot.label}", details=event)
        else:
            pid = event.get("product_id")
            if pid not in {r["product_id"] for r in self._research[slot.profile.id]["records"]}:
                raise PlanError(f"{pid!r} is not a {slot.profile.label.lower()} SmartNest has found")
            old = self._choices.get(slot_id)
            self._choices[slot_id] = pid
            self._refused_choice.pop(slot_id, None)
            self.log.record(COORD, "replan", f"user chose {pid} for {slot.label}", details=event)
        candidate = self._solve(previous=previous)
        if slot_id in self._refused_choice:                      # the pick is not valid: keep the plan as it was
            reason = self._refused_choice.pop(slot_id)
            if event["type"] == "choose_product":
                if old is None:
                    self._choices.pop(slot_id, None)
                else:
                    self._choices[slot_id] = old
            self.log.record(COORD, "warning", f"choice refused: {reason}")
            raise PlanError(reason)
        self.plan = candidate
        return self.plan

    def toggle_shortlist(self, product_id: str) -> list[str]:
        known = {o["product_id"] for line in (self.plan.lines.values() if self.plan else [])
                 for o in [line.get("product") or {}, *line.get("alternatives", [])] if o}
        if product_id not in known:
            raise PlanError(f"unknown product {product_id!r}")
        if product_id in self.shortlist:
            self.shortlist.remove(product_id)
        else:
            self.shortlist.append(product_id)
        return list(self.shortlist)

    # ── Internals ────────────────────────────────────────────────────────

    def _require_product(self, event: dict[str, Any]) -> str:
        pid = event.get("product_id")
        known = {p for r in self._research.values() for p in (x["product_id"] for x in r["records"])}
        if pid not in known:
            raise PlanError(f"unknown product {pid!r}")
        return pid

    def _set_beliefs(self, req: FurnishingRequest) -> None:
        self.beliefs.update("user", "total_budget", float(req.total_budget), source="user")
        self.beliefs.update("user", "bedrooms", req.bedrooms, source="user")
        self.beliefs.update("user", "style", req.style, source="user")
        self.beliefs.update("user", "colour", req.colour, source="user")

    def _research_category(self, category: str) -> dict[str, Any]:
        if category not in self._research:
            result = self.bus.call("product_research", "research", {"category": category},
                                   summary=f"asked Product Research for {category} listings")
            self._research[category] = result
            origin = {ORIGIN_REAL: "real SerpApi snapshot", ORIGIN_SIMULATED: "SIMULATED sample"}.get(
                result["origin"], "no data")
            self.log.record("product_research", "decision",
                            f"{category}: {len(result['records'])} usable of {result['found']} listings ({origin})",
                            details={"dropped": result["dropped"][:5], "notes": result["notes"]})
        return self._research[category]

    def _solve(self, previous: dict[str, dict[str, Any]] | None) -> Plan:
        req = self._request
        assert req is not None
        slots = [s for s in self._slots if s.slot_id not in self._removed]
        slot_dicts = [s.to_dict() for s in slots]
        budget = float(req.total_budget)
        ceilings = self.bus.call("budget", "allocate", {"budget": budget, "slots": slot_dicts},
                                 summary="asked Budget to split the budget over the items")["ceilings"]
        self.log.record("budget", "decision", f"item ceilings sum to Rs {sum(ceilings.values()):,.0f}")
        for category in sorted({s.profile.id for s in slots}):
            self._research_category(category)

        results: dict[str, dict[str, Any]] = {}
        open_ids = {s.slot_id for s in slots}
        for round_no in range(MAX_ROUNDS):
            for slot in slots:
                if slot.slot_id not in open_ids:
                    continue
                res = self.bus.call("comparison", "compare", {
                    "slot": slot.to_dict(), "ceiling": ceilings[slot.slot_id],
                    "records": self._research[slot.profile.id]["records"], "style": req.style,
                    "colour": req.colour, "overrides": self._overrides},
                    summary=f"asked Comparison to rank {slot.label} under Rs {ceilings[slot.slot_id]:,.0f}")
                results[slot.slot_id] = res
                if res["selected"] is not None:
                    open_ids.discard(slot.slot_id)
                    self.log.record("comparison", "decision",
                                    f"{slot.label}: {res['selected']['name']} at Rs {res['selected']['price']:,.0f} "
                                    f"({res['selected']['state']}, utility {res['selected']['utility']:.1f})",
                                    details={"inspected": res["inspected"], "eligible": res.get("eligible"),
                                             "unverified": res.get("unverified")})
            if not open_ids:
                break
            settled = {sid: r["selected"]["price"] for sid, r in results.items() if r["selected"]}
            ceilings.update(self.bus.call("budget", "reallocate", {
                "budget": budget, "slots": slot_dicts, "settled": settled, "open": sorted(open_ids)},
                summary="asked Budget to share unspent money with unfilled items")["ceilings"])
            self.log.record("budget", "decision", f"{len(open_ids)} item(s) unfilled; re-sharing the leftover budget")

        self._apply_choices(slots, results, budget)

        lines: dict[str, dict[str, Any]] = {}
        prices: dict[str, float] = {}
        for slot in slots:
            res = results.get(slot.slot_id) or {"selected": None, "options": []}
            sel = res["selected"]
            research = self._research[slot.profile.id]
            line = {"slot_id": slot.slot_id, "room_id": slot.room_id, "room": slot.room_label,
                    "category": slot.profile.id, "category_label": slot.profile.label, "label": slot.label,
                    "ceiling": ceilings[slot.slot_id], "inspected": res.get("inspected", 0),
                    "dropped_accessories": len(research["dropped"]), "data_origin": research["origin"],
                    "rejected_examples": res.get("rejected_examples", [])}
            if sel is None:
                line.update(status="unfilled", product=None, utility=None, completeness=None, alternatives=[],
                            why=(f"No {slot.profile.label.lower()} could be chosen within {inr(ceilings[slot.slot_id])}"
                                 f" from {res.get('inspected', 0)} listing(s); nothing was relaxed to force a match."))
            else:
                prices[slot.slot_id] = float(sel["price"])
                line.update(status=sel["state"], user_choice=bool(res.get("user_choice")), product=_product_view(sel), utility=sel["utility"],
                            completeness=sel["completeness"], unavailable=sel["unavailable"], gaps=sel["gaps"],
                            fit=sel["fit"], components=sel["components"], weights=sel["weights"],
                            contributions=sel["contributions"],
                            alternatives=[_alternative(o) for o in res["options"] if o["product_id"] != sel["product_id"]][:4],
                            why=self._why(slot, sel, res))
            lines[slot.slot_id] = line

        summary = self.bus.call("budget", "summarise", {"budget": budget, "slots": slot_dicts, "prices": prices},
                                summary="asked Budget for the spend analytics")
        summary["confirmed"] = sum(1 for ln in lines.values() if ln["status"] == "confirmed")
        summary["unverified"] = sum(1 for ln in lines.values() if ln["status"] == "unverified")
        summary["unfilled_count"] = sum(1 for ln in lines.values() if ln["status"] == "unfilled")
        summary["items"] = len(lines)
        self.log.record("budget", "decision", f"spend Rs {summary['spent']:,.0f} of Rs {budget:,.0f} "
                        f"({summary['utilisation']:.0%}); within budget: {summary['within_budget']}")

        notes = sorted({n for c in {s.profile.id for s in slots} for n in self._research[c]["notes"]})
        self._record_transitions(previous, lines)
        version = (self.plan.version + 1) if self.plan else 1
        return Plan(req, self._assumptions, self._rooms, lines, summary, notes, version)

    def _apply_choices(self, slots: list[Slot], results: dict[str, dict[str, Any]], budget: float) -> None:
        """Let the user's own picks replace the automatic ones - only if the optimizer still accepts them and the
        plan stays inside the total budget.  A refused pick is reported, never silently bent."""
        by_id = {s.slot_id: s for s in slots}
        for slot_id, pid in list(self._choices.items()):
            slot = by_id.get(slot_id)
            if slot is None:
                continue
            record = next((r for r in self._research[slot.profile.id]["records"] if r["product_id"] == pid), None)
            others = sum(r["selected"]["price"] for sid, r in results.items() if sid != slot_id and r.get("selected"))
            room = round(budget - others, 2)
            current = results.get(slot_id) or {"options": []}
            res = self.bus.call("comparison", "compare", {
                "slot": slot.to_dict(), "ceiling": room, "records": [record] if record else [],
                "style": self._request.style, "colour": self._request.colour, "overrides": self._overrides},
                summary=f"asked Comparison to validate the user's choice for {slot.label} (room left Rs {room:,.0f})")
            if res["selected"] is None:
                why = (res.get("rejected_examples") or [{}])[0].get("reasons") or ["it is not valid"]
                self._refused_choice[slot_id] = (f"that product cannot be bought for {slot.label}: {why[0]}"
                                                 if room > 0 else "there is no budget left for it")
                continue
            chosen = res["selected"]
            alternatives = [o for o in current.get("options", []) if o["product_id"] != pid]
            results[slot_id] = dict(current, selected=chosen, user_choice=True, options=[chosen, *alternatives],
                                    inspected=current.get("inspected", res["inspected"]), ceiling=room)

    @staticmethod
    def _why(slot: Slot, sel: dict[str, Any], res: dict[str, Any]) -> str:
        if res.get("user_choice"):
            return (f"Chosen by you. SmartNest checked it: utility {sel['utility']:.1f}/100, "
                    f"price {inr(sel['price'])} fits the remaining budget."
                    + (" Stock is not confirmed - check the retailer before buying." if sel["state"] == "unverified" else ""))
        sentence = (f"Highest utility ({sel['utility']:.1f}/100) among {res['inspected']} {slot.profile.label.lower()} "
                    f"listing(s) priced within {inr(res['ceiling'])}.")
        if sel["unavailable"]:
            sentence += f" Not stated by the listing, so not scored: {', '.join(sel['unavailable'])}."
        if sel["state"] == "unverified":
            sentence += " Stock is not confirmed - check the retailer before buying."
        return sentence

    def _record_transitions(self, previous: dict[str, dict[str, Any]] | None, lines: dict[str, dict[str, Any]]) -> None:
        for slot_id, line in lines.items():
            now = (line.get("product") or {}).get("product_id")
            before_line = (previous or {}).get(slot_id)
            before = ((before_line or {}).get("product") or {}).get("product_id")
            if previous is None:
                kind = "adopted" if now else "unfilled"
                reason = "initial plan"
            elif before == now:
                continue                                    # intention retained: nothing to report
            elif before and now:
                kind, reason = "replaced", "the previous choice is no longer the best valid option"
            elif before and not now:
                kind, reason = "invalidated", "the previous choice is no longer valid and no replacement fits"
            else:
                kind, reason = "adopted", "an option became available"
            self.transitions.append({"time": _now(), "slot_id": slot_id, "label": line["label"], "kind": kind,
                                     "before": before, "after": now, "reason": reason})
            if previous is not None:
                self.log.record(COORD, "replan", f"{line['label']}: {kind} ({before} -> {now})")
        if previous is not None and all(((previous.get(k) or {}).get("product") or {}).get("product_id")
                                        == (v.get("product") or {}).get("product_id") for k, v in lines.items()):
            self.log.record(COORD, "replan", "no intention changed: every existing choice is still the best")
