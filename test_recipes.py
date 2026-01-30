#!/usr/bin/env python3
"""Comprehensive test harness for evaluating recipe quality across all scenarios."""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from recipebuilder import ProfileRecipeBuilder, StockRepository, choose_profile


@dataclass
class TestCase:
    """A test scenario with expected outcomes."""
    name: str
    bar: str
    responses: Dict[str, Any]
    expected_family: str
    should_contain: List[str] = field(default_factory=list)
    should_not_contain: List[str] = field(default_factory=list)
    notes: str = ""


@dataclass
class RecipeEvaluation:
    """Evaluation result for a single recipe."""
    test_name: str
    bar: str
    profile: str
    ingredients: List[str]
    glassware: str
    garnish: str
    method: List[str]

    contains_expected: List[str]
    missing_expected: List[str]
    contains_forbidden: List[str]

    has_base: bool
    has_sour: bool
    has_sweetener: bool
    has_juice_or_mixer: bool
    component_count: int

    issues: List[str]

    @property
    def score(self) -> float:
        """Calculate overall quality score 0-10."""
        score = 5.0

        if self.has_base:
            score += 1.0
        else:
            score -= 2.0

        if self.has_sour or self.has_sweetener:
            score += 0.5

        if 4 <= self.component_count <= 7:
            score += 1.0
        elif self.component_count < 4:
            score -= 1.0
        elif self.component_count > 8:
            score -= 0.5

        score += len(self.contains_expected) * 0.5
        score -= len(self.missing_expected) * 0.5
        score -= len(self.contains_forbidden) * 2.0
        score -= len(self.issues) * 0.5

        return max(0.0, min(10.0, score))


def _ingredients_text(ingredients: List[str]) -> str:
    return " ".join(ingredients).lower()


# Garnish color coherence rules
GARNISH_COLOR_RULES = {
    # Garnish -> compatible drink colors/ingredients
    "orange": ["orange", "citrus", "rum", "bourbon", "whiskey", "tequila"],
    "lemon": ["lemon", "citrus", "vodka", "gin", "light", "fresh"],
    "lime": ["lime", "citrus", "rum", "tequila", "mojito", "green"],
    "mint": ["mojito", "fresh", "green", "lime", "cucumber"],
    "pineapple": ["tropical", "rum", "pineapple", "coconut", "yellow"],
    "cherry": ["cherry", "cola", "bourbon", "whiskey", "red", "grenadine"],
    "raspberry": ["berry", "raspberry", "pink", "red", "vodka"],
    "strawberry": ["strawberry", "berry", "pink", "red", "sweet"],
    "rosemary": ["gin", "woody", "herbal", "autumn", "winter"],
}


def check_garnish_coherence(garnish: str, ingredients: List[str], responses: Dict[str, Any]) -> Optional[str]:
    """Check if garnish makes sense with the drink."""
    if not garnish:
        return "NO_GARNISH"

    garnish_lower = garnish.lower()
    txt = _ingredients_text(ingredients)
    aroma = (responses.get("aroma_preference") or "").lower()
    season = (responses.get("season") or "").lower()

    # Check specific mismatches
    if "rosemary" in garnish_lower:
        if "tropical" in txt or "pineapple" in txt or "coconut" in txt:
            if "woody" not in aroma:
                return "ROSEMARY_WITH_TROPICAL"

    if "pineapple" in garnish_lower:
        if "woody" in aroma and season in ("autumn", "winter"):
            if "pineapple" not in txt and "tropical" not in txt:
                return "PINEAPPLE_GARNISH_WITH_WOODY"

    return None


