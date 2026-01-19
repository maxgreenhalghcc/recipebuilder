from __future__ import annotations

from collections import defaultdict
from typing import Set

import pytest

from recipebuilder.profile_builder import (
    PROFILES,
    ProfileRecipeBuilder,
    _ingredient_overlap_ratio,
    choose_profile,
)
from recipebuilder.recipe_engine import StockItem, StockRepository
from recipebuilder.preferences import _tokenise_values


class _MiniRepository:
    def __init__(self, items):
        self._all_items = list(items)
        cache = defaultdict(list)
        for item in self._all_items:
            profiles = item.profiles or {"neutral"}
            for profile in profiles:
                cache[profile].append(item)
            if item.neutral:
                cache["neutral"].append(item)
        self._profile_cache = cache

    def items_for_profile(self, profile, *, role=None):
        items = [
            item
            for item in self._profile_cache.get(profile, [])
            if (profile in item.profiles or item.neutral) and profile not in item.avoid_profiles
        ]
        if role:
            items = [i for i in items if i.role == role]
        return items

    def neutral_items(self, *, role=None):
        items = [i for i in self._all_items if i.neutral]
        if role:
            items = [i for i in items if i.role == role]
        return items

    def find_flavoured_spirits(self, base_family, flavour, profile=None):
        flavour = flavour.lower()
        results = []
        for item in self._all_items:
            if item.category != "spirit":
                continue
            if base_family not in item.name.lower():
                continue
            if flavour not in item.name.lower():
                continue
            if profile and profile in item.avoid_profiles:
                continue
            results.append(item)
        return results

    def prime_cache(self, bar_id):
        return self._all_items


def _assert_profile_compatibility(profile: str, names: Set[str], suggestions):
    for suggestion in suggestions:
        item = suggestion.ingredient
        if suggestion.role in {"garnish", "mixer"}:
            continue
        assert (
            profile in item.profiles or item.neutral
        ), f"{item.name} not compatible with profile {profile} (profiles={item.profiles}, neutral={item.neutral})"


def test_profile_builder_respects_profile_and_limits_juices():
    repo = StockRepository()
    repo.prime_cache("demo-bar")
    builder = ProfileRecipeBuilder(repo)

    responses = {
        "bar_id": "demo-bar",
        "base_spirit": "rum",
        "carbonation_texture": "properly sparkling",
        "abv_lane": "medium",
        "house_type": "beach house",
    }

    recipe = builder.build_recipe(responses, profile="tropical", seed=7)

    juice_names = {s.ingredient.name for s in recipe.ingredients if s.role == "juice"}
    sweeteners = [s for s in recipe.ingredients if s.role == "sweetener"]
    flavoured_sweeteners = [s for s in sweeteners if not s.ingredient.neutral]

    assert len(juice_names) <= 2
    assert len(flavoured_sweeteners) <= 1
    _assert_profile_compatibility("tropical", set(), recipe.ingredients)

    mixer = next((s for s in recipe.ingredients if s.role == "mixer"), None)
    assert mixer is not None
    assert mixer.amount_ml > 0


def test_sweet_profiles_keep_sour_in_range():
    repo = StockRepository()
    repo.prime_cache("demo-bar")
    builder = ProfileRecipeBuilder(repo)

    responses = {
        "bar_id": "demo-bar",
        "base_spirit": "vodka",
        "house_type": "modern house",
        "sweetener_question": "rich",
        "carbonation_texture": "still",
    }

    recipe = builder.build_recipe(responses, profile="dessert", seed=15)

    sour = next((s for s in recipe.ingredients if s.role == "sour"), None)
    assert sour is not None
    assert 15.0 <= sour.amount_ml <= 25.0


def _citrus_amount(suggestions):
    citrus_tokens = {"orange", "cranberry", "lemon", "lime", "grapefruit"}
    total = 0.0
    for suggestion in suggestions:
        if suggestion.role not in {"juice", "sour"}:
            continue
        name = suggestion.ingredient.name.lower()
        if any(tok in name for tok in citrus_tokens):
            total += suggestion.amount_ml or 0.0
    return total


def _sweet_load(suggestions):
    sweet_tokens = ("syrup", "sweet", "vanilla", "caramel", "grenadine", "honey", "passion")
    return sum(
        (suggestion.amount_ml or 0.0)
        for suggestion in suggestions
        if suggestion.role in {"sweetener", "modifier"}
        and any(tok in suggestion.ingredient.name.lower() for tok in sweet_tokens)
    )


def _thick_amount(suggestions):
    thick_tokens = ("passion", "mango", "puree")
    return sum(
        (s.amount_ml or 0.0)
        for s in suggestions
        if s.role == "juice" and any(tok in s.ingredient.name.lower() for tok in thick_tokens)
    )


