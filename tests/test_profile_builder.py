from __future__ import annotations

from typing import Set

import pytest

from recipebuilder.profile_builder import ProfileRecipeBuilder, choose_profile
from recipebuilder.recipe_engine import StockRepository, generate_cocktail_recipe


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
    assert mixer.amount_ml >= 0.4 * 400


def test_generate_cocktail_recipe_uses_profile_and_fallbacks():
    responses = {
        "base_spirit": "vodka",
        "bar_id": "demo-bar",
        "carbonation_texture": "still & silky",
        "abv_lane": "low",
        "profile": "creamy_dessert",
    }

    recipe = generate_cocktail_recipe(responses, bar_id="demo-bar")

    juice_names = {s.ingredient.name for s in recipe.ingredients if s.role == "juice"}
    assert len(juice_names) <= 2
    _assert_profile_compatibility("tropical", set(), recipe.ingredients)


def test_choose_profile_explicit():
    assert choose_profile({"profile": "berry"}) == "berry"
    assert choose_profile({}) == "tropical"
