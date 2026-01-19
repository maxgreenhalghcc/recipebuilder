"""Flask service exposing the recipe builder engine."""
from __future__ import annotations

import collections
import json
import logging
from collections import deque
from pathlib import Path
from typing import Any, Deque, Dict, FrozenSet, Hashable, Optional, Tuple

from flask import Flask, Response, jsonify, request

from recipebuilder import (
    FlavourAssociationModel,
    ProfileRecipeBuilder,
    StockRepository,
    choose_profile,
    generate_cocktail_recipe,
)
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

_RECENT_RECIPES: Dict[Tuple[str, Hashable], Deque[Tuple[FrozenSet[str], str, str]]] = collections.defaultdict(deque)
_RECENT_LIMIT = 10


def _ingredient_signature(ingredients: list[str]) -> FrozenSet[str]:
    return frozenset(ing.lower() for ing in ingredients)


def _recipe_signature(recipe: Dict[str, Any]) -> Tuple[FrozenSet[str], str, str]:
    body = recipe.get("body", {}) if isinstance(recipe, dict) else {}
    ingredients = body.get("ingredients") or []
    glass = str(body.get("glassware", "")).lower()
    garnish = str(body.get("garnish", "")).lower()
    return (_ingredient_signature(list(ingredients)), glass, garnish)


def _recent_key(bar_id: str, session_id: Optional[str]) -> Tuple[str, Hashable]:
    return (bar_id, session_id or "anon")


def _remember_recipe(bar_id: str, session_id: Optional[str], recipe: Dict[str, Any]) -> None:
    sig = _recipe_signature(recipe)
    bucket = _RECENT_RECIPES[_recent_key(bar_id, session_id)]
    bucket.append(sig)
    if len(bucket) > _RECENT_LIMIT:
        bucket.popleft()


def _is_recent_duplicate(bar_id: str, session_id: Optional[str], recipe: Dict[str, Any]) -> bool:
    sig = _recipe_signature(recipe)
    bucket = _RECENT_RECIPES.get(_recent_key(bar_id, session_id))
    return bucket is not None and sig in bucket


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
        lower_name = name.lower()
        if suggestion.role == "mixer":
            items.append(f"Top with {name} (mixer)")
            continue
        if "bitters" in lower_name:
            items.append(f"Dashes of {name}")
            continue
        if "salt" in lower_name or "saline" in lower_name:
            items.append(f"Pinch of {name}")
            continue
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

    session_info = payload.get("session") if isinstance(payload.get("session"), dict) else None

    bar_id_value = payload.get("bar_id") or payload.get("barId") or payload.get("bar")
    if session_info:
        bar_id_value = (
            session_info.get("barId")
            or session_info.get("bar_id")
            or session_info.get("bar")
            or bar_id_value
        )

    if bar_id_value is None:
        bar_id = "demo-bar"
    else:
        bar_id = str(bar_id_value).strip() or "demo-bar"

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
    reserved_keys = {"bar","bar_id", "barId", "session", "session_id", "sessionId"}
    responses = _normalise_responses({key: value for key, value in payload.items() if key not in reserved_keys})

    try:
        _REPOSITORY.prime_cache(bar_id)
    except UnknownBarError as exc:
        return _json_error(str(exc), 404)

    responses_with_bar = dict(responses)
    responses_with_bar["bar_id"] = bar_id
    profile = choose_profile(responses_with_bar)
    builder = ProfileRecipeBuilder(_REPOSITORY)

    seed_value: Optional[int] = None
    if responses_with_bar.get("seed") is not None:
        try:
            seed_value = int(str(responses_with_bar.get("seed")))
        except (TypeError, ValueError):
            seed_value = None

    try:
        candidate_recipes = builder.build_candidates(
            responses_with_bar,
            profile,
            seed=seed_value,
            num_candidates=3,
        )
    except ValueError as exc:
        return _json_error(str(exc), 400)

    if not candidate_recipes:
        return _json_error("Unable to generate a recipe with the provided stock.", 400)

    rendered_candidates = []
    for cand in candidate_recipes:
        ingredients_list = _format_ingredient_list(cand)
        signature_recipe = {
            "body": {
                "ingredients": ingredients_list,
                "glassware": cand.glassware,
                "garnish": cand.garnish or "",
            }
        }
        rendered_candidates.append((cand, ingredients_list, signature_recipe))

    chosen_tuple = next(
        (
            candidate
            for candidate in rendered_candidates
            if not _is_recent_duplicate(bar_id, session_id, candidate[2])
        ),
        rendered_candidates[0],
    )

    recipe, ingredients_list, signature_recipe = chosen_tuple
    method_text = _build_method_text(recipe)
    warnings = _collect_warnings(recipe)
    _remember_recipe(bar_id, session_id, signature_recipe)

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
