"""Flask service exposing the recipe builder engine."""
from __future__ import annotations

import collections
import json
import logging
import re
from collections import deque
from pathlib import Path
from typing import Any, Deque, Dict, FrozenSet, Hashable, Optional, Tuple
import traceback; traceback.print_exc()

from flask import Flask, Response, jsonify, request

from recipebuilder import (
    FlavourAssociationModel,
    ProfileRecipeBuilder,
    StockRepository,
    choose_profile,
    generate_cocktail_recipe,
)
from recipebuilder.recipe_engine import UnknownBarError
from recipebuilder.bartender_score import iterate_until_pass

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

# --- Measurement / alcohol normalisation (minimal "rogue catcher") ---
_ML_PREFIX_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*ml\s+(.*)$", re.IGNORECASE)


def _parse_ml_prefix(line: str) -> tuple[Optional[int], str]:
    """
    Returns (ml, rest). If no leading ml, returns (None, original line).
    """
    m = _ML_PREFIX_RE.match(line)
    if not m:
        return None, line
    ml = int(round(float(m.group(1))))
    rest = m.group(2).strip()
    return ml, rest


def _is_top_with_or_meta(line: str) -> bool:
    lower = line.lower().strip()
    return (
        lower.startswith("top with")
        or lower.startswith("serve over")
        or lower.startswith("serve in")
        or lower.startswith("garnish:")
    )


def _role_from_tags(line: str) -> Optional[str]:
    """
    Uses your existing tags. Returns: modifier, sweetener, sour, juice, mixer or None.
    """
    lower = line.lower()
    for tag in ("modifier", "sweetener", "sour", "juice", "mixer"):
        if f"({tag})" in lower:
            return tag
    return None


def _is_spirit_line(line: str) -> bool:
    """
    Spirits are usually untagged in your output (e.g., '45ml Vodka').
    Treat any ml-line that is not tagged juice/sour/sweetener/mixer and not 'Top with/Serve/Garnish' as spirit.
    """
    if _is_top_with_or_meta(line):
        return False
    role = _role_from_tags(line)
    if role in ("juice", "sour", "sweetener", "mixer"):
        return False
    if role == "modifier":
        return False
    return True


def _snap_to_allowed(value: int, allowed: list[int]) -> int:
    """
    Snap to nearest allowed bucket (ties go to the smaller value).
    """
    allowed_sorted = sorted(allowed)
    best = allowed_sorted[0]
    best_dist = abs(value - best)
    for a in allowed_sorted[1:]:
        d = abs(value - a)
        if d < best_dist or (d == best_dist and a < best):
            best = a
            best_dist = d
    return best


def _snap_to_step(value: int, step: int) -> int:
    """
    Snap to nearest multiple of step, never returning 0 if value > 0.
    """
    snapped = int(round(value / step) * step)
    if value > 0 and snapped == 0:
        return step
    return snapped


def normalise_measurements_and_cap_alcohol(ingredients: list[str]) -> list[str]:
    """
    Minimal post-processor:
    - Spirits: snap to [25, 35, 50] (keeps common UK 35ml specs)
    - Modifiers (liqueurs): snap to 5ml steps
    - Juice/sour/sweetener: snap to 5ml steps
    - Mixer/meta lines: untouched
    - Enforce total alcohol (spirits + modifiers) <= 50ml by reducing modifiers first, then spirits.
    """
    recs: list[dict[str, Any]] = []
    for line in ingredients:
        ml, rest = _parse_ml_prefix(line)
        recs.append({"line": line, "ml": ml, "rest": rest})

    # 1) Snap measures
    for r in recs:
        ml = r["ml"]
        if ml is None:
            continue
        line = r["line"]
        if _is_top_with_or_meta(line):
            continue

        role = _role_from_tags(line)

        if _is_spirit_line(line):
            snapped = _snap_to_allowed(ml, [25, 35, 50])
        elif role == "modifier":
            snapped = _snap_to_step(ml, 5)
        elif role in ("juice", "sour", "sweetener"):
            snapped = _snap_to_step(ml, 5)
        else:
            snapped = _snap_to_step(ml, 5)

        r["ml"] = snapped
        r["line"] = f"{snapped}ml {r['rest']}"

    # 2) Cap alcohol <= 50 (spirits + modifiers)
    def is_alcohol(r: dict[str, Any]) -> bool:
        if r["ml"] is None:
            return False
        if r["line"] is None:
            return False
        if _is_top_with_or_meta(r["line"]):
            return False
        role = _role_from_tags(r["line"])
        return _is_spirit_line(r["line"]) or (role == "modifier")

    def alcohol_total() -> int:
        return int(sum(r["ml"] for r in recs if is_alcohol(r) and r["ml"] is not None))

    if alcohol_total() <= 50:
        return [r["line"] for r in recs if r["line"] is not None]

    # Reduce modifiers first (in 5ml steps down to 0)
    for r in recs:
        if alcohol_total() <= 50:
            break
        if r["ml"] is None or r["line"] is None:
            continue
        if _role_from_tags(r["line"]) != "modifier":
            continue
        while r["ml"] > 0 and alcohol_total() > 50:
            r["ml"] = max(0, r["ml"] - 5)
            if r["ml"] == 0:
                r["line"] = None
                break
            r["line"] = f"{r['ml']}ml {r['rest']}"

    # Then reduce spirits (50 -> 35 -> 25 -> remove)
    spirit_buckets = [50, 35, 25, 0]
    for r in recs:
        if alcohol_total() <= 50:
            break
        if r["ml"] is None or r["line"] is None:
            continue
        if not _is_spirit_line(r["line"]):
            continue

        current = r["ml"]
        if current not in (25, 35, 50):
            current = _snap_to_allowed(current, [25, 35, 50])

        while alcohol_total() > 50 and current > 0:
            idx = spirit_buckets.index(current)
            current = spirit_buckets[idx + 1]  # next lower bucket
            if current == 0:
                r["ml"] = 0
                r["line"] = None
                break
            r["ml"] = current
            r["line"] = f"{current}ml {r['rest']}"

    return [r["line"] for r in recs if r["line"] is not None]


