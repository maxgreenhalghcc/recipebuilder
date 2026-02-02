#!/usr/bin/env python3
"""Test weekend recipes to measure quality improvements."""

import json
from recipebuilder import generate_cocktail_recipe

# Weekend recipes from Max (17 real orders)
WEEKEND_RECIPES = [
    # Recipe 1: Rebel - Vanilla Vodka (Still)
    {
        'name': 'Recipe 1: Vanilla Vodka Still',
        'bar_id': 'rebel',
        'base_spirit': 'vodka',
        'season': 'summer',
        'house_type': 'beach house',
        'dining_style': 'refreshing and vibrant flavours which awaken my senses',
        'music_preference': 'pop',
        'aroma_preference': 'floral',
        'bitterness_tolerance': 'medium',
        'sweetener_question': 'rich',
        'carbonation_texture': 'still & silky',
        'foam_toggle': 'no',
        'abv_lane': 'medium',
        'allergens': 'none',
        'seed': 804123533
    },
    # Recipe 2: Demo Bar - Elderflower Vodka
    {
        'name': 'Recipe 2: Elderflower Vodka Still',
        'bar_id': 'demo-bar',
        'base_spirit': 'vodka',
        'season': 'summer',
        'house_type': 'modern house',
        'dining_style': 'subtle tastes which advertise freshness',
        'music_preference': 'pop',
        'aroma_preference': 'floral',
        'bitterness_tolerance': 'medium',
        'sweetener_question': 'rich',
        'carbonation_texture': 'still & silky',
        'foam_toggle': 'no',
        'abv_lane': 'medium',
        'allergens': 'none',
        'seed': 804123533
    },
    # Recipe 3: Rebel - Spiced Rum Fizz (FOAM FAIL + ABV STRONG FAIL)
    {
        'name': 'Recipe 3: Spiced Rum Fizz FOAM+ABV',
        'bar_id': 'rebel',
        'base_spirit': 'rum',
        'season': 'autumn',
        'house_type': 'tree house',
        'dining_style': 'refreshing and vibrant flavours which awaken my senses',
        'music_preference': 'pop',
        'aroma_preference': 'woody',
        'bitterness_tolerance': 'low',
        'sweetener_question': 'rich',
        'carbonation_texture': 'properly sparkling',
        'foam_toggle': 'yes',
        'abv_lane': 'strong',
        'allergens': 'none',
        'seed': 784977427
    },
    # Recipe 4: Aviary - Rum Collins (FOAM FAIL + WOODY FAIL)
    {
        'name': 'Recipe 4: Rum Collins FOAM+WOODY',
        'bar_id': 'aviary',
        'base_spirit': 'rum',
        'season': 'autumn',
        'house_type': 'modern house',
        'dining_style': 'a balanced blend of flavours',
        'music_preference': 'rock',
        'aroma_preference': 'woody',
        'bitterness_tolerance': 'medium',
        'sweetener_question': 'zesty',
        'carbonation_texture': 'properly sparkling',
        'foam_toggle': 'yes',
        'abv_lane': 'medium',
        'allergens': 'none',
        'seed': 162873286
    },
    # Recipe 5: Rebel - Vodka Collins (WOODY FAIL)
    {
        'name': 'Recipe 5: Vodka Collins WOODY',
        'bar_id': 'rebel',
        'base_spirit': 'vodka',
        'season': 'summer',
        'house_type': 'beach house',
        'dining_style': 'a balanced blend of flavours',
        'music_preference': 'jazz/blues',
        'aroma_preference': 'woody',
        'bitterness_tolerance': 'medium',
        'sweetener_question': 'rich',
        'carbonation_texture': 'still & silky',
        'foam_toggle': 'no',
        'abv_lane': 'medium',
        'allergens': 'none',
        'seed': 661604045
    },
    # Recipe 6: Rebel - Gin Sour (FOAM FAIL + MVC FAIL)
    {
        'name': 'Recipe 6: Gin Sour FOAM+MVC',
        'bar_id': 'rebel',
        'base_spirit': 'gin',
        'season': 'summer',
        'house_type': 'beach house',
        'dining_style': 'refreshing and vibrant flavours which awaken my senses',
        'music_preference': 'pop',
        'aroma_preference': 'floral',
        'bitterness_tolerance': 'medium',
        'sweetener_question': 'zesty',
        'carbonation_texture': 'still & silky',
        'foam_toggle': 'yes',
        'abv_lane': 'medium',
        'allergens': 'none',
        'seed': 1833650512
    },
    # Recipe 11: Rebel - Rum Tropical (ABV STRONG + WOODY FAIL)
    {
        'name': 'Recipe 11: Rum Tropical ABV+WOODY',
        'bar_id': 'rebel',
        'base_spirit': 'rum',
        'season': 'summer',
        'house_type': 'beach house',
        'dining_style': 'refreshing and vibrant flavours which awaken my senses',
        'music_preference': 'rock',
        'aroma_preference': 'woody',
        'bitterness_tolerance': 'medium',
        'sweetener_question': 'classic',
        'carbonation_texture': 'properly sparkling',
        'foam_toggle': 'no',
        'abv_lane': 'strong',
        'allergens': 'none',
        'seed': 2074028513
    },
]


