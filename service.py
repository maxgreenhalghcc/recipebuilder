"""Flask service exposing the recipe builder engine."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Optional

from flask import Flask, Response, jsonify, request

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


@app.get("/health")
def health_check():  # pragma: no cover - trivial endpoint
    """Simple health probe so hosting platforms can verify readiness."""

    return jsonify({"ok": True}), 200


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


def _render_recipe_text(recipe, ingredients: list[str]) -> str:
    """Produce a bartender-friendly plain text description of the recipe."""

    lines: list[str] = []

    title = recipe.name or "Signature Cocktail"
    lines.append(title)
    glass_line = f"Glass: {recipe.glassware}"
    if recipe.ice:
        glass_line += f" | Ice: {recipe.ice.lower()}"
    lines.append(glass_line)

    lines.append("")
    lines.append("Ingredients:")
    for item in ingredients:
        lines.append(f"  - {item}")

    lines.append("")
    lines.append("Method:")
    for index, step in enumerate(recipe.steps, start=1):
        lines.append(f"  {index}. {step}")

    if recipe.garnish:
        lines.append("")
        lines.append(f"Garnish: {recipe.garnish}")

    if recipe.flavour_profile:
        lines.append("")
        lines.append("Flavour focus:")
        for flavour, weight in recipe.flavour_profile:
            lines.append(f"  - {flavour}: {weight:.2f}")

    if recipe.explanations:
        lines.append("")
        lines.append("Why this works:")
        for line in recipe.explanations:
            lines.append(f"  - {line}")

    if recipe.notes:
        lines.append("")
        lines.append(f"Notes: {recipe.notes}")

    return "\n".join(lines)


def _text_error(message: str, status_code: int) -> Response:
    """Return a plain text error message."""

    return Response(f"Error: {message}\n", status=status_code, mimetype="text/plain")


@app.route("/generate", methods=["POST"])
def generate_bespoke_cocktail():  # pragma: no cover - invoked via HTTP
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return _text_error("Request body must be a JSON object.", 400)

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
        return _text_error(str(exc), 404)
    except ValueError as exc:
        return _text_error(str(exc), 400)

    ingredients_list = _format_ingredient_list(recipe)
    recipe_text = _render_recipe_text(recipe, ingredients_list)

    return Response(recipe_text + "\n", mimetype="text/plain")


if __name__ == "__main__":  # pragma: no cover - manual execution entrypoint
    app.run(host="0.0.0.0", port=5000)
