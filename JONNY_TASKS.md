# Jonny — Recipe Engine P0 Tasks (Max Request)

**From:** Max via Maximus  
**Date:** 2 Feb 2026 09:01  
**Priority:** P0 (needed for £10k/mo path)  
**Current Score:** 9.1/10 (target: 9.5+/10)

---

## Your Task List (2.5-3.5 hours total)

### 1. ABV Strong Lane Fix (P0 - 1-2 hours)

**Problem:** Strong ABV requests get 35ml base instead of 50-60ml

**Root cause:** Line 781 in `profile_builder.py`:
```python
base_ml = base_target - modifier_ml  # ❌ WRONG
```

**Fix:** Don't subtract modifier from base target
```python
base_ml = base_target  # ✅ ABV lane sets total base spirit
```

**Test:**
```bash
cd recipebuilder
python3 test_weekend_recipes.py
# Check Recipe 3 and Recipe 11 (both should have 50-60ml base for strong lane)
```

**Files:** `recipebuilder/profile_builder.py` line 781-790

---

### 2. Ingredient Redundancy (P2 - 30 minutes)

**Problem:** Double elderflower (liqueur + cordial in same recipe)

**Fix:** Add redundancy check after ingredient selection:
```python
REDUNDANCY_FAMILIES = {
    'elderflower': ['elderflower liqueur', 'elderflower cordial'],
    'peach': ['peach schnapps', 'peach syrup'],
    'lime': ['lime juice', 'lime cordial'],
}

# After building ingredient list, check for doubles and remove weaker one
```

**Test:** Recipe 2 should not have both elderflower items

**Files:** `recipebuilder/profile_builder.py` (add check in `_build_recipe_from_template`)

---

### 3. Woody + Profile Conflict (P1 - 1 hour)

**Problem:** When profile='citrus_fresh' + aroma='woody', profile wins (rejects amaretto)

**Options:**
- **A)** Make aroma override profile (expand pool to all stock when aroma set)
- **B)** Add woody items (amaretto, cognac) to citrus_fresh profile data

**Recommendation:** Option B is safer (less breaking changes)

**Test:** Recipe 4 should select amaretto when aroma='woody' even with citrus profile

**Files:** Either `profile_builder.py` (option A) or `data/profiles/*.json` (option B)

---

## How to Run Tests

```bash
cd /home/openclaw/.openclaw/workspace/recipebuilder

# Clear cache (important!)
find . -type d -name __pycache__ -exec rm -rf {} +

# Run test suite
python3 test_weekend_recipes.py

# Target: 9.5+/10 average
# Current: 9.1/10
```

---

## Success Criteria

- ✅ ABV_STRONG_FAIL: 0 (currently 2)
- ✅ INGREDIENT_REDUNDANCY: 0 (currently 1)
- ✅ WOODY_MISSING: 0 (currently 1)
- ✅ Overall score: 9.5+/10

---

## Full Context

See `RECIPE_FIXES_FOR_JONNY.md` for:
- Complete bug analysis
- Code examples
- Test cases
- Architecture notes

**Branch:** feat/iterate-recipes-to-9  
**Test suite:** `test_weekend_recipes.py` (7 real weekend orders)

---

## Questions?

Ping Max or check the detailed doc. All fixes have exact file locations + line numbers.

**This is the £10k/mo lever — recipe quality = bar retention.**
