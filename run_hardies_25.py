"""Run 25 human-style quiz payloads against hardies bar and print structured results."""
from __future__ import annotations
import json
from recipebuilder import generate_cocktail_recipe, StockRepository
from recipebuilder.bartender_score import score_recipe, iterate_until_pass

PAYLOADS = [
    # 1 — Summer beach, gin, citrus, sparkling
    {
        "base_spirit": "gin", "season": "summer", "house_type": "beach house",
        "dining_style": "refreshing and vibrant", "music_preference": "pop",
        "aroma_preference": "citrus", "bitterness_tolerance": "low",
        "sweetener_question": "zesty", "carbonation_texture": "lightly fizzy",
        "foam_toggle": "no", "abv_lane": "medium", "allergens": "", "seed": 1001,
    },
    # 2 — Autumn tree, whiskey, woody, still
    {
        "base_spirit": "whiskey", "season": "autumn", "house_type": "tree house",
        "dining_style": "rich and indulgent", "music_preference": "jazz/blues",
        "aroma_preference": "woody", "bitterness_tolerance": "medium",
        "sweetener_question": "classic", "carbonation_texture": "still & silky",
        "foam_toggle": "no", "abv_lane": "strong", "allergens": "", "seed": 1002,
    },
    # 3 — Summer beach, vodka, berry, sparkling, foam
    {
        "base_spirit": "vodka", "season": "summer", "house_type": "beach house",
        "dining_style": "refreshing and vibrant", "music_preference": "pop",
        "aroma_preference": "sweet", "bitterness_tolerance": "low",
        "sweetener_question": "fruity", "carbonation_texture": "properly sparkling",
        "foam_toggle": "yes", "abv_lane": "medium", "allergens": "", "seed": 1003,
    },
    # 4 — Winter haunted, rum, sweet, still
    {
        "base_spirit": "rum", "season": "winter", "house_type": "haunted house",
        "dining_style": "sweet tooth indulging in rich flavours", "music_preference": "rock",
        "aroma_preference": "sweet", "bitterness_tolerance": "low",
        "sweetener_question": "rich", "carbonation_texture": "still & silky",
        "foam_toggle": "no", "abv_lane": "medium", "allergens": "", "seed": 1004,
    },
    # 5 — Spring modern, gin, floral, still, foam
    {
        "base_spirit": "gin", "season": "spring", "house_type": "modern house",
        "dining_style": "subtle and balanced", "music_preference": "jazz/blues",
        "aroma_preference": "floral", "bitterness_tolerance": "medium",
        "sweetener_question": "floral", "carbonation_texture": "still & silky",
        "foam_toggle": "yes", "abv_lane": "medium", "allergens": "", "seed": 1005,
    },
    # 6 — Summer beach, tequila, citrus, fizzy
    {
        "base_spirit": "tequila", "season": "summer", "house_type": "beach house",
        "dining_style": "refreshing and vibrant", "music_preference": "pop",
        "aroma_preference": "citrus", "bitterness_tolerance": "high",
        "sweetener_question": "zesty", "carbonation_texture": "lightly fizzy",
        "foam_toggle": "no", "abv_lane": "strong", "allergens": "", "seed": 1006,
    },
    # 7 — Spring tree, vodka, berry, fizzy
    {
        "base_spirit": "vodka", "season": "spring", "house_type": "tree house",
        "dining_style": "balanced blend of flavours", "music_preference": "pop",
        "aroma_preference": "berry", "bitterness_tolerance": "low",
        "sweetener_question": "fruity", "carbonation_texture": "lightly fizzy",
        "foam_toggle": "no", "abv_lane": "medium", "allergens": "", "seed": 1007,
    },
    # 8 — Autumn haunted, whiskey, woody, fizzy, high bitterness
    {
        "base_spirit": "whiskey", "season": "autumn", "house_type": "haunted house",
        "dining_style": "rich and indulgent", "music_preference": "rock",
        "aroma_preference": "woody", "bitterness_tolerance": "high",
        "sweetener_question": "classic", "carbonation_texture": "lightly fizzy",
        "foam_toggle": "no", "abv_lane": "strong", "allergens": "", "seed": 1008,
    },
    # 9 — Summer beach, rum, tropical, sparkling
    {
        "base_spirit": "rum", "season": "summer", "house_type": "beach house",
        "dining_style": "refreshing and vibrant", "music_preference": "pop",
        "aroma_preference": "sweet", "bitterness_tolerance": "low",
        "sweetener_question": "fruity", "carbonation_texture": "properly sparkling",
        "foam_toggle": "no", "abv_lane": "medium", "allergens": "", "seed": 1009,
    },
    # 10 — Winter modern, vodka, sweet, still, foam
    {
        "base_spirit": "vodka", "season": "winter", "house_type": "modern house",
        "dining_style": "sweet tooth indulging in rich flavours", "music_preference": "pop",
        "aroma_preference": "sweet", "bitterness_tolerance": "low",
        "sweetener_question": "rich", "carbonation_texture": "still & silky",
        "foam_toggle": "yes", "abv_lane": "medium", "allergens": "", "seed": 1010,
    },
    # 11 — Summer modern, gin, citrus, still
    {
        "base_spirit": "gin", "season": "summer", "house_type": "modern house",
        "dining_style": "refreshing and vibrant", "music_preference": "jazz/blues",
        "aroma_preference": "citrus", "bitterness_tolerance": "medium",
        "sweetener_question": "zesty", "carbonation_texture": "still & silky",
        "foam_toggle": "no", "abv_lane": "medium", "allergens": "", "seed": 1011,
    },
    # 12 — Spring beach, vodka, floral, sparkling
    {
        "base_spirit": "vodka", "season": "spring", "house_type": "beach house",
        "dining_style": "refreshing and vibrant", "music_preference": "pop",
        "aroma_preference": "floral", "bitterness_tolerance": "low",
        "sweetener_question": "floral", "carbonation_texture": "properly sparkling",
        "foam_toggle": "no", "abv_lane": "low", "allergens": "", "seed": 1012,
    },
    # 13 — Autumn tree, rum, woody, still
    {
        "base_spirit": "rum", "season": "autumn", "house_type": "tree house",
        "dining_style": "rich and indulgent", "music_preference": "rock",
        "aroma_preference": "woody", "bitterness_tolerance": "medium",
        "sweetener_question": "classic", "carbonation_texture": "still & silky",
        "foam_toggle": "no", "abv_lane": "strong", "allergens": "", "seed": 1013,
    },
    # 14 — Summer beach, tequila, citrus, still, foam
    {
        "base_spirit": "tequila", "season": "summer", "house_type": "beach house",
        "dining_style": "refreshing and vibrant", "music_preference": "pop",
        "aroma_preference": "citrus", "bitterness_tolerance": "medium",
        "sweetener_question": "zesty", "carbonation_texture": "still & silky",
        "foam_toggle": "yes", "abv_lane": "medium", "allergens": "", "seed": 1014,
    },
    # 15 — Winter haunted, vodka, sweet, fizzy, low ABV
    {
        "base_spirit": "vodka", "season": "winter", "house_type": "haunted house",
        "dining_style": "balanced blend of flavours", "music_preference": "pop",
        "aroma_preference": "sweet", "bitterness_tolerance": "low",
        "sweetener_question": "classic", "carbonation_texture": "lightly fizzy",
        "foam_toggle": "no", "abv_lane": "low", "allergens": "", "seed": 1015,
    },
    # 16 — Spring modern, gin, floral, fizzy
    {
        "base_spirit": "gin", "season": "spring", "house_type": "modern house",
        "dining_style": "subtle and balanced", "music_preference": "jazz/blues",
        "aroma_preference": "floral", "bitterness_tolerance": "medium",
        "sweetener_question": "floral", "carbonation_texture": "lightly fizzy",
        "foam_toggle": "no", "abv_lane": "medium", "allergens": "", "seed": 1016,
    },
    # 17 — Summer tree, rum, tropical, fizzy
    {
        "base_spirit": "rum", "season": "summer", "house_type": "tree house",
        "dining_style": "refreshing and vibrant", "music_preference": "pop",
        "aroma_preference": "sweet", "bitterness_tolerance": "low",
        "sweetener_question": "fruity", "carbonation_texture": "lightly fizzy",
        "foam_toggle": "no", "abv_lane": "medium", "allergens": "", "seed": 1017,
    },
    # 18 — Winter tree, whiskey, woody, still, high bitterness
    {
        "base_spirit": "whiskey", "season": "winter", "house_type": "tree house",
        "dining_style": "rich and indulgent", "music_preference": "jazz/blues",
        "aroma_preference": "woody", "bitterness_tolerance": "high",
        "sweetener_question": "classic", "carbonation_texture": "still & silky",
        "foam_toggle": "no", "abv_lane": "strong", "allergens": "", "seed": 1018,
    },
    # 19 — Spring beach, vodka, citrus, sparkling, foam
    {
        "base_spirit": "vodka", "season": "spring", "house_type": "beach house",
        "dining_style": "refreshing and vibrant", "music_preference": "pop",
        "aroma_preference": "citrus", "bitterness_tolerance": "low",
        "sweetener_question": "zesty", "carbonation_texture": "properly sparkling",
        "foam_toggle": "yes", "abv_lane": "medium", "allergens": "", "seed": 1019,
    },
    # 20 — Summer modern, tequila, citrus, still
    {
        "base_spirit": "tequila", "season": "summer", "house_type": "modern house",
        "dining_style": "refreshing and vibrant", "music_preference": "jazz/blues",
        "aroma_preference": "citrus", "bitterness_tolerance": "medium",
        "sweetener_question": "zesty", "carbonation_texture": "still & silky",
        "foam_toggle": "no", "abv_lane": "medium", "allergens": "", "seed": 1020,
    },
    # 21 — Autumn haunted, rum, sweet, still
    {
        "base_spirit": "rum", "season": "autumn", "house_type": "haunted house",
        "dining_style": "rich and indulgent", "music_preference": "rock",
        "aroma_preference": "sweet", "bitterness_tolerance": "low",
        "sweetener_question": "rich", "carbonation_texture": "still & silky",
        "foam_toggle": "no", "abv_lane": "medium", "allergens": "", "seed": 1021,
    },
    # 22 — Spring modern, vodka, berry, still, foam
    {
        "base_spirit": "vodka", "season": "spring", "house_type": "modern house",
        "dining_style": "balanced blend of flavours", "music_preference": "pop",
        "aroma_preference": "berry", "bitterness_tolerance": "low",
        "sweetener_question": "fruity", "carbonation_texture": "still & silky",
        "foam_toggle": "yes", "abv_lane": "medium", "allergens": "", "seed": 1022,
    },
    # 23 — Summer beach, gin, citrus, fizzy, high bitterness
    {
        "base_spirit": "gin", "season": "summer", "house_type": "beach house",
        "dining_style": "refreshing and vibrant", "music_preference": "rock",
        "aroma_preference": "citrus", "bitterness_tolerance": "high",
        "sweetener_question": "zesty", "carbonation_texture": "lightly fizzy",
        "foam_toggle": "no", "abv_lane": "strong", "allergens": "", "seed": 1023,
    },
    # 24 — Winter modern, whiskey, woody, still
    {
        "base_spirit": "whiskey", "season": "winter", "house_type": "modern house",
        "dining_style": "rich and indulgent", "music_preference": "jazz/blues",
        "aroma_preference": "woody", "bitterness_tolerance": "medium",
        "sweetener_question": "classic", "carbonation_texture": "still & silky",
        "foam_toggle": "no", "abv_lane": "strong", "allergens": "", "seed": 1024,
    },
    # 25 — Autumn beach, rum, tropical, sparkling
    {
        "base_spirit": "rum", "season": "autumn", "house_type": "beach house",
        "dining_style": "refreshing and vibrant", "music_preference": "pop",
        "aroma_preference": "sweet", "bitterness_tolerance": "low",
        "sweetener_question": "fruity", "carbonation_texture": "properly sparkling",
        "foam_toggle": "no", "abv_lane": "medium", "allergens": "", "seed": 1025,
    },
]