def evaluate_recipe(test: TestCase, recipe, profile: str) -> RecipeEvaluation:
    """Evaluate a recipe against test expectations."""

    ingredients = []
    for sug in recipe.ingredients:
        name = sug.ingredient.name
        role = sug.role
        ml = sug.amount_ml
        if role == "mixer":
            ingredients.append(f"Top with {name}")
        elif ml and ml > 0:
            ingredients.append(f"{ml:.0f}ml {name} ({role})")
        else:
            ingredients.append(f"{name} ({role})")

    txt = _ingredients_text(ingredients)

    contains_expected = [kw for kw in test.should_contain if kw.lower() in txt]
    missing_expected = [kw for kw in test.should_contain if kw.lower() not in txt]
    contains_forbidden = [kw for kw in test.should_not_contain if kw.lower() in txt]

    has_base = any(s.role == "base" for s in recipe.ingredients)
    has_sour = any(s.role == "sour" for s in recipe.ingredients)
    has_sweetener = any(s.role == "sweetener" for s in recipe.ingredients)
    has_juice_or_mixer = any(s.role in ("juice", "mixer") for s in recipe.ingredients)
    component_count = len([s for s in recipe.ingredients if s.role != "garnish"])

    issues = []

    # Check carbonation coherence
    carb = (test.responses.get("carbonation_texture") or "").lower()
    if "still" in carb:
        for ing in ingredients:
            if "top with" in ing.lower():
                mixer_name = ing.lower()
                if any(fizzy in mixer_name for fizzy in ["lemonade", "soda", "tonic", "ginger beer"]):
                    issues.append(f"STILL_WITH_FIZZY_TOP: {ing}")

    # Check bitterness coherence
    bitter = (test.responses.get("bitterness_tolerance") or "").lower()
    if bitter == "high":
        bitter_ingredients = ["tonic", "bitter", "amaro", "grapefruit", "campari", "aperol"]
        if not any(b in txt for b in bitter_ingredients):
            issues.append("HIGH_BITTER_NO_BITTER_ELEMENT")
    elif bitter == "low":
        if "tonic" in txt:
            issues.append("LOW_BITTER_HAS_TONIC")

    # Check woody/autumn coherence (only when aroma is explicitly woody)
    aroma = (test.responses.get("aroma_preference") or "").lower()
    sweetener_style = (test.responses.get("sweetener_question") or "").lower()
    dining_style = (test.responses.get("dining_style") or "").lower()

    is_woody_profile = "woody" in aroma or "campfire" in aroma
    is_dessert_profile = "rich" in sweetener_style or "dessert" in dining_style or "sweet tooth" in dining_style

    if is_woody_profile and not is_dessert_profile:
        tropical_ingredients = ["malibu", "coconut syrup", "passionfruit syrup", "pineapple syrup", "banana liqueur"]
        found_tropical = [t for t in tropical_ingredients if t in txt]
        if found_tropical and "beach" not in (test.responses.get("house_type") or "").lower():
            issues.append(f"WOODY_WITH_TROPICAL: {found_tropical}")

    # Check for too many juices
    juice_count = sum(1 for s in recipe.ingredients if s.role == "juice")
    if juice_count > 2:
        issues.append(f"TOO_MANY_JUICES: {juice_count}")

    # Check garnish coherence
    garnish_issue = check_garnish_coherence(recipe.garnish or "", ingredients, test.responses)
    if garnish_issue:
        issues.append(garnish_issue)

    # Check for duplicate ingredients
    ingredient_names = [s.ingredient.name.lower() for s in recipe.ingredients]
    duplicates = [name for name in set(ingredient_names) if ingredient_names.count(name) > 1]
    if duplicates:
        issues.append(f"DUPLICATE_INGREDIENTS: {duplicates}")

    return RecipeEvaluation(
        test_name=test.name,
        bar=test.bar,
        profile=profile,
        ingredients=ingredients,
        glassware=recipe.glassware or "",
        garnish=recipe.garnish or "",
        method=recipe.steps or [],
        contains_expected=contains_expected,
        missing_expected=missing_expected,
        contains_forbidden=contains_forbidden,
        has_base=has_base,
        has_sour=has_sour,
        has_sweetener=has_sweetener,
        has_juice_or_mixer=has_juice_or_mixer,
        component_count=component_count,
        issues=issues,
    )


def print_evaluation(eval: RecipeEvaluation, verbose: bool = True) -> None:
    """Pretty print an evaluation result."""
    print(f"\n{'='*60}")
    print(f"TEST: {eval.test_name}")
    print(f"BAR: {eval.bar} | PROFILE: {eval.profile}")
    print(f"SCORE: {eval.score:.1f}/10")
    print(f"{'='*60}")

    if verbose:
        print("\nINGREDIENTS:")
        for ing in eval.ingredients:
            print(f"  - {ing}")

        print(f"\nGLASS: {eval.glassware}")
        print(f"GARNISH: {eval.garnish}")

        print(f"\nSTRUCTURE: {eval.component_count} components")
        print(f"  Base: {'YES' if eval.has_base else 'NO'}")
        print(f"  Sour: {'YES' if eval.has_sour else 'NO'}")
        print(f"  Sweetener: {'YES' if eval.has_sweetener else 'NO'}")
        print(f"  Juice/Mixer: {'YES' if eval.has_juice_or_mixer else 'NO'}")

    if eval.contains_expected:
        print(f"\n[OK] Contains expected: {eval.contains_expected}")
    if eval.missing_expected:
        print(f"\n[WARN] Missing expected: {eval.missing_expected}")
    if eval.contains_forbidden:
        print(f"\n[FAIL] Contains forbidden: {eval.contains_forbidden}")

    if eval.issues:
        print(f"\n[ISSUES]:")
        for issue in eval.issues:
            print(f"  - {issue}")

    if verbose:
        print(f"\nMETHOD:")
        for step in eval.method:
            print(f"  {step}")


