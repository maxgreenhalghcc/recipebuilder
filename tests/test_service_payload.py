"""Tests for the Flask service response payload formatting."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("flask")

import service


class DummySuggestion:
    def __init__(self, name: str, role: str, amount_ml: float) -> None:
        self.ingredient = SimpleNamespace(name=name)
        self.role = role
        self.amount_ml = amount_ml


def _build_dummy_recipe():
    return SimpleNamespace(
        name="Test Drink",
        glassware="martini glass",
        ice="none",
        garnish="lemon twist",
        steps=["Shake with ice", "Fine strain"],
        explanations=["Matches citrus focus"],
        notes="Guest prefers bright citrus",
        ingredients=[DummySuggestion("Vodka", "base", 50.0)],
        flavour_profile=[("citrus", 0.8)],
    )


@pytest.fixture()
def client():
    service.app.testing = True
    return service.app.test_client()


def test_generate_returns_structured_payload(monkeypatch, client):
    dummy_recipe = _build_dummy_recipe()

    def fake_build_candidates(self, responses, profile, seed=None, num_candidates=3, max_attempts=10):
        return [dummy_recipe]

    monkeypatch.setattr(service.ProfileRecipeBuilder, "build_candidates", fake_build_candidates)
    monkeypatch.setattr(service, "choose_profile", lambda responses: "tropical")

    payload = {
        "bar_id": "cross_axes",
        "base_spirit": "gin",
        "season": "summer",
        "house_type": "modern house",
    }

    response = client.post("/generate", json=payload)
    assert response.status_code == 200

    data = response.get_json()
    assert data["data"]["body"]["glassware"] == "martini glass"
    assert data["data"]["body"]["garnish"] == "lemon twist"
    assert data["data"]["body"]["ingredients"][0].startswith("50ml Vodka")
    assert "1. Shake with ice" in data["data"]["body"]["method"]
    assert data["data"]["body"]["warnings"] == [
        "Matches citrus focus",
        "Guest prefers bright citrus",
    ]


def test_mixer_formatting_is_top_with():
    dummy_recipe = _build_dummy_recipe()
    dummy_recipe.ingredients.append(DummySuggestion("Lemonade", "mixer", 120.0))

    items = service._format_ingredient_list(dummy_recipe)

    assert any(item.lower().startswith("top with lemonade") for item in items)
    assert not any("ml lemonade" in item.lower() for item in items)


def test_generate_uses_session_identifiers(monkeypatch, client):
    dummy_recipe = _build_dummy_recipe()
    captured = {}

    def fake_build_candidates(self, responses, profile, seed=None, num_candidates=3, max_attempts=10):
        captured["bar_id"] = responses.get("bar_id")
        return [dummy_recipe]

    monkeypatch.setattr(service.ProfileRecipeBuilder, "build_candidates", fake_build_candidates)
    monkeypatch.setattr(service, "choose_profile", lambda responses: "tropical")

    payload = {
        "session": {"barId": "enchanted", "id": "session-123"},
        "base_spirit": "vodka",
        "season": "spring",
        "house_type": "tree house",
    }

    response = client.post("/generate", json=payload)
    assert response.status_code == 200

    data = response.get_json()
    assert captured["bar_id"] == "enchanted"
    assert data["data"]["barId"] == "enchanted"
    assert data["data"]["sessionId"] == "session-123"


def test_recent_recipe_cache_tracks_duplicates():
    service._RECENT_RECIPES.clear()
    recipe_dict = {
        "body": {
            "ingredients": ["50ml Vodka", "Top with lemonade (mixer)"],
            "glassware": "long glass",
            "garnish": "lemon twist",
        }
    }

    assert not service._is_recent_duplicate("demo-bar", "session-x", recipe_dict)
    service._remember_recipe("demo-bar", "session-x", recipe_dict)
    assert service._is_recent_duplicate("demo-bar", "session-x", recipe_dict)
