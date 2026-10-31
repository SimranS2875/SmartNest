"""Simulated product catalogues for CNP bidding agents.

Each bidding agent has its own independent catalogue.  The catalogues
are clearly labelled as simulated — they contain no real retailer URLs.

The ``ProductCatalog`` class is separate from the Assignment 2.1
``CatalogProvider`` because CNP agents need richer product attributes
(material, color, style, seating capacity) and don't need thread-safe
price/availability updates.

Future compatibility:
    The ``ProductCatalog`` interface allows replacement with real
    product APIs (Assignment 4.2) without changing the bidding agent.
"""

from __future__ import annotations

import threading
from typing import Any

# Availability values that mean the product cannot be offered at all.
_UNAVAILABLE = frozenset({"out_of_stock", "unavailable", "discontinued"})


class ProductCatalog:
    """A simulated product catalogue for a CNP bidding agent.

    Thread-safe: a bidding agent's server handles each coordinator
    connection in its own thread, so reads are protected by a lock.

    Args:
        agent_id: The agent that owns this catalogue.
        products: List of product dicts.
    """

    def __init__(
        self,
        agent_id: str,
        products: list[dict[str, Any]],
    ) -> None:
        self._agent_id = agent_id
        self._lock = threading.RLock()
        self._products: dict[str, dict[str, Any]] = {}
        for p in products:
            pid = p["product_id"]
            self._products[pid] = dict(p)

    @property
    def agent_id(self) -> str:
        return self._agent_id

    def get_all_products(self) -> list[dict[str, Any]]:
        """Return all products in the catalogue."""
        with self._lock:
            return [dict(p) for p in self._products.values()]

    def get_product(self, product_id: str) -> dict[str, Any] | None:
        """Return a single product by ID, or ``None``."""
        with self._lock:
            p = self._products.get(product_id)
            return dict(p) if p else None

    def get_in_stock_products(self) -> list[dict[str, Any]]:
        """Return only products that are in stock."""
        with self._lock:
            return [
                dict(p) for p in self._products.values()
                if p.get("availability") == "in_stock"
            ]

    def find_eligible_products(
        self,
        max_budget: float,
        room_width: float | None,
        preferred_style: str = "",
        seating_capacity: int | None = None,
        max_width: float | None = None,
    ) -> list[dict[str, Any]]:
        """Find products that satisfy all given constraints.

        Args:
            max_budget: Maximum allowed price.
            room_width: Available room width (products must not exceed).
                ``None`` means the width has not been announced, so no
                width filter is applied.
            preferred_style: If given, filter by style (case-insensitive).
            seating_capacity: If given, filter by minimum seating capacity.
            max_width: Explicit max width override (defaults to room_width).

        Returns:
            List of eligible product dicts (copies).  Products that can't
            be bought (out of stock, unavailable, discontinued) are never
            eligible; low-stock or backorder items are, and the
            coordinator's evaluator scores the delivery risk.
        """
        width_limit = max_width if max_width is not None else room_width
        results: list[dict[str, Any]] = []

        with self._lock:
            products = [dict(p) for p in self._products.values()]

        for p in products:
            if str(p.get("availability", "")).lower() in _UNAVAILABLE or not p.get("availability"):
                continue
            if p.get("price", 0) > max_budget:
                continue
            if width_limit is not None and p.get("width_cm", 0) > width_limit:
                continue
            if seating_capacity and p.get("seating_capacity", 0) < seating_capacity:
                continue
            if preferred_style and p.get("style", "").lower() != preferred_style.lower():
                continue
            results.append(p)

        return results


# ── Default simulated catalogues ────────────────────────────────────────────

def catalog_agent_a() -> ProductCatalog:
    """Simulated catalogue for Product Research Agent A.

    All entries are simulated data for educational purposes.
    No real retailer listings or URLs are included.
    """
    return ProductCatalog(
        agent_id="research_agent_a",
        products=[
            {
                "product_id": "SOFA_A1",
                "name": "Minimalist Three-Seater Sofa A1",
                "price": 24999,
                "width_cm": 230,
                "material": "fabric",
                "color": "grey",
                "style": "minimalist",
                "seating_capacity": 3,
                "availability": "in_stock",
            },
            {
                "product_id": "SOFA_A2",
                "name": "Compact Minimalist Sofa A2",
                "price": 27499,
                "width_cm": 195,
                "material": "leather",
                "color": "neutral",
                "style": "minimalist",
                "seating_capacity": 3,
                "availability": "in_stock",
            },
            {
                "product_id": "SOFA_A3",
                "name": "Premium Minimalist Sofa A3",
                "price": 29499,
                "width_cm": 205,
                "material": "fabric",
                "color": "beige",
                "style": "minimalist",
                "seating_capacity": 3,
                "availability": "in_stock",
            },
        ],
    )


def catalog_agent_b() -> ProductCatalog:
    """Simulated catalogue for Product Research Agent B.

    All entries are simulated data for educational purposes.
    No real retailer listings or URLs are included.
    """
    return ProductCatalog(
        agent_id="research_agent_b",
        products=[
            {
                "product_id": "SOFA_B1",
                "name": "Scandi Three-Seater Sofa B1",
                "price": 22999,
                "width_cm": 225,
                "material": "fabric",
                "color": "white",
                "style": "minimalist",
                "seating_capacity": 3,
                "availability": "in_stock",
            },
            {
                "product_id": "SOFA_B2",
                "name": "Urban Minimalist Sofa B2",
                "price": 28499,
                "width_cm": 200,
                "material": "leather",
                "color": "grey",
                "style": "minimalist",
                "seating_capacity": 3,
                "availability": "in_stock",
            },
            {
                "product_id": "SOFA_B3",
                "name": "Slim Profile Sofa B3",
                "price": 26999,
                "width_cm": 190,
                "material": "fabric",
                "color": "neutral",
                "style": "minimalist",
                "seating_capacity": 3,
                "availability": "in_stock",
            },
        ],
    )
