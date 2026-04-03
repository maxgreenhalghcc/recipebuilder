"""Bartender scoring and iterative improvement for generated recipes.

Implements a deterministic scoring system that mimics how a good bartender
judges a spec, plus an improve_recipe function that applies targeted
corrections when a recipe scores below threshold.

Operates on the *rendered* ingredient strings (post _format_ingredient_list),
so it can be dropped into the service layer without touching the generator.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers for parsing rendered ingredient lines
# ---------------------------------------------------------------------------

_ML_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*ml\s+(.+)$", re.IGNORECASE)


def _parse_line(line: str) -> Tuple[Optional[int], str, Optional[str]]:
    """Return (ml, name, role_tag) from a rendered ingredient line."""
    lower = line.lower().strip()
    if lower.startswith("top with"):
        rest = line.strip()[len("Top with "):].strip()
        role = "mixer"
        # strip trailing (mixer) tag
        rest_clean = re.sub(r"\s*\(mixer\)\s*$", "", rest, flags=re.IGNORECASE).strip()
        return None, rest_clean, role

    m = _ML_RE.match(line)
    if not m:
        return None, line.strip(), None
    ml = int(round(float(m.group(1))))
    rest = m.group(2).strip()
    role = None
    role_match = re.search(r"\((\w+)\)\s*$", rest)
    if role_match:
        role = role_match.group(1).lower()
        rest = rest[: role_match.start()].strip()
    return ml, rest, role


def _name_lower(name: str) -> str:
    return name.lower().strip()


def _has_role(ingredients: List[str], role: str) -> bool:
    for line in ingredients:
        _, _, r = _parse_line(line)
        if r == role:
            return True
    return False


def _count_role(ingredients: List[str], role: str) -> int:
    return sum(1 for line in ingredients if _parse_line(line)[2] == role)


def _has_top_with(ingredients: List[str]) -> bool:
    return any(line.lower().strip().startswith("top with") for line in ingredients)


def _ingredient_names_lower(ingredients: List[str]) -> List[str]:
    return [_parse_line(line)[1].lower() for line in ingredients]


def _contains_any(text: str, patterns: List[str]) -> bool:
    t = text.lower()
    return any(p.lower() in t for p in patterns)


def _all_text(ingredients: List[str]) -> str:
    return " | ".join(ingredients).lower()


# ---------------------------------------------------------------------------
# Tropical / neon / clash sets
# ---------------------------------------------------------------------------

TROPICAL_SET = {
    "malibu", "coconut rum", "coconut syrup", "pineapple syrup",
    "pineapple juice", "passionfruit syrup", "passion fruit syrup",
    "passionfruit juice", "passion fruit juice", "blue curacao",
    "blue curaçao", "blue curaçao orange liqueur",
}

NEON_MODIFIERS = {"blue curacao", "blue curaçao", "blue curaçao orange liqueur", "midori", "melon liqueur"}

CITRUS_GARNISHES = {
    "lemon wheel", "lemon twist", "lemon wedge", "lemon peel", "lemon slice",
    "lime wheel", "lime twist", "lime wedge", "lime slice",
    "orange peel", "orange twist", "orange slice", "orange wheel",
    "grapefruit peel", "grapefruit twist", "grapefruit wedge",
    "citrus twist", "citrus peel", "citrus wheel",
}

FLORAL_INGREDIENTS = {
    "elderflower", "st germain", "violet", "rose", "lavender",
    "elderflower cordial", "elderflower liqueur",
}

WOODY_INGREDIENTS = {
    "bitters", "angostura", "orange bitters", "aromatic bitters",
    "rosemary", "cinnamon", "vanilla", "honey", "maple",
    "apple", "pear", "ginger", "dark rum", "spiced rum",
    "bourbon", "whiskey", "whisky", "orange peel",
}

RICH_SWEETENERS = {
    "vanilla syrup", "vanilla", "honey syrup", "honey",
    "demerara syrup", "demerara", "caramel syrup", "caramel",
    "maple syrup", "maple", "rich simple syrup",
}

CITRUS_LED_SWEETENERS = {
    "simple syrup", "agave", "agave syrup", "lime cordial",
    "lemon cordial", "citrus syrup",
}

SWEET_MIXERS = {"lemonade", "ginger ale", "ginger beer", "tonic water", "tonic"}
NEUTRAL_MIXERS = {"soda water", "soda", "sparkling water", "club soda"}


# ---------------------------------------------------------------------------
# Scoring reason dataclass
# ---------------------------------------------------------------------------

@dataclass
class ScoringReason:
    """A single penalty with an actionable correction."""
    category: str        # "quality" or "bespoke"
    subcategory: str     # e.g. "structure", "vibe_match", "aroma_match"
    description: str     # human readable
    penalty: float       # points subtracted
    fix_action: str      # machine-readable action key
    fix_params: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ScoreResult:
    quality_score: float
    bespoke_score: float
    reasons: List[ScoringReason] = field(default_factory=list)


# ---------------------------------------------------------------------------
# score_recipe
# ---------------------------------------------------------------------------

def score_recipe(
    ingredients: List[str],
    method: str,
    glassware: str,
    garnish: str,
    payload: Dict[str, Any],
    stock_names: Optional[List[str]] = None,
) -> ScoreResult:
    """Score a rendered recipe against bartender quality and bespoke accuracy.

    Returns ScoreResult with quality_score, bespoke_score, and actionable reasons.
    """
    reasons: List[ScoringReason] = []
    txt = _all_text(ingredients)
    method_lower = method.lower()
    garnish_lower = (garnish or "").lower()
    glass_lower = (glassware or "").lower()

    # Extract payload fields
    carbonation = (payload.get("carbonation_texture") or "").lower().strip()
    foam_toggle = (payload.get("foam_toggle") or "").lower().strip()
    aroma = (payload.get("aroma_preference") or "").lower().strip()
    season = (payload.get("season") or "").lower().strip()
    house_type = (payload.get("house_type") or "").lower().strip()
    sweetener_q = (payload.get("sweetener_question") or "").lower().strip()
    bitterness = (payload.get("bitterness_tolerance") or "").lower().strip()
    abv_lane = (payload.get("abv_lane") or "").lower().strip()
    dining_style = (payload.get("dining_style") or "").lower().strip()

    still = "still" in carbonation
    properly_sparkling = "proper" in carbonation or "spark" in carbonation
    lightly_fizzy = "light" in carbonation

    has_top = _has_top_with(ingredients)
    has_sour = _has_role(ingredients, "sour")
    has_sweetener = _has_role(ingredients, "sweetener")
    has_mixer = has_top
    juice_count = _count_role(ingredients, "juice")
    modifier_count = _count_role(ingredients, "modifier")

    # Count non-garnish liquid ingredients
    liquid_count = 0
    for line in ingredients:
        ml, name, role = _parse_line(line)
        if ml is not None or role == "mixer":
            liquid_count += 1

    # ===================================================================
    # A) COCKTAIL QUALITY SCORE (start at 10, subtract penalties)
    # ===================================================================

    # 1) Structure / Balance (0-4 penalties)

    # Missing sour when template expects it (highball / collins / sour builds)
    is_sour_template = (has_sweetener or juice_count > 0) and not still == False
    # Better heuristic: if there's a sweetener AND juice but no sour, it's unbalanced
    if has_sweetener and juice_count > 0 and not has_sour:
        reasons.append(ScoringReason(
            "quality", "structure", "Missing sour — drink will be flat/cloying",
            4.0, "inject_sour",
        ))
    elif has_sweetener and not has_sour and liquid_count >= 3:
        # Sweetener without sour is usually unbalanced (except OLD_FASHIONED style)
        if modifier_count == 0 or juice_count > 0:
            reasons.append(ScoringReason(
                "quality", "structure", "Has sweetener but no sour — likely unbalanced",
                3.0, "inject_sour",
            ))

    # Double-souring (cordial + fresh citrus at full strength)
    has_cordial = _contains_any(txt, ["cordial"])
    has_fresh_citrus_sour = False
    for line in ingredients:
        ml, name, role = _parse_line(line)
        if role == "sour" and ml and ml >= 15:
            has_fresh_citrus_sour = True
    if has_cordial and has_fresh_citrus_sour:
        reasons.append(ScoringReason(
            "quality", "structure", "Double-souring: cordial + fresh citrus at full strength",
            2.0, "reduce_double_sour",
        ))

    # Excess sugar stacking (syrup + sweet liqueur + sweet mixer)
    sweet_count = 0
    if has_sweetener:
        sweet_count += 1
    if _contains_any(txt, list(NEON_MODIFIERS)):
        sweet_count += 1
    if has_mixer and _contains_any(txt, ["lemonade", "ginger ale"]):
        sweet_count += 1
    # Count extra syrups beyond the first
    sweetener_lines = [line for line in ingredients if _parse_line(line)[2] == "sweetener"]
    if len(sweetener_lines) > 1:
        sweet_count += 1
    if sweet_count >= 3:
        reasons.append(ScoringReason(
            "quality", "structure", "Excess sugar stacking: syrup + sweet modifier + sweet mixer",
            2.0, "reduce_sugar_stacking",
        ))

    # Juice overload (more than 2 juices)
    if juice_count > 2:
        reasons.append(ScoringReason(
            "quality", "structure", f"Juice overload ({juice_count} juices) — reads as fruit punch",
            2.0, "drop_excess_juice",
        ))

    # 2) Coherence / Pairing (0-3 penalties)

    # Neon / cheap modifier without reason
    has_neon = _contains_any(txt, list(NEON_MODIFIERS))
    summer_beach_tropical = (
        season == "summer" or "beach" in house_type
        or "vibrant" in dining_style or "tropical" in dining_style
    )
    if has_neon and not summer_beach_tropical:
        reasons.append(ScoringReason(
            "quality", "coherence",
            "Neon modifier (blue curaçao/midori) without tropical/fun vibe",
            2.0, "swap_neon_modifier",
        ))

    # Ingredient clash: toffee/dessert spirit + aggressive citrus
    spirit_names = []
    for line in ingredients:
        ml, name, role = _parse_line(line)
        if role is None and ml is not None:  # spirits are untagged
            spirit_names.append(name.lower())
    has_dessert_spirit = _contains_any(" ".join(spirit_names), [
        "toffee", "caramel", "chocolate", "coffee",
    ])
    has_aggressive_citrus = _contains_any(txt, ["lime cordial", "lime juice"])
    if has_dessert_spirit and has_aggressive_citrus:
        # Check if the whole build is lime-heavy
        lime_count = sum(1 for line in ingredients if "lime" in line.lower())
        if lime_count >= 2:
            reasons.append(ScoringReason(
                "quality", "coherence",
                "Dessert spirit clashes with aggressive lime-heavy build",
                2.0, "swap_citrus_for_dessert_spirit",
            ))

    # Garnish mismatch
    if garnish_lower and garnish_lower != "none":
        garnish_matches_aroma = False
        if aroma == "citrus" and any(g in garnish_lower for g in ["lemon", "lime", "orange", "grapefruit", "citrus"]):
            garnish_matches_aroma = True
        elif aroma == "floral" and any(g in garnish_lower for g in ["flower", "elderflower", "lavender", "rose", "herb"]):
            garnish_matches_aroma = True
        elif aroma == "woody" and any(g in garnish_lower for g in ["orange", "rosemary", "cinnamon", "star anise", "peel"]):
            garnish_matches_aroma = True
        elif aroma == "sweet" and any(g in garnish_lower for g in ["cherry", "berry", "strawberry", "raspberry", "orange"]):
            garnish_matches_aroma = True
        elif aroma not in ("citrus", "floral", "woody", "sweet"):
            garnish_matches_aroma = True  # unknown aroma, don't penalize

        # Also check if garnish echoes an ingredient in the drink
        garnish_echoes_drink = any(
            g in txt for g in garnish_lower.split()
            if len(g) > 3 and g not in ("with", "slice", "wheel", "twist", "peel", "wedge", "fresh")
        )
        if not garnish_matches_aroma and not garnish_echoes_drink:
            reasons.append(ScoringReason(
                "quality", "coherence",
                f"Garnish '{garnish}' doesn't echo aroma preference '{aroma}' or drink flavours",
                1.0, "fix_garnish",
            ))

    # 3) Build correctness (0-2 penalties)

    # Glassware mismatch with template
    if still and ("long" in glass_lower or "highball" in glass_lower):
        # Still drinks in long glasses look wrong unless they have high volume
        if not has_top:
            reasons.append(ScoringReason(
                "quality", "build",
                "Still & silky drink in long glass without top — should be coupe/rocks",
                1.0, "fix_glassware_still",
            ))
    if has_top and ("martini" in glass_lower or "coupe" in glass_lower):
        reasons.append(ScoringReason(
            "quality", "build",
            "Top-up drink in martini/coupe glass — should be long glass",
            1.0, "fix_glassware_topped",
        ))

    # Method missing "Top with" when mixer ingredient exists
    if has_mixer and "top" not in method_lower:
        reasons.append(ScoringReason(
            "quality", "build",
            "Mixer ingredient present but method doesn't mention topping",
            1.0, "fix_method_top",
        ))

    # 4) Premium feel (0-1 penalty)
    # Reads like generic punch: many juices, sweet mixer, no modifier/complexity
    if juice_count >= 2 and has_mixer and modifier_count == 0:
        reasons.append(ScoringReason(
            "quality", "premium",
            "Reads like generic punch — multiple juices + sweet mixer, no modifier for complexity",
            1.0, "add_complexity_modifier",
        ))

    # ===================================================================
    # B) BESPOKE ACCURACY SCORE (start at 10, subtract penalties)
    # ===================================================================

    # 1) Vibe match: season + house_type (0-3 penalties)
    autumn_winter_woody = (
        season in ("autumn", "winter")
        or "tree" in house_type
        or "haunted" in house_type
        or aroma == "woody"
    )
    has_tropical = any(
        _contains_any(txt, [t]) for t in TROPICAL_SET
    )
    strong_tropical_override = (
        season == "summer" and "beach" in house_type
        and aroma in ("sweet", "citrus")
    )
    if autumn_winter_woody and has_tropical and not strong_tropical_override:
        reasons.append(ScoringReason(
            "bespoke", "vibe_match",
            "Autumn/winter/woody/haunted/treehouse vibe but recipe uses tropical ingredients",
            3.0, "remove_tropical_for_vibe",
        ))

    summer_beach = season == "summer" or "beach" in house_type
    has_only_heavy_winter = (
        _contains_any(txt, ["cinnamon", "gingerbread", "mulled", "hot"])
        and not _contains_any(txt, ["citrus", "lemon", "lime", "orange", "tropical"])
    )
    if summer_beach and has_only_heavy_winter:
        reasons.append(ScoringReason(
            "bespoke", "vibe_match",
            "Summer/beach vibe but recipe is heavy winter/spice-only",
            2.0, "lighten_for_summer",
        ))

    # 2) Aroma preference match (0-3 penalties)
    if aroma == "citrus":
        has_citrus_backbone = _contains_any(txt, [
            "lemon", "lime", "grapefruit", "orange juice", "citrus",
        ])
        has_citrus_garnish = any(g in garnish_lower for g in [
            "lemon", "lime", "orange", "grapefruit", "citrus",
        ])
        if not has_citrus_backbone:
            reasons.append(ScoringReason(
                "bespoke", "aroma_match",
                "Citrus aroma requested but no clear citrus backbone in spec",
                2.0, "add_citrus_backbone",
            ))
        elif not has_citrus_garnish and garnish_lower and garnish_lower != "none":
            reasons.append(ScoringReason(
                "bespoke", "aroma_match",
                "Citrus aroma requested but garnish isn't citrus",
                1.0, "fix_garnish_citrus",
            ))

    if aroma == "woody":
        has_woody = _contains_any(txt, list(WOODY_INGREDIENTS))
        # Also check spirit names for woody spirits
        has_woody_spirit = _contains_any(" ".join(spirit_names), [
            "spiced", "dark rum", "bourbon", "whiskey", "whisky",
        ])
        if not has_woody and not has_woody_spirit:
            reasons.append(ScoringReason(
                "bespoke", "aroma_match",
                "Woody aroma requested but no woody/spice/herbal elements in spec",
                2.0, "add_woody_elements",
            ))

    if aroma == "floral":
        has_floral = _contains_any(txt, list(FLORAL_INGREDIENTS))
        # Also check if gin is present (botanical/floral spirit)
        has_gin = _contains_any(" ".join(spirit_names), ["gin"])
        if not has_floral and not has_gin:
            if stock_names:
                stock_has_floral = any(
                    _contains_any(s, list(FLORAL_INGREDIENTS))
                    for s in stock_names
                )
                if stock_has_floral:
                    reasons.append(ScoringReason(
                        "bespoke", "aroma_match",
                        "Floral aroma requested, stock has floral ingredients but none used",
                        2.0, "add_floral_element",
                    ))
                else:
                    reasons.append(ScoringReason(
                        "bespoke", "aroma_match",
                        "Floral aroma requested but no floral ingredients in stock",
                        1.0, "warn_no_floral_stock",
                    ))
            else:
                reasons.append(ScoringReason(
                    "bespoke", "aroma_match",
                    "Floral aroma requested but no floral elements in spec",
                    2.0, "add_floral_element",
                ))

    # 3) Sweetener question match: rich vs zesty vs classic (0-2 penalties)
    if sweetener_q == "zesty":
        # Check if sweetener is tropical at full strength
        for line in ingredients:
            ml, name, role = _parse_line(line)
            if role == "sweetener":
                is_tropical_sweet = _contains_any(name, [
                    "pineapple", "passionfruit", "passion fruit", "coconut",
                    "mango", "grenadine", "strawberry",
                ])
                if is_tropical_sweet and ml and ml > 10:
                    reasons.append(ScoringReason(
                        "bespoke", "sweetener_match",
                        f"Zesty sweetener requested but using tropical '{name}' at {ml}ml",
                        2.0, "fix_zesty_sweetener",
                        {"current_sweetener": name, "current_ml": ml},
                    ))
                    break

    if sweetener_q == "rich":
        has_rich_sweet = any(
            _contains_any(_parse_line(line)[1], list(RICH_SWEETENERS))
            for line in ingredients if _parse_line(line)[2] == "sweetener"
        )
        # Also consider if the spec is thin/acidic
        if not has_rich_sweet:
            total_sour_ml = sum(
                _parse_line(line)[0] or 0
                for line in ingredients if _parse_line(line)[2] == "sour"
            )
            total_sweet_ml = sum(
                _parse_line(line)[0] or 0
                for line in ingredients if _parse_line(line)[2] == "sweetener"
            )
            if total_sour_ml > total_sweet_ml:
                reasons.append(ScoringReason(
                    "bespoke", "sweetener_match",
                    "Rich sweetener requested but spec is thin/acidic without body",
                    2.0, "fix_rich_sweetener",
                ))
            elif total_sweet_ml > 0:
                # Has some sweetener but not a rich one — mild penalty
                reasons.append(ScoringReason(
                    "bespoke", "sweetener_match",
                    "Rich sweetener requested but sweetener isn't a rich type (vanilla/honey/demerara)",
                    1.0, "fix_rich_sweetener",
                ))

    # 4) Carbonation texture match (0-2 penalties)
    if still:
        if has_mixer:
            # Check if the mixer is carbonated
            for line in ingredients:
                if line.lower().strip().startswith("top with"):
                    mixer_name = _parse_line(line)[1].lower()
                    if _contains_any(mixer_name, [
                        "soda", "tonic", "lemonade", "ginger ale", "ginger beer",
                        "sparkling", "prosecco", "champagne",
                    ]):
                        reasons.append(ScoringReason(
                            "bespoke", "carbonation_match",
                            "Still & silky requested but recipe has carbonated top",
                            2.0, "remove_carbonated_top",
                        ))
                        break

    if properly_sparkling:
        if not has_mixer:
            reasons.append(ScoringReason(
                "bespoke", "carbonation_match",
                "Properly sparkling requested but no mixer/top in spec",
                1.0, "add_sparkling_top",
            ))

    # 5) ABV lane match (0-1 penalty)
    if abv_lane == "low":
        total_spirit_ml = 0
        total_modifier_ml = 0
        for line in ingredients:
            ml, name, role = _parse_line(line)
            if ml is None:
                continue
            if role is None:  # spirits are untagged
                if not line.lower().strip().startswith("top with"):
                    total_spirit_ml += ml
            elif role == "modifier":
                total_modifier_ml += ml
        total_alcohol = total_spirit_ml + total_modifier_ml
        if total_alcohol > 40:
            reasons.append(ScoringReason(
                "bespoke", "abv_match",
                f"Low ABV requested but total alcohol is {total_alcohol}ml",
                1.0, "reduce_abv",
                {"total_alcohol": total_alcohol},
            ))

    # 6) Foam toggle as texture/build preference
    if foam_toggle in ("yes", "true", "1"):
        # Foam toggle means shaken build / texture preference
        # Check method mentions shaking
        if "shake" not in method_lower and "shak" not in method_lower:
            reasons.append(ScoringReason(
                "bespoke", "foam_match",
                "Foam toggle on but method doesn't include shaking",
                1.0, "fix_method_shaken",
            ))

    # 7) Dining style: "subtle fresh" steering
    subtle_fresh = "subtle" in dining_style or "freshness" in dining_style
    if subtle_fresh:
        # Cap syrups — if total syrup > 15ml, penalize
        total_syrup_ml = sum(
            _parse_line(line)[0] or 0
            for line in ingredients if _parse_line(line)[2] == "sweetener"
        )
        if total_syrup_ml > 20:
            reasons.append(ScoringReason(
                "bespoke", "dining_style_match",
                f"Subtle/fresh dining style but {total_syrup_ml}ml of syrup is heavy",
                1.0, "reduce_syrup_subtle",
            ))
        # Prefer soda over lemonade for subtle
        if has_mixer and _contains_any(txt, ["lemonade"]) and not _contains_any(txt, ["soda"]):
            reasons.append(ScoringReason(
                "bespoke", "dining_style_match",
                "Subtle/fresh dining style — soda would be cleaner than lemonade",
                1.0, "swap_lemonade_to_soda",
            ))
        # Heavy juice volume
        if juice_count >= 2:
            reasons.append(ScoringReason(
                "bespoke", "dining_style_match",
                "Subtle/fresh dining style but multiple juices make it heavy",
                1.0, "reduce_juice_subtle",
            ))

    # Calculate final scores
    quality_penalties = sum(r.penalty for r in reasons if r.category == "quality")
    bespoke_penalties = sum(r.penalty for r in reasons if r.category == "bespoke")

    quality_score = max(0.0, 10.0 - quality_penalties)
    bespoke_score = max(0.0, 10.0 - bespoke_penalties)

    return ScoreResult(
        quality_score=round(quality_score, 1),
        bespoke_score=round(bespoke_score, 1),
        reasons=reasons,
    )


# ---------------------------------------------------------------------------
# improve_recipe — apply targeted corrections based on scoring reasons
# ---------------------------------------------------------------------------

def improve_recipe(
    ingredients: List[str],
    method: str,
    glassware: str,
    garnish: str,
    payload: Dict[str, Any],
    reasons: List[ScoringReason],
    stock_names: Optional[List[str]] = None,
) -> Tuple[List[str], str, str, str]:
    """Apply targeted corrections based on scoring reasons.

    Returns (improved_ingredients, improved_method, improved_glassware, improved_garnish).
    Operates on rendered ingredient strings. Does NOT change payload shape.
    """
    ingredients = list(ingredients)  # don't mutate caller's list
    aroma = (payload.get("aroma_preference") or "").lower().strip()
    season = (payload.get("season") or "").lower().strip()
    house_type = (payload.get("house_type") or "").lower().strip()
    sweetener_q = (payload.get("sweetener_question") or "").lower().strip()
    carbonation = (payload.get("carbonation_texture") or "").lower().strip()
    dining_style = (payload.get("dining_style") or "").lower().strip()
    foam_toggle = (payload.get("foam_toggle") or "").lower().strip()

    still = "still" in carbonation
    properly_sparkling = "proper" in carbonation or "spark" in carbonation

    for reason in reasons:
        action = reason.fix_action

        if action == "inject_sour":
            # Add sour only if not already present and a sour exists in stock
            if not _has_role(ingredients, "sour"):
                sour_name = "Lemon Juice"
                if stock_names:
                    stock_lower = [s.lower() for s in stock_names]
                    for candidate in ("fresh lemon juice", "lemon juice", "fresh lime juice", "lime juice", "citrus juice"):
                        if any(candidate in s for s in stock_lower):
                            sour_name = next(s for s in stock_names if candidate in s.lower())
                            break
                    else:
                        # No sour in stock — skip injection
                        continue
                ingredients.append(f"15ml {sour_name} (sour)")
                if "shake" in method.lower() or "stir" in method.lower():
                    pass  # method already has a build step
                else:
                    method = method.rstrip()

        elif action == "reduce_double_sour":
            # If cordial + fresh citrus, reduce fresh citrus to 10ml
            for i, line in enumerate(ingredients):
                ml, name, role = _parse_line(line)
                if role == "sour" and ml and ml > 10:
                    ingredients[i] = f"10ml {name} (sour)"
                    break

        elif action == "reduce_sugar_stacking":
            # Strategy: if sweet mixer + syrup, reduce syrup; if neon modifier, swap it
            has_sweet_mixer = _has_top_with(ingredients) and _contains_any(
                _all_text(ingredients), ["lemonade", "ginger ale"]
            )
            if has_sweet_mixer:
                # Reduce sweetener to 10ml
                for i, line in enumerate(ingredients):
                    ml, name, role = _parse_line(line)
                    if role == "sweetener" and ml and ml > 10:
                        ingredients[i] = f"10ml {name} (sweetener)"
                        break

        elif action == "drop_excess_juice":
            # Drop the last juice (lowest priority)
            juice_indices = [
                i for i, line in enumerate(ingredients) if _parse_line(line)[2] == "juice"
            ]
            if len(juice_indices) > 2:
                # Remove from the end (last added = lowest priority)
                for idx in reversed(juice_indices[2:]):
                    ingredients.pop(idx)

        elif action == "swap_neon_modifier":
            # Replace neon modifier with a more appropriate one
            for i, line in enumerate(ingredients):
                ml, name, role = _parse_line(line)
                if role == "modifier" and _contains_any(name, list(NEON_MODIFIERS)):
                    # Pick a replacement based on vibe
                    replacement = _pick_vibe_modifier(aroma, season, house_type, stock_names)
                    if replacement and ml:
                        ingredients[i] = f"{ml}ml {replacement} (modifier)"
                    elif not ml:
                        ingredients.pop(i)
                    break

        elif action == "swap_citrus_for_dessert_spirit":
            # Swap lime to lemon for dessert spirits
            for i, line in enumerate(ingredients):
                ml, name, role = _parse_line(line)
                if role == "sour" and "lime" in name.lower():
                    ingredients[i] = f"{ml}ml Lemon Juice (sour)" if ml else "15ml Lemon Juice (sour)"
                    break
            # Also remove lime cordial if present
            for i, line in enumerate(ingredients):
                if "cordial" in line.lower() and "lime" in line.lower():
                    ingredients.pop(i)
                    break

        elif action == "fix_garnish":
            garnish = _pick_garnish_for_aroma(aroma, ingredients)

        elif action == "fix_glassware_still":
            if still and not _has_top_with(ingredients):
                glassware = "rocks glass"
                method = method.replace("long glass", "rocks glass")

        elif action == "fix_glassware_topped":
            if _has_top_with(ingredients):
                glassware = "long glass"
                method = method.replace("martini glass", "long glass").replace("coupe", "long glass")

        elif action == "fix_method_top":
            if _has_top_with(ingredients) and "top" not in method.lower():
                # Find the mixer name
                for line in ingredients:
                    if line.lower().strip().startswith("top with"):
                        mixer_name = re.sub(
                            r"\s*\(mixer\)\s*$", "",
                            line.strip()[len("Top with "):],
                            flags=re.IGNORECASE,
                        ).strip()
                        # Add top step to method
                        steps = method.strip().split("\n")
                        # Insert top step before garnish step
                        garnish_step_idx = None
                        for idx, step in enumerate(steps):
                            if "garnish" in step.lower():
                                garnish_step_idx = idx
                                break
                        top_step = f"{len(steps) + 1}. Top with {mixer_name}."
                        if garnish_step_idx is not None:
                            steps.insert(garnish_step_idx, f"{garnish_step_idx + 1}. Top with {mixer_name}.")
                            # Renumber subsequent steps
                            for j in range(garnish_step_idx + 1, len(steps)):
                                steps[j] = re.sub(r"^\d+\.", f"{j + 1}.", steps[j])
                        else:
                            steps.append(top_step)
                        method = "\n".join(steps)
                        break

        elif action == "add_complexity_modifier":
            # Add a modifier for complexity if we have too many juices + mixer
            modifier = _pick_vibe_modifier(aroma, season, house_type, stock_names)
            if modifier:
                ingredients.insert(1, f"15ml {modifier} (modifier)")

        elif action == "remove_tropical_for_vibe":
            # Replace ALL tropical ingredients with vibe-appropriate ones
            # Collect indices to modify first, then apply in reverse to avoid index shift
            tropical_indices = []
            for i, line in enumerate(ingredients):
                ml, name, role = _parse_line(line)
                name_lower = name.lower()
                is_tropical = any(t in name_lower for t in TROPICAL_SET)
                if is_tropical:
                    tropical_indices.append((i, ml, name, role))

            # Track what replacements we've already used to avoid duplicates
            used_replacements: set = set()
            for i, ml, name, role in reversed(tropical_indices):
                if role == "sweetener":
                    replacement = _pick_vibe_sweetener(aroma, season, sweetener_q, stock_names)
                    if replacement and replacement.lower() not in used_replacements:
                        ingredients[i] = f"{ml or 15}ml {replacement} (sweetener)"
                        used_replacements.add(replacement.lower())
                    else:
                        ingredients.pop(i)
                elif role == "modifier":
                    replacement = _pick_vibe_modifier(aroma, season, house_type, stock_names)
                    if replacement and ml and replacement.lower() not in used_replacements:
                        ingredients[i] = f"{ml}ml {replacement} (modifier)"
                        used_replacements.add(replacement.lower())
                    else:
                        ingredients.pop(i)
                elif role == "juice":
                    replacement = _pick_vibe_juice(aroma, season, stock_names)
                    if replacement and ml and replacement.lower() not in used_replacements:
                        ingredients[i] = f"{ml}ml {replacement} (juice)"
                        used_replacements.add(replacement.lower())
                    else:
                        ingredients.pop(i)
                else:
                    # Spirit or untagged — remove if tropical
                    ingredients.pop(i)

        elif action == "add_citrus_backbone":
            if not _has_role(ingredients, "sour"):
                ingredients.append("15ml Lemon Juice (sour)")

        elif action == "fix_garnish_citrus":
            garnish = _pick_citrus_garnish(ingredients)

        elif action == "add_woody_elements":
            # Try to add woody modifier
            modifier = _pick_woody_modifier(stock_names)
            if modifier:
                ingredients.insert(1, f"10ml {modifier} (modifier)")

        elif action == "add_floral_element":
            floral = _pick_floral_ingredient(stock_names)
            if floral:
                ingredients.insert(1, f"15ml {floral} (modifier)")

        elif action == "fix_zesty_sweetener":
            # Replace tropical sweetener with citrus-led one
            for i, line in enumerate(ingredients):
                ml, name, role = _parse_line(line)
                if role == "sweetener":
                    is_tropical_sweet = _contains_any(name, [
                        "pineapple", "passionfruit", "passion fruit",
                        "coconut", "mango",
                    ])
                    if is_tropical_sweet:
                        replacement = _pick_zesty_sweetener(stock_names)
                        if replacement:
                            new_ml = min(ml or 10, 10)  # reduce if tropical
                            ingredients[i] = f"{new_ml}ml {replacement} (sweetener)"
                        break

        elif action == "fix_rich_sweetener":
            # Replace with a rich sweetener
            for i, line in enumerate(ingredients):
                ml, name, role = _parse_line(line)
                if role == "sweetener":
                    has_rich = _contains_any(name, list(RICH_SWEETENERS))
                    if not has_rich:
                        replacement = _pick_rich_sweetener(stock_names)
                        if replacement:
                            ingredients[i] = f"{ml or 15}ml {replacement} (sweetener)"
                        break

        elif action == "remove_carbonated_top":
            # Remove carbonated mixer for still drinks
            ingredients = [
                line for line in ingredients
                if not line.lower().strip().startswith("top with")
            ]
            # Switch to rocks/coupe if in long glass
            if "long" in glassware.lower():
                glassware = "rocks glass"
                method = method.replace("long glass", "rocks glass")

        elif action == "add_sparkling_top":
            if properly_sparkling:
                # Add soda water as default
                ingredients.append("Top with Soda Water (mixer)")
                if "long" not in glassware.lower() and "highball" not in glassware.lower():
                    glassware = "long glass"

        elif action == "reduce_abv":
            # Reduce spirit volume
            for i, line in enumerate(ingredients):
                ml, name, role = _parse_line(line)
                if role is None and ml is not None and ml >= 50:
                    if not line.lower().strip().startswith("top with"):
                        ingredients[i] = f"25ml {name}"
                        break

        elif action == "fix_method_shaken":
            # Ensure method mentions shaking
            if "shake" not in method.lower():
                steps = method.strip().split("\n")
                if steps:
                    steps[0] = "1. Add ingredients to a shaker with ice and shake hard."
                    # Renumber rest
                    for j in range(1, len(steps)):
                        steps[j] = re.sub(r"^\d+\.", f"{j + 1}.", steps[j])
                    method = "\n".join(steps)

        elif action == "reduce_syrup_subtle":
            for i, line in enumerate(ingredients):
                ml, name, role = _parse_line(line)
                if role == "sweetener" and ml and ml > 10:
                    ingredients[i] = f"10ml {name} (sweetener)"
                    break

        elif action == "swap_lemonade_to_soda":
            for i, line in enumerate(ingredients):
                if line.lower().strip().startswith("top with") and "lemonade" in line.lower():
                    # Find soda in stock
                    soda_name = _find_in_stock(stock_names, ["soda water", "soda", "sparkling water"])
                    if soda_name:
                        ingredients[i] = f"Top with {soda_name} (mixer)"
                    else:
                        ingredients[i] = "Top with Soda Water (mixer)"
                    # Update method text too
                    method = re.sub(
                        r"(?i)lemonade",
                        soda_name or "Soda Water",
                        method,
                    )
                    break

        elif action == "reduce_juice_subtle":
            # Reduce to 1 juice for subtle/fresh
            juice_indices = [
                i for i, line in enumerate(ingredients) if _parse_line(line)[2] == "juice"
            ]
            if len(juice_indices) > 1:
                for idx in reversed(juice_indices[1:]):
                    ingredients.pop(idx)

    return ingredients, method, glassware, garnish


# ---------------------------------------------------------------------------
# Stock-aware ingredient pickers (for improve_recipe corrections)
# ---------------------------------------------------------------------------

def _find_in_stock(stock_names: Optional[List[str]], candidates: List[str]) -> Optional[str]:
    """Find the first candidate that exists in stock (case-insensitive).

    Uses exact match first, then falls back to substring match. Avoids false
    positives like 'Apple Juice' matching 'Pineapple Juice'.
    """
    if not stock_names:
        return candidates[0] if candidates else None
    stock_lower = {s.lower().strip(): s for s in stock_names}
    # Pass 1: exact match
    for c in candidates:
        cl = c.lower().strip()
        if cl in stock_lower:
            return stock_lower[cl]
    # Pass 2: substring match but only if candidate is a word-boundary match
    for c in candidates:
        cl = c.lower().strip()
        for sl, original in stock_lower.items():
            # Ensure substring match is at a word boundary (not "apple" in "pineapple")
            if cl in sl and (sl.startswith(cl) or f" {cl}" in sl):
                return original
    return None


def _pick_vibe_modifier(
    aroma: str, season: str, house_type: str,
    stock_names: Optional[List[str]],
) -> Optional[str]:
    """Pick a modifier that matches the vibe."""
    if aroma == "floral":
        return _find_in_stock(stock_names, [
            "Elderflower Liqueur", "Elderflower Cordial", "St Germain",
        ])
    if aroma == "woody":
        return _find_in_stock(stock_names, [
            "Angostura Bitters", "Orange Bitters", "Amaro",
        ])
    if aroma == "citrus":
        return _find_in_stock(stock_names, [
            "Cointreau", "Triple Sec", "Limoncello", "Orange Liqueur",
        ])
    if season in ("autumn", "winter") or "haunted" in house_type or "tree" in house_type:
        return _find_in_stock(stock_names, [
            "Angostura Bitters", "Orange Bitters",
            "Amaretto", "Amaro",
        ])
    return _find_in_stock(stock_names, [
        "Cointreau", "Triple Sec", "Elderflower Liqueur",
    ])


def _pick_vibe_sweetener(
    aroma: str, season: str, sweetener_q: str,
    stock_names: Optional[List[str]],
) -> Optional[str]:
    """Pick a sweetener that matches the vibe. Always falls back to simple syrup."""
    result = None
    if sweetener_q == "rich":
        result = _find_in_stock(stock_names, [
            "Vanilla Syrup", "Honey Syrup", "Demerara Syrup",
            "Raspberry Syrup", "Strawberry Syrup", "Simple Syrup",
        ])
    elif sweetener_q == "zesty":
        result = _find_in_stock(stock_names, [
            "Simple Syrup", "Agave Syrup", "Lime Cordial",
        ])
    elif aroma == "woody" or season in ("autumn", "winter"):
        result = _find_in_stock(stock_names, [
            "Vanilla Syrup", "Honey Syrup", "Simple Syrup",
            "Raspberry Syrup",
        ])
    elif aroma == "floral":
        result = _find_in_stock(stock_names, [
            "Elderflower Cordial", "Honey Syrup", "Simple Syrup",
        ])
    if result is None:
        result = _find_in_stock(stock_names, [
            "Simple Syrup", "Vanilla Syrup", "Raspberry Syrup",
            "Strawberry Syrup", "Peach Syrup",
        ])
    return result


def _pick_vibe_juice(
    aroma: str, season: str,
    stock_names: Optional[List[str]],
) -> Optional[str]:
    """Pick a juice that matches the vibe."""
    if aroma == "citrus":
        return _find_in_stock(stock_names, [
            "Orange Juice", "Grapefruit Juice", "Cranberry Juice",
        ])
    if aroma == "woody" or season in ("autumn", "winter"):
        return _find_in_stock(stock_names, [
            "Cranberry Juice", "Apple Juice", "Orange Juice",
        ])
    if aroma == "sweet":
        return _find_in_stock(stock_names, [
            "Cranberry Juice", "Orange Juice", "Apple Juice",
        ])
    return _find_in_stock(stock_names, [
        "Cranberry Juice", "Apple Juice", "Orange Juice",
    ])


def _pick_garnish_for_aroma(aroma: str, ingredients: List[str]) -> str:
    """Pick a garnish that echoes the aroma preference."""
    if aroma == "citrus":
        return "Lemon Wheel"
    if aroma == "woody":
        return "Orange Peel"
    if aroma == "floral":
        return "Lemon Twist"
    if aroma == "sweet":
        # Try to match a fruit in the drink
        txt = _all_text(ingredients)
        if "raspberry" in txt or "strawberry" in txt:
            return "Fresh Berries"
        if "cranberry" in txt:
            return "Lemon Twist"
        return "Orange Slice"
    return "Lemon Twist"


def _pick_citrus_garnish(ingredients: List[str]) -> str:
    """Pick a citrus garnish based on citrus in the drink."""
    txt = _all_text(ingredients)
    if "lime" in txt:
        return "Lime Wedge"
    if "orange" in txt:
        return "Orange Peel"
    if "grapefruit" in txt:
        return "Grapefruit Twist"
    return "Lemon Wheel"


def _pick_woody_modifier(stock_names: Optional[List[str]]) -> Optional[str]:
    """Pick a woody modifier from stock."""
    return _find_in_stock(stock_names, [
        "Angostura Bitters", "Orange Bitters", "Amaro",
        "Amaretto",
    ])


def _pick_floral_ingredient(stock_names: Optional[List[str]]) -> Optional[str]:
    """Pick a floral ingredient from stock."""
    return _find_in_stock(stock_names, [
        "Elderflower Liqueur", "Elderflower Cordial", "St Germain",
        "Violet Liqueur",
    ])


def _pick_zesty_sweetener(stock_names: Optional[List[str]]) -> Optional[str]:
    """Pick a zesty/citrus-led sweetener from stock."""
    return _find_in_stock(stock_names, [
        "Simple Syrup", "Agave Syrup", "Lime Cordial",
    ])


def _pick_rich_sweetener(stock_names: Optional[List[str]]) -> Optional[str]:
    """Pick a rich sweetener from stock."""
    return _find_in_stock(stock_names, [
        "Vanilla Syrup", "Honey Syrup", "Demerara Syrup",
        "Raspberry Syrup", "Strawberry Syrup", "Peach Syrup",
        "Simple Syrup",
    ])


# ---------------------------------------------------------------------------
# iterate_until_pass — the main quality gate loop
# ---------------------------------------------------------------------------

def iterate_until_pass(
    ingredients: List[str],
    method: str,
    glassware: str,
    garnish: str,
    payload: Dict[str, Any],
    stock_names: Optional[List[str]] = None,
    max_iterations: int = 4,
    target_score: float = 8.0,
) -> Tuple[List[str], str, str, str, ScoreResult]:
    """Run score -> improve loop until both scores >= target or max iterations reached.

    Returns (ingredients, method, glassware, garnish, final_score_result).
    """
    best_ingredients = list(ingredients)
    best_method = method
    best_glassware = glassware
    best_garnish = garnish
    best_total = 0.0

    for iteration in range(max_iterations):
        result = score_recipe(
            ingredients, method, glassware, garnish,
            payload, stock_names,
        )

        total = result.quality_score + result.bespoke_score
        if total > best_total:
            best_ingredients = list(ingredients)
            best_method = method
            best_glassware = glassware
            best_garnish = garnish
            best_total = total

        logger.debug(
            "Scoring iteration %d: quality=%.1f bespoke=%.1f reasons=%d",
            iteration + 1, result.quality_score, result.bespoke_score, len(result.reasons),
        )

        if result.quality_score >= target_score and result.bespoke_score >= target_score:
            logger.info(
                "Recipe passed scoring at iteration %d: quality=%.1f bespoke=%.1f",
                iteration + 1, result.quality_score, result.bespoke_score,
            )
            return ingredients, method, glassware, garnish, result

        if not result.reasons:
            break

        # Apply improvements
        ingredients, method, glassware, garnish = improve_recipe(
            ingredients, method, glassware, garnish,
            payload, result.reasons, stock_names,
        )

    # Final scoring after all iterations
    final_result = score_recipe(
        best_ingredients, best_method, best_glassware, best_garnish,
        payload, stock_names,
    )

    # Return the best version we found
    current_result = score_recipe(
        ingredients, method, glassware, garnish,
        payload, stock_names,
    )
    current_total = current_result.quality_score + current_result.bespoke_score

    if current_total >= best_total:
        logger.info(
            "Recipe scoring complete after %d iterations: quality=%.1f bespoke=%.1f",
            max_iterations, current_result.quality_score, current_result.bespoke_score,
        )
        return ingredients, method, glassware, garnish, current_result
    else:
        logger.info(
            "Recipe scoring complete (using best from iteration): quality=%.1f bespoke=%.1f",
            final_result.quality_score, final_result.bespoke_score,
        )
        return best_ingredients, best_method, best_glassware, best_garnish, final_result