SCENARIO_LABELS = [
    "Summer beach gin citrus fizzy",
    "Autumn tree whiskey woody still strong",
    "Summer beach vodka berry sparkling foam",
    "Winter haunted rum sweet still",
    "Spring modern gin floral still foam",
    "Summer beach tequila citrus fizzy high-bitterness",
    "Spring tree vodka berry fizzy",
    "Autumn haunted whiskey woody fizzy high-bitterness",
    "Summer beach rum tropical sparkling",
    "Winter modern vodka sweet still foam",
    "Summer modern gin citrus still",
    "Spring beach vodka floral sparkling low-ABV",
    "Autumn tree rum woody still strong",
    "Summer beach tequila citrus still foam",
    "Winter haunted vodka sweet fizzy low-ABV",
    "Spring modern gin floral fizzy",
    "Summer tree rum tropical fizzy",
    "Winter tree whiskey woody still high-bitterness strong",
    "Spring beach vodka citrus sparkling foam",
    "Summer modern tequila citrus still",
    "Autumn haunted rum sweet still",
    "Spring modern vodka berry still foam",
    "Summer beach gin citrus fizzy high-bitterness strong",
    "Winter modern whiskey woody still strong",
    "Autumn beach rum tropical sparkling",
]


def fmt_recipe(n, label, payload, recipe, score_result):
    lines = []
    lines.append(f"\n{'='*60}")
    lines.append(f"#{n:02d} {label}")
    lines.append(f"  Base: {payload['base_spirit']} | Season: {payload['season']} | House: {payload['house_type']}")
    lines.append(f"  Aroma: {payload['aroma_preference']} | Bitterness: {payload['bitterness_tolerance']} | ABV: {payload['abv_lane']}")
    lines.append(f"  Carbonation: {payload['carbonation_texture']} | Foam: {payload['foam_toggle']}")
    lines.append(f"  Dining: {payload['dining_style']}")
    lines.append(f"\n  SCORE: {score_result[0]:.1f}/10")
    lines.append(f"  Glass: {recipe.glassware} | Garnish: {recipe.garnish}")
    lines.append(f"  Ingredients:")
    for ing in recipe.ingredients:
        amt = f"{ing.amount_ml:.0f}ml " if ing.amount_ml else ""
        lines.append(f"    - {amt}{ing.ingredient.name} ({ing.role})")
    if score_result[1]:
        lines.append(f"  Issues: {', '.join(r.category + '/' + r.subcategory for r in score_result[1])}")
    return "\n".join(lines)