# =============================================================================
# COMPREHENSIVE TEST CASES
# =============================================================================

# Core test cases from the original brief
CORE_TEST_CASES = [
    TestCase(
        name="Rum Woody Autumn Classic",
        bar="aviary",
        responses={
            "base_spirit": "rum",
            "season": "autumn",
            "house_type": "tree house",
            "dining_style": "balanced blend which marries contrasting ingredients",
            "music_preference": "rock",
            "aroma_preference": "woody",
            "bitterness_tolerance": "medium",
            "sweetener_question": "classic",
            "carbonation_texture": "lightly fizzy",
            "foam_toggle": "no",
            "abv_lane": "medium",
        },
        expected_family="DARK_SPICED_WOODY",
        should_contain=["spiced rum", "apple"],
        should_not_contain=["malibu", "passionfruit syrup", "pineapple syrup", "coconut syrup"],
    ),
    TestCase(
        name="Tequila Woody Autumn Still Foam",
        bar="aviary",
        responses={
            "base_spirit": "tequila",
            "season": "autumn",
            "house_type": "tree house",
            "dining_style": "balanced blend",
            "music_preference": "jazz/blues",
            "aroma_preference": "woody",
            "bitterness_tolerance": "medium",
            "sweetener_question": "classic",
            "carbonation_texture": "still & silky",
            "foam_toggle": "yes",
            "abv_lane": "strong",
        },
        expected_family="DARK_SPICED_WOODY",
        should_contain=["tequila", "lime"],
        should_not_contain=["lemonade", "soda", "ginger beer", "violet", "melon"],
    ),
    TestCase(
        name="Vodka Zesty Citrus Fresh",
        bar="aviary",
        responses={
            "base_spirit": "vodka",
            "season": "summer",
            "house_type": "modern house",
            "dining_style": "subtle tastes which advertise freshness",
            "music_preference": "jazz/blues",
            "aroma_preference": "citrus",
            "bitterness_tolerance": "medium",
            "sweetener_question": "zesty",
            "carbonation_texture": "lightly fizzy",
            "foam_toggle": "yes",
            "abv_lane": "strong",
        },
        expected_family="FRESH_CITRUS_ZESTY",
        should_contain=["vodka", "lemon", "lime"],
        should_not_contain=["bubblegum", "melon", "banana", "marshmallow", "caramel"],
    ),
    TestCase(
        name="Tequila Zesty Still Silky",
        bar="aviary",
        responses={
            "base_spirit": "tequila",
            "season": "summer",
            "house_type": "modern house",
            "dining_style": "refreshing vibrant flavours and bright colours",
            "music_preference": "pop",
            "aroma_preference": "citrus",
            "bitterness_tolerance": "low",
            "sweetener_question": "zesty",
            "carbonation_texture": "still & silky",
            "foam_toggle": "yes",
            "abv_lane": "strong",
        },
        expected_family="FRESH_CITRUS_ZESTY",
        should_contain=["tequila", "lime"],
        should_not_contain=["lemonade", "soda", "tonic", "ginger beer"],
    ),
    TestCase(
        name="Rum Summer Beach Tropical",
        bar="aviary",
        responses={
            "base_spirit": "rum",
            "season": "summer",
            "house_type": "beach house",
            "dining_style": "indulging in rich flavours and sweet tooth desserts",
            "music_preference": "pop",
            "aroma_preference": "sweet",
            "bitterness_tolerance": "low",
            "sweetener_question": "rich",
            "carbonation_texture": "still & silky",
            "foam_toggle": "yes",
            "abv_lane": "strong",
        },
        expected_family="TROPICAL_BRIGHT",
        should_contain=["rum", "pineapple"],
        should_not_contain=[],
    ),
    TestCase(
        name="Rum High Bitter Sparkling",
        bar="aviary",
        responses={
            "base_spirit": "rum",
            "season": "spring",
            "house_type": "modern house",
            "dining_style": "refreshing vibrant flavours",
            "music_preference": "pop",
            "aroma_preference": "woody",
            "bitterness_tolerance": "high",
            "sweetener_question": "classic",
            "carbonation_texture": "properly sparkling",
            "foam_toggle": "yes",
            "abv_lane": "medium",
        },
        expected_family="DARK_SPICED_WOODY",
        should_contain=["rum", "tonic"],
        should_not_contain=["banana"],
    ),
    TestCase(
        name="Gin Low Bitter Floral",
        bar="aviary",
        responses={
            "base_spirit": "gin",
            "season": "spring",
            "house_type": "tree house",
            "dining_style": "subtle tastes which advertise freshness",
            "music_preference": "jazz/blues",
            "aroma_preference": "floral",
            "bitterness_tolerance": "low",
            "sweetener_question": "floral",
            "carbonation_texture": "lightly fizzy",
            "foam_toggle": "no",
            "abv_lane": "medium",
        },
        expected_family="FLORAL_ELEGANT",
        should_contain=["gin", "elderflower"],
        should_not_contain=["tonic", "peach schnapps"],
    ),
    TestCase(
        name="Vodka Winter Dessert Rich",
        bar="aviary",
        responses={
            "base_spirit": "vodka",
            "season": "winter",
            "house_type": "modern house",
            "dining_style": "indulging in rich flavours and sweet tooth desserts",
            "music_preference": "jazz/blues",
            "aroma_preference": "sweet",
            "bitterness_tolerance": "low",
            "sweetener_question": "rich",
            "carbonation_texture": "still & silky",
            "foam_toggle": "yes",
            "abv_lane": "medium",
        },
        expected_family="DESSERT_INDULGENT",
        should_contain=["vodka", "vanilla"],
        should_not_contain=[],
    ),
]

