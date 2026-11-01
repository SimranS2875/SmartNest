"""Groq browser search as a product provider (replaces SerpApi for live search).

One discovery run::

    search text --(1) Groq + browser_search--> answer + tool evidence (pages the search returned)
                --(2) Groq, no tools, JSON mode--> product list (names, prices, URLs copied from the answer)
                --(3) build_record--> records the optimizer accepts | rejected listings with reasons

Rules (the same missing-data policy as the rest of SmartNest, plus provenance, because the answer is model-written):
    * mandatory: a name, a positive INR price, and a URL;
    * the URL must appear among the pages the browser search returned (``evidence.urls``); a URL that is only in the
      model's prose is rejected.  If Groq reports no pages at all, NO listing is accepted and the run says so;
    * the price must be printed in the text the search returned; a rating or review count is kept only if it is too;
    * stock is not taken from the model: it stays ``unknown`` (the plan labels such picks unverified);
    * anything not stated stays empty; nothing is defaulted.
Step (2) is skipped when step (1) reported no pages, so no request is wasted.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime
from typing import Any, Callable

from smartnest.integrations.products.groq.client import GroqClient, GroqError
from smartnest.integrations.products.groq.config import GroqSettings
from smartnest.integrations.products.groq.evidence import Evidence, collect
from smartnest.integrations.products.models import (
    DiscoveryResult,
    ProductDiscoveryError,
    ProductQuery,
    RejectedListing,
    utc_now,
)
from smartnest.integrations.products.normalizer import price_in_text
from smartnest.integrations.products.serpapi.parsing import URL_GOOGLE, URL_RETAILER, classify_url
from smartnest.integrations.products.url_validation import AllowedUrls, canonical_key, domain_of
from smartnest.optimization import validate_record

logger = logging.getLogger(__name__)

PROVIDER_ID = "groq_browser_search"
_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL | re.IGNORECASE)
#: A link that lists SEVERAL products (a collection, category or search page) rather than one product page.  Search-style
#: answers often give these ("urbanladder.com/collection/sofas-under-30000" for four different sofas), so a link is
#: labelled for what it is, and a product is identified by link AND name, not by the link alone.
_LISTING_PATH = re.compile(r"/(collections?|categor(?:y|ies)|product-category|search|browse|catalog(?:ue)?|shop|c)(?:/|\.|$)|"
                           r"-under-\d+|/all-products|/s/", re.IGNORECASE)
_LISTING_QUERY = re.compile(r"(?:^|&)(?:q|k|query|search|searchterm|keyword)=", re.IGNORECASE)
URL_LISTING = "retailer_listing"
_SEATS = re.compile(r"\b([1-9])\s*[- ]?\s*seater\b", re.IGNORECASE)

SEARCH_SYSTEM = (
    "You find products that are for sale online in India. Use the browser search tool. Report only what the pages you "
    "opened say: never guess a price, a link or a rating. Copy every URL exactly as it appears.")


MAX_PAGE_OPENS = 3


def search_prompt(text: str, limit: int) -> str:
    # Cost matters: every page the model opens is fed back into it as tokens (one full product page can be 30,000+
    # characters), and Groq's free tier allows 200,000 tokens a day.  A retailer CATEGORY page is small and lists many
    # products with prices, so the model is steered to those and limited in how many pages it may open.
    return (f"Find up to {limit} different products for sale in India for: \"{text}\".\n"
            f"Work cheaply: open at most {MAX_PAGE_OPENS} pages in total, and stop as soon as you have enough products.\n"
            "Prefer a retailer's CATEGORY or COLLECTION page for this kind of product (for example on urbanladder.com, "
            "pepperfry.com, wakefit.co, ikea.com/in, godrejinterio.com): one such page shows many products with their prices. "
            "Use a single product page only if no category page works.\n"
            "Only list a product from a page you actually opened. For each product give: the exact product name; the price in "
            "rupees exactly as printed on that page; the shop; the full https URL of the page where you saw it; and the "
            "rating out of 5 and number of reviews ONLY if that page shows them.")


EXTRACT_SYSTEM = (
    "You convert a shopping answer into JSON. You never invent values: a value that is not in the answer is null. "
    "URLs must be copied character for character from the ALLOWED URLS list; a product whose URL is not in that list "
    "must be left out.")


def extract_prompt(answer: str, allowed: list[str], limit: int) -> str:
    urls = "\n".join(allowed[:200])
    return ("Return a JSON object {\"products\": [...]} with at most " + str(limit) + " products. Each product: "
            "{\"name\": str, \"price\": number (rupees, digits only), \"currency\": \"INR\" or null, \"retailer\": str or "
            "null, \"url\": str (from ALLOWED URLS only), \"rating\": number or null, \"review_count\": integer or null}.\n\n"
            f"ALLOWED URLS:\n{urls}\n\nANSWER:\n{answer[:20000]}")


def parse_products(content: str | None) -> list[dict[str, Any]]:
    """The product list from the extraction reply; ``[]`` for anything that is not a JSON object with a list."""
    if not isinstance(content, str) or not content.strip():
        return []
    text = content.strip()
    match = _FENCE.match(text)
    if match:
        text = match.group(1)
    try:
        data = json.loads(text)
    except ValueError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return []
        try:
            data = json.loads(text[start:end + 1])
        except ValueError:
            return []
    items = data.get("products") if isinstance(data, dict) else None
    return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []


def is_listing_page(url: str) -> bool:
    from urllib.parse import urlsplit
    parts = urlsplit(url)
    return bool(_LISTING_PATH.search(parts.path) or _LISTING_QUERY.search(parts.query))


def product_id_for(url: str, name: str) -> str:
    """Stable id from the link AND the name: several products can share one listing page."""
    digest = hashlib.sha1(f"{canonical_key(url)}|{name.strip().lower()}".encode("utf-8")).hexdigest()[:12].upper()
    return f"GROQ_{digest}"


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if value == value else None
    if isinstance(value, str):
        digits = re.sub(r"[^\d.]", "", value.replace(",", ""))
        try:
            return float(digits) if digits else None
        except ValueError:
            return None
    return None


def _in_text(value: float, text: str) -> bool:
    forms = {f"{value:g}", f"{value:.1f}"}
    if float(value).is_integer():
        forms.add(f"{int(value):,}")                      # "1,051"
    return any(re.search(rf"(?<![\d.]){re.escape(f)}(?![\d])", text) for f in forms)


def build_record(item: dict[str, Any], *, allowed: AllowedUrls, evidence: Evidence, retrieved_at: datetime,
                 category: str) -> tuple[dict | None, list[str], str | None]:
    """``(record, reasons, name)``: a record when every rule holds, else the reasons it was refused."""
    reasons: list[str] = []
    name = item.get("name").strip() if isinstance(item.get("name"), str) else None
    if not name or len(name) < 2 or len(name) > 300:
        reasons.append("name is missing")
        name = None

    # The link first: it must be a page the search tool returned.
    url = None
    raw = item.get("url") if isinstance(item.get("url"), str) else None
    if not raw:
        reasons.append("URL is missing")
    else:
        url = allowed.lookup(raw.strip())
        if url is None:
            reasons.append("URL is not among the pages the search returned")

    # The price, rating and reviews must be printed on THAT page.  A real response says which page each text belongs to;
    # a page that was only listed (never opened) cannot vouch for anything.  If the tool output carries no per-page
    # attribution at all, the whole tool output is the best available evidence.
    if url is not None and evidence.attributed:
        proof = evidence.page_text(url)
        proof_note = "the search never opened this page, so its price was not checked"
    else:
        proof = evidence.text
        proof_note = None

    price = _number(item.get("price"))
    currency = str(item.get("currency") or "INR").strip().upper()
    if currency in ("RS", "RS.", "RUPEES", "\u20b9"):
        currency = "INR"
    if price is None or price <= 0:
        reasons.append("price is missing or not positive")
        price = None
    elif currency != "INR":
        reasons.append(f"price is in {currency}, not INR")
    elif url is not None and proof is None:
        reasons.append(proof_note or "no page text to check the price against")
    elif proof is not None and not price_in_text(price, proof):
        reasons.append(f"price {price:,.0f} is not printed on the page the link points to")

    if reasons:
        return None, reasons, name
    kind, clean_url = classify_url(url)
    if kind not in (URL_RETAILER, URL_GOOGLE) or not clean_url:
        return None, ["URL is not a usable product page"], name

    rating = _number(item.get("rating"))
    rating = rating if rating is not None and 0 <= rating <= 5 and _in_text(rating, proof or "") else None
    reviews = _number(item.get("review_count"))
    reviews = int(reviews) if reviews is not None and reviews >= 0 and reviews.is_integer() \
        and _in_text(reviews, proof or "") else None
    seats = _SEATS.search(name)
    domain = domain_of(clean_url)
    if kind == URL_RETAILER and is_listing_page(clean_url):
        kind = URL_LISTING
    retailer = item.get("retailer").strip() if isinstance(item.get("retailer"), str) and item.get("retailer").strip() \
        else domain
    record = {
        "product_id": product_id_for(clean_url, name),
        "name": name, "price": float(price), "width_cm": None, "depth_cm": None,
        "seating_capacity": int(seats.group(1)) if seats else None, "material": None, "colour": None, "style": None,
        "availability": "unknown", "rating": rating, "review_count": reviews, "source_agent_id": PROVIDER_ID,
        "category": category.lower(), "currency": "INR", "retailer": retailer, "merchant": retailer,
        "url": clean_url if kind in (URL_RETAILER, URL_LISTING) else None, "url_type": kind,
        "product_page_url": clean_url if kind == URL_GOOGLE else None, "price_source": "search_page_text",
        "image_url": None, "height_cm": None, "verified_at": retrieved_at.isoformat(), "inferred_fields": [],
        "discovery_method": PROVIDER_ID, "listing_url": clean_url, "listing_url_type": kind,
    }
    _, problems = validate_record(record)
    if problems:
        return None, [f"failed record validation: {p}" for p in problems], name
    return record, [], name


class GroqProvider:
    """Live product discovery with Groq browser search (``ProductProvider``)."""

    provider_id = PROVIDER_ID

    def __init__(self, client: GroqClient, settings: GroqSettings, *, clock: Callable[[], datetime] = utc_now) -> None:
        self._client = client
        self._settings = settings
        self._clock = clock

    def discover(self, query: ProductQuery) -> DiscoveryResult:
        text = query.category
        limit = min(query.limit, self._settings.max_results) if query.limit else self._settings.max_results
        result = DiscoveryResult(provider_id=self.provider_id, query=query, retrieved_at=self._clock())
        result.search_queries = [text]

        try:
            searched = self._client.chat(
                [{"role": "system", "content": SEARCH_SYSTEM}, {"role": "user", "content": search_prompt(text, limit)}],
                tools=[{"type": "browser_search"}], tool_choice="required", include_reasoning=True, max_tokens=4096)
        except GroqError as exc:
            raise ProductDiscoveryError(self.provider_id, str(exc)) from None
        result.api_calls += 1
        message = searched["choices"][0]["message"]
        tokens = (searched.get("usage") or {}).get("total_tokens") if isinstance(searched.get("usage"), dict) else None
        if isinstance(tokens, int):
            result.notes.append(f"the search used {tokens:,} Groq tokens (free tier: 200,000 a day)")
        answer = message.get("content") if isinstance(message.get("content"), str) else ""
        evidence = collect(message)
        allowed = AllowedUrls()
        for url in evidence.urls:
            allowed.add(url)
        result.sources = allowed.urls()
        if len(allowed) and not evidence.shows_prices:
            result.notes.append("the search tool's output contains no prices, so no price could be checked against what the "
                                "search saw and no listing was accepted")
            return result
        if not len(allowed):
            result.notes.append("Groq did not report any page the search returned (no tool output with links), so no "
                                "listing could be traced to a source and none was accepted")
            return result
        if not answer.strip():
            result.notes.append("the search returned no answer to read products from")
            return result

        try:
            extracted = self._client.chat(
                [{"role": "system", "content": EXTRACT_SYSTEM},
                 {"role": "user", "content": extract_prompt(answer, allowed.urls(), limit)}],
                response_format={"type": "json_object"}, max_tokens=4096, temperature=0.0)
        except GroqError as exc:
            raise ProductDiscoveryError(self.provider_id, f"extraction failed: {exc}") from None
        result.api_calls += 1
        items = parse_products(extracted["choices"][0]["message"].get("content"))
        result.extracted_count = len(items)
        seen: set[str] = set()
        for item in items[:limit]:
            record, reasons, name = build_record(item, allowed=allowed, evidence=evidence,
                                                 retrieved_at=result.retrieved_at, category=text)
            if record is None:
                result.rejected.append(RejectedListing(name, item.get("url") if isinstance(item.get("url"), str) else None,
                                                       reasons))
                continue
            if record["product_id"] in seen:
                result.rejected.append(RejectedListing(name, record["listing_url"], ["duplicate of a listing already accepted"]))
                continue
            seen.add(record["product_id"])
            result.listings.append(record)
        logger.info("Groq discovery %r: %d accepted, %d rejected, %d page(s) seen", text, len(result.listings),
                    len(result.rejected), len(allowed))
        return result
