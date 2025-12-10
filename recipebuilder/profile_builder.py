"""Rule-based profile recipe builder."""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence

from recipebuilder.recipe_engine import CocktailRecipe, IngredientSuggestion, StockItem


@dataclass
class Glass:
    name: str
    capacity_ml: int
    sparkling: bool


PROFILES = [
    "tropical",
    "citrus_fresh",
    "berry",
    "classic_boozy",
    "candy_fun",
    "dessert",
]


def choose_profile(responses: Dict[str, Any]) -> str:
    explicit = (responses.get("flavour_profile") or responses.get("profile") or "").strip().lower()
    if explicit in PROFILES:
        return explicit

    scores = {profile: 0.0 for profile in PROFILES}

    season = (responses.get("season") or "").lower()
    if "summer" in season:
        scores["tropical"] += 3
        scores["candy_fun"] += 2
    if "spring" in season:
        scores["berry"] += 2
        scores["citrus_fresh"] += 2
    if "autumn" in season:
        scores["classic_boozy"] += 2
    if "winter" in season:
        scores["dessert"] += 3
        scores["classic_boozy"] += 1

    house = (responses.get("house_type") or "").lower()
    if "beach" in house:
        scores["tropical"] += 3
    if "tree" in house:
        scores["tropical"] += 2
        scores["citrus_fresh"] += 1
    if "modern" in house:
        scores["citrus_fresh"] += 2
        scores["classic_boozy"] += 1
    if "haunted" in house:
        scores["classic_boozy"] += 2
        scores["berry"] += 1

    dining = (responses.get("dining_style") or "").lower()
    if "balanced blend" in dining:
        scores["citrus_fresh"] += 2
        scores["berry"] += 2
    if "subtle" in dining or "fresh" in dining:
        scores["citrus_fresh"] += 3
    if "refreshing" in dining or "vibrant" in dining or "bright" in dining or "zesty" in dining:
        scores["tropical"] += 3
        scores["citrus_fresh"] += 2
    if "sweet tooth" in dining or "dessert" in dining or "indulging in rich flavours" in dining:
        scores["dessert"] += 3
        scores["candy_fun"] += 2

    music = (responses.get("music_preference") or "").lower()
    if "jazz" in music or "blues" in music:
        scores["classic_boozy"] += 2
    if "pop" in music:
        scores["candy_fun"] += 2
        scores["berry"] += 1
    if "rock" in music:
        scores["classic_boozy"] += 2
        scores["tropical"] += 1
    if "rap" in music:
        scores["candy_fun"] += 2
        scores["tropical"] += 1

    aroma = (responses.get("aroma_preference") or "").lower()
    if "citrus" in aroma:
        scores["citrus_fresh"] += 3
        scores["tropical"] += 1
    if "campfire" in aroma or "wood" in aroma:
        scores["classic_boozy"] += 3
    if "floral" in aroma:
        scores["berry"] += 3
        scores["citrus_fresh"] += 1
    if "sweet sugar" in aroma:
        scores["candy_fun"] += 2
        scores["dessert"] += 1

    base = (responses.get("base_spirit") or "").lower()
    if "rum" in base:
        scores["tropical"] += 3
        scores["candy_fun"] += 1
    if "gin" in base:
        scores["berry"] += 2
        scores["citrus_fresh"] += 2
        scores["candy_fun"] += 1
    if "vodka" in base:
        for profile in PROFILES:
            if profile != "dessert":
                scores[profile] += 1
    if "tequila" in base:
        scores["classic_boozy"] += 3
        scores["citrus_fresh"] += 1

    bitter = (responses.get("bitterness_tolerance") or "").lower()
    if "low" in bitter:
        scores["candy_fun"] += 2
        scores["tropical"] += 2
        scores["dessert"] += 1
    if "medium" in bitter:
        scores["citrus_fresh"] += 1
        scores["berry"] += 1
    if "high" in bitter:
        scores["classic_boozy"] += 3

    sweet_style = (responses.get("sweetener_question") or "").lower()
    if "classic" in sweet_style:
        scores["classic_boozy"] += 3
        scores["citrus_fresh"] += 1
    if "floral" in sweet_style:
        scores["berry"] += 3
    if "rich" in sweet_style:
        scores["dessert"] += 3
        scores["candy_fun"] += 2
    if "zesty" in sweet_style:
        scores["citrus_fresh"] += 3
        scores["tropical"] += 2

    abv = (responses.get("abv_lane") or "").lower()
    if "strong" in abv:
        scores["classic_boozy"] += 3
        scores["tropical"] += 1
    if "medium" in abv:
        scores["tropical"] += 1
        scores["citrus_fresh"] += 1
        scores["berry"] += 1
    if "low" in abv:
        scores["candy_fun"] += 2
        scores["citrus_fresh"] += 2

    carbonation = (responses.get("carbonation_texture") or "").lower()
    if "sparkling" in carbonation or "fizzy" in carbonation:
        scores["tropical"] += 2
        scores["citrus_fresh"] += 2
        scores["candy_fun"] += 2
    if "still" in carbonation:
        scores["classic_boozy"] += 2
        scores["dessert"] += 1

    if all(score == 0 for score in scores.values()):
        if "rum" in base:
            return "tropical"
        if "tequila" in base:
            return "classic_boozy"
        if "gin" in base:
            return "citrus_fresh"
        return "tropical"

    return max(scores.items(), key=lambda kv: kv[1])[0]