def _merge_duplicate_alcohol_lines(ingredients: list[str]) -> list[str]:
    """
    Collapse duplicate alcohol lines like:
      25ml Orange Gin
      25ml Orange Gin
    into:
      50ml Orange Gin

    Only merges identical alcohol items (spirits + modifiers). Never merges juices/sours/sweeteners/mixers,
    and never touches 'Top with', 'Garnish:', or 'Serve over' lines.
    """
    # First pass: totals + first-seen display name + whether it was a modifier line
    totals: dict[str, int] = {}
    display: dict[str, str] = {}
    is_modifier: dict[str, bool] = {}

    def key_for(line: str) -> Optional[tuple[str, str, bool, int]]:
        ml, rest = _parse_ml_prefix(line)
        if ml is None:
            return None
        if _is_top_with_or_meta(line):
            return None
        role = _role_from_tags(line)
        if role in ("juice", "sour", "sweetener", "mixer"):
            return None
        # Only spirits/modifiers (alcohol lanes)
        if not (_is_spirit_line(line) or role == "modifier"):
            return None

        base_name = rest
        base_name = re.sub(r"\s+\((?:modifier)\)\s*$", "", base_name, flags=re.IGNORECASE).strip()
        k = base_name.lower()
        return k, base_name, (role == "modifier"), ml

    for line in ingredients:
        info = key_for(line)
        if info is None:
            continue
        k, base_name, mod, ml = info
        totals[k] = totals.get(k, 0) + ml
        if k not in display:
            display[k] = base_name
            is_modifier[k] = mod
        else:
            is_modifier[k] = is_modifier.get(k, False) or mod

    # Second pass: emit merged line on first encounter, skip subsequent duplicates
    emitted: set[str] = set()
    out: list[str] = []
    for line in ingredients:
        info = key_for(line)
        if info is None:
            out.append(line)
            continue
        k, _, _, _ = info
        if k in emitted:
            continue
        emitted.add(k)
        total_ml = totals.get(k, 0)
        name = display.get(k, "")
        suffix = " (modifier)" if is_modifier.get(k, False) else ""
        out.append(f"{int(total_ml)}ml {name}{suffix}")

    return out


# --- Candidate reranking + coherence fixes (minimal, taste-preserving) ---

def _contains_any(haystack: str, needles: list[str]) -> bool:
    h = haystack.lower()
    return any(n.lower() in h for n in needles)


def _ingredients_text(ingredients_list: list[str]) -> str:
    return " | ".join(ingredients_list)


def _has_top_with(ingredients_list: list[str]) -> bool:
    return any(line.lower().strip().startswith("top with") for line in ingredients_list)


def _has_serve_over_none(ingredients_list: list[str]) -> bool:
    return any(line.lower().strip().startswith("serve over none") for line in ingredients_list)


def _has_foaming_agent(ingredients_list: list[str]) -> bool:
    txt = _ingredients_text(ingredients_list).lower()
    return ("aquafaba" in txt) or ("egg white" in txt) or ("eggwhite" in txt)


