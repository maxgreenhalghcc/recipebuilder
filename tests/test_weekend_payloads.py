"""Replay weekend human payloads through the scoring loop and assert quality.

Usage:
    python -m pytest tests/test_weekend_payloads.py -v
    python tests/test_weekend_payloads.py          # standalone with summary table
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# Allow running from project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

pytest.importorskip("flask")

import service
from recipebuilder import ProfileRecipeBuilder, StockRepository, choose_profile
from recipebuilder.bartender_score import ScoreResult, iterate_until_pass, score_recipe

FIXTURES_PATH = Path(__file__).resolve().parent / "fixtures" / "weekend_payloads.json"
TARGET_SCORE = 8.0


def _load_payloads() -> List[Dict[str, Any]]:
    with open(FIXTURES_PATH) as f:
        return json.load(f)


def _extract_bar_id(payload: Dict[str, Any]) -> str:
    return str(payload.get("bar") or payload.get("bar_id") or "demo-bar").strip()


def _generate_and_score(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Run the full generate pipeline for a payload and return recipe + scores."""
    bar_id = _extract_bar_id(payload)
    repository = StockRepository()

    try:
        repository.prime_cache(bar_id)
    except Exception as exc:
        return {"error": str(exc), "quality_score": 0, "bespoke_score": 0}

    reserved_keys = {"bar", "bar_id", "barId", "session", "session_id", "sessionId"}
    responses = {k: (str(v) if v is not None else None) for k, v in payload.items() if k not in reserved_keys}
    responses["bar_id"] = bar_id

    profile = choose_profile(responses)
    builder = ProfileRecipeBuilder(repository)

    seed_value = None
    if responses.get("seed") is not None:
        try:
            seed_value = int(str(responses["seed"]))
        except (TypeError, ValueError):
            seed_value = None

    try:
        candidates = builder.build_candidates(responses, profile, seed=seed_value, num_candidates=3)
    except ValueError as exc:
        return {"error": str(exc), "quality_score": 0, "bespoke_score": 0}

    if not candidates:
        return {"error": "No candidates generated", "quality_score": 0, "bespoke_score": 0}

    # Pick first candidate (seed makes it deterministic)
    recipe = candidates[0]
    ingredients_list = service._format_ingredient_list(recipe)
    ingredients_list = service.normalise_measurements_and_cap_alcohol(ingredients_list)

    method_text = service._build_method_text(recipe)
    glassware = recipe.glassware or "rocks glass"
    garnish = recipe.garnish or ""

    # Apply serving coherence
    glassware, ingredients_list, method_text = service._apply_serving_coherence(
        recipe, ingredients_list, method_text
    )
    ingredients_list = service._merge_duplicate_alcohol_lines(ingredients_list)

    # Get stock names
    try:
        stock_names = [item.name for item in repository._all_items]
    except Exception:
        stock_names = None

    # Score BEFORE improvement
    pre_score = score_recipe(ingredients_list, method_text, glassware, garnish, payload, stock_names)

    # Run the iterate-until-pass loop
    improved_ingredients, improved_method, improved_glass, improved_garnish, final_score = iterate_until_pass(
        ingredients_list, method_text, glassware, garnish,
        payload, stock_names=stock_names, max_iterations=4, target_score=TARGET_SCORE,
    )

    # Clean non-liquid lines
    improved_ingredients = [
        s for s in improved_ingredients
        if not s.startswith("Garnish:") and not s.startswith("Serve over")
    ]

    return {
        "bar": bar_id,
        "ingredients": improved_ingredients,
        "method": improved_method,
        "glassware": improved_glass,
        "garnish": improved_garnish,
        "quality_score_before": pre_score.quality_score,
        "bespoke_score_before": pre_score.bespoke_score,
        "quality_score": final_score.quality_score,
        "bespoke_score": final_score.bespoke_score,
        "reasons_remaining": [
            {"desc": r.description, "penalty": r.penalty, "action": r.fix_action}
            for r in final_score.reasons
        ],
    }


# ---------------------------------------------------------------------------
# Pytest parametrized tests
# ---------------------------------------------------------------------------