def test_dessert_payload_limits_citrus_and_adds_fizz():
    repo = StockRepository()
    repo.prime_cache("demo-bar")
    builder = ProfileRecipeBuilder(repo)

    responses = {
        "bar_id": "demo-bar",
        "base_spirit": "vodka",
        "season": "autumn",
        "house_type": "modern house",
        "dining_style": "a sweet tooth indulging in rich flavours",
        "music_preference": "pop",
        "aroma_preference": "woody",
        "bitterness_tolerance": "medium",
        "sweetener_question": "floral",
        "carbonation_texture": "lightly fizzy",
        "abv_lane": "medium",
        "allergens": "none",
    }

    profile = choose_profile(responses)
    assert profile in {"dessert", "candy_fun"}

    recipe = builder.build_recipe(responses, profile=profile, seed=595512594)
    citrus_ml = _citrus_amount(recipe.ingredients)
    assert citrus_ml <= 45.0
    mixer = next((s for s in recipe.ingredients if s.role == "mixer"), None)
    assert mixer is not None
    assert any(token in mixer.ingredient.name.lower() for token in ("lemonade", "soda", "tonic"))


def test_sparkling_payload_uses_fizz_and_avoids_claggy_mix():
    repo = StockRepository()
    repo.prime_cache("demo-bar")
    builder = ProfileRecipeBuilder(repo)

    responses = {
        "bar_id": "demo-bar",
        "base_spirit": "vodka",
        "season": "summer",
        "house_type": "beach house",
        "dining_style": "refreshing and vibrant flavours which awaken my senses",
        "music_preference": "jazz/blues",
        "aroma_preference": "citrus",
        "bitterness_tolerance": "high",
        "sweetener_question": "classic",
        "carbonation_texture": "properly sparkling",
        "abv_lane": "strong",
        "allergens": "none",
    }

    profile = choose_profile(responses)
    recipe = builder.build_recipe(responses, profile=profile, seed=1025268697)

    mixer = next((s for s in recipe.ingredients if s.role == "mixer"), None)
    assert mixer is not None
    assert any(token in mixer.ingredient.name.lower() for token in ("lemonade", "soda", "tonic"))

    sweet_plus_thick = _sweet_load(recipe.ingredients) + _thick_amount(recipe.ingredients)
    assert sweet_plus_thick <= 55.0


def test_choose_profile_mapping_cases():
    assert choose_profile({"profile": "berry"}) == "berry"

    tropical_case = {
        "season": "summer",
        "house_type": "beach house",
        "base_spirit": "rum",
        "sweetener_question": "zesty",
        "bitterness_tolerance": "low",
    }
    assert choose_profile(tropical_case) == "tropical"

    berry_case = {
        "season": "spring",
        "aroma_preference": "floral",
        "base_spirit": "gin",
        "sweetener_question": "floral",
        "bitterness_tolerance": "medium",
    }
    assert choose_profile(berry_case) == "berry"

    boozy_case = {
        "season": "winter",
        "aroma_preference": "campfire wood",
        "base_spirit": "tequila",
        "sweetener_question": "classic",
        "abv_lane": "strong",
        "carbonation_texture": "still",
    }
    assert choose_profile(boozy_case) == "classic_boozy"

    dessert_case = {
        "season": "winter",
        "dining_style": "sweet tooth indulging in rich flavours",
        "aroma_preference": "sweet sugar",
        "sweetener_question": "rich",
        "base_spirit": "vodka",
    }
    assert choose_profile(dessert_case) == "dessert"


def test_choose_profile_explicit():
    assert choose_profile({"profile": "berry"}) == "berry"
    assert choose_profile({}) == "tropical"


def test_no_creamy_items_in_profiles():
    repo = StockRepository()
    repo.prime_cache("demo-bar")
    builder = ProfileRecipeBuilder(repo)
    responses = {"bar_id": "demo-bar", "house_type": "modern house"}
    for profile in PROFILES:
        recipe = builder.build_recipe(responses, profile=profile, seed=3)
        assert all(not ("cream" in s.ingredient.name.lower()) for s in recipe.ingredients)


def test_tequila_relaxation_limits_dessert_components():
    repo = StockRepository()
    repo.prime_cache("demo-bar")
    builder = ProfileRecipeBuilder(repo)

    responses = {
        "bar_id": "demo-bar",
        "base_spirit": "tequila",
        "season": "winter",
        "house_type": "modern house",
        "dining_style": "a sweet tooth indulging in rich flavours",
        "music_preference": "pop",
        "aroma_preference": "sweet sugar",
        "bitterness_tolerance": "medium",
        "sweetener_question": "rich",
        "carbonation_texture": "still",
        "abv_lane": "medium",
        "allergens": "none",
    }

    profile = choose_profile(responses)
    recipe = builder.build_recipe(responses, profile=profile, seed=2446613597)

    base_name = next(s.ingredient.name for s in recipe.ingredients if s.role == "base")
    assert "tequila" in base_name.lower()

    dessert_bits = [s for s in recipe.ingredients if getattr(s.ingredient, "dessert_only", False)]
    assert len(dessert_bits) <= 1
    assert not (
        any("amaretto" in s.ingredient.name.lower() for s in dessert_bits)
        and any("caramel" in s.ingredient.name.lower() for s in dessert_bits)
    )
    assert recipe.meta.get("used_fallback")


