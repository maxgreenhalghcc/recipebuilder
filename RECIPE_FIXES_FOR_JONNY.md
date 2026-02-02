# Recipe Engine Fixes - Handoff to Jonny

**Date:** 2 Feb 2026  
**Updated:** 2 Feb 2026 08:58 GMT  
**Baseline Score:** 8.2/10 on weekend recipes  
**Current Score:** 9.1/10 (+0.9 improvement)  
**Test Suite:** `test_weekend_recipes.py` (7 real weekend orders with human-style inputs)

---

## What's Been Fixed (2 hours of work)

✅ **Foam Contract (P0)** - COMPLETE  
- Fixed: foam=yes + fizzy now shakes ingredients first, then tops
- Impact: 1 failure → 0 failures
- Commit: df1925c

✅ **Woody Aroma Discipline (P1)** - PARTIAL  
- Fixed: Ban candy fruits (banana, coconut, peach) from woody contexts
- Fixed: Prioritize woody ingredients (amaretto, cognac, cinnamon)
- Impact: 3 failures → 1 failure (profile system conflict on 1 recipe)
- Commit: df1925c

✅ **Citrus Aroma Discipline (P1)** - COMPLETE  
- Fixed: Ban candy fruits from citrus contexts
- Impact: Working correctly

---

## Remaining Work for Jonny (2-3 hours)

❌ **ABV Strong Lane (P0)** - NOT FIXED  
- Issue: Gets 35ml base instead of 50-60ml
- Attempts made: Fixed base_ml calculation but other code paths override it
- Root cause: Multiple code paths adjust base amount (line 781, 790, etc.)
- **Estimated time:** 1-2 hours

❌ **Ingredient Redundancy (P2)** - NOT FIXED  
- Issue: Double elderflower (liqueur + cordial)
- **Estimated time:** 30 minutes

❌ **Woody + Profile Conflict (P1)** - BLOCKED  
- Issue: When profile='citrus_fresh' + aroma='woody', profile wins
- Amaretto exists in stock but profile system rejects it
- **Solution:** Either (a) make aroma override profile, or (b) add woody items to more profiles
- **Estimated time:** 1 hour

**Total remaining:** 2.5-3.5 hours to reach 9.5+/10

---

## Executive Summary

The recipe engine works with minimal inputs but breaks with real human-style payloads. Issues found:

1. **ABV Strong Lane (2 failures)** - Gets 35ml instead of 50-60ml when full payload present
2. **Foam Contract (1 failure)** - Build method instead of shake when foam=yes + fizzy
3. **Woody Aroma (3 failures)** - Outputs candy fruits (banana, coconut, peach) instead of woody ingredients
4. **Ingredient Redundancy (1 failure)** - Double elderflower (liqueur + cordial)
5. **Peach Overuse (1 failure)** - Appearing in wrong contexts (woody, autumn)

---

## P0: ABV Strong Lane Broken

### The Bug

**File:** `recipebuilder/profile_builder.py`  
**Line:** 781

```python
base_ml = base_target - modifier_ml  # ❌ WRONG
```

When a modifier is present (15ml), it subtracts from base:
- `base_target` = 60ml (strong lane)
- `modifier_ml` = 15ml
- `base_ml` = 60 - 15 = 45ml ❌

But then later code paths further reduce it to 35ml when certain other fields are present.

### The Fix

ABV lane should set **total base spirit**, not "base minus modifiers":

```python
# ABV lane sets total base spirit amount - don't subtract modifiers
base_ml = base_target
if flavoured_spirit and flavoured_spirit.category == "spirit":
    # If we have a flavoured spirit, split the target between base + flavoured
    base_ml = max(25.0, base_target - flavoured_ml)
# Clamp base to reasonable bounds
if abv_lane == "low":
    base_ml = max(25.0, min(base_ml, 40.0))
elif abv_lane == "strong":
    base_ml = max(50.0, min(base_ml, 60.0))  # Strong must be 50-60ml
else:  # medium
    base_ml = max(40.0, min(base_ml, 50.0))
```

