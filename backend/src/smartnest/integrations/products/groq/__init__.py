"""Groq browser search as SmartNest's live product search (replaces SerpApi for live search).

    config.py    GroqSettings / load_groq_settings (GROQ_API_KEY in backend/.env)
    client.py    httpx client for chat completions; 401/429/timeout/shape handling; optional request cap
    evidence.py  what the search tool actually returned (never the model's own answer)
    provider.py  GroqProvider: search -> extract -> records that pass provenance and the missing-data policy
"""

from smartnest.integrations.products.groq.client import (
    GroqAuthError,
    GroqClient,
    GroqError,
    GroqQuotaError,
    GroqResponseError,
    GroqTimeoutError,
)
from smartnest.integrations.products.groq.config import (
    SEARCH_MODELS,
    GroqConfigError,
    GroqSettings,
    groq_configured,
    load_groq_settings,
)
from smartnest.integrations.products.groq.evidence import Evidence, collect
from smartnest.integrations.products.groq.provider import PROVIDER_ID, GroqProvider, build_record, parse_products

__all__ = [
    "PROVIDER_ID", "SEARCH_MODELS", "Evidence", "GroqAuthError", "GroqClient", "GroqConfigError", "GroqError",
    "GroqProvider", "GroqQuotaError", "GroqResponseError", "GroqSettings", "GroqTimeoutError", "build_record", "collect",
    "groq_configured", "load_groq_settings", "parse_products",
]