if __name__ == "__main__":
    repo = StockRepository()
    results = []
    scores = []

    for n, (label, payload) in enumerate(zip(SCENARIO_LABELS, PAYLOADS), 1):
        try:
            recipe = generate_cocktail_recipe(payload, bar_id="hardies")
            ing_strings = [
                f"{i.amount_ml:.0f}ml {i.ingredient.name} ({i.role})" if i.amount_ml
                else f"Top with {i.ingredient.name} (mixer)"
                for i in recipe.ingredients
            ]
            sr = score_recipe(
                ing_strings,
                (" ".join(recipe.steps) if recipe.steps else ""),
                recipe.glassware or "",
                recipe.garnish or "",
                payload,
            )
            score_val = (sr.quality_score + sr.bespoke_score) / 2
            scores.append(score_val)
            results.append(fmt_recipe(n, label, payload, recipe, (score_val, sr.reasons)))
        except Exception as e:
            results.append(f"\n#{n:02d} {label}\n  ERROR: {e}")
            scores.append(0)

    output = "\n".join(results)
    output += f"\n\n{'='*60}\nSUMMARY: {len(scores)} recipes\n"
    output += f"Average score: {sum(scores)/len(scores):.2f}/10\n"
    output += f"Range: {min(scores):.1f} - {max(scores):.1f}\n"
    output += f">=8.0: {sum(1 for s in scores if s >= 8.0)}\n"
    output += f"7.0-7.9: {sum(1 for s in scores if 7.0 <= s < 8.0)}\n"
    output += f"<7.0: {sum(1 for s in scores if s < 7.0)}\n"

    print(output)
    with open("recipe_analysis/hardies_25.txt", "w") as f:
        f.write(output)
    print("\nSaved to recipe_analysis/hardies_25.txt")