# Rebel bar tests (limited stock)
REBEL_TEST_CASES = [
    TestCase(
        name="Rebel Vodka Summer Beach",
        bar="rebel",
        responses={
            "base_spirit": "vodka",
            "season": "summer",
            "house_type": "beach house",
            "dining_style": "refreshing vibrant flavours",
            "music_preference": "pop",
            "aroma_preference": "sweet",
            "bitterness_tolerance": "low",
            "sweetener_question": "zesty",
            "carbonation_texture": "lightly fizzy",
            "foam_toggle": "no",
            "abv_lane": "medium",
        },
        expected_family="TROPICAL_BRIGHT",
        should_contain=["vodka"],
        should_not_contain=[],
    ),
    TestCase(
        name="Rebel Rum Autumn Woody",
        bar="rebel",
        responses={
            "base_spirit": "rum",
            "season": "autumn",
            "house_type": "haunted house",
            "dining_style": "balanced blend",
            "music_preference": "rock",
            "aroma_preference": "woody",
            "bitterness_tolerance": "medium",
            "sweetener_question": "classic",
            "carbonation_texture": "lightly fizzy",
            "foam_toggle": "no",
            "abv_lane": "strong",
        },
        expected_family="DARK_SPICED_WOODY",
        should_contain=["spiced rum", "apple"],
        should_not_contain=["malibu", "coconut", "pineapple syrup"],
    ),
    TestCase(
        name="Rebel Gin Spring Floral",
        bar="rebel",
        responses={
            "base_spirit": "gin",
            "season": "spring",
            "house_type": "modern house",
            "dining_style": "subtle tastes which advertise freshness",
            "music_preference": "jazz/blues",
            "aroma_preference": "floral",
            "bitterness_tolerance": "low",
            "sweetener_question": "floral",
            "carbonation_texture": "lightly fizzy",
            "foam_toggle": "no",
            "abv_lane": "medium",
        },
        expected_family="FLORAL_ELEGANT",
        should_contain=["gin"],
        should_not_contain=["tonic"],
    ),
]

