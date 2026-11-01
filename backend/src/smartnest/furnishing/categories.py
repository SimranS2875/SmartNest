"""Furniture and appliance categories SmartNest can plan for.

A :class:`CategoryProfile` holds the *domain knowledge that is not product data*: what to search for, which
room it belongs in, what share of a furnishing budget it normally takes, and two plausibility guards against
accessories being mistaken for the product (a Rs 499 "refrigerator" is a magnet or a cover, not a refrigerator).
Nothing here describes a particular product, so nothing here can fabricate product information.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CategoryProfile:
    id: str
    label: str
    search_term: str                 # what is typed into the shopping search
    min_plausible_price: float       # below this the listing is almost certainly an accessory
    max_plausible_price: float
    exclude_words: tuple[str, ...]   # title words that mark an accessory / part
    default_weight: float            # share of the whole budget before renormalisation
    needs_seating: bool = False
    theme_sensitive: bool = False    # the listing title is expected to carry a theme (posters, bedding...)

    def search_query(self, themes: tuple[str, ...] = ()) -> str:
        """The shopping search text for this category; user themes are added only where a theme makes sense."""
        words = list(themes[:3]) if self.theme_sensitive else []
        return " ".join([*words, self.search_term])

    def accessory_reason(self, name: str, price: float) -> str | None:
        """Why a listing looks like an accessory rather than a ``label``, or ``None``."""
        if price < self.min_plausible_price:
            return (f"price Rs {price:,.0f} is below the plausible range for a {self.label.lower()} "
                    f"(from Rs {self.min_plausible_price:,.0f}); likely an accessory")
        if price > self.max_plausible_price:
            return (f"price Rs {price:,.0f} is above the plausible range for a {self.label.lower()} "
                    f"(up to Rs {self.max_plausible_price:,.0f})")
        lowered = name.lower()
        for word in self.exclude_words:
            if word in lowered:
                return f"the title mentions '{word}', which marks an accessory or part, not a {self.label.lower()}"
        return None


_ACCESSORIES = ("cover", "protector", "topper", "pillow", "magnet", "cleaner", "stand only", "remote", "toy",
                "miniature", "replacement", "spare", "sticker")

CATEGORIES: dict[str, CategoryProfile] = {c.id: c for c in (
    CategoryProfile("sofa", "Sofa", "sofa", 5000, 400000, _ACCESSORIES, 0.16, needs_seating=True),
    CategoryProfile("tv_unit", "TV unit", "tv unit", 2000, 150000, _ACCESSORIES, 0.06),
    CategoryProfile("bed", "Bed", "double bed", 5000, 400000, _ACCESSORIES + ("mattress only",), 0.10),
    CategoryProfile("mattress", "Mattress", "queen size mattress", 2500, 200000,
                    tuple(w for w in _ACCESSORIES if w != "topper") + ("topper only",), 0.06),
    CategoryProfile("wardrobe", "Wardrobe", "wardrobe", 4000, 300000, _ACCESSORIES + ("hanger", "organizer"), 0.09),
    CategoryProfile("dining_table", "Dining table", "dining table set", 4000, 300000,
                    _ACCESSORIES + ("runner", "placemat", "table cloth", "tablecloth"), 0.12, needs_seating=True),
    CategoryProfile("refrigerator", "Refrigerator", "refrigerator", 8000, 400000,
                    _ACCESSORIES + ("shelf", "tray", "gasket", "water filter", "bottle", " mat"), 0.16),
    CategoryProfile("kids_bed", "Kid's bed", "kids single bed", 4000, 150000, _ACCESSORIES + ("mattress only",), 0.10),
    CategoryProfile("study_table", "Study table", "study table", 2000, 100000,
                    _ACCESSORIES + ("lamp", "organizer", "stand only", "chair only"), 0.08),
    CategoryProfile("bookshelf", "Bookshelf", "bookshelf", 1500, 100000, _ACCESSORIES + ("bookend", "organizer"), 0.06),
    CategoryProfile("wall_decor", "Wall decor", "wall poster", 99, 25000, ("toy", "replacement", "spare"), 0.03,
                    theme_sensitive=True),
)}


def category(category_id: str) -> CategoryProfile:
    try:
        return CATEGORIES[category_id]
    except KeyError:
        raise KeyError(f"unknown category {category_id!r}; known: {', '.join(sorted(CATEGORIES))}") from None
