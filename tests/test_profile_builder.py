from __future__ import annotations

from typing import Set

import pytest

from recipebuilder.profile_builder import PROFILES, ProfileRecipeBuilder, choose_profile
from recipebuilder.recipe_engine import StockRepository


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