def _pick_first_matching(candidates: Sequence[StockItem], keywords: Iterable[str]) -> Optional[StockItem]:
    lowered = [kw.lower() for kw in keywords]
    for cand in candidates:
        name = cand.name.lower()
        if any(kw in name for kw in lowered):
            return cand
    return candidates[0] if candidates else None


def is_creamy(item: StockItem) -> bool:
    name = item.name.lower()
    creamy_keywords = [
        "cream",
        "baileys",
        "irish cream",
        "milk",
        "custard",
        "egg white",
        "eggwhite",
        "egg ",
    ]
    return any(keyword in name for keyword in creamy_keywords)


class ProfileRecipeBuilder:
    """Build cocktails using profile-guarded stock items."""

    def __init__(self, repository, glass_logic=None) -> None:
        self.repository = repository
        self.glass_logic = glass_logic

    def build_recipe(self, responses: Dict[str, Any], profile: str, seed: int | None = None) -> CocktailRecipe:
        rnd = random.Random(seed)
        stock = self.repository.prime_cache(responses.get("bar_id", "")) if hasattr(self.repository, "prime_cache") else self.repository.load_bar_stock(responses.get("bar_id", ""))
        all_items = [item for item in getattr(self.repository, "_all_items", stock) if not is_creamy(item)]

        abv_lane = (responses.get("abv_lane") or "medium").strip().lower()
        base_target = {"strong": 60.0, "medium": 50.0, "low": 40.0}.get(abv_lane, 50.0)

        carbonation = (responses.get("carbonation_texture") or "still").strip().lower()

        glass = self._choose_glass(responses, carbonation)

        def profile_items(role: str) -> List[StockItem]:
            items = [item for item in self.repository.items_for_profile(profile, role=role) if not is_creamy(item)]
            if role != "garnish":
                items.extend([i for i in self.repository.neutral_items(role=role) if not is_creamy(i)])
            unique = []
            seen = set()
            for item in items:
                key = item.name.lower()
                if key in seen:
                    continue
                seen.add(key)
                unique.append(item)
            return unique

        prefs = self._profile_preferences(profile)

        base = self._choose_base(responses, profile, profile_items("base"), prefs["base_keywords"])
        if base is None:
            raise ValueError("No base spirit available for the selected profile.")

        juices = self._choose_juices(profile_items("juice"), prefs["juice_keywords"], limit=2)
        sweetener = self._choose_sweetener(profile_items("sweetener"), prefs["sweetener_keywords"], profile_items("base"))
        modifier = self._choose_modifier(profile_items("modifier"), prefs["modifier_keywords"])
        sour = self._maybe_add_sour(profile, profile_items("sour"), prefs["needs_sour"])

        sweet_ml = rnd.uniform(12, 18) if sweetener else 0.0
        sour_ml = 0.0 if sour is None else prefs["sour_ml"]
        modifier_ml = 0.0 if modifier is None else 15.0
        base_ml = base_target - modifier_ml
        base_ml = max(40.0, base_ml) if abv_lane != "low" else max(25.0, base_ml)
        juice_amounts = self._assign_juice_amounts(juices, carbonation, prefs["juice_ml"])

        core_volume = base_ml + modifier_ml + sweet_ml + sour_ml + sum(juice_amounts)
        mixer_item: Optional[StockItem] = None
        mixer_ml = 0.0
        if glass.sparkling:
            mixer_item = self._select_mixer(profile_items("mixer"), prefs["mixer_keywords"], carbonation)
            mixer_ml = max(0.0, glass.capacity_ml - core_volume)
        elif glass.capacity_ml - core_volume > 40 and carbonation.startswith("lightly"):
            mixer_item = self._select_mixer(profile_items("mixer"), prefs["mixer_keywords"], carbonation)
            mixer_ml = max(25.0, min(60.0, glass.capacity_ml - core_volume)) if mixer_item else 0.0

        if mixer_item is None and glass.sparkling:
            mixer_item = _pick_first_matching(profile_items("juice"), prefs["juice_keywords"]) if juices else None
            if mixer_item:
                mixer_ml = max(25.0, glass.capacity_ml - core_volume)

        suggestions: List[IngredientSuggestion] = [IngredientSuggestion(base, base_ml, "base")]
        if modifier:
            suggestions.append(IngredientSuggestion(modifier, modifier_ml, "modifier"))
        if sweetener:
            suggestions.append(IngredientSuggestion(sweetener, sweet_ml, "sweetener"))
        for juice, amount in zip(juices, juice_amounts):
            suggestions.append(IngredientSuggestion(juice, amount, "juice"))
        if sour:
            suggestions.append(IngredientSuggestion(sour, sour_ml, "sour"))
        if mixer_item and mixer_ml > 0:
            suggestions.append(IngredientSuggestion(mixer_item, mixer_ml, "mixer"))

        garnish = self._pick_garnish(profile_items("garnish"), juices, prefs["garnish_keywords"])
        steps = self._build_steps(glass.name, mixer_item.name if mixer_item else None)

        self._validate(profile, suggestions)

        return CocktailRecipe(
            name="Signature Serve",
            glassware=glass.name,
            ice="cubed" if glass.sparkling else "none",
            ingredients=suggestions,
            steps=steps,
            flavour_profile=[(profile, 1.0)],
            garnish=garnish,
            notes=None,
            explanations=(),
        )

    def _profile_preferences(self, profile: str) -> Dict[str, Any]:
        defaults = {
            "base_keywords": ["vodka"],
            "juice_keywords": ["orange"],
            "sweetener_keywords": ["simple"],
            "modifier_keywords": [],
            "mixer_keywords": ["lemonade"],
            "garnish_keywords": ["twist"],
            "needs_sour": True,
            "sour_ml": 12.0,
            "juice_ml": (25.0, 40.0),
        }
        profiles: Dict[str, Dict[str, Any]] = {
            "tropical": {
                "base_keywords": ["rum", "vodka"],
                "juice_keywords": ["pineapple", "orange", "passion"],
                "sweetener_keywords": ["grenadine", "vanilla", "passion"],
                "modifier_keywords": ["liqueur", "schnapps"],
                "mixer_keywords": ["lemonade"],
                "garnish_keywords": ["orange", "pineapple", "mint"],
            },
            "berry": {
                "base_keywords": ["gin", "vodka"],
                "juice_keywords": ["cranberry", "berry"],
                "sweetener_keywords": ["rasp", "straw", "grenadine"],
                "mixer_keywords": ["lemonade"],
                "garnish_keywords": ["berry", "lemon"],
            },
            "citrus_fresh": {
                "base_keywords": ["gin", "vodka"],
                "juice_keywords": ["lemon", "lime", "orange", "cranberry"],
                "sweetener_keywords": ["simple", "sugar"],
                "mixer_keywords": ["soda", "light lemonade"],
                "garnish_keywords": ["lemon", "lime"],
            },
            "classic_boozy": {
                "base_keywords": ["tequila", "bourbon", "whiskey"],
                "juice_keywords": ["orange", "passion"],
                "sweetener_keywords": ["simple", "grenadine"],
                "mixer_keywords": [],
                "garnish_keywords": ["orange", "twist"],
                "needs_sour": False,
                "juice_ml": (20.0, 30.0),
            },
            "candy_fun": {
                "base_keywords": ["vodka", "gin"],
                "juice_keywords": ["cranberry", "berry", "orange"],
                "sweetener_keywords": ["blue", "grenadine", "rasp"],
                "mixer_keywords": ["lemonade"],
                "garnish_keywords": ["berry", "orange"],
            },
            "dessert": {
                "base_keywords": ["vodka", "rum"],
                "juice_keywords": ["passion", "pineapple"],
                "sweetener_keywords": ["vanilla", "caramel", "passion"],
                "modifier_keywords": ["coffee", "cocoa"],
                "mixer_keywords": ["soda", "lemonade"],
                "garnish_keywords": ["orange", "passion", "cherry"],
                "needs_sour": True,
                "sour_ml": 10.0,
                "juice_ml": (20.0, 35.0),
            },
        }
        return {**defaults, **profiles.get(profile, {})}

    def _choose_glass(self, responses: Dict[str, Any], carbonation: str) -> Glass:
        mapping = {
            "beach house": Glass("long glass", 400, sparkling=True),
            "modern house": Glass("martini glass", 250, sparkling=False),
            "haunted house": Glass("skull glass", 400, sparkling=False),
            "tree house": Glass("gin glass", 500, sparkling=True),
        }
        glass = mapping.get((responses.get("house_type") or "").strip().lower())
        if glass:
            return glass
        if carbonation.startswith("properly") or carbonation.startswith("light"):
            return Glass("long glass", 400, sparkling=True)
        return Glass("martini glass", 250, sparkling=False)

    def _choose_base(self, responses: Dict[str, Any], profile: str, items: Sequence[StockItem], keywords: Sequence[str]) -> Optional[StockItem]:
        desired = (responses.get("base_spirit") or "").lower()
        if desired:
            match = _pick_first_matching(items, [desired])
            if match:
                return match
        preferred = _pick_first_matching(items, keywords)
        return preferred or (items[0] if items else None)

    def _choose_juices(self, items: Sequence[StockItem], keywords: Sequence[str], limit: int) -> List[StockItem]:
        filtered = [i for i in items if not is_creamy(i)]
        picks: List[StockItem] = []
        for keyword in keywords:
            match = _pick_first_matching([i for i in filtered if i not in picks], [keyword])
            if match and match not in picks:
                picks.append(match)
            if len(picks) >= limit:
                break
        return picks[:limit]

    def _choose_sweetener(self, sweeteners: Sequence[StockItem], keywords: Sequence[str], flavoured_bases: Sequence[StockItem]) -> Optional[StockItem]:
        sweeteners = [s for s in sweeteners if not is_creamy(s)]
        match = _pick_first_matching(sweeteners, keywords)
        if match:
            return match
        return _pick_first_matching(flavoured_bases, keywords)

    def _choose_modifier(self, modifiers: Sequence[StockItem], keywords: Sequence[str]) -> Optional[StockItem]:
        modifiers = [m for m in modifiers if not is_creamy(m)]
        if not modifiers:
            return None
        return _pick_first_matching(modifiers, keywords) or modifiers[0]

    def _maybe_add_sour(self, profile: str, sours: Sequence[StockItem], needs_sour: bool) -> Optional[StockItem]:
        if not needs_sour:
            return None
        return sours[0] if sours else None

    def _assign_juice_amounts(self, juices: Sequence[StockItem], carbonation: str, ml_range: tuple[float, float]) -> List[float]:
        if not juices:
            return []
        base, high = ml_range
        amounts = []
        for idx, _ in enumerate(juices):
            amt = high - idx * 5
            if carbonation.startswith("properly"):
                amt = max(base, amt - 5)
            amounts.append(max(base, amt))
        return amounts

    def _select_mixer(self, mixers: Sequence[StockItem], keywords: Sequence[str], carbonation: str) -> Optional[StockItem]:
        if not mixers and not carbonation.startswith("properly"):
            return None
        target_keywords = list(keywords)
        if "tonic" not in target_keywords and "tonic" in [m.name.lower() for m in mixers]:
            target_keywords.append("tonic")
        return _pick_first_matching(list(mixers), target_keywords) if mixers else None

    def _pick_garnish(self, garnishes: Sequence[StockItem], juices: Sequence[StockItem], keywords: Sequence[str]) -> str:
        choices = list(garnishes)
        if juices:
            juice_tokens = " ".join(j.name.lower() for j in juices).split()
            garnish = _pick_first_matching(choices, juice_tokens)
            if garnish:
                return garnish.name
        garnish = _pick_first_matching(choices, keywords)
        return garnish.name if garnish else ""

    def _build_steps(self, glass: str, mixer: Optional[str]) -> List[str]:
        steps = [
            f"Add ingredients to a shaker and shake hard.",
            f"Strain into a chilled {glass}.",
        ]
        if mixer:
            steps.append(f"Top with {mixer}.")
        steps.append("Garnish and serve.")
        return steps

    def _validate(self, profile: str, suggestions: Sequence[IngredientSuggestion]) -> None:
        juices = [s for s in suggestions if s.role == "juice"]
        sweeteners = [s for s in suggestions if s.role == "sweetener" and not s.ingredient.neutral]
        if len(juices) > 2:
            raise ValueError("Too many juices selected")
        if len(sweeteners) > 1:
            raise ValueError("Too many flavoured sweeteners selected")
        for suggestion in suggestions:
            item = suggestion.ingredient
            if suggestion.role in {"garnish", "mixer"}:
                continue
            if profile not in item.profiles and not item.neutral:
                raise ValueError(f"Ingredient {item.name} not compatible with profile {profile}")
            if profile in item.avoid_profiles:
                raise ValueError(f"Ingredient {item.name} avoids profile {profile}")

