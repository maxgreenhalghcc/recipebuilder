"""Flask service exposing the recipe builder engine."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Optional

from flask import Flask, jsonify, request

from recipebuilder import FlavourAssociationModel, StockRepository, generate_cocktail_recipe
from recipebuilder.recipe_engine import UnknownBarError

app = Flask(__name__)

_REPOSITORY = StockRepository()


def _load_association_model() -> Optional[FlavourAssociationModel]:
    """Load the most appropriate flavour association model for service use."""

    weights_path = Path("data/training/latest_weights.json")
    associations_path = Path("data/flavour_associations.json")

    if weights_path.exists():
        try:
            return FlavourAssociationModel.from_weights_file(weights_path)
        except (FileNotFoundError, ValueError, json.JSONDecodeError):
            pass

    if associations_path.exists():
        try:
            return FlavourAssociationModel.from_file(associations_path)
        except (FileNotFoundError, ValueError, json.JSONDecodeError):
            pass

    return FlavourAssociationModel()


_ASSOCIATION_MODEL = _load_association_model()


def _normalise_responses(payload: Dict[str, object]) -> Dict[str, Optional[str]]:
    """Ensure all questionnaire fields are treated as strings or None."""

    normalised: Dict[str, Optional[str]] = {}
    for key, value in payload.items():
        if value is None:
            normalised[key] = None
        elif isinstance(value, str):
            normalised[key] = value
        else:
            normalised[key] = str(value)
    return normalised


def _format_ingredient_list(recipe) -> list[str]:
    """Transform ingredient suggestions into bartender-friendly text."""

    items: list[str] = []
    for suggestion in recipe.ingredients:
        name = suggestion.ingredient.name
        role = suggestion.role if suggestion.role != "base" else ""
        if suggestion.amount_ml and suggestion.amount_ml > 0:
            amount_text = f"{suggestion.amount_ml:.0f}ml"
        else:
            amount_text = "to taste"
        if role:
            items.append(f"{amount_text} {name} ({role})")
        else:
            items.append(f"{amount_text} {name}")

    if recipe.garnish:
        items.append(f"Garnish: {recipe.garnish}")
    if recipe.ice:
        items.append(f"Serve over {recipe.ice.lower()}")
    return items


def _build_recipe_html(glass: str, ingredients: list[str], steps: list[str]) -> str:
    """Create an HTML snippet mirroring the legacy integration output."""

    ingredient_items = "".join(f"<li>{item}</li>" for item in ingredients)
    step_items = "".join(f"<li>{step}</li>" for step in steps)
    return (
        f"<h3>Glass: {glass}</h3>"
        f"<h3>Ingredients:</h3><ul>{ingredient_items}</ul>"
        f"<h3>Method:</h3><ol>{step_items}</ol>"
    )


@app.route("/generate", methods=["POST"])
def generate_bespoke_cocktail():  # pragma: no cover - invoked via HTTP
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return jsonify({"error": "Request body must be a JSON object."}), 400

    bar_id = payload.get("bar_id") or "cross_axes"
    responses = _normalise_responses({key: value for key, value in payload.items() if key != "bar_id"})

    try:
        recipe = generate_cocktail_recipe(
            responses,
            bar_id=bar_id,
            repository=_REPOSITORY,
            association_model=_ASSOCIATION_MODEL,
        )
    except UnknownBarError as exc:
        return jsonify({"error": str(exc)}), 404
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    ingredients_list = _format_ingredient_list(recipe)
    recipe_html = _build_recipe_html(recipe.glassware, ingredients_list, recipe.steps)

    flavour_profile = [
        {"flavour": flavour, "weight": weight}
        for flavour, weight in recipe.flavour_profile
    ]

    response_payload = {
        "name": recipe.name,
        "glass": recipe.glassware,
        "ice": recipe.ice,
        "ingredients_list": ingredients_list,
        "garnish": recipe.garnish,
        "steps": recipe.steps,
        "flavour_profile": flavour_profile,
        "recipe_html": recipe_html,
        "notes": recipe.notes,
    }

    return jsonify(response_payload)


if __name__ == "__main__":  # pragma: no cover - manual execution entrypoint
    app.run(host="0.0.0.0", port=5000)
