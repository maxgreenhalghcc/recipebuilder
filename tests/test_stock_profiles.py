"""Stock classification and flavoured spirit substitution tests."""

from collections import defaultdict

from recipebuilder.profile_builder import ProfileRecipeBuilder
from recipebuilder.recipe_engine import StockItem, StockRepository


def test_demo_bar_stock_profiles_and_neutral_flags():
    repo = StockRepository()
    items = repo.load_bar_stock("demo_bar")

    orange = next(i for i in items if i.name.lower() == "orange juice")
    assert "dessert" in orange.avoid_profiles

    lemon = next(i for i in items if i.name.lower() == "lemon juice")
    assert lemon.neutral is True
    assert lemon.role == "sour"

    for item in items:
        if item.category == "garnish":
            continue
        assert item.profiles or item.neutral


class _FakeRepository:
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


def test_flavoured_spirit_used_when_syrup_missing():
    items = [
        StockItem(
            name="Vodka",
            category="spirit",
            role="base",
            profiles={"berry"},
            avoid_profiles=set(),
            neutral=False,
            default_measure_ml=50.0,
        ),
        StockItem(
            name="Raspberry Vodka",
            category="spirit",
            role="base",
            profiles={"berry"},
            avoid_profiles=set(),
            neutral=False,
            default_measure_ml=50.0,
        ),
        StockItem(
            name="Cranberry Juice",
            category="juice",
            role="juice",
            profiles={"berry"},
            avoid_profiles=set(),
            neutral=False,
            default_measure_ml=25.0,
        ),
        StockItem(
            name="Lemon Juice",
            category="sour",
            role="sour",
            profiles={"berry"},
            avoid_profiles=set(),
            neutral=True,
            default_measure_ml=20.0,
        ),
        StockItem(
            name="Lemonade",
            category="mixer",
            role="mixer",
            profiles={"berry"},
            avoid_profiles=set(),
            neutral=False,
            default_measure_ml=75.0,
        ),
    ]
    repo = _FakeRepository(items)
    builder = ProfileRecipeBuilder(repository=repo)
    responses = {
        "bar_id": "test",
        "house_type": "beach house",
        "base_spirit": "vodka",
        "abv_lane": "medium",
        "carbonation_texture": "lightly fizzy",
    }

    recipe = builder.build_recipe(responses, profile="berry", seed=123)

    spirit_names = [s.ingredient.name for s in recipe.ingredients if s.ingredient.category == "spirit"]
    assert any("raspberry vodka" in name.lower() for name in spirit_names)

    total_spirit_ml = sum(s.amount_ml for s in recipe.ingredients if s.ingredient.category == "spirit")
    assert 40 <= total_spirit_ml <= 60

    sweet_ml = sum(s.amount_ml for s in recipe.ingredients if s.role == "sweetener")
    assert sweet_ml <= 20