# Edge case tests
EDGE_CASE_TESTS = [
    TestCase(
        name="Whiskey Winter Classic Strong",
        bar="aviary",
        responses={
            "base_spirit": "whiskey",
            "season": "winter",
            "house_type": "haunted house",
            "dining_style": "balanced blend",
            "music_preference": "jazz/blues",
            "aroma_preference": "woody",
            "bitterness_tolerance": "high",
            "sweetener_question": "classic",
            "carbonation_texture": "still & silky",
            "foam_toggle": "no",
            "abv_lane": "strong",
        },
        expected_family="DARK_SPICED_WOODY",
        should_contain=["bourbon", "whiskey"],
        should_not_contain=["coconut", "pineapple", "banana", "melon"],
    ),
    TestCase(
        name="Gin Summer Beach Candy",
        bar="aviary",
        responses={
            "base_spirit": "gin",
            "season": "summer",
            "house_type": "beach house",
            "dining_style": "refreshing vibrant flavours and bright colours",
            "music_preference": "pop",
            "aroma_preference": "sweet",
            "bitterness_tolerance": "low",
            "sweetener_question": "zesty",
            "carbonation_texture": "properly sparkling",
            "foam_toggle": "no",
            "abv_lane": "low",
        },
        expected_family="CANDY_FUN",
        should_contain=["gin"],
        should_not_contain=[],
    ),
    TestCase(
        name="Vodka Spring Berry Fresh",
        bar="aviary",
        responses={
            "base_spirit": "vodka",
            "season": "spring",
            "house_type": "modern house",
            "dining_style": "balanced blend which marries contrasting ingredients",
            "music_preference": "pop",
            "aroma_preference": "floral",
            "bitterness_tolerance": "medium",
            "sweetener_question": "floral",
            "carbonation_texture": "lightly fizzy",
            "foam_toggle": "no",
            "abv_lane": "medium",
        },
        expected_family="BERRY_FRUITY",
        should_contain=["vodka"],
        should_not_contain=["coconut", "banana", "melon"],
    ),
    TestCase(
        name="Tequila Summer Citrus Low ABV",
        bar="aviary",
        responses={
            "base_spirit": "tequila",
            "season": "summer",
            "house_type": "beach house",
            "dining_style": "subtle tastes which advertise freshness",
            "music_preference": "pop",
            "aroma_preference": "citrus",
            "bitterness_tolerance": "low",
            "sweetener_question": "zesty",
            "carbonation_texture": "properly sparkling",
            "foam_toggle": "no",
            "abv_lane": "low",
        },
        expected_family="FRESH_CITRUS_ZESTY",
        should_contain=["tequila", "lime"],
        should_not_contain=["bubblegum", "marshmallow"],
    ),
]


def get_all_bars() -> List[str]:
    """Get all available bar IDs."""
    bars_dir = Path(__file__).parent / "data" / "bars"
    return [f.stem for f in bars_dir.glob("*.json")]


def generate_random_test(bar: str, seed: int) -> TestCase:
    """Generate a random test case for a bar."""
    rnd = random.Random(seed)

    spirits = ["vodka", "gin", "rum", "tequila", "whiskey"]
    seasons = ["spring", "summer", "autumn", "winter"]
    houses = ["modern house", "beach house", "haunted house", "tree house"]
    dining = [
        "subtle tastes which advertise freshness",
        "balanced blend which marries contrasting ingredients",
        "refreshing vibrant flavours and bright colours",
        "indulging in rich flavours and sweet tooth desserts",
    ]
    music = ["jazz/blues", "pop", "rock", "rap"]
    aromas = ["citrus", "floral", "woody", "sweet"]
    bitterness = ["low", "medium", "high"]
    sweeteners = ["classic", "rich", "floral", "zesty"]
    carbonation = ["still & silky", "lightly fizzy", "properly sparkling"]
    foam = ["yes", "no"]
    abv = ["low", "medium", "strong"]

    return TestCase(
        name=f"Random_{bar}_{seed}",
        bar=bar,
        responses={
            "base_spirit": rnd.choice(spirits),
            "season": rnd.choice(seasons),
            "house_type": rnd.choice(houses),
            "dining_style": rnd.choice(dining),
            "music_preference": rnd.choice(music),
            "aroma_preference": rnd.choice(aromas),
            "bitterness_tolerance": rnd.choice(bitterness),
            "sweetener_question": rnd.choice(sweeteners),
            "carbonation_texture": rnd.choice(carbonation),
            "foam_toggle": rnd.choice(foam),
            "abv_lane": rnd.choice(abv),
        },
        expected_family="UNKNOWN",
        should_contain=[],
        should_not_contain=[],
        notes="Randomly generated test",
    )


