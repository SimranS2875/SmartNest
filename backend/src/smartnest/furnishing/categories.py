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


#: Title words that mark content that is not for a child.  A deliberately small, obvious list: it cannot judge
#: suitability (a title says little), so for a child's room the interface also tells the user to check each pick.
MATURE_WORDS = ("18+", "adult", "adults only", "horror", "gore", "bloody", "sexy", "nude", "erotic", "alcohol", "beer",
                "whisky", "whiskey", "vodka", "cannabis", "weed", "murder", "serial killer", "satan", "gun ", "firearm",
                "thriller", "psychological", "nightmare")


#: Title words that signal a listing is meant for children (used only to rank, never to invent attributes).
KID_WORDS = ("kids", "kid", "children", "child", "baby", "toddler", "playroom", "nursery", "cute", "cartoon", "boys", "girls")


def kid_signal(name: str) -> int:
    """How many child-oriented words the title itself contains."""
    words = set(name.lower().replace(",", " ").replace("|", " ").replace("-", " ").split())
    return sum(1 for w in KID_WORDS if w in words)


def mature_reason(name: str) -> str | None:
    """Why a title looks unsuitable for a child, or ``None``."""
    lowered = f" {name.lower()} "
    for word in MATURE_WORDS:
        if word in lowered:
            return f"the title mentions '{word.strip()}', which is not for a child's room"
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
    CategoryProfile("study_table", "Study table", "study table", 1200, 100000,
                    _ACCESSORIES + ("lamp", "organizer", "stand only", "chair only"), 0.08),
    CategoryProfile("bookshelf", "Bookshelf", "bookshelf", 700, 100000, _ACCESSORIES + ("bookend", "organizer"), 0.06),
    CategoryProfile("wall_decor", "Wall decor", "wall poster", 99, 25000, ("toy", "replacement", "spare"), 0.03,
                    theme_sensitive=True),
    CategoryProfile("study_chair", "Study chair", "study chair", 1000, 60000,
                    _ACCESSORIES + ("wheel", "caster", "armrest", "gas lift", "seat pad"), 0.05),
    CategoryProfile("table_lamp", "Table lamp", "table lamp", 199, 15000, ("bulb", "shade only", "replacement", "spare"), 0.02),
    CategoryProfile("rug", "Rug", "kids rug", 299, 60000, ("rug pad", "gripper", "replacement", "spare"), 0.04,
                    theme_sensitive=True),
    CategoryProfile("bean_bag", "Bean bag", "bean bag", 500, 20000, ("refill", "beans only", "bean refill", "spare"), 0.03),
    CategoryProfile("toy_storage", "Toy storage", "toy storage organizer", 400, 30000, ("replacement", "spare"), 0.03),
)}


def category(category_id: str) -> CategoryProfile:
    try:
        return CATEGORIES[category_id]
    except KeyError:
        raise KeyError(f"unknown category {category_id!r}; known: {', '.join(sorted(CATEGORIES))}") from None