def _score_candidate(
    bar_id: str,
    cand,
    ingredients_list: list[str],
    responses_with_bar: Dict[str, Optional[str]],
) -> float:
    """
    Soft scoring only: does NOT change what gets generated, only which of the already-generated
    candidates we pick. This is where we fix "tonic bias", "martini + top-up" mismatches, and
    floral preference for elderflower.
    """
    score = 0.0
    txt = _ingredients_text(ingredients_list).lower()

    bitterness = (responses_with_bar.get("bitterness_tolerance") or "").lower()
    aroma = (responses_with_bar.get("aroma_preference") or "").lower()
    season = (responses_with_bar.get("season") or "").lower()
    house_type = (responses_with_bar.get("house_type") or "").lower()
    dining_style = (responses_with_bar.get("dining_style") or "").lower()
    foam_toggle = (responses_with_bar.get("foam_toggle") or "").lower()

    # 1) Tonic usage: only good when bitterness is high (user instruction)
    uses_tonic = "tonic" in txt and "top with" in txt
    if uses_tonic:
        if bitterness == "high":
            score += 2.0
        elif bitterness == "medium":
            score -= 4.0
        else:  # low/unknown
            score -= 9.0

    # 2) Serve coherence penalties: top-up drinks must not be martini/coupe and must have ice
    glass = str(getattr(cand, "glassware", "") or "").lower()
    if _has_top_with(ingredients_list):
        if "martini" in glass or "coupe" in glass:
            score -= 10.0
        if _has_serve_over_none(ingredients_list):
            score -= 7.0

    # 3) Floral preference: strongly prefer elderflower when present
    if aroma == "floral":
        if "elderflower" in txt:
            score += 6.0
        # Peach schnapps often reads "random sweet lane" against floral/subtle briefs
        if _contains_any(txt, ["archers", "peach schnapps"]):
            score -= 3.0
        # Tonic can read quinine/medicinal vs floral, unless bitterness was requested high
        if "tonic" in txt and bitterness != "high":
            score -= 2.0

    # 4) Malibu/coconut appropriateness: penalise when not summer/tropical signalled
    coconut_present = _contains_any(txt, ["malibu", "coconut syrup", "coconut rum"])
    if coconut_present:
        # Only "free" when clearly summer/beach/tropical vibes
        summerish = (season == "summer") or ("beach" in house_type) or ("vibrant" in dining_style)
        if not summerish:
            score -= 4.0

    # 5) "Subtle/fresh" brief: de-emphasise heavy sweet stacking
    subtleish = ("subtle" in dining_style) or ("fresh" in dining_style) or ("freshness" in dining_style)
    if subtleish:
        if _contains_any(txt, ["simple syrup", "vanilla syrup", "caramel", "grenadine", "raspberry cordial"]):
            score -= 2.0
        if _contains_any(txt, ["orange juice", "pineapple juice"]) and "top with" in txt:
            score -= 1.5

    # 6) Foam toggle sanity: if foam requested, prefer candidates that include a foaming agent
    if foam_toggle in ("yes", "true", "1"):
        if _has_foaming_agent(ingredients_list):
            score += 3.0
        else:
            score -= 3.0

    # 7) Nut liqueur/allergen landmine (e.g., Frangelico) — soft penalty unless explicitly desired
    if _contains_any(txt, ["frangelico", "hazelnut"]):
        score -= 3.0

    return score