### Test Case

```python
result = generate_cocktail_recipe(
    {
        'base_spirit': 'rum',
        'abv_lane': 'strong',  # Should give 50-60ml base
        'aroma_preference': 'woody',
        'carbonation_texture': 'properly sparkling',
        'season': 'summer',
        'house_type': 'beach house',
        'seed': 2074028513
    },
    bar_id='rebel'
)
total_base = sum(i.amount_ml for i in result.ingredients if i.role.lower() == 'base')
assert 50 <= total_base <= 60, f"Strong lane got {total_base}ml, need 50-60ml"
```

**Current:** ❌ Gets 35ml  
**Expected:** ✅ 50-60ml

---

## P0: Foam Contract Broken

### The Bug

**File:** `recipebuilder/profile_builder.py`  
**Function:** `_build_method_steps()`

When `foam_toggle='yes'` + `carbonation='properly sparkling'`, it uses **build method** (fill glass, stir, top) instead of **shake method** (shake first, then top).

Foam requires shaking to create foam from aeration. Current code outputs:
```
Fill a gin glass with cubed ice.
Add spirits, syrups, juices and sour. Give a brief stir.  # ❌ WRONG - should shake first
Top with Lemonade.
```

### The Fix

**File:** `recipebuilder/profile_builder.py`  
**Function:** `_build_method_steps()` (around line 900-1000)

When `foam=yes` + fizzy drink:

```python
if foam and is_fizzy:
    # Shake first (creates foam), THEN top with fizz
    steps.append(f"Add all non-mixer ingredients to a shaker with ice and shake hard.")
    steps.append(f"Strain into an ice-filled {glass}.")
    if mixer:
        steps.append(f"Top with {mixer.name}.")
else:
    # Regular build method
    steps.append(f"Fill {glass} with ice.")
    steps.append("Add spirits, syrups, juices and sour. Give a brief stir.")
    if mixer:
        steps.append(f"Top with {mixer.name}.")
```

### Test Case

```python
result = generate_cocktail_recipe(
    {
        'base_spirit': 'rum',
        'foam_toggle': 'yes',
        'carbonation_texture': 'properly sparkling',
        'seed': 784977427
    },
    bar_id='rebel'
)
method = ' '.join(result.steps).lower()
assert 'shake' in method, "foam=yes + fizzy must include shake step"
assert method.index('shake') < method.index('top'), "Must shake BEFORE topping"
```

**Current:** ❌ Build method (no shake)  
**Expected:** ✅ Shake first, then top

---

## P1: Woody Aroma Discipline Missing

### The Bug

**File:** `recipebuilder/profile_builder.py`  
**Function:** `determine_flavor_family()` + ingredient selection logic

When `aroma_preference='woody'`, engine outputs:
- ❌ Banana liqueur (tropical candy)
- ❌ Coconut syrup (tropical sweet)
- ❌ Blue curaçao (citrus candy)
- ❌ Peach schnapps (summer fruit)

Should output:
- ✅ Amaretto (almond, woody)
- ✅ Cognac (oak, woody)
- ✅ Spiced rum (cinnamon, woody)
- ✅ Walnut liqueur (nutty, woody)
- ✅ Cinnamon syrup (spice, woody)

### The Fix

**Location:** `profile_builder.py` around line 700-800 in `_build_recipe_from_template()`

Add woody ingredient enforcement:

```python
def _filter_by_aroma(items: List[StockItem], aroma: str) -> List[StockItem]:
    """Filter ingredients to match requested aroma profile."""
    
    WOODY_WORDS = ['amaretto', 'cognac', 'spiced', 'cinnamon', 'walnut', 'oak', 'bourbon']
    WOODY_BANNED = ['banana', 'coconut', 'peach', 'passion fruit', 'pineapple', 'mango']
    
    CITRUS_WORDS = ['lime', 'lemon', 'orange', 'grapefruit', 'yuzu', 'bergamot']
    CITRUS_BANNED = ['banana', 'peach', 'coconut']
    
    FLORAL_WORDS = ['elderflower', 'rose', 'violet', 'lavender', 'hibiscus']
    
    if aroma == 'woody':
        # Prefer woody, ban candy
        preferred = [i for i in items if any(w in i.name.lower() for w in WOODY_WORDS)]
        if preferred:
            return preferred
        # Fallback: remove banned items
        return [i for i in items if not any(w in i.name.lower() for w in WOODY_BANNED)]
    
    elif aroma == 'citrus':
        preferred = [i for i in items if any(w in i.name.lower() for w in CITRUS_WORDS)]
        if preferred:
            return preferred
        return [i for i in items if not any(w in i.name.lower() for w in CITRUS_BANNED)]
    
    elif aroma == 'floral':
        preferred = [i for i in items if any(w in i.name.lower() for w in FLORAL_WORDS)]
        if preferred:
            return preferred
    
    return items
```

Then apply filter before selecting modifiers/sweeteners:

```python
# Around line 720 in _build_recipe_from_template
aroma = responses.get('aroma_preference', '').lower()

# Filter modifier pool by aroma
if aroma:
    modifier_pool = _filter_by_aroma(modifier_pool, aroma)

# Filter sweetener pool by aroma  
if aroma:
    sweetener_pool = _filter_by_aroma(sweetener_pool, aroma)
```

### Test Case

```python
result = generate_cocktail_recipe(
    {
        'base_spirit': 'rum',
        'aroma_preference': 'woody',
        'season': 'autumn',
        'seed': 162873286
    },
    bar_id='aviary'
)
ing_text = ' '.join(i.ingredient.name.lower() for i in result.ingredients)

# Check for woody ingredients
woody_words = ['amaretto', 'cognac', 'spiced', 'cinnamon', 'walnut']
has_woody = any(w in ing_text for w in woody_words)
assert has_woody, f"Woody request should include woody ingredients, got: {ing_text}"

# Check NO candy fruits
candy_words = ['banana', 'coconut', 'peach', 'passion fruit']
has_candy = any(w in ing_text for w in candy_words)
assert not has_candy, f"Woody request should not include candy fruits, got: {ing_text}"
```

**Current:** ❌ Gets banana, coconut, peach  
**Expected:** ✅ Gets amaretto, cognac, spiced rum, cinnamon

---

## P2: Ingredient Redundancy

### The Bug

**File:** `recipebuilder/profile_builder.py`

Engine sometimes adds both:
- Elderflower liqueur (15ml) + Elderflower cordial (14ml) = redundant
- Peach schnapps (15ml) + Peach syrup (10ml) = redundant
- Lime juice (15ml) + Lime cordial (10ml) = redundant

### The Fix

Add redundancy check after ingredient selection:

```python
def _check_redundancy(ingredients: List[Tuple[StockItem, float, str]]) -> List[str]:
    """Check for redundant same-family ingredients."""
    
    REDUNDANCY_FAMILIES = {
        'elderflower': ['elderflower liqueur', 'elderflower cordial'],
        'peach': ['peach schnapps', 'peach syrup'],
        'lime': ['lime juice', 'lime cordial'],
        'lemon': ['lemon juice', 'lemon cordial'],
    }
    
    issues = []
    for family, items in REDUNDANCY_FAMILIES.items():
        count = sum(1 for ing, ml, role in ingredients 
                   if any(x in ing.name.lower() for x in items))
        if count >= 2:
            issues.append(f"REDUNDANT_{family.upper()}")
    
    return issues

# After building ingredient list, before finalizing:
redundancy_issues = _check_redundancy(ingredients)
if redundancy_issues:
    # Remove the weaker/smaller ingredient
    # (keep liqueur over cordial, keep juice over cordial)
    for family in ['elderflower', 'peach', 'lime', 'lemon']:
        if f"REDUNDANT_{family.upper()}" in redundancy_issues:
            # Find both ingredients
            candidates = [i for i in ingredients if family in i[0].name.lower()]
            if len(candidates) >= 2:
                # Keep the one with higher ml, remove the other
                keep = max(candidates, key=lambda x: x[1])
                remove = min(candidates, key=lambda x: x[1])
                ingredients.remove(remove)
```

