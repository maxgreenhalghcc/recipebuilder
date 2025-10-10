"""Static preference mappings derived from questionnaire options."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

OZ_TO_ML = 29.5735


def oz_to_ml(oz: float) -> float:
    """Convert fluid ounces to millilitres."""
    return oz * OZ_TO_ML


MUSIC_STRENGTH: Dict[str, float] = {
    "jazz/blues": 1.5,
    "rap": 1.75,
    "pop": 2.0,
    "rock": 2.25,
}

DINING_BALANCES: Dict[str, Dict[str, float]] = {
    "a balanced blend of flavours": {"modifier": 0.75, "sweetener": 0.5},
    "subtle tastes which advertise freshness": {"modifier": 0.5, "sweetener": 0.25},
    "refreshing and vibrant flavours which awaken my senses": {
        "modifier": 0.75,
        "sweetener": 0.25,
    },
    "a sweet tooth indulging in rich flavours": {"modifier": 1.0, "sweetener": 0.75},
}

COLOR_MODIFIERS: Dict[str, List[str]] = {
    "emerald": ["mint", "herbal", "fresh"],
    "amber": ["maple", "rich", "sweet"],
    "pale pink": ["berry", "sweet"],
    "citrus yellow": ["citrus", "zesty"],
}

SWEETENER_STYLES: Dict[str, List[str]] = {
    "classic": ["sweet", "balanced"],
    "rich": ["maple", "rich", "caramel"],
    "floral": ["floral", "honey"],
    "zesty": ["citrus", "zesty"],
}

SEASONAL_ACCENTS: Dict[str, List[str]] = {
    "spring": ["elderflower", "floral", "fresh"],
    "summer": ["mint", "citrus", "tropical"],
    "autumn": ["maple", "spice", "stone-fruit"],
    "winter": ["cranberry", "vanilla", "baking spice"],
}

AROMA_GARNISH: Dict[str, str] = {
    "floral": "mint sprig",
    "citrus": "lemon twist",
    "woody": "rosemary sprig",
    "sweet": "seasonal fruit slice",
}

DESSERT_TWISTS: Dict[str, List[str]] = {
    "vanilla": ["vanilla", "rich", "sweet"],
    "tangfastics": ["zesty", "vibrant", "sour"],
    "fresh fruit": ["fresh", "subtle", "natural"],
}

GLASSWARE: Dict[str, Dict[str, float]] = {
    "modern house": {"type": "short glass", "min_ml": 150},
    "tree house": {"type": "gin glass", "min_ml": 250},
    "beach house": {"type": "long glass", "min_ml": 300},
    "haunted house": {"type": "skull glass", "min_ml": 100},
}

JUICE_PREFERENCES: Dict[str, str] = {
    "spring": "orange",
    "summer": "pineapple",
    "autumn": "cranberry",
    "winter": "passion fruit",
}

DINING_JUICE_MAP: Dict[str, str] = {
    "a balanced blend of flavours": "orange",
    "subtle tastes which advertise freshness": "cranberry",
    "refreshing and vibrant flavours which awaken my senses": "pineapple",
    "a sweet tooth indulging in rich flavours": "passion fruit",
}

FLAVOUR_PROFILE_MAP: Dict[str, List[str]] = {
    "orange": ["balanced", "classic", "zesty"],
    "pineapple": ["vibrant", "tropical", "zesty"],
    "cranberry": ["fresh", "subtle", "dry"],
    "passion fruit": ["sweet", "indulgent", "exotic"],
}

BASE_SPIRIT_TAGS: Dict[str, List[str]] = {
    "gin": ["herbal", "citrus", "floral"],
    "vodka": ["clean", "citrus"],
    "rum": ["tropical", "molasses", "sweet"],
    "whisky": ["oak", "smoke", "spice"],
    "tequila": ["agave", "citrus", "tropical"],
    "mezcal": ["smoke", "agave"],
    "brandy": ["stone-fruit", "oak", "rich"],
}

HOUSE_STYLE_TAGS: Dict[str, List[str]] = {
    "modern house": ["bright", "polished", "citrus"],
    "tree house": ["fresh", "herbal", "earthy"],
    "beach house": ["tropical", "refreshing", "citrus"],
    "haunted house": ["rich", "dark", "spice"],
}

MUSIC_PROFILE_TAGS: Dict[str, List[str]] = {
    "jazz/blues": ["smooth", "citrus", "herbal"],
    "rap": ["bold", "spice", "sweet"],
    "pop": ["bright", "sweet", "tropical"],
    "rock": ["bold", "smoke", "rich"],
}

DINING_PROFILE_TAGS: Dict[str, List[str]] = {
    "a balanced blend of flavours": ["balanced", "citrus", "classic"],
    "subtle tastes which advertise freshness": ["fresh", "subtle", "herbal"],
    "refreshing and vibrant flavours which awaken my senses": ["refreshing", "vibrant", "zesty"],
    "a sweet tooth indulging in rich flavours": ["sweet", "indulgent", "rich"],
}

AROMA_PROFILE_TAGS: Dict[str, List[str]] = {
    "floral": ["floral", "aromatic"],
    "citrus": ["citrus", "bright"],
    "woody": ["oak", "herbal"],
    "sweet": ["sweet", "dessert"],
}

DESSERT_PROFILE_TAGS: Dict[str, List[str]] = {
    key: values for key, values in DESSERT_TWISTS.items()
}

MODIFIER_PROFILE_TAGS: Dict[str, List[str]] = {
    key: values for key, values in COLOR_MODIFIERS.items()
}

SWEETENER_PROFILE_TAGS: Dict[str, List[str]] = {
    key: values for key, values in SWEETENER_STYLES.items()
}

SEASON_PROFILE_TAGS: Dict[str, List[str]] = {
    key: values for key, values in SEASONAL_ACCENTS.items()
}


@dataclass
class PreferencePlan:
    """Pre-computed pour and flavour information for a guest."""

    strength_oz: float
    modifier_oz: float
    sweetener_oz: float
    glass_type: str
    glass_min_ml: float
    garnish_hint: str | None
    juice_focus: str | None

    modifier_tags: Sequence[str]
    sweetener_tags: Sequence[str]
    juice_tags: Sequence[str]
    seasonal_tags: Sequence[str]

    @property
    def base_ml(self) -> float:
        return oz_to_ml(self.strength_oz)

    @property
    def modifier_ml(self) -> float:
        return oz_to_ml(self.modifier_oz)

    @property
    def sweetener_ml(self) -> float:
        return oz_to_ml(self.sweetener_oz)

    def total_core_ml(self) -> float:
        return self.base_ml + self.modifier_ml + self.sweetener_ml


def build_preference_plan(responses: Dict[str, Optional[str]]) -> PreferencePlan:
    music_key = (responses.get("music_preference") or "").strip().lower()
    dining_key = (responses.get("dining_style") or "").strip().lower()
    house_key = (responses.get("house_type") or "").strip().lower()
    modifier_key = (responses.get("modifier_question") or "").strip().lower()
    sweetener_key = (responses.get("sweetener_question") or "").strip().lower()
    season_key = (responses.get("season") or "").strip().lower()

    strength = MUSIC_STRENGTH.get(music_key, 2.0)
    dining_balance = DINING_BALANCES.get(
        dining_key,
        {"modifier": 0.75, "sweetener": 0.5},
    )
    glass = GLASSWARE.get(house_key, {"type": "double old fashioned", "min_ml": 160})
    garnish = AROMA_GARNISH.get((responses.get("aroma_preference") or "").strip().lower())

    juice_choice = JUICE_PREFERENCES.get(season_key)
    dining_juice = DINING_JUICE_MAP.get(dining_key)
    juice_focus = juice_choice or dining_juice
    if not juice_focus and dining_juice:
        juice_focus = dining_juice

    return PreferencePlan(
        strength_oz=strength,
        modifier_oz=dining_balance.get("modifier", 0.75),
        sweetener_oz=dining_balance.get("sweetener", 0.5),
        glass_type=glass.get("type", "double old fashioned"),
        glass_min_ml=float(glass.get("min_ml", 160.0)),
        garnish_hint=garnish,
        juice_focus=juice_focus,
        modifier_tags=MODIFIER_PROFILE_TAGS.get(modifier_key, []),
        sweetener_tags=SWEETENER_PROFILE_TAGS.get(sweetener_key, []),
        juice_tags=FLAVOUR_PROFILE_MAP.get(juice_focus or "", []),
        seasonal_tags=SEASON_PROFILE_TAGS.get(season_key, []),
    )


def collect_profile_tags(
    responses: Dict[str, Optional[str]], plan: PreferencePlan
) -> Dict[str, float]:
    """Aggregate flavour keywords derived from questionnaire responses."""

    weights: Dict[str, float] = {}

    def _add(tags: Sequence[str], weight: float = 1.0) -> None:
        for tag in tags:
            key = tag.strip().lower()
            if not key:
                continue
            weights[key] = weights.get(key, 0.0) + weight

    base_key = (responses.get("base_spirit") or "").strip().lower()
    house_key = (responses.get("house_type") or "").strip().lower()
    music_key = (responses.get("music_preference") or "").strip().lower()
    dining_key = (responses.get("dining_style") or "").strip().lower()
    dessert_key = (responses.get("favourite_dessert") or "").strip().lower()
    aroma_key = (responses.get("aroma_preference") or "").strip().lower()

    _add(BASE_SPIRIT_TAGS.get(base_key, []), 0.8)
    _add(HOUSE_STYLE_TAGS.get(house_key, []), 0.6)
    _add(MUSIC_PROFILE_TAGS.get(music_key, []), 0.6)
    _add(DINING_PROFILE_TAGS.get(dining_key, []), 0.9)
    _add(DESSERT_PROFILE_TAGS.get(dessert_key, []), 0.7)
    _add(AROMA_PROFILE_TAGS.get(aroma_key, []), 0.8)

    _add(plan.modifier_tags, 0.6)
    _add(plan.sweetener_tags, 0.6)
    _add(plan.juice_tags, 0.6)
    _add(plan.seasonal_tags, 0.5)

    notes = (responses.get("notes") or "").lower()
    if notes:
        if "smok" in notes:
            _add(["smoke"], 0.7)
        if "citrus" in notes:
            _add(["citrus"], 0.7)
        if "sweet" in notes:
            _add(["sweet"], 0.6)
        if "bitter" in notes:
            _add(["bitter"], 0.5)
        if "herb" in notes:
            _add(["herbal"], 0.5)
        if "spice" in notes:
            _add(["spice"], 0.5)

    total = sum(weights.values())
    if total:
        return {tag: weight / total for tag, weight in weights.items()}
    return weights