def run_tests(
    test_cases: List[TestCase],
    verbose: bool = True,
    max_per_bar: int = 100,
) -> Dict[str, Any]:
    """Run all test cases and return summary statistics."""

    repo = StockRepository()
    results = []
    bar_results: Dict[str, List[RecipeEvaluation]] = {}

    for test in test_cases:
        if test.bar not in bar_results:
            bar_results[test.bar] = []

        if len(bar_results[test.bar]) >= max_per_bar:
            continue

        try:
            repo.prime_cache(test.bar)
        except Exception as e:
            if verbose:
                print(f"[ERROR] Could not load bar {test.bar}: {e}")
            continue

        responses = dict(test.responses)
        responses["bar_id"] = test.bar

        profile = choose_profile(responses)
        builder = ProfileRecipeBuilder(repo)

        seed = random.randint(1, 1000000)

        try:
            recipe = builder.build_recipe(responses, profile, seed=seed)
        except Exception as e:
            if verbose:
                print(f"[ERROR] {test.name}: {e}")
            continue

        eval_result = evaluate_recipe(test, recipe, profile)
        results.append(eval_result)
        bar_results[test.bar].append(eval_result)

        if verbose:
            print_evaluation(eval_result, verbose=True)

    # Summary
    scores = [r.score for r in results]
    avg_score = sum(scores) / len(scores) if scores else 0

    all_issues = []
    for r in results:
        all_issues.extend(r.issues)

    issue_counts = {}
    for issue in all_issues:
        key = issue.split(":")[0]
        issue_counts[key] = issue_counts.get(key, 0) + 1

    forbidden_hits = []
    for r in results:
        if r.contains_forbidden:
            forbidden_hits.append((r.test_name, r.contains_forbidden))

    # Per-bar summary
    bar_summaries = {}
    for bar, evals in bar_results.items():
        if evals:
            bar_scores = [e.score for e in evals]
            bar_summaries[bar] = {
                "count": len(evals),
                "avg_score": sum(bar_scores) / len(bar_scores),
                "min_score": min(bar_scores),
                "max_score": max(bar_scores),
            }

    summary = {
        "total_tests": len(results),
        "average_score": avg_score,
        "score_distribution": {
            "excellent (8-10)": len([s for s in scores if s >= 8]),
            "good (6-8)": len([s for s in scores if 6 <= s < 8]),
            "mediocre (4-6)": len([s for s in scores if 4 <= s < 6]),
            "poor (0-4)": len([s for s in scores if s < 4]),
        },
        "issue_counts": issue_counts,
        "forbidden_ingredient_violations": forbidden_hits,
        "bar_summaries": bar_summaries,
    }

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Total tests: {summary['total_tests']}")
    print(f"Average score: {summary['average_score']:.1f}/10")
    print(f"\nScore distribution:")
    for bucket, count in summary["score_distribution"].items():
        print(f"  {bucket}: {count}")
    print(f"\nIssue counts:")
    for issue, count in sorted(summary["issue_counts"].items(), key=lambda x: -x[1]):
        print(f"  {issue}: {count}")
    if summary["forbidden_ingredient_violations"]:
        print(f"\nForbidden ingredient violations:")
        for test_name, forbidden in summary["forbidden_ingredient_violations"]:
            print(f"  {test_name}: {forbidden}")
    if bar_summaries:
        print(f"\nPer-bar scores:")
        for bar, stats in sorted(bar_summaries.items(), key=lambda x: -x[1]["avg_score"]):
            print(f"  {bar}: {stats['avg_score']:.1f}/10 (n={stats['count']}, range={stats['min_score']:.1f}-{stats['max_score']:.1f})")

    return summary


def run_comprehensive_tests(verbose: bool = False) -> Dict[str, Any]:
    """Run comprehensive tests across all bars."""
    all_tests = CORE_TEST_CASES + REBEL_TEST_CASES + EDGE_CASE_TESTS

    # Add random tests for each bar
    bars = get_all_bars()
    for bar in bars:
        for i in range(3):  # 3 random tests per bar
            all_tests.append(generate_random_test(bar, seed=hash(f"{bar}_{i}") % 1000000))

    return run_tests(all_tests, verbose=verbose)


if __name__ == "__main__":
    import sys

    verbose = "--verbose" in sys.argv or "-v" in sys.argv
    comprehensive = "--comprehensive" in sys.argv or "-c" in sys.argv

    if comprehensive:
        print("Running comprehensive tests across all bars...")
        run_comprehensive_tests(verbose=verbose)
    else:
        print("Running core test cases...")
        all_tests = CORE_TEST_CASES + REBEL_TEST_CASES + EDGE_CASE_TESTS
        run_tests(all_tests, verbose=verbose)
