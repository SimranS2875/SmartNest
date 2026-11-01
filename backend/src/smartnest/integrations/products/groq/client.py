"""Thin HTTP client for Groq's OpenAI-compatible chat completions API (``httpx``).

Every failure is a :class:`GroqError` subclass with a redacted message.  No automatic retries: a retry can start another
(billed, rate-limited) browsing session, and 401/429 cannot succeed on retry.

    401 / 403          -> GroqAuthError     (bad or missing key)
    429                -> GroqQuotaError    (rate limit / daily quota) - also raised by the local request cap
    timeout            -> GroqTimeoutError
    5xx / network      -> GroqError
    non-JSON / no choices -> GroqResponseError
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Callable

import httpx

from smartnest.integrations.products.groq.config import GroqSettings
from smartnest.integrations.products.serpapi.client import UNCAPPED, SerpApiQuotaError, UsageLedger


_WAIT = re.compile(r"try again in\s+(?:(\d+)h)?(?:(\d+)m(?!s))?(?:(\d+(?:\.\d+)?)s)?(?:(\d+(?:\.\d+)?)ms)?", re.IGNORECASE)


def retry_wait(header: str | None, message: str) -> float | None:
    """Seconds Groq asks us to wait: the ``Retry-After`` header, else "Please try again in 6.795s" / "1m2.5s" / "250ms"."""
    if header:
        try:
            return max(0.0, float(header))
        except ValueError:
            pass
    match = _WAIT.search(message or "")
    if not match or not any(match.groups()):
        return None
    hours, minutes, seconds, millis = (float(g) if g else 0.0 for g in match.groups())
    return hours * 3600 + minutes * 60 + seconds + millis / 1000


class GroqError(Exception):
    """Base class for every error raised by the Groq layer."""


class GroqAuthError(GroqError):
    """The key was rejected."""


class GroqQuotaError(GroqError):
    """Rate limit, daily quota or the local request cap."""


class GroqTimeoutError(GroqError):
    """No answer in time."""


class GroqResponseError(GroqError):
    """The response did not have the expected shape."""


class GroqClient:
    """Makes chat-completion requests; counts them when a usage file is configured."""

    def __init__(self, settings: GroqSettings, *, transport: httpx.BaseTransport | None = None,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self._settings = settings
        self._transport = transport
        self._sleep = sleep
        self.waited = 0.0                      # seconds spent waiting out rate limits
        if settings.usage_file is not None:
            limit = settings.request_limit if settings.request_limit is not None else UNCAPPED
            self.ledger: UsageLedger | None = UsageLedger(settings.usage_file, limit)
        else:
            self.ledger = None
        self.calls = 0

    def chat(self, messages: list[dict[str, str]], *, tools: list[dict] | None = None, tool_choice: str | None = None,
             response_format: dict | None = None, include_reasoning: bool | None = None, max_tokens: int = 4096,
             temperature: float = 0.2) -> dict[str, Any]:
        """One completion.  Returns the whole response object (``choices``, ``usage``, any tool metadata)."""
        s = self._settings
        payload: dict[str, Any] = {"model": s.model, "messages": messages, "max_completion_tokens": max_tokens,
                                   "temperature": temperature, "reasoning_effort": s.reasoning_effort}
        if tools:
            payload["tools"] = tools
            if tool_choice:
                payload["tool_choice"] = tool_choice
        if response_format:
            payload["response_format"] = response_format
        if include_reasoning is not None:
            payload["include_reasoning"] = include_reasoning
        for attempt in range(s.max_retries + 1):
            if self.ledger is not None:
                try:
                    self.ledger.reserve()        # raises before any network access once the cap is reached
                except SerpApiQuotaError:
                    raise GroqQuotaError(f"the local Groq request cap is reached ({self.ledger.used} of "
                                         f"{self.ledger.limit}); raise GROQ_REQUEST_LIMIT to allow more") from None
            self.calls += 1
            try:
                with httpx.Client(timeout=s.timeout_seconds, transport=self._transport) as http:
                    response = http.post(s.endpoint, json=payload, headers={
                        "Authorization": f"Bearer {s.api_key}", "Content-Type": "application/json"})
            except httpx.TimeoutException:
                raise GroqTimeoutError(f"Groq did not answer within {s.timeout_seconds:g} s") from None
            except httpx.HTTPError as exc:
                raise GroqError(f"Groq request failed ({type(exc).__name__})") from None

            body = self._json(response)
            message = ""
            if isinstance(body, dict) and isinstance(body.get("error"), dict):
                message = s.redact(str(body["error"].get("message", "")))
            status = response.status_code
            if status in (401, 403):
                raise GroqAuthError(f"Groq rejected the API key (HTTP {status})")
            if status == 429:
                wait = retry_wait(response.headers.get("retry-after"), message)
                if wait is not None and wait <= s.max_wait_seconds and attempt < s.max_retries:
                    self.waited += wait + 0.5
                    self._sleep(wait + 0.5)       # a per-minute limit: Groq says how long; wait, then try once more
                    continue
                raise GroqQuotaError(f"Groq rate limit or quota reached (HTTP 429){': ' + message if message else ''}")
            if status >= 500:
                raise GroqError(f"Groq server error (HTTP {status})")
            if status >= 400:
                raise GroqError(f"Groq returned HTTP {status}{': ' + message if message else ''}")
            if not isinstance(body, dict) or not isinstance(body.get("choices"), list) or not body["choices"] \
                    or not isinstance(body["choices"][0], dict) or not isinstance(body["choices"][0].get("message"), dict):
                raise GroqResponseError("Groq response has no choices[0].message")
            return body
        raise GroqQuotaError("Groq rate limit reached and retries are used up")      # pragma: no cover

    @staticmethod
    def _json(response: httpx.Response) -> Any:
        try:
            return response.json()
        except (json.JSONDecodeError, ValueError):
            if response.status_code >= 400:
                return None
            raise GroqResponseError("Groq response is not valid JSON") from None
