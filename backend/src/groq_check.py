#!/usr/bin/env python3
"""SmartNest - live Groq browser-search check (spends 2 Groq requests per query).

    python src/groq_check.py "3 seater sofa"            # one real search + extraction, with diagnostics
    python src/groq_check.py "bed" --save raw.json      # also save the raw search response (no key is in it)

It prints, for the REAL response: which fields Groq returned, how many pages the search tool reported, whether any
rupee price appears in the tool output (the evidence a price is checked against), and which listings SmartNest accepted
or rejected and why.  Needs GROQ_API_KEY in backend/.env.

Exit codes: 0 = a search ran, 1 = the API call failed, 2 = not configured.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from smartnest.integrations.products import ProductDiscoveryError, ProductQuery  # noqa: E402
from smartnest.integrations.products.groq import (  # noqa: E402
    GroqClient,
    GroqConfigError,
    GroqError,
    GroqProvider,
    collect,
    load_groq_settings,
)
from smartnest.integrations.products.groq.provider import SEARCH_SYSTEM, search_prompt  # noqa: E402


def shape(value, depth=0):
    """A compact description of a JSON value: types and sizes, never the long text itself."""
    if isinstance(value, dict):
        return {k: shape(v, depth + 1) for k, v in value.items()} if depth < 4 else "{...}"
    if isinstance(value, list):
        return [f"{len(value)} item(s)"] + ([shape(value[0], depth + 1)] if value else [])
    if isinstance(value, str):
        return f"str[{len(value)}]"
    return type(value).__name__


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("query")
    ap.add_argument("--save", metavar="FILE", help="save the raw search response here")
    args = ap.parse_args()
    try:
        settings = load_groq_settings()
    except GroqConfigError as exc:
        print(f"Groq is not configured: {exc}")
        return 2
    client = GroqClient(settings)
    print(f"model {settings.model}; requests used so far: {client.ledger.used if client.ledger else 'not counted'}")

    # 1. The raw search call, to see exactly what Groq returns.
    try:
        raw = client.chat([{"role": "system", "content": SEARCH_SYSTEM},
                           {"role": "user", "content": search_prompt(args.query, settings.max_results)}],
                          tools=[{"type": "browser_search"}], tool_choice="required", include_reasoning=True)
    except GroqError as exc:
        print(f"FAILED: {exc}")
        return 1
    message = raw["choices"][0]["message"]
    print("\nmessage fields:", json.dumps(shape(message), indent=1, ensure_ascii=False))
    ev = collect(message)
    print(f"\nevidence: {len(ev.urls)} page link(s), {len(ev.text):,} characters of tool output, "
          f"{ev.tools_reported} tool call(s), from {ev.sources or 'nothing'}")
    print("rupee prices present in the tool output:", "YES" if ev.shows_prices else "NO  (prices cannot be verified)")
    for url in ev.urls[:8]:
        print("   ", url)
    if args.save:
        Path(args.save).write_text(json.dumps(raw, indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"raw response saved to {args.save}")

    # 2. The same query through the provider (a second search + extraction: 2 more requests).
    print("\n--- provider run (search + extraction) ---")
    try:
        result = GroqProvider(GroqClient(settings), settings).discover(ProductQuery(category=args.query))
    except ProductDiscoveryError as exc:
        print(f"FAILED: {exc}")
        return 1
    print(f"accepted {len(result.listings)}, rejected {len(result.rejected)}; notes: {result.notes or 'none'}")
    for rec in result.listings:
        print(f"  OK  ₹{rec['price']:>9,.0f}  {rec['name'][:55]}  [{rec['listing_url_type']}]  {rec['listing_url']}")
    for rej in result.rejected:
        print(f"  NO  {str(rej.name)[:55]}: {'; '.join(rej.reasons)}")
    print(f"requests used now: {client.ledger.used if client.ledger else '?'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