_PAYLOADS = _load_payloads()


@pytest.mark.parametrize(
    "fixture",
    _PAYLOADS,
    ids=[f"payload_{p['id']}_{p['description'][:40]}" for p in _PAYLOADS],
)
def test_weekend_payload_scores(fixture):
    """Each weekend payload should achieve >= 8 in both quality and bespoke after scoring loop."""
    result = _generate_and_score(fixture["payload"])

    assert "error" not in result, f"Generation error: {result.get('error')}"

    quality = result["quality_score"]
    bespoke = result["bespoke_score"]
    reasons = result.get("reasons_remaining", [])

    msg_parts = [
        f"Payload #{fixture['id']}: {fixture['description']}",
        f"  Quality: {result['quality_score_before']:.1f} -> {quality:.1f}",
        f"  Bespoke: {result['bespoke_score_before']:.1f} -> {bespoke:.1f}",
        f"  Glass: {result['glassware']}",
        f"  Ingredients: {result['ingredients']}",
        f"  Garnish: {result['garnish']}",
    ]
    if reasons:
        msg_parts.append("  Remaining issues:")
        for r in reasons:
            msg_parts.append(f"    - [{r['penalty']:.1f}] {r['desc']} (fix: {r['action']})")

    detail = "\n".join(msg_parts)

    assert quality >= TARGET_SCORE, f"Quality {quality:.1f} < {TARGET_SCORE}\n{detail}"
    assert bespoke >= TARGET_SCORE, f"Bespoke {bespoke:.1f} < {TARGET_SCORE}\n{detail}"


# ---------------------------------------------------------------------------
# Standalone runner with summary table
# ---------------------------------------------------------------------------

def run_standalone():
    """Run all payloads and print a summary table."""
    payloads = _load_payloads()
    results = []
    pass_count = 0

    print(f"\n{'='*90}")
    print(f"WEEKEND PAYLOAD SCORING — TARGET: {TARGET_SCORE}/10 quality AND bespoke")
    print(f"{'='*90}\n")

    for fixture in payloads:
        pid = fixture["id"]
        desc = fixture["description"]
        result = _generate_and_score(fixture["payload"])

        if "error" in result:
            print(f"  #{pid:2d} ERROR: {result['error']}")
            results.append((pid, desc, 0, 0, 0, 0, "ERROR"))
            continue

        q_before = result["quality_score_before"]
        b_before = result["bespoke_score_before"]
        q_after = result["quality_score"]
        b_after = result["bespoke_score"]
        passed = q_after >= TARGET_SCORE and b_after >= TARGET_SCORE
        if passed:
            pass_count += 1

        status = "PASS" if passed else "FAIL"
        results.append((pid, desc, q_before, b_before, q_after, b_after, status))

        print(f"  #{pid:2d} [{status}] {desc[:55]}")
        print(f"       Quality: {q_before:.1f} -> {q_after:.1f}  |  Bespoke: {b_before:.1f} -> {b_after:.1f}")
        print(f"       Glass: {result['glassware']}  |  Garnish: {result['garnish']}")

        # Show ingredients
        for ing in result["ingredients"]:
            print(f"         - {ing}")

        remaining = result.get("reasons_remaining", [])
        if remaining:
            print(f"       Remaining issues ({len(remaining)}):")
            for r in remaining:
                print(f"         [{r['penalty']:.1f}] {r['desc']}")
        print()

    # Summary table
    print(f"\n{'='*90}")
    print(f"{'#':>3}  {'Q(pre)':>6} {'Q(post)':>7} {'B(pre)':>6} {'B(post)':>7} {'Status':>6}  Description")
    print(f"{'-'*90}")
    for pid, desc, qb, bb, qa, ba, status in results:
        print(f"{pid:3d}  {qb:6.1f} {qa:7.1f} {bb:6.1f} {ba:7.1f} {status:>6}  {desc[:50]}")
    print(f"{'-'*90}")
    print(f"PASSED: {pass_count}/{len(results)}  (target: both >= {TARGET_SCORE})")
    print(f"{'='*90}\n")


if __name__ == "__main__":
    run_standalone()
