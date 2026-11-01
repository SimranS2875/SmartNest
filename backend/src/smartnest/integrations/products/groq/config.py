"""Groq configuration, loaded from ``backend/.env`` and the environment.

    GROQ_API_KEY            required (https://console.groq.com/keys)
    GROQ_MODEL              optional, default ``openai/gpt-oss-120b``.  Browser search only works on the gpt-oss models.
    GROQ_TIMEOUT_SECONDS    optional, default 90 (a browsing session takes a while)
    GROQ_MAX_RESULTS        optional, default 8 products per search
    GROQ_REQUEST_LIMIT      optional cap on TOTAL Groq requests made for product search (a number), or ``unlimited``;
                            counted in ``backend/.groq_usage.json``.  One live search uses up to 2 requests.

Real environment variables win over ``.env``.  The key is stored with ``repr=False`` and every message is redacted.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from dotenv import dotenv_values

from smartnest.integrations.gemini.errors import redact_secrets

BACKEND = Path(__file__).resolve().parents[5]
DEFAULT_ENV_FILE = BACKEND / ".env"
DEFAULT_USAGE_FILE = BACKEND / ".groq_usage.json"
ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"
#: Models on which Groq offers the ``browser_search`` tool.
SEARCH_MODELS = ("openai/gpt-oss-120b", "openai/gpt-oss-20b")

_PLACEHOLDERS = {"your_api_key", "your-api-key", "changeme", "<api_key>", "xxx", "todo", "<your_groq_api_key>"}


class GroqConfigError(Exception):
    """Groq is not configured (or configured with an invalid value)."""


@dataclass(frozen=True)
class GroqSettings:
    api_key: str = field(repr=False)
    model: str = "openai/gpt-oss-120b"
    timeout_seconds: float = 90.0
    max_results: int = 8
    request_limit: int | None = None
    uncapped: bool = False
    usage_file: Path | None = None           # None (tests): nothing is counted; set when loaded from the .env
    reasoning_effort: str = "low"            # Groq recommends low for browsing: shorter sessions, fewer tokens
    endpoint: str = ENDPOINT

    def __post_init__(self) -> None:
        key = (self.api_key or "").strip()
        if not key:
            raise GroqConfigError("GROQ_API_KEY is not set; add it to backend/.env")
        if key.lower() in _PLACEHOLDERS or key.startswith("<") or any(c.isspace() for c in key):
            raise GroqConfigError("GROQ_API_KEY still looks like a placeholder; put the real key in backend/.env")
        object.__setattr__(self, "api_key", key)
        if self.model not in SEARCH_MODELS:
            raise GroqConfigError(f"GROQ_MODEL must be one of {', '.join(SEARCH_MODELS)} (browser search needs them)")
        if isinstance(self.timeout_seconds, bool) or not 5 <= self.timeout_seconds <= 300:
            raise GroqConfigError("GROQ_TIMEOUT_SECONDS must be between 5 and 300")
        if isinstance(self.max_results, bool) or not 1 <= self.max_results <= 20:
            raise GroqConfigError("GROQ_MAX_RESULTS must be between 1 and 20")
        if self.request_limit is not None and (isinstance(self.request_limit, bool) or self.request_limit < 0):
            raise GroqConfigError("GROQ_REQUEST_LIMIT must be a whole number >= 0 or 'unlimited'")
        if self.reasoning_effort not in ("low", "medium", "high"):
            raise GroqConfigError("reasoning_effort must be low, medium or high")

    def redact(self, text: str) -> str:
        return redact_secrets(text, (self.api_key,))

    def __str__(self) -> str:
        return f"GroqSettings(model={self.model}, max_results={self.max_results}, api_key=set)"


def load_groq_settings(env_file: str | Path | None = None, environ: Mapping[str, str] | None = None) -> GroqSettings:
    """Load settings; raises :class:`GroqConfigError` for a missing/placeholder key or a bad value."""
    path = Path(env_file) if env_file is not None else DEFAULT_ENV_FILE
    file_values = dotenv_values(path) if path.is_file() else {}
    env = os.environ if environ is None else environ

    def get(name: str) -> str | None:
        value = env.get(name)
        return value if value is not None and value.strip() else file_values.get(name)

    def number(name: str, default, cast):
        raw = get(name)
        if raw is None or not str(raw).strip():
            return default
        try:
            return cast(str(raw).strip())
        except ValueError:
            raise GroqConfigError(f"{name} must be a number") from None

    limit_raw = str(get("GROQ_REQUEST_LIMIT") or "").strip().lower()
    uncapped = limit_raw in ("unlimited", "none", "off", "no")
    return GroqSettings(
        api_key=get("GROQ_API_KEY") or "",
        model=(get("GROQ_MODEL") or "openai/gpt-oss-120b").strip(),
        timeout_seconds=number("GROQ_TIMEOUT_SECONDS", 90.0, float),
        max_results=number("GROQ_MAX_RESULTS", 8, int),
        request_limit=None if uncapped else number("GROQ_REQUEST_LIMIT", None, int),
        uncapped=uncapped,
        usage_file=DEFAULT_USAGE_FILE,
    )


def groq_configured(env_file: str | Path | None = None, environ: Mapping[str, str] | None = None) -> bool:
    """Whether a usable key is configured (never raises, never reveals the key)."""
    try:
        load_groq_settings(env_file, environ)
    except GroqConfigError:
        return False
    return True
