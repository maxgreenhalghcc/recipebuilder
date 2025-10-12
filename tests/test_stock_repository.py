import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from recipebuilder.recipe_engine import StockRepository  # noqa: E402


def test_loads_cross_axes_bar_inventory():
    repo = StockRepository()
    ingredients = repo.load_bar_stock("cross_axes")
    names = {ingredient.name.lower() for ingredient in ingredients}
    assert "vodka" in names
    assert any("juice" in name for name in names)


def test_loads_enchanted_bar_inventory():
    repo = StockRepository()
    ingredients = repo.load_bar_stock("enchanted")
    names = {ingredient.name.lower() for ingredient in ingredients}
    assert "normal gin" in names or "gin" in names
    assert "pineapple juice" in names
