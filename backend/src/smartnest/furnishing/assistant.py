"""The chat assistant: turns messages into plan events and plan facts into replies.

    message --parse (rules, optionally Gemini)--> event --Coordinator--> new plan --facts--> reply text

Every reply is built from the plan's own numbers by deterministic code.  If a Gemini service is supplied it may
*reword* that text, but its wording is accepted only if it introduces no number that was not in the original (the same
rule the single-item explanations follow); otherwise the deterministic text is used.  Gemini never chooses a product,
a price or a budget.
"""

from __future__ import annotations

import logging
import re
import threading
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from smartnest.furnishing.categories import CATEGORIES
from smartnest.furnishing.coordinator import FurnishingCoordinator, FurnishingRequest, Plan, PlanError
from smartnest.furnishing.intent import ParsedMessage, parse_message
from smartnest.furnishing.money import inr

logger = logging.getLogger(__name__)

MAX_MESSAGE_CHARS = 1000
HELP_TEXT = ("Tell me about your home and I'll plan it. For example: \"I just moved into a 2BHK apartment. "
             "My furnishing budget is ₹2 lakh. I prefer minimalist furniture. Help me furnish the entire apartment.\"")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Reply:
    text: str
    kind: str                       # the parsed action, or "error"
    changed: bool = False           # whether the plan changed
    needs: list[str] = field(default_factory=list)
    wording: str = "rules"          # "rules" | "gemini"


class HomeSession:
    """One user's conversation and plan.  The event list is enough to rebuild the plan exactly (it is deterministic)."""

    def __init__(self, coordinator: FurnishingCoordinator, session_id: str | None = None) -> None:
        self.id = session_id or secrets.token_urlsafe(12)
        self.coordinator = coordinator
        self.history: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []
        self.draft: dict[str, Any] = {}
        self.request: FurnishingRequest | None = None
        self.created_at = _now()
        self.lock = threading.RLock()

    @property
    def plan(self) -> Plan | None:
        return self.coordinator.plan

    def say(self, role: str, text: str, kind: str = "") -> None:
        self.history.append({"role": role, "text": text, "kind": kind, "time": _now()})
        del self.history[:-200]

    # ── Creating and changing the plan (also used by the API directly) ───

    def start(self, request: FurnishingRequest) -> Plan:
        plan = self.coordinator.create_plan(request)
        self.request = request
        self.events = []
        return plan

    def apply(self, event: dict[str, Any]) -> Plan:
        plan = self.coordinator.handle_event(event)
        self.events.append(dict(event))
        return plan


