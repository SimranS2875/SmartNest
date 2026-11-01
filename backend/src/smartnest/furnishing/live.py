"""On-demand live product search for categories SmartNest has no (suitable) listings for yet.

Two interchangeable backends, chosen by the settings object passed in:

    GroqSettings     Groq browser search (the default live search): one search + one extraction request; every
                     accepted link and price must appear in what the search tool returned (see ``products.groq``)
    SerpApiSettings  SerpApi Google Shopping: one request, structured listings (kept as an option)

Either way the result is saved as a snapshot (one file per themed search) and the plan is re-made with it.

Safety (it spends a limited allowance, so it is deliberately hard to trigger by accident):
    * off unless ``SMARTNEST_ALLOW_LIVE_SEARCH=1`` and the provider's key is configured;
    * the user must ask for it explicitly (a button / API call); the planner never searches by itself;
    * only categories that are in the current plan can be searched;
    * an optional request cap (``GROQ_REQUEST_LIMIT`` / ``SERPAPI_LIFETIME_LIMIT``) refuses once used up;
    * an empty result never overwrites an existing snapshot.
"""

from __future__ import annotations

import dataclasses
import logging
from typing import Any

from smartnest.furnishing.catalog import ORIGIN_REAL, CategoryListings, SnapshotCatalog
from smartnest.furnishing.categories import category
from smartnest.integrations.products import ProductDiscoveryError, ProductQuery
from smartnest.integrations.products.groq import GroqClient, GroqError, GroqProvider, GroqQuotaError, GroqSettings
from smartnest.integrations.products.serpapi import (
    SearchCache,
    SerpApiClient,
    SerpApiProvider,
    SerpApiSettings,
)
from smartnest.integrations.products.serpapi.client import UNCAPPED, SerpApiError, SerpApiQuotaError, UsageLedger

logger = logging.getLogger(__name__)


class LiveSearchError(Exception):
    """A live search could not be done.  ``code`` is ``quota``, ``disabled`` or ``failed``."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class LiveSearcher:
    def __init__(self, settings: GroqSettings | SerpApiSettings, snapshots: SnapshotCatalog, *,
                 cache: SearchCache | None = None, transport: Any = None) -> None:
        if isinstance(settings, GroqSettings):
            self.provider = "groq"
            self._settings: Any = settings
        else:
            self.provider = "serpapi"
            self._settings = dataclasses.replace(settings, max_detail_calls=0)
        self._snapshots = snapshots
        self._cache = cache
        self._transport = transport

    @property
    def provider_label(self) -> str:
        return "Groq" if self.provider == "groq" else "SerpApi"

    # ── Allowance ────────────────────────────────────────────────────────

    @property
    def limit(self) -> int | None:
        return self._settings.request_limit if self.provider == "groq" else self._settings.lifetime_limit

    @property
    def used(self) -> int:
        path = self._settings.usage_file
        return UsageLedger(path, self.limit or UNCAPPED).used if path is not None else 0

    @property
    def remaining(self) -> int | None:
        return None if self.limit is None else max(0, self.limit - self.used)

    def status(self) -> dict[str, Any]:
        return {"enabled": True, "provider": self.provider, "remaining": self.remaining, "limit": self.limit,
                "used": self.used, "requests_per_search": 2 if self.provider == "groq" else 1}

    # ── Search ───────────────────────────────────────────────────────────

    def search(self, category_id: str, themes: tuple[str, ...] = ()) -> CategoryListings:
        profile = category(category_id)
        if self.remaining is not None and self.remaining < 1:
            setting = "GROQ_REQUEST_LIMIT" if self.provider == "groq" else "SERPAPI_LIFETIME_LIMIT"
            raise LiveSearchError("quota", f"The {self.provider_label} allowance is used up ({self.used} of {self.limit} "
                                           f"requests). Raise {setting} in backend/.env to search again.")
        text = profile.search_query(themes)
        try:
            result = self._discover(text)
        except (GroqQuotaError, SerpApiQuotaError) as exc:
            raise LiveSearchError("quota", f"{self.provider_label} quota or rate limit reached: {exc}") from None
        except (ProductDiscoveryError, SerpApiError, GroqError) as exc:
            if "quota" in str(exc).lower() or "rate limit" in str(exc).lower() or "cap is reached" in str(exc).lower():
                raise LiveSearchError("quota", f"{self.provider_label}: {exc}") from None
            raise LiveSearchError("failed", f"The search did not work: {exc}") from None
        records = [dict(r, category=category_id, data_origin=ORIGIN_REAL) for r in result.listings]
        listings = CategoryListings(category_id, records, ORIGIN_REAL, result.retrieved_at.isoformat(), text,
                                    [{"name": r.name, "reasons": r.reasons} for r in result.rejected], list(result.notes))
        if records:                                    # an empty result must not overwrite an existing snapshot
            self._snapshots.save(listings)
        logger.info("live search (%s) %r: %d listing(s), %d request(s)", self.provider, text, len(records),
                    result.api_calls)
        return listings

    def _discover(self, text: str):
        if self.provider == "groq":
            provider = GroqProvider(GroqClient(self._settings, transport=self._transport), self._settings)
        else:
            provider = SerpApiProvider(SerpApiClient(self._settings, transport=self._transport), self._settings,
                                       cache=self._cache)
        return provider.discover(ProductQuery(category=text))
