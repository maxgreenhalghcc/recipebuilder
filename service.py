"""Flask service exposing the recipe builder engine."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, Optional

from flask import Flask, Response, jsonify, request

from recipebuilder import FlavourAssociationModel, StockRepository, generate_cocktail_recipe
from recipebuilder.recipe_engine import UnknownBarError

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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

def _build_method_text(recipe) -> str:
    """Condense the method instructions into a readable paragraph."""

    if recipe.steps:
        steps = [f"{index}. {step}" for index, step in enumerate(recipe.steps, start=1)]
        return "\n".join(steps)
    return "Please use your judgement based on guest preferences."


def _collect_warnings(recipe) -> list[str]:
    warnings: list[str] = []
    if recipe.explanations:
        warnings.extend(recipe.explanations)
    if recipe.notes:
        warnings.append(recipe.notes)
    return warnings


def _extract_bar_and_session(payload: Dict[str, object]) -> tuple[str, Optional[str]]:
    """Determine the bar ID and session ID from the request payload."""
    logger.info(
        "BAR EXTRACTION: bar=%s bar_id=%s barId=%s",
        payload.get("bar"),
        payload.get("bar_id"),
        payload.get("barId"),
    )
    session_info = payload.get("session") if isinstance(payload.get("session"), dict) else None

    bar_id_value = payload.get("bar_id") or payload.get("barId")
    if session_info:
        bar_id_value = session_info.get("barId") or session_info.get("bar_id") or bar_id_value

    if bar_id_value is None:
        bar_id = "cross_axes"
    else:
        bar_id = str(bar_id_value).strip() or "cross_axes"

    session_id_value = (
        payload.get("session_id")
        or payload.get("sessionId")
        or (session_info.get("id") if session_info else None)
        or (session_info.get("sessionId") if session_info else None)
    )
    session_id = str(session_id_value) if session_id_value is not None else None

    return bar_id, session_id


def _json_error(message: str, status_code: int) -> Response:
    """Return a JSON error payload."""

    return jsonify({"error": message}), status_code


@app.route("/generate", methods=["POST"])
def generate_bespoke_cocktail():  # pragma: no cover - invoked via HTTP
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return _json_error("Request body must be a JSON object.", 400)

    bar_id, session_id = _extract_bar_and_session(payload)
    logger.info("Received generation request for bar=%s session=%s", bar_id, session_id)
    reserved_keys = {"bar_id", "barId", "session", "session_id", "sessionId"}
    responses = _normalise_responses({key: value for key, value in payload.items() if key not in reserved_keys})

    try:
        recipe = generate_cocktail_recipe(
            responses,
            bar_id=bar_id,
            repository=_REPOSITORY,
            association_model=_ASSOCIATION_MODEL,
        )
    except UnknownBarError as exc:
        return _json_error(str(exc), 404)
    except ValueError as exc:
        return _json_error(str(exc), 400)

    ingredients_list = _format_ingredient_list(recipe)
    method_text = _build_method_text(recipe)
    warnings = _collect_warnings(recipe)

    response_payload = {
        "data": {
            "barId": bar_id,
            "sessionId": session_id,
            "name": recipe.name or "Custom cocktail",
            "description": "A bespoke cocktail created from your quiz answers.",
            "body": {
                "ingredients": ingredients_list,
                "method": method_text,
                "glassware": recipe.glassware,
                "garnish": recipe.garnish or "",
                "warnings": warnings,
            },
            "abvEstimate": None,
        }
    }

    return jsonify(response_payload)


if __name__ == "__main__":  # pragma: no cover - manual execution entrypoint
    app.run(host="0.0.0.0", port=5000)