class ChatAssistant:
    def __init__(self, gemini: Any = None) -> None:
        self._gemini = gemini            # a GeminiService, or None

    # ── Entry point ──────────────────────────────────────────────────────

    def respond(self, session: HomeSession, message: str) -> Reply:
        if not isinstance(message, str) or not message.strip():
            return Reply("Please type a message.", "error")
        if len(message) > MAX_MESSAGE_CHARS:
            return Reply(f"That message is too long (over {MAX_MESSAGE_CHARS} characters).", "error")
        with session.lock:
            session.say("user", message.strip())
            parsed = parse_message(message, has_plan=session.plan is not None)
            parsed = self._fill_from_gemini(parsed, session)
            try:
                reply = self._dispatch(session, parsed)
            except PlanError as exc:
                reply = Reply(f"I couldn't do that: {exc}", "error")
            reply = self._reword(reply)
            session.say("assistant", reply.text, reply.kind)
            return reply

    # ── Understanding ────────────────────────────────────────────────────

    def _fill_from_gemini(self, parsed: ParsedMessage, session: HomeSession) -> ParsedMessage:
        """If the rules understood nothing, ask Gemini for a budget / style / colour (validated like any other value)."""
        if self._gemini is None or parsed.action not in ("unknown", "help"):
            return parsed
        try:
            ext = self._gemini.extract_requirements(parsed.text)
        except Exception as exc:  # Gemini down or invalid output: carry on with the rules only
            logger.info("Gemini understanding unavailable: %s", type(exc).__name__)
            return parsed
        budget = ext.total_budget or ext.sofa_budget
        style, colour = ext.preferred_style, ext.preferred_colour
        if budget or style or colour:
            parsed.budget = parsed.budget or budget
            parsed.style = parsed.style or (style.lower() if style else None)
            parsed.colour = parsed.colour or (colour.lower() if colour else None)
            if session.plan is None:
                parsed.action = "new_request" if (parsed.bedrooms or session.draft.get("bedrooms")) else "help"
            elif budget:
                parsed.action = "set_budget"
            elif style or colour:
                parsed.action = "set_preference"
            parsed.notes.append("understood with Gemini")
        return parsed

    # ── Actions ──────────────────────────────────────────────────────────

    def _dispatch(self, session: HomeSession, p: ParsedMessage) -> Reply:
        handler = getattr(self, f"_do_{p.action}", None)
        return handler(session, p) if handler else Reply(HELP_TEXT, "help")

    def _do_help(self, session: HomeSession, p: ParsedMessage) -> Reply:
        if session.plan is None and (session.draft or p.bedrooms or p.budget):
            return self._do_new_request(session, p)
        return Reply(HELP_TEXT if session.plan is None else
                     "You can ask me to change the budget, switch style, remove an item, mark something out of stock, "
                     "explain a choice, compare options, or show a room.", "help")

    def _do_unknown(self, session: HomeSession, p: ParsedMessage) -> Reply:
        return self._do_help(session, p)

    def _do_new_request(self, session: HomeSession, p: ParsedMessage) -> Reply:
        d = session.draft
        for key, value in (("bedrooms", p.bedrooms), ("budget", p.budget), ("style", p.style), ("colour", p.colour)):
            if value is not None:
                d[key] = value
        missing = [name for name, label in (("bedrooms", "how many bedrooms (e.g. 2BHK)"),
                                            ("budget", "your total budget (e.g. ₹2 lakh)")) if d.get(name) is None]
        if missing:
            ask = " and ".join(label for name, label in (("bedrooms", "how many bedrooms (e.g. 2BHK)"),
                                                          ("budget", "your total budget (e.g. ₹2 lakh)"))
                               if name in missing)
            return Reply(f"Got it. I still need {ask}.", "new_request", needs=missing)
        plan = session.start(FurnishingRequest(d["budget"], d["bedrooms"], d.get("style"), d.get("colour")))
        session.draft = {}
        return Reply(self.describe_plan(plan, intro=True), "new_request", changed=True)

    def _need_plan(self, session: HomeSession) -> Reply | None:
        return None if session.plan else Reply("There is no plan yet. " + HELP_TEXT, "help")

    def _do_set_budget(self, session: HomeSession, p: ParsedMessage) -> Reply:
        if (r := self._need_plan(session)):
            return r
        before = session.plan.summary
        old = session.request.total_budget if session.request else None
        plan = session.apply({"type": "budget_change", "total_budget": p.budget})
        text = f"Budget changed{f' from {inr(old)}' if old else ''} to {inr(p.budget)}. " + self._delta(before, plan)
        if abs(plan.summary["spent"] - before["spent"]) < 0.01 and old and p.budget > old:
            text += (" My picks were already the best-scoring options, so the extra money stays unspent - "
                     "you can upgrade any item from its alternatives.")
        return Reply(text, "set_budget", changed=True)

    def _do_set_preference(self, session: HomeSession, p: ParsedMessage) -> Reply:
        if (r := self._need_plan(session)):
            return r
        event = {"type": "preference_change"}
        if p.style:
            event["style"] = p.style
        if p.colour:
            event["colour"] = p.colour
        plan = session.apply(event)
        said = " and ".join(x for x in (p.style, p.colour) if x)
        return Reply(f"Noted: you prefer {said}. Real listings rarely state style or colour, so this only changes the "
                     f"ranking where a product actually states it. " + self._delta(None, plan), "set_preference", changed=True)

    def _slots_matching(self, session: HomeSession, p: ParsedMessage) -> list[str]:
        out = []
        for slot in session.coordinator._slots:
            if slot.slot_id in session.coordinator._removed:
                continue
            if p.categories and slot.profile.id not in p.categories:
                continue
            if p.room and slot.room_id != p.room:
                continue
            out.append(slot.slot_id)
        return out

    def _do_remove_item(self, session: HomeSession, p: ParsedMessage) -> Reply:
        if (r := self._need_plan(session)):
            return r
        ids = self._slots_matching(session, p)
        if not ids:
            return Reply("There is nothing like that left in the plan.", "remove_item")
        plan = None
        for sid in ids:
            plan = session.apply({"type": "remove_item", "slot_id": sid})
        names = ", ".join(sid.replace("_", " ").replace(".", " - ") for sid in ids)
        return Reply(f"Removed from the plan: {names}. " + self._delta(None, plan), "remove_item", changed=True)

    def _do_out_of_stock(self, session: HomeSession, p: ParsedMessage) -> Reply:
        if (r := self._need_plan(session)):
            return r
        ids = [sid for sid in self._slots_matching(session, p) if (session.plan.lines[sid].get("product"))]
        if not ids:
            return Reply("I have no chosen product like that to mark out of stock.", "out_of_stock")
        lines = []
        plan = session.plan
        for sid in ids:
            line = plan.lines.get(sid)
            product = (line or {}).get("product")
            if not product:
                continue
            old_name = product["name"]
            plan = session.apply({"type": "out_of_stock", "product_id": product["product_id"]})
            new = plan.lines[sid].get("product")
            lines.append(f"{old_name} is out of stock; " + (f"I switched to {new['name']} at {inr(new['price'])}."
                                                              if new else "no other option fits, so that item is now unfilled."))
        return Reply(" ".join(lines) + " " + self._delta(None, plan), "out_of_stock", changed=True)

    def _do_why(self, session: HomeSession, p: ParsedMessage) -> Reply:
        if (r := self._need_plan(session)):
            return r
        ids = [s for s in self._slots_matching(session, p) if session.plan.lines.get(s)]
        if not ids:
            return Reply("Which item should I explain? For example: \"why this sofa?\"", "why")
        parts = []
        for sid in ids[:2]:
            line = session.plan.lines[sid]
            prod = line.get("product")
            if not prod:
                parts.append(f"{line['label']}: {line['why']}")
                continue
            contrib = ", ".join(f"{k} {v:.1f}" for k, v in (line.get("contributions") or {}).items())
            parts.append(f"{line['label']}: {prod['name']} at {inr(prod['price'])}. {line['why']}"
                         + (f" Score points: {contrib}." if contrib else ""))
        return Reply(" ".join(parts), "why")

    def _do_compare(self, session: HomeSession, p: ParsedMessage) -> Reply:
        if (r := self._need_plan(session)):
            return r
        ids = [s for s in self._slots_matching(session, p) if session.plan.lines.get(s)]
        if not ids:
            return Reply("Which item should I compare? For example: \"compare wardrobes\".", "compare")
        line = session.plan.lines[ids[0]]
        options = ([line["product"]] if line.get("product") else []) + line.get("alternatives", [])
        if len(options) < 2:
            return Reply(f"{line['label']}: there is only one option within the budget.", "compare")
        rows = "; ".join(f"{o['name']} at {inr(o['price'])}" for o in options[:4])
        return Reply(f"Options for {line['label']}: {rows}. The first is my pick; use the compare view for details.",
                     "compare")

    def _do_budget_status(self, session: HomeSession, p: ParsedMessage) -> Reply:
        if (r := self._need_plan(session)):
            return r
        s = session.plan.summary
        return Reply(f"You have spent {inr(s['spent'])} of {inr(s['total_budget'])} ({s['utilisation']:.0%}); "
                     f"{inr(s['remaining'])} is left." + (f" {s['unfilled_count']} item(s) are unfilled."
                                                         if s["unfilled_count"] else ""), "budget_status")

    def _do_overview(self, session: HomeSession, p: ParsedMessage) -> Reply:
        if (r := self._need_plan(session)):
            return r
        if p.room:
            lines = [ln for ln in session.plan.lines.values() if ln["room_id"] == p.room]
            if not lines:
                return Reply("That room is not in the plan.", "overview")
            return Reply(f"{lines[0]['room']}: " + "; ".join(
                f"{ln['category_label']} - " + (f"{ln['product']['name']} ({inr(ln['product']['price'])})"
                                                if ln.get("product") else "not filled") for ln in lines) + ".", "overview")
        return Reply(self.describe_plan(session.plan), "overview")

    # ── Cart ─────────────────────────────────────────────────────────────

    @staticmethod
    def _cart_line(cart: dict[str, Any]) -> str:
        return (f"Your cart has {cart['count']} item(s) totalling {inr(cart['total'])} "
                f"({inr(cart['remaining'])} of your budget left)."
                + (f" Stock is unconfirmed for {cart['unverified']} line(s)." if cart["unverified"] else ""))

    def _picks_for(self, session: HomeSession, p: ParsedMessage) -> list[tuple[str, str]]:
        """(slot id, product id) for the plan items the message refers to."""
        return [(sid, session.plan.lines[sid]["product"]["product_id"])
                for sid in self._slots_matching(session, p)
                if session.plan.lines.get(sid) and session.plan.lines[sid].get("product")]

    def _do_cart_add(self, session: HomeSession, p: ParsedMessage) -> Reply:
        if (r := self._need_plan(session)):
            return r
        co = session.coordinator
        wants_all = bool(re.search(r"\b(everything|all|whole|entire)\b", p.text, re.IGNORECASE)) and not p.categories
        if wants_all and not p.room:
            session.apply({"type": "cart_add_plan"})
            return Reply("Added every chosen product to your cart. " + self._cart_line(co.cart_view()), "cart_add", changed=True)
        picks = self._picks_for(session, p) if (p.categories or p.room) else []
        if not picks:
            return Reply("Which item should I add? For example: \"add the sofa to my cart\" or \"add everything to my cart\".",
                         "cart_add")
        added, already = [], []
        for sid, pid in picks:
            if pid in co.cart or pid in [a[1] for a in added]:
                already.append(session.plan.lines[sid]["category_label"].lower())
                continue
            session.apply({"type": "cart_add", "product_id": pid, "qty": 1})
            added.append((sid, pid))
        text = ""
        if added:
            text += "Added to your cart: " + ", ".join(session.plan.lines[s]["product"]["name"][:50] for s, _ in added) + ". "
        if already:
            text += f"Already in your cart: {', '.join(sorted(set(already)))}. "
        return Reply(text + self._cart_line(co.cart_view()), "cart_add", changed=bool(added))

    def _do_cart_remove(self, session: HomeSession, p: ParsedMessage) -> Reply:
        if (r := self._need_plan(session)):
            return r
        co = session.coordinator
        gone = []
        for _, pid in (self._picks_for(session, p) if (p.categories or p.room) else []):
            if pid in co.cart:
                session.apply({"type": "cart_remove", "product_id": pid})
                gone.append(pid)
        if not gone:
            return Reply("That isn't in your cart.", "cart_remove")
        return Reply("Removed from your cart. " + self._cart_line(co.cart_view()), "cart_remove", changed=True)

    def _do_cart_clear(self, session: HomeSession, p: ParsedMessage) -> Reply:
        if (r := self._need_plan(session)):
            return r
        if not session.coordinator.cart:
            return Reply("Your cart is already empty.", "cart_clear")
        session.apply({"type": "cart_clear"})
        return Reply("Your cart is empty.", "cart_clear", changed=True)

    def _do_cart_show(self, session: HomeSession, p: ParsedMessage) -> Reply:
        if (r := self._need_plan(session)):
            return r
        cart = session.coordinator.cart_view()
        if not cart["lines"]:
            return Reply("Your cart is empty. Say \"add everything to my cart\" or press Add to cart on a product.", "cart_show")
        rows = "; ".join(f"{ln['qty']} x {ln['product']['name'][:40]} ({inr(ln['line_total'])})" for ln in cart["lines"][:6])
        return Reply(f"{rows}. " + self._cart_line(cart), "cart_show")

    # ── Wording ──────────────────────────────────────────────────────────

    @staticmethod
    def _delta(before: dict[str, Any] | None, plan: Plan) -> str:
        s = plan.summary
        return (f"The plan now costs {inr(s['spent'])} of {inr(s['total_budget'])} "
                f"({inr(s['remaining'])} left)" + (f"; {s['unfilled_count']} item(s) could not be filled." if s["unfilled_count"] else "."))

    @staticmethod
    def describe_plan(plan: Plan, *, intro: bool = False) -> str:
        s, r = plan.summary, plan.request
        head = (f"I planned your {r.bedrooms}BHK: {s['items']} items across {len(plan.rooms)} rooms, "
                f"{inr(s['spent'])} of your {inr(s['total_budget'])} budget ({s['utilisation']:.0%}), "
                f"{inr(s['remaining'])} left.")
        notes = []
        if s["unverified"]:
            notes.append(f"Stock is not confirmed for {s['unverified']} item(s), so I marked them unverified - "
                         "check the retailer before buying.")
        if s["unfilled_count"]:
            notes.append(f"{s['unfilled_count']} item(s) could not be filled within the budget; I did not relax any limit.")
        origins = {ln["data_origin"] for ln in plan.lines.values()}
        if "simulated" in origins:
            notes.append("Some listings are SIMULATED samples, not real products.")
        extra = " You can ask me to change the budget, remove an item, or explain any choice." if intro else ""
        return " ".join([head, *notes]) + extra

    def _reword(self, reply: Reply) -> Reply:
        """Let Gemini phrase the reply naturally, but only if it adds no number that is not already in it."""
        if self._gemini is None or reply.kind in ("error", "help") or len(reply.text) < 40:
            return reply
        try:
            from smartnest.integrations.gemini.schemas import RecommendationExplanation
            out = self._gemini._structured(
                "Rewrite the assistant reply below so it reads naturally and warmly in 2-4 short sentences. "
                "Keep EVERY number, rupee amount and product name exactly as written, add no new facts or numbers, and "
                "keep every caution (unverified stock, simulated data, unfilled items). "
                'Return JSON {"text": "..."}.\n\nREPLY:\n' + reply.text,
                RecommendationExplanation, "You rewrite text without changing facts.", what="reply wording")
        except Exception as exc:
            logger.info("reply wording unavailable: %s", type(exc).__name__)
            return reply
        if self._numbers(out.text) - self._numbers(reply.text):
            logger.warning("Gemini reply rejected: it introduced a number")
            return reply
        if ("unverified" in reply.text.lower() and "unverified" not in out.text.lower() and "not confirmed" not in out.text.lower()) \
                or ("SIMULATED" in reply.text and "simulated" not in out.text.lower()):
            return reply
        return Reply(out.text.strip(), reply.kind, reply.changed, reply.needs, "gemini")

    @staticmethod
    def _numbers(text: str) -> set[float]:
        return {float(m.replace(",", "")) for m in re.findall(r"\d[\d,]*(?:\.\d+)?", text)}