def _apply_serving_coherence(cand, ingredients_list: list[str], method_text: str) -> tuple[str, list[str], str]:
    """
    If a recipe has a top-up mixer, enforce that the serve format is coherent:
    - avoid martini/coupe glassware for topped drinks
    - ensure it is served over cubed ice (unless already specified)
    - update method text to match chosen glass where possible
    """
    glass = str(getattr(cand, "glassware", "") or "").strip()
    glass_lower = glass.lower()
    has_top = _has_top_with(ingredients_list)

    new_ingredients = list(ingredients_list)
    new_method = method_text
    new_glass = glass

    if has_top:
        # Force ice to cubed
        found_serve_over = False
        for i, line in enumerate(new_ingredients):
            if line.lower().strip().startswith("serve over"):
                found_serve_over = True
                # Replace "none" with "cubed"
                if line.lower().strip() == "serve over none":
                    new_ingredients[i] = "Serve over cubed"
                break
        if not found_serve_over:
            new_ingredients.append("Serve over cubed")

        # If glass is martini/coupe and there's a top-up, switch to long glass
        if "martini" in glass_lower or "coupe" in glass_lower:
            new_glass = "long glass"
            # Best-effort method text swap (keeps other steps intact)
            new_method = new_method.replace("martini glass", "long glass").replace("Martini glass", "long glass")
            new_method = new_method.replace("coupe", "long glass").replace("Coupe", "long glass")

    return new_glass, new_ingredients, new_method


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
        if suggestion.role == "mixer":
            items.append(f"Top with {name} (mixer)")
            continue
        if suggestion.amount_ml and suggestion.amount_ml > 0:
            amount_text = f"{suggestion.amount_ml:.0f}ml"
        else:
            amount_text = "to taste"
        if role:
            items.append(f"{amount_text} {name} ({role})")
        else:
            items.append(f"{amount_text} {name}")

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
    reserved_keys = {"bar", "bar_id", "barId", "session", "session_id", "sessionId"}
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

    # Render + normalise candidates, then pick the best one with soft preference scoring.
    rendered_candidates: list[tuple[float, Any, list[str], dict[str, Any]]] = []
    for cand in candidate_recipes:
        ingredients_list = _format_ingredient_list(cand)
        # Guardrails: snap measures + cap total alcohol <= 50ml (incl modifiers)
        ingredients_list = normalise_measurements_and_cap_alcohol(ingredients_list)

        signature_recipe = {
            "body": {
                "ingredients": ingredients_list,
                "glassware": cand.glassware,
                "garnish": cand.garnish or "",
            }
        }

        score = _score_candidate(bar_id, cand, ingredients_list, responses_with_bar)
        rendered_candidates.append((score, cand, ingredients_list, signature_recipe))

    # Prefer non-duplicate candidates; within that set, pick highest score.
    non_dupes = [c for c in rendered_candidates if not _is_recent_duplicate(bar_id, session_id, c[3])]
    pool = non_dupes if non_dupes else rendered_candidates
    # Sort by score descending, but keep deterministic tie-break by original order (stable sort)
    pool_sorted = sorted(pool, key=lambda x: x[0], reverse=True)
    chosen_score, recipe, ingredients_list, signature_recipe = pool_sorted[0]

    method_text = _build_method_text(recipe)
    warnings = _collect_warnings(recipe)

    # Apply final serve coherence (top-up drinks should not be martini/coupe, should have ice).
    coerced_glassware, coerced_ingredients, coerced_method = _apply_serving_coherence(
        recipe, ingredients_list, method_text
    )

    # If we coerced glassware/ingredients, update signature for memory + response payload.
    ingredients_list = coerced_ingredients
    method_text = coerced_method

    # Collapse duplicate alcohol lines (e.g., 25ml Orange Gin + 25ml Orange Gin -> 50ml Orange Gin).
    ingredients_list = _merge_duplicate_alcohol_lines(ingredients_list)

    # --- Bartender scoring loop: iterate until quality + bespoke >= 8 each ---
    try:
        stock_names = [item.name for item in _REPOSITORY._all_items]
    except Exception:
        stock_names = None

    ingredients_list, method_text, coerced_glassware, garnish_text = iterate_until_pass(
        ingredients_list,
        method_text,
        coerced_glassware,
        recipe.garnish or "",
        responses_with_bar,
        stock_names=stock_names,
        max_iterations=4,
        target_score=8.0,
    )[:4]

    signature_recipe = {
        "body": {
            "ingredients": ingredients_list,
            "glassware": coerced_glassware,
            "garnish": garnish_text,
        }
    }

    # Optional helpful warning if tonic is used against low bitterness (doesn't change taste).
    bitterness = (responses_with_bar.get("bitterness_tolerance") or "").lower()
    if any(line.lower().startswith("top with") and "tonic" in line.lower() for line in ingredients_list) and bitterness != "high":
        warnings.append("Tonic is typically best for higher bitterness preferences.")

    _remember_recipe(bar_id, session_id, signature_recipe)

    ingredients_list = [
        s for s in ingredients_list
        if not s.startswith("Garnish:") and not s.startswith("Serve over")
    ]


    response_payload = {
        "data": {
            "barId": bar_id,
            "sessionId": session_id,
            "name": recipe.name or "Custom cocktail",
            "description": "A bespoke cocktail created from your quiz answers.",
            "body": {
                "ingredients": ingredients_list,
                "method": method_text,
                "glassware": coerced_glassware,
                "garnish": garnish_text,
                "warnings": warnings,
            },
            "abvEstimate": None,
        }
    }

    return jsonify(response_payload)


if __name__ == "__main__":  # pragma: no cover - manual execution entrypoint
    app.run(host="0.0.0.0", port=5000)


