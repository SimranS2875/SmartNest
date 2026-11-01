"""What the Groq browser search actually saw, as opposed to what the model wrote.

A browsing model's final answer (``message.content``) is model output.  Groq documents that browser search returns no
citations, and its response schema for tool activity is not documented, so this module reads tool activity defensively
from wherever Groq reports it:

    message.executed_tools[*]   (every string inside: arguments, outputs, search results, titles, URLs)
    message.reasoning           (the browsing transcript of the gpt-oss models, when returned)

and NEVER from ``message.content``.  The result is the evidence a listing is checked against: its URL must appear among
the pages the search returned, and its price must be printed in the text the search returned.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from smartnest.integrations.products.url_validation import check_url

_URL = re.compile(r"https?://[^\s<>\"'`\\\]\)】†|]+", re.IGNORECASE)
_TRAILING = ".,;:!?'\""
_PRICE = re.compile(r"(?:₹|rs\.?|inr|rupees?)\s*\d|\d[\d,]*\s*(?:₹|rs\b|inr\b|rupees?\b|/-)", re.IGNORECASE)
MAX_EVIDENCE_CHARS = 400_000


@dataclass
class Evidence:
    text: str = ""
    urls: list[str] = field(default_factory=list)
    tools_reported: int = 0              # how many executed-tool entries Groq returned
    sources: list[str] = field(default_factory=list)   # where the evidence came from ("executed_tools", "reasoning")

    @property
    def empty(self) -> bool:
        return not self.text.strip()

    @property
    def shows_prices(self) -> bool:
        """Whether the tool output contains any price at all (a rupee amount); without one no price can be verified."""
        return bool(_PRICE.search(self.text))


def _strings(value: Any, out: list[str], depth: int = 0) -> None:
    if depth > 12:
        return
    if isinstance(value, str):
        out.append(value)
    elif isinstance(value, dict):
        for v in value.values():
            _strings(v, out, depth + 1)
    elif isinstance(value, (list, tuple)):
        for v in value:
            _strings(v, out, depth + 1)


def _clean(url: str) -> str:
    return url.rstrip(_TRAILING)


def collect(message: dict[str, Any]) -> Evidence:
    """Evidence from one ``choices[0].message`` (the final ``content`` is deliberately excluded)."""
    parts: list[str] = []
    sources: list[str] = []
    tools = message.get("executed_tools")
    tools = tools if isinstance(tools, list) else []
    if tools:
        before = len(parts)
        _strings(tools, parts)
        if len(parts) > before:
            sources.append("executed_tools")
    reasoning = message.get("reasoning")
    if isinstance(reasoning, str) and reasoning.strip():
        parts.append(reasoning)
        sources.append("reasoning")
    text = "\n".join(parts)[:MAX_EVIDENCE_CHARS]
    urls: list[str] = []
    for raw in _URL.findall(text):
        url = _clean(raw)
        if url not in urls and check_url(url) is None:
            urls.append(url)
    return Evidence(text=text, urls=urls, tools_reported=len(tools), sources=sources)
