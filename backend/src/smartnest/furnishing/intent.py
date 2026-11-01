"""Understanding what the user typed, deterministically.

A small, auditable parser for the things a person says when furnishing a home: the home type ("2BHK"), a budget
("₹2 lakh", "Rs 1,50,000", "80k"), a style ("minimalist"), a colour, and later commands ("increase the budget to 3
lakh", "I don't need a TV unit", "the sofa is out of stock", "why this sofa?").  It never guesses a number: money is
read only when it carries a currency marker, a unit (lakh/crore/k) or the word "budget" next to it.

Gemini, when configured, is used only for messages this parser cannot understand at all, and its answer is validated
like every other value (see :mod:`smartnest.furnishing.assistant`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from smartnest.furnishing.categories import CATEGORIES
from smartnest.furnishing.rooms import parse_home_type

STYLES = ("minimalist", "minimal", "modern", "contemporary", "classic", "traditional", "scandinavian", "industrial",
          "rustic", "bohemian", "boho", "mid-century", "japandi", "luxury", "vintage", "compact")
COLOURS = ("white", "black", "grey", "gray", "beige", "brown", "blue", "green", "red", "yellow", "cream", "ivory",
           "walnut", "oak", "teak", "charcoal", "navy", "pink", "orange", "tan")

#: Words people use for each category.
CATEGORY_WORDS: dict[str, tuple[str, ...]] = {
    "sofa": ("sofa", "couch", "settee"), "tv_unit": ("tv unit", "tv stand", "tv cabinet", "television unit", "tv"),
    "bed": ("bed", "cot"), "mattress": ("mattress",), "wardrobe": ("wardrobe", "closet", "almirah"),
    "dining_table": ("dining table", "dining set", "dining"), "refrigerator": ("refrigerator", "fridge"),
}

_NUM = r"(\d[\d,]*(?:\.\d+)?)"
_LAKH = re.compile(_NUM + r"\s*(?:lakhs?|lacs?|lks?)\b", re.IGNORECASE)
_CRORE = re.compile(_NUM + r"\s*(?:crores?|cr)\b", re.IGNORECASE)
_THOUSAND = re.compile(_NUM + r"\s*k\b", re.IGNORECASE)
_RUPEE = re.compile(r"(?:₹|rs\.?|inr)\s*" + _NUM, re.IGNORECASE)
_BUDGET = re.compile(r"budget[^0-9₹]{0,25}" + _NUM, re.IGNORECASE)
_ROOM_REF = re.compile(r"(living\s*room|hall|kitchen|bed\s*room\s*([1-5])|bedroom\s*([1-5])|master\s*bed\s*room)", re.IGNORECASE)


def _number(text: str) -> float:
    return float(text.replace(",", ""))


def parse_money(text: str) -> float | None:
    """The rupee amount in ``text``, or ``None``.  Needs a unit, a currency marker, or the word "budget"."""
    for pattern, factor in ((_CRORE, 1e7), (_LAKH, 1e5), (_THOUSAND, 1e3)):
        match = pattern.search(text)
        if match:
            return round(_number(match.group(1)) * factor, 2)
    for pattern in (_RUPEE, _BUDGET):
        match = pattern.search(text)
        if match:
            try:
                return _number(match.group(1))
            except ValueError:
                return None
    return None


def parse_style(text: str) -> str | None:
    lowered = text.lower()
    for word in STYLES:
        if re.search(rf"\b{re.escape(word)}\b", lowered):
            return {"minimal": "minimalist", "boho": "bohemian", "gray": "grey"}.get(word, word)
    return None


def parse_colour(text: str) -> str | None:
    lowered = text.lower()
    for word in COLOURS:
        if re.search(rf"\b{word}\b", lowered):
            return "grey" if word == "gray" else word
    return None


def categories_in(text: str) -> list[str]:
    """Category ids mentioned in ``text`` (longest phrase wins, so 'tv unit' is not also read as 'tv')."""
    lowered = text.lower()
    found: list[str] = []
    for cat, words in CATEGORY_WORDS.items():
        for word in sorted(words, key=len, reverse=True):
            if re.search(rf"\b{re.escape(word)}s?\b", lowered):
                # 'bed' must not match inside 'bedroom'; \b already prevents that
                found.append(cat)
                break
    return found


def room_in(text: str) -> str | None:
    """A room id mentioned in ``text`` ('bedroom 2' -> 'bedroom_2'), else ``None``."""
    match = _ROOM_REF.search(text)
    if not match:
        return None
    word = match.group(1).lower().replace(" ", "")
    if word.startswith("living") or word == "hall":
        return "living_room"
    if word == "kitchen":
        return "kitchen"
    if word.startswith("master"):
        return "bedroom_1"
    n = match.group(2) or match.group(3)
    return f"bedroom_{n}" if n else None


@dataclass
class ParsedMessage:
    text: str
    bedrooms: int | None = None
    budget: float | None = None
    style: str | None = None
    colour: str | None = None
    categories: list[str] = field(default_factory=list)
    room: str | None = None
    action: str = "unknown"      # cart_add | cart_remove | cart_clear | cart_show | new_request | set_budget | set_preference | remove_item | out_of_stock |
                                 # why | budget_status | overview | compare | help | unknown
    notes: list[str] = field(default_factory=list)


_REMOVE = re.compile(r"\b(don'?t need|do not need|no need|skip|remove|drop|without|leave out|exclude|not required|"
                     r"not buying|already (?:have|own))\b", re.IGNORECASE)
_STOCK = re.compile(r"\b(out of stock|sold out|unavailable|not available|discontinued)\b", re.IGNORECASE)
_BUDGET_CHANGE = re.compile(r"\b(increase|raise|reduce|lower|cut|change|update|set|make|now|extend|stretch|"
                            r"decrease|bump)\b", re.IGNORECASE)
_WHY = re.compile(r"\b(why|how come|reason|explain|justify)\b", re.IGNORECASE)
_STATUS = re.compile(r"\b(how much|left|remaining|spent|spend|total|cost|afford|budget status|summary|so far)\b",
                     re.IGNORECASE)
_OVERVIEW = re.compile(r"\b(show|list|what|overview|plan|everything|recommend|rooms?)\b", re.IGNORECASE)
_COMPARE = re.compile(r"\b(compare|versus|vs\.?|alternatives?|options?|other choices|cheaper|better)\b", re.IGNORECASE)
_CART = re.compile(r"\b(cart|basket)\b", re.IGNORECASE)
_CLEAR = re.compile(r"\b(clear|empty|reset)\b", re.IGNORECASE)
_TAKE_OUT = re.compile(r"\b(remove|delete|take out|drop|get rid of)\b", re.IGNORECASE)
_ADD = re.compile(r"\b(add|put|include|buy|save)\b", re.IGNORECASE)
_PREFERENCE = re.compile(r"\b(prefer|style|switch|change|make it|instead|like|want it|go for|colour|color)\b", re.IGNORECASE)


def parse_message(text: str, *, has_plan: bool) -> ParsedMessage:
    """Classify ``text`` and pull out the values it states.  Pure; does no I/O."""
    text = (text or "").strip()
    parsed = ParsedMessage(text=text, bedrooms=parse_home_type(text), budget=parse_money(text),
                           style=parse_style(text), colour=parse_colour(text), categories=categories_in(text),
                           room=room_in(text))
    if not text:
        parsed.action = "help"
        return parsed
    if not has_plan:
        parsed.action = "new_request" if (parsed.bedrooms or parsed.budget) else "help"
        return parsed
    if _CART.search(text):
        if _CLEAR.search(text):
            parsed.action = "cart_clear"
        elif _TAKE_OUT.search(text) and (parsed.categories or parsed.room):
            parsed.action = "cart_remove"
        elif _ADD.search(text):
            parsed.action = "cart_add"
        else:
            parsed.action = "cart_show"
    elif parsed.bedrooms and parsed.budget and not parsed.categories:
        parsed.action = "new_request"
    elif parsed.categories and _STOCK.search(text):
        parsed.action = "out_of_stock"
    elif parsed.categories and _REMOVE.search(text):
        parsed.action = "remove_item"
    elif parsed.budget is not None and (_BUDGET_CHANGE.search(text) or "budget" in text.lower()):
        parsed.action = "set_budget"
    elif (parsed.style or parsed.colour) and _PREFERENCE.search(text):
        parsed.action = "set_preference"
    elif _WHY.search(text) and (parsed.categories or parsed.room):
        parsed.action = "why"
    elif _COMPARE.search(text) and (parsed.categories or parsed.room):
        parsed.action = "compare"
    elif _STATUS.search(text):
        parsed.action = "budget_status"
    elif parsed.room or _OVERVIEW.search(text):
        parsed.action = "overview"
    else:
        parsed.action = "unknown"
    return parsed
