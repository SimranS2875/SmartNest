"""What the Groq browser search actually saw, as opposed to what the model wrote.

A browsing model's final answer (``message.content``) is model output.  Evidence is only what the search TOOL returned.
From a real response, ``message.executed_tools`` is a list of tool calls, each like::

    {"type": "browser_search" | "browser.open" | "function", "name": "browser.search" | "browser.open",
     "arguments": "<json string>", "output": "L0: \\nL1: URL: https://...\\nL2: ... page or results text ...",
     "search_results": {"results": [{"title": ..., "url": ..., "content": ..., "score": 0}]}}

A ``browser.open`` output starts with ``L1: URL: <the page>`` followed by that page's own text, which is where a product's
price, rating and review count are printed.  So evidence is kept PER PAGE:

    pages[<canonical url>]   the text of every tool call that opened that page (the price must be printed there)
    text                     all tool output together (only used when the tool output carries no page attribution)
    urls                     every link the tool returned (search results and opened pages)

The search engine's own pages (``exa.ai/search?...``) are never evidence of a product.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from smartnest.integrations.products.url_validation import canonical_key, check_url

_URL = re.compile(r"https?://[^\s<>\"'`\\\]\)】†|]+", re.IGNORECASE)
# "L1: URL: https://..."  or  "L1: URL:" with the link on the next line ("L2: https://...")
_PAGE_LINE = re.compile(r"^\s*L\d+:\s*URL:[ \t]*(?:\n\s*L\d+:[ \t]*)?(https?://\S+)", re.MULTILINE)
_TRAILING = ".,;:!?'\""
_PRICE = re.compile(r"(?:₹|rs\.?|inr|rupees?)\s*\d|\d[\d,]*\s*(?:₹|rs\b|inr\b|rupees?\b|/-)", re.IGNORECASE)
MAX_EVIDENCE_CHARS = 600_000
#: The search tool's own host: its result pages are not product pages.
_ENGINE_HOSTS = ("exa.ai",)


@dataclass
class Evidence:
    text: str = ""
    urls: list[str] = field(default_factory=list)
    pages: dict[str, str] = field(default_factory=dict)      # canonical url -> text of the calls that opened it
    tools_reported: int = 0
    sources: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.text.strip()

    @property
    def shows_prices(self) -> bool:
        """Whether the tool output contains any price at all (a rupee amount); without one no price can be verified."""
        return bool(_PRICE.search(self.text))

    def page_text(self, url: str) -> str | None:
        """The text of the page at ``url`` if a tool call opened it, else ``None`` (it was only listed, never read)."""
        return self.pages.get(canonical_key(url))

    @property
    def attributed(self) -> bool:
        """Whether the tool output says which page each text belongs to (a real response does)."""
        return bool(self.pages)


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


def _usable(url: str) -> bool:
    if check_url(url) is not None:
        return False
    host = url.split("/")[2].lower().removeprefix("www.") if url.count("/") >= 2 else ""
    return not any(host == h or host.endswith("." + h) for h in _ENGINE_HOSTS)


def _opened_page(tool: dict[str, Any], output: str) -> str | None:
    """The page a ``browser.open`` call read: from its own search_results entry, else from the ``URL:`` line."""
    if tool.get("name") == "browser.open" or tool.get("type") == "browser.open":
        results = (tool.get("search_results") or {}).get("results") if isinstance(tool.get("search_results"), dict) else None
        for entry in results if isinstance(results, list) else []:
            if isinstance(entry, dict) and isinstance(entry.get("url"), str):
                return _clean(entry["url"])
    match = _PAGE_LINE.search(output)
    return _clean(match.group(1)) if match else None


def collect(message: dict[str, Any]) -> Evidence:
    """Evidence from one ``choices[0].message`` (the final ``content`` is deliberately excluded)."""
    parts: list[str] = []
    sources: list[str] = []
    pages: dict[str, str] = {}
    tools = message.get("executed_tools")
    tools = tools if isinstance(tools, list) else []
    for tool in tools:
        strings: list[str] = []
        _strings(tool, strings)
        text = "\n".join(strings)
        parts.append(text)
        output = tool.get("output") if isinstance(tool, dict) else None
        if isinstance(output, str):
            page_url = _opened_page(tool, output)
            if page_url and _usable(page_url):
                key = canonical_key(page_url)
                pages[key] = (pages.get(key, "") + "\n" + output)[:MAX_EVIDENCE_CHARS]
    if tools and any(p.strip() for p in parts):
        sources.append("executed_tools")
    reasoning = message.get("reasoning")
    if isinstance(reasoning, str) and reasoning.strip():
        parts.append(reasoning)
        sources.append("reasoning")
    text = "\n".join(parts)[:MAX_EVIDENCE_CHARS]
    urls: list[str] = []
    for raw in _URL.findall(text):
        url = _clean(raw)
        if url not in urls and _usable(url):
            urls.append(url)
    return Evidence(text=text, urls=urls, pages=pages, tools_reported=len(tools), sources=sources)