def test_pineapple_ratio_is_capped():
    items = [
        StockItem(
            name="Spiced Rum",
            category="spirit",
            role="base",
            profiles={"tropical"},
            avoid_profiles=set(),
            neutral=False,
            default_measure_ml=50.0,
            spirit_subtype="spiced",
        ),
        StockItem(
            name="Orange Juice",
            category="juice",
            role="juice",
            profiles={"tropical"},
            avoid_profiles=set(),
            neutral=False,
            default_measure_ml=40.0,
        ),
        StockItem(
            name="Pineapple Juice",
            category="juice",
            role="juice",
            profiles={"tropical"},
            avoid_profiles=set(),
            neutral=False,
            default_measure_ml=30.0,
        ),
        StockItem(
            name="Lemon Juice",
            category="sour",
            role="sour",
            profiles={"tropical"},
            avoid_profiles=set(),
            neutral=True,
            default_measure_ml=20.0,
        ),
        StockItem(
            name="Lemonade",
            category="mixer",
            role="mixer",
            profiles={"tropical"},
            avoid_profiles=set(),
            neutral=False,
            default_measure_ml=75.0,
        ),
    ]

    repo = _MiniRepository(items)
    builder = ProfileRecipeBuilder(repo)
    responses = {
        "bar_id": "demo", "house_type": "beach house", "base_spirit": "rum", "carbonation_texture": "properly sparkling"
    }

    recipe = builder.build_recipe(responses, profile="tropical", seed=11)

    pineapple = sum(s.amount_ml for s in recipe.ingredients if "pineapple" in s.ingredient.name.lower())
    juice_total = sum(s.amount_ml for s in recipe.ingredients if s.role in {"juice", "sour"})
    assert pineapple <= 0.25 * juice_total + 1e-6


def test_rum_subtypes_follow_profile():
    repo = StockRepository()
    repo.prime_cache("demo-bar")
    builder = ProfileRecipeBuilder(repo)

    tropical = {
        "bar_id": "demo-bar",
        "house_type": "beach house",
        "base_spirit": "rum",
        "carbonation_texture": "properly sparkling",
    }
    tropical_recipe = builder.build_recipe(tropical, profile="tropical", seed=5)
    base_name = next(s.ingredient.name for s in tropical_recipe.ingredients if s.role == "base")
    assert any(token in base_name.lower() for token in ["spiced", "dark", "aged"])

    citrus = {
        "bar_id": "demo-bar",
        "house_type": "modern house",
        "base_spirit": "rum",
        "carbonation_texture": "still",
        "abv_lane": "medium",
    }
    citrus_recipe = builder.build_recipe(citrus, profile="citrus_fresh", seed=9)
    citrus_base = next(s.ingredient.name for s in citrus_recipe.ingredients if s.role == "base")
    assert "white" in citrus_base.lower() or "light" in citrus_base.lower()


def test_build_candidates_are_diverse():
    repo = StockRepository()
    repo.prime_cache("demo-bar")
    builder = ProfileRecipeBuilder(repo)

    responses = {
        "bar_id": "demo-bar",
        "house_type": "beach house",
        "base_spirit": "rum",
        "carbonation_texture": "properly sparkling",
    }

    candidates = builder.build_candidates(responses, profile="tropical", seed=22, num_candidates=3)
    assert len(candidates) >= 2

    overlap = _ingredient_overlap_ratio(candidates[0], candidates[1])
    assert overlap <= 0.7


def test_allergen_strings_filter_stock():
    repo = StockRepository()
    repo.prime_cache("cross_axes")
    builder = ProfileRecipeBuilder(repo)

    responses = {
        "bar_id": "cross_axes",
        "house_type": "beach house",
        "base_spirit": "rum",
        "season": "summer",
        "sweetener_question": "classic",
        "carbonation_texture": "properly sparkling",
        "allergens": "coconut, nuts",
    }

    recipe = builder.build_recipe(responses, profile="tropical", seed=7)

    lowered = [s.ingredient.name.lower() for s in recipe.ingredients]
    assert not any("coconut" in name or "malibu" in name for name in lowered)


def test_allergen_none_values_are_ignored():
    tokens = _tokenise_values("none, no, NA, n/a, nope, nah, 0")
    assert tokens == set()


def test_relaxed_base_allows_recipe_when_profiles_conflict():
    repo = StockRepository()
    repo.prime_cache("demo-bar")
    builder = ProfileRecipeBuilder(repo)

    responses = {
        "bar_id": "demo-bar",
        "base_spirit": "gin",
        "season": "winter",
        "house_type": "modern house",
        "dining_style": "a balanced blend of flavours",
        "music_preference": "jazz/blues",
        "aroma_preference": "woody",
        "bitterness_tolerance": "medium",
        "sweetener_question": "classic",
        "carbonation_texture": "properly sparkling",
        "foam_toggle": "yes",
        "abv_lane": "strong",
        "allergens": "none",
    }

    recipe = builder.build_recipe(responses, profile="classic_boozy", seed=2446613597)

    base_names = [s.ingredient.name.lower() for s in recipe.ingredients if s.role == "base"]
    assert any("gin" in name for name in base_names)