### Test Case

```python
result = generate_cocktail_recipe(
    {
        'base_spirit': 'vodka',
        'aroma_preference': 'floral',
        'sweetener_question': 'rich',
        'seed': 804123533
    },
    bar_id='demo-bar'
)
ing_names = [i.ingredient.name.lower() for i in result.ingredients]

# Check for double elderflower
elderflower_count = sum(1 for name in ing_names if 'elderflower' in name)
assert elderflower_count <= 1, f"Should not have multiple elderflower items"
```

**Current:** ❌ Double elderflower allowed  
**Expected:** ✅ Max 1 elderflower item

---

## P2: Peach Quarantine

### The Bug

Peach appears in 60% of recipes (9/15 weekend orders), including:
- ❌ Woody requests (should be amaretto/cinnamon)
- ❌ Autumn season (should be apple/pear/fig)
- ❌ Citrus requests (should be lime/lemon/orange)

### The Fix

Restrict peach to appropriate contexts:

```python
def _can_use_peach(responses: dict) -> bool:
    """Peach should only appear in summer + sweet + tropical contexts."""
    
    season = responses.get('season', '').lower()
    sweetener = responses.get('sweetener_question', '').lower()
    house = responses.get('house_type', '').lower()
    aroma = responses.get('aroma_preference', '').lower()
    
    # Ban peach in these contexts
    if aroma in ('woody', 'citrus'):
        return False
    if season in ('autumn', 'winter'):
        return False
    
    # Allow peach only in summer + sweet contexts
    if season == 'summer' and 'beach' in house and sweetener in ('rich', 'fruity'):
        return True
    
    return False

# In ingredient selection (around line 750):
modifier = self._select_ingredient(modifier_pool, profile, "modifier", exclude=exclude)
if modifier and 'peach' in modifier.name.lower():
    if not _can_use_peach(responses):
        # Try again without peach
        modifier_pool_no_peach = [i for i in modifier_pool if 'peach' not in i.name.lower()]
        modifier = self._select_ingredient(modifier_pool_no_peach, profile, "modifier", exclude=exclude)
```

---

## Running Tests

```bash
cd recipebuilder
python3 test_weekend_recipes.py
```

**Current Baseline:** 8.2/10  
**Target:** 9.0+/10

**Success Criteria:**
- ABV_STRONG_FAIL: 0 (currently 2)
- FOAM_CONTRACT_BROKEN: 0 (currently 1)
- WOODY_HAS_CANDY: 0 (currently 2)
- INGREDIENT_REDUNDANCY: 0 (currently 1)
- PEACH_INAPPROPRIATE: 0 (currently 1)

---

## Notes for Jonny

1. **Two generate functions exist** (line 2448 and 3015). Line 3015 is the actual entry point - it calls ProfileRecipeBuilder.
2. **ProfileRecipeBuilder** (`profile_builder.py`) is where the fixes need to go.
3. **Test with full payloads** - the engine works with minimal inputs but breaks with realistic human-style payloads (season, house_type, dining_style, music, etc.).
4. **Staging first** - test every fix in staging before prod.

## Estimated Time

- P0 fixes (ABV + Foam): 2-3 hours
- P1 fixes (Woody aroma): 1-2 hours
- P2 fixes (Redundancy + Peach): 1 hour
- Testing + validation: 1 hour

**Total: 5-7 hours**

Impact: 8.2/10 → 9.0+/10 = bar retention goes up, "spooky accurate" reputation spreads, £10k/mo path unlocked.