def score_recipe(payload, recipe):
    """Score a recipe 0-10 based on quality + bespoke match."""
    score = 10.0
    issues = []
    
    # Extract ingredients and method from CocktailRecipe dataclass
    ingredients = [f"{ing.amount_ml}ml {ing.ingredient.name}" for ing in recipe.ingredients]
    method = ' '.join(recipe.steps)
    
    # 1. Check foam contract (if foam=yes + fizzy → must shake ingredients, not just stir)
    if payload.get('foam_toggle') == 'yes':
        carbonation = payload.get('carbonation_texture', '')
        if 'fizzy' in carbonation or 'sparkling' in carbonation:
            # Check that we shake ingredients (not just stir them)
            if 'shake' not in method.lower():
                score -= 2.0
                issues.append("FOAM_CONTRACT_BROKEN: foam=yes + fizzy but no shake step")
            elif 'stir' in method.lower() and 'shake' not in method.lower().split('stir')[0]:
                # If we stir before shaking, that's build method (wrong)
                score -= 2.0
                issues.append("FOAM_CONTRACT_BROKEN: foam=yes + fizzy but using stir instead of shake")
    
    # 2. Check ABV lane (strong → 50-60ml total base spirits)
    if payload.get('abv_lane') == 'strong':
        # Calculate total base spirit amount (role='base')
        total_base_ml = sum(ing.amount_ml for ing in recipe.ingredients 
                           if ing.role.lower() == 'base')
        if total_base_ml < 50:
            score -= 2.0
            issues.append(f"ABV_STRONG_FAIL: got {total_base_ml:.0f}ml base (need 50-60ml)")
    
    # 3. Check woody aroma (should have woody ingredients, not candy)
    if payload.get('aroma_preference') == 'woody':
        woody_words = ['amaretto', 'cognac', 'spiced', 'cinnamon', 'walnut', 'oak', 'hazelnut', 'frangelico', 'maple']
        candy_words = ['peach', 'banana', 'bubblegum', 'coconut', 'passion fruit']
        
        ing_text = ' '.join(ingredients).lower()
        has_woody = any(w in ing_text for w in woody_words)
        has_candy = any(w in ing_text for w in candy_words)
        
        if not has_woody:
            score -= 1.5
            issues.append("WOODY_MISSING: no woody ingredients")
        if has_candy:
            score -= 1.5
            issues.append("WOODY_HAS_CANDY: contains candy fruits")
    
    # 4. Check citrus aroma
    if payload.get('aroma_preference') == 'citrus':
        citrus_words = ['lime', 'lemon', 'orange', 'grapefruit']
        candy_words = ['peach', 'banana', 'passion fruit']
        
        ing_text = ' '.join(ingredients).lower()
        has_citrus = any(w in ing_text for w in citrus_words)
        has_candy = any(w in ing_text for w in candy_words)
        
        if not has_citrus:
            score -= 1.0
            issues.append("CITRUS_MISSING: no citrus ingredients")
        if has_candy:
            score -= 1.0
            issues.append("CITRUS_HAS_CANDY: contains candy fruits instead")
    
    # 5. Check for peach overuse
    ing_text = ' '.join(ingredients).lower()
    if 'peach' in ing_text:
        # Peach should only be in summer + beach + sweet contexts
        if payload.get('season') != 'summer' or 'woody' in payload.get('aroma_preference', ''):
            score -= 1.0
            issues.append("PEACH_INAPPROPRIATE: peach in wrong context")
    
    # 6. Check ingredient redundancy
    if ing_text.count('peach') >= 2:
        score -= 1.0
        issues.append("INGREDIENT_REDUNDANCY: double peach")
    if ing_text.count('elderflower') >= 2:
        score -= 1.0
        issues.append("INGREDIENT_REDUNDANCY: double elderflower")
    
    # 7. Check minimum viable cocktail (at least 4 ingredients)
    if len(ingredients) < 4:
        score -= 2.0
        issues.append(f"MVC_FAIL: only {len(ingredients)} ingredients")
    
    return max(0, score), issues


def main():
    print("Testing Weekend Recipes...")
    print("=" * 80)
    
    total_score = 0
    total_count = 0
    all_issues = []
    
    for test in WEEKEND_RECIPES:
        name = test.pop('name')
        bar_id = test.pop('bar_id')
        print(f"\n{name}")
        print("-" * 80)
        
        try:
            result = generate_cocktail_recipe(test, bar_id=bar_id)
            score, issues = score_recipe(test, result)
            
            print(f"Score: {score:.1f}/10")
            if issues:
                for issue in issues:
                    print(f"  ❌ {issue}")
                    all_issues.append(issue)
            else:
                print("  ✅ No issues found")
            
            # Show ingredients
            print("\nIngredients:")
            for ing in result.ingredients:
                print(f"  • {ing.amount_ml}ml {ing.ingredient.name}")
            
            # Show method
            print("\nMethod:")
            for step in result.steps:
                print(f"  {step}")
            
            total_score += score
            total_count += 1
            
        except Exception as e:
            print(f"  ERROR: {e}")
            total_count += 1
    
    print("\n" + "=" * 80)
    print(f"OVERALL SCORE: {total_score/total_count:.1f}/10 (n={total_count})")
    print(f"\nIssue frequency:")
    from collections import Counter
    issue_types = [i.split(':')[0] for i in all_issues]
    for issue_type, count in Counter(issue_types).most_common():
        print(f"  {issue_type}: {count}")


if __name__ == '__main__':
    main()
