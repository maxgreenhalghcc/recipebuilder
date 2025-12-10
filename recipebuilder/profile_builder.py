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


def choose_profile(responses: Dict[str, Any]) -> str:
    explicit = (responses.get("flavour_profile") or responses.get("profile") or "").strip().lower()
    valid = {
        "tropical",
        "citrus_fresh",
        "berry",
        "classic_boozy",
        "candy_fun",
        "creamy_dessert",
    }
    if explicit in valid:
        return explicit
    return "tropical"


def _pick_first_matching(candidates: Sequence[StockItem], keywords: Iterable[str]) -> Optional[StockItem]:
    lowered = [kw.lower() for kw in keywords]
    for cand in candidates:
        name = cand.name.lower()
        if any(kw in name for kw in lowered):
            return cand
    return candidates[0] if candidates else None


class ProfileRecipeBuilder:
    """Build cocktails using profile-guarded stock items."""

    def __init__(self, repository, glass_logic=None) -> None:
        self.repository = repository
        self.glass_logic = glass_logic

    def build_recipe(self, responses: Dict[str, Any], profile: str, seed: int | None = None) -> CocktailRecipe:
        rnd = random.Random(seed)
        stock = self.repository.prime_cache(responses.get("bar_id", "")) if hasattr(self.repository, "prime_cache") else self.repository.load_bar_stock(responses.get("bar_id", ""))
        all_items = getattr(self.repository, "_all_items", stock)

        abv_lane = (responses.get("abv_lane") or "medium").strip().lower()
        target_spirit_ml = 40.0
        if abv_lane == "strong":
            target_spirit_ml = 50.0
        elif abv_lane == "low":
            target_spirit_ml = 25.0

        carbonation = (responses.get("carbonation_texture") or "still & silky").strip().lower()

        glass = self._choose_glass(responses, carbonation)

        base = self._choose_base(responses, profile, all_items)
        if base is None:
            raise ValueError("No base spirit available for the selected profile.")

        modifier = self._choose_modifier(profile, all_items)
        sweetener, sweetener_is_spirit = self._choose_sweetener(profile, all_items)
        juices = self._choose_juices(profile, all_items, limit=2)
        sour = self._maybe_add_sour(profile, all_items, responses)

        spirit_pool = target_spirit_ml
        modifier_ml = 0.0
        if modifier and modifier.category.lower() in {"spirit", "modifier"}:
            modifier_ml = 15.0
            spirit_pool -= modifier_ml

        sweet_ml = rnd.uniform(12, 18)
        sweet_as_spirit_ml = 0.0
        if sweetener_is_spirit:
            sweet_as_spirit_ml = min(15.0, sweet_ml)
            spirit_pool -= sweet_as_spirit_ml

        base_ml = max(20.0, spirit_pool)
        if base_ml + sweet_as_spirit_ml + modifier_ml < target_spirit_ml:
            base_ml = target_spirit_ml - sweet_as_spirit_ml - modifier_ml

        juice_amounts = []
        for idx, _ in enumerate(juices):
            juice_amounts.append(30.0 if carbonation.startswith("still") else 25.0 - idx * 2)

        sour_ml = 0.0 if sour is None else 12.0

        core_volume = base_ml + modifier_ml + sweet_ml + sum(juice_amounts) + sour_ml
        mixer_name = None
        mixer_ml = 0.0

        if carbonation.startswith("still"):
            desired = min(glass.capacity_ml * 0.85, glass.capacity_ml - 20)
            extra = max(0.0, desired - core_volume)
            if extra > 0 and juices:
                juice_amounts[0] += extra
            core_volume = base_ml + modifier_ml + sweet_ml + sum(juice_amounts) + sour_ml
        elif carbonation.startswith("lightly"):
            mixer_name = self._select_lengthener(responses, prefer_tonic=False)
            mixer_ml = max(0.0, min(glass.capacity_ml * 0.4, glass.capacity_ml - core_volume))
        else:
            mixer_name = self._select_lengthener(responses, prefer_tonic=True)
            mixer_ml = max(glass.capacity_ml * 0.5, glass.capacity_ml - core_volume)

        suggestions: List[IngredientSuggestion] = []
        suggestions.append(IngredientSuggestion(base, base_ml, "base"))
        if modifier:
            suggestions.append(IngredientSuggestion(modifier, modifier_ml, "modifier"))
        if sweetener:
            suggestions.append(IngredientSuggestion(sweetener, sweet_ml, "sweetener"))
        for juice, amount in zip(juices, juice_amounts):
            suggestions.append(IngredientSuggestion(juice, amount, "juice"))
        if sour:
            suggestions.append(IngredientSuggestion(sour, sour_ml, "sour"))
        if mixer_name:
            mixer_item = _pick_first_matching(
                [item for item in all_items if item.role == "mixer" or "lemonade" in item.name.lower() or "soda" in item.name.lower()],
                [mixer_name],
            )
            if mixer_item:
                suggestions.append(IngredientSuggestion(mixer_item, mixer_ml, "mixer"))
            elif juices and mixer_ml > 0:
                # Fallback: reuse the primary juice as a lengthener when no mixer exists in stock.
                suggestions.append(IngredientSuggestion(juices[0], mixer_ml, "mixer"))

        garnish = self._pick_garnish(profile, all_items, juices)
        steps = self._build_steps(glass.name, mixer_name)

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
        if carbonation.startswith("properly") or carbonation.startswith("lightly"):
            return Glass("long glass", 400, sparkling=True)
        return Glass("martini glass", 250, sparkling=False)

    def _choose_base(self, responses: Dict[str, Any], profile: str, items: Sequence[StockItem]) -> Optional[StockItem]:
        desired = (responses.get("base_spirit") or "").lower()
        profile_items = [item for item in items if (profile in item.profiles or item.neutral) and item.role == "base"]
        if desired:
            match = _pick_first_matching(profile_items or items, [desired])
            if match:
                return match
        return profile_items[0] if profile_items else None

    def _choose_modifier(self, profile: str, items: Sequence[StockItem]) -> Optional[StockItem]:
        mods = [item for item in items if (profile in item.profiles or item.neutral) and item.role == "modifier"]
        return mods[0] if mods else None

    def _choose_sweetener(self, profile: str, items: Sequence[StockItem]) -> tuple[Optional[StockItem], bool]:
        sweeteners = [item for item in items if (profile in item.profiles or item.neutral) and item.role == "sweetener"]
        if sweeteners:
            return sweeteners[0], False
        flavoured_spirits = [
            item
            for item in items
            if item.role == "base" and (profile in item.profiles or item.neutral) and any(tag for tag in item.flavour_tags if tag)
        ]
        return (flavoured_spirits[0], True) if flavoured_spirits else (None, False)

    def _choose_juices(self, profile: str, items: Sequence[StockItem], limit: int = 2) -> List[StockItem]:
        juices = [item for item in items if (profile in item.profiles or item.neutral) and item.role == "juice"]
        unique: List[StockItem] = []
        for juice in juices:
            if any(existing.name.lower() == juice.name.lower() for existing in unique):
                continue
            unique.append(juice)
            if len(unique) >= limit:
                break
        return unique

    def _maybe_add_sour(self, profile: str, items: Sequence[StockItem], responses: Dict[str, Any]) -> Optional[StockItem]:
        wants_citrus = profile in {"citrus_fresh", "tropical", "candy_fun"}
        if not wants_citrus:
            return None
        sour_items = [item for item in items if item.role == "sour" and (profile in item.profiles or item.neutral)]
        return sour_items[0] if sour_items else None

    def _select_lengthener(self, responses: Dict[str, Any], prefer_tonic: bool) -> str:
        bitterness = (responses.get("bitterness_tolerance") or "medium").lower()
        if prefer_tonic and bitterness == "high":
            return "tonic"
        if prefer_tonic:
            return "lemonade"
        if bitterness == "high":
            return "tonic"
        return "lemonade"

    def _pick_garnish(self, profile: str, items: Sequence[StockItem], juices: Sequence[StockItem]) -> str:
        choices = [item for item in items if item.role == "garnish" and (profile in item.profiles or item.neutral)]
        if juices:
            juice_names = " ".join(j.name.lower() for j in juices)
            garnish = _pick_first_matching(choices, [token for token in juice_names.split()])
            if garnish:
                return garnish.name
        return choices[0].name if choices else ""

    def _build_steps(self, glass: str, mixer: Optional[str]) -> List[str]:
        steps = [
            f"Add ingredients to a shaker and shake hard.",
            f"Strain into a chilled {glass}.",
        ]
        if mixer:
            steps.append(f"Top with {mixer}.")
        steps.append("Garnish and serve.")
        return steps

