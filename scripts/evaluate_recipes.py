"""Evaluate recipe outputs per bar stock and iterate via seed search.

This script is intentionally heuristic: we’re approximating a human 0–10 rating
using measurable signals from the engine output.

Usage:
  python3 scripts/evaluate_recipes.py --bars all --seeds 0:60

Outputs:
  - prints top recipes per bar per scenario
  - writes a JSON report to /tmp/recipe_eval_report.json
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from recipebuilder import generate_cocktail_recipe
from recipebuilder.flavour_context import FlavourKnowledgeBase, compute_recipe_similarity
from recipebuilder.preferences import build_preference_plan


RE_ALIGNMENT = re.compile(r"Flavour alignment score: ([0-9]+\.[0-9]+)")


SCENARIOS: List[Dict[str, str]] = [
    {
        "name": "Bright Summer Citrus",
        "season": "summer",
        "house_type": "beach house",
        "dining_style": "refreshing and vibrant flavours which awaken my senses",
        "music_preference": "pop",
        "aroma_preference": "citrus",
        "base_spirit": "gin",
        "bitterness_tolerance": "low",
        "sweetener_question": "zesty",
        "abv_lane": "medium",
        "notes": "Guest loves bright citrus, crisp refreshing drinks, low bitterness. Keep it very clean and vibrant.",
    },
    {
        "name": "Spooky Winter Bitter",
        "season": "winter",
        "house_type": "haunted house",
        "dining_style": "a balanced blend of flavours",
        "music_preference": "rock",
        "aroma_preference": "woody",
        "base_spirit": "rum",
        "bitterness_tolerance": "high",
        "sweetener_question": "rich",
        "abv_lane": "strong",
        "notes": "Dark, brooding, smoky/woody vibe. Loves bitterness. Strong but balanced. Feels bespoke and 'dangerously good'.",
    },
    {
        "name": "Modern Spring Floral",
        "season": "spring",
        "house_type": "modern house",
        "dining_style": "subtle tastes which advertise freshness",
        "music_preference": "jazz/blues",
        "aroma_preference": "floral",
        "base_spirit": "vodka",
        "bitterness_tolerance": "medium",
        "sweetener_question": "floral",
        "abv_lane": "low",
        "notes": "Elegant, subtle, fresh. Light ABV. Floral top notes, not perfumey. Highly polished and clean.",
    },
]


def _alignment_similarity(knowledge: FlavourKnowledgeBase, responses: Dict[str, str], recipe) -> float:
    """Compute cosine similarity between target vector and recipe vector."""
    plan = build_preference_plan({k: str(v) for k, v in responses.items()})
    target = knowledge.build_target_vector(responses, plan=plan)
    payload = [(s.ingredient, float(s.amount_ml)) for s in recipe.ingredients if (s.amount_ml or 0) > 0]
    return float(compute_recipe_similarity(knowledge, target, payload) or 0.0)


def _detect_color(recipe) -> Optional[str]:
    # profile_builder has color detection for garnish; we do a similar lightweight inference
    names = " ".join((s.ingredient.name or "").lower() for s in recipe.ingredients)
    if any(t in names for t in ("blue", "curaçao", "curacao")):
        return "blue"
    if any(t in names for t in ("violet", "purple")):
        return "violet"
    if any(t in names for t in ("grenadine", "cranberry", "cherry")):
        return "red"
    if any(t in names for t in ("raspberry", "strawberry")):
        return "pink"
    if "orange juice" in names or " orange" in names:
        return "orange"
    if any(t in names for t in ("pineapple", "passion", "mango")):
        return "yellow"
    if any(t in names for t in ("mint", "midori", "kiwi")):
        return "green"
    if any(t in names for t in ("bourbon", "whiskey", "dark rum", "spiced", "coffee", "caramel")):
        return "brown"
    return None


def _structure_score(recipe) -> float:
    roles = [getattr(s, "role", "").lower() for s in recipe.ingredients]
    n = len(roles)
    # Basic cocktail structure: base + modifier + (sweetener) + (acid) + (mixer/juice)
    has_base = "base" in roles
    has_modifier = "modifier" in roles
    has_sweetener = "sweetener" in roles
    has_sour = "sour" in roles
    has_juice = "juice" in roles
    has_mixer = "mixer" in roles

    score = 0.0
    score += 1.5 if has_base else 0.0
    score += 1.2 if has_modifier else 0.0
    score += 0.8 if has_sweetener else 0.0
    score += 1.0 if (has_sour or (has_juice and "citrus" in " ".join(s.ingredient.name.lower() for s in recipe.ingredients))) else 0.0
    score += 0.8 if (has_mixer or has_juice) else 0.0

    # Penalize too sparse or too bloated
    if n < 4:
        score -= 1.0
    if n > 9:
        score -= 0.5

    return max(0.0, min(5.0, score))  # out of 5


def _bespoke_score(recipe, responses: Dict[str, str]) -> float:
    # Heuristic: did we express the requested direction in observable choices?
    # We award points for: matching base spirit, aroma family presence, bitterness, sweetness style cues.
    text = " ".join((s.ingredient.name or "").lower() for s in recipe.ingredients)

    score = 0.0

    # Base spirit fidelity
    base = responses.get("base_spirit", "").lower()
    if base and base in text:
        score += 1.5

    # Aroma cue
    aroma = responses.get("aroma_preference", "").lower()
    if aroma == "citrus" and any(k in text for k in ("lime", "lemon", "orange", "grapefruit")):
        score += 1.0
    if aroma == "floral" and any(k in text for k in ("elderflower", "rose", "violet", "lavender", "hibiscus")):
        score += 1.0
    if aroma == "woody" and any(k in text for k in ("smoke", "smoky", "wood", "oak", "cinnamon", "clove", "coffee")):
        score += 1.0
    if aroma == "sweet" and any(k in text for k in ("vanilla", "caramel", "chocolate", "honey")):
        score += 1.0

    # Sweetener style
    sweet_style = responses.get("sweetener_question", "").lower()
    if sweet_style == "zesty" and any(k in text for k in ("lemon", "lime", "orange")):
        score += 0.5
    if sweet_style == "rich" and any(k in text for k in ("demerara", "vanilla", "caramel", "honey", "maple")):
        score += 0.5
    if sweet_style == "floral" and any(k in text for k in ("elderflower", "rose", "lavender")):
        score += 0.5

    # Bitterness tolerance expressed via obvious bitter ingredients
    bitter = responses.get("bitterness_tolerance", "").lower()
    has_bitter_marker = any(k in text for k in ("amaro", "campari", "aperol", "bitters", "tonic", "vermouth"))
    if bitter == "high" and has_bitter_marker:
        score += 0.8
    if bitter == "low" and not has_bitter_marker:
        score += 0.4

    return max(0.0, min(4.0, score))  # out of 4


def _overall_score(knowledge: FlavourKnowledgeBase, recipe, responses: Dict[str, str]) -> Tuple[float, Dict[str, float]]:
    similarity = _alignment_similarity(knowledge, responses, recipe)
    structure = _structure_score(recipe)  # 0..5
    bespoke = _bespoke_score(recipe, responses)  # 0..4

    # Weight flavour match more heavily than before.
    flavour_match = max(0.0, min(1.0, similarity)) * 3.0  # 0..3

    # Renormalise structure & bespoke down slightly so total still caps at 10.
    structure_scaled = (structure / 5.0) * 4.0  # 0..4
    bespoke_scaled = (bespoke / 4.0) * 3.0      # 0..3

    total = structure_scaled + bespoke_scaled + flavour_match
    total = max(0.0, min(10.0, total))

    return total, {
        "structure_0_4": structure_scaled,
        "bespoke_0_3": bespoke_scaled,
        "flavour_match_0_3": flavour_match,
        "similarity_0_1": similarity,
        "total_0_10": total,
    }


def _format_recipe(recipe) -> Dict[str, Any]:
    return {
        "name": recipe.name,
        "glassware": recipe.glassware,
        "ice": recipe.ice,
        "garnish": recipe.garnish,
        "color": _detect_color(recipe),
        "ingredients": [
            {
                "name": s.ingredient.name,
                "role": s.role,
                "amount_ml": s.amount_ml,
            }
            for s in recipe.ingredients
        ],
        "steps": list(recipe.steps),
        "explanations": list(recipe.explanations or []),
    }


def parse_seed_range(seed_arg: str) -> List[int]:
    if ":" in seed_arg:
        a, b = seed_arg.split(":", 1)
        start = int(a)
        end = int(b)
        return list(range(start, end))
    return [int(seed_arg)]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bars", default="all")
    parser.add_argument("--seeds", default="0:60")
    parser.add_argument("--scenario", default="all")
    args = parser.parse_args()

    bars_dir = Path("data/bars")
    bar_ids = sorted(p.stem for p in bars_dir.glob("*.json"))
    if args.bars != "all":
        wanted = {b.strip() for b in args.bars.split(",")}
        bar_ids = [b for b in bar_ids if b in wanted]

    seeds = parse_seed_range(args.seeds)

    scenarios = SCENARIOS
    if args.scenario != "all":
        wanted = {s.strip().lower() for s in args.scenario.split(",")}
        scenarios = [s for s in scenarios if s["name"].lower() in wanted]

    report: Dict[str, Any] = {
        "bars": {},
        "seeds": seeds,
        "scenarios": [s["name"] for s in scenarios],
    }

    knowledge = FlavourKnowledgeBase()

    for bar_id in bar_ids:
        report["bars"][bar_id] = {}
        for scenario in scenarios:
            best: Optional[Dict[str, Any]] = None
            best_score = -1.0

            responses = {k: v for k, v in scenario.items() if k != "name"}

            for seed in seeds:
                resp = dict(responses)
                resp["seed"] = str(seed)
                recipe = generate_cocktail_recipe(resp, bar_id=bar_id, recipe_name=f"{scenario['name']} ({bar_id})")
                score, breakdown = _overall_score(knowledge, recipe, responses)
                if score > best_score:
                    best_score = score
                    best = {
                        "seed": seed,
                        "scenario": scenario["name"],
                        "score": breakdown,
                        "recipe": _format_recipe(recipe),
                    }
                if best_score >= 9.0:
                    break

            report["bars"][bar_id][scenario["name"]] = best
            print(f"{bar_id:16s} | {scenario['name']:<22s} | best={best_score:.2f} seed={best['seed'] if best else 'n/a'}")

    out_path = Path("/tmp/recipe_eval_report.json")
    out_path.write_text(json.dumps(report, indent=2))
    print(f"\nWrote: {out_path}")


if __name__ == "__main__":
    main()
