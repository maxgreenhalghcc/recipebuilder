# Perfect Recipe Map — Getting to 10/10 "Woah That's Good" Cocktails

**Date:** 2 Feb 2026  
**Current Score:** 6-7/10 (reliable but generic)  
**Target:** 10/10 (bartenders want to steal the recipe)  

---

## Current State Assessment

### ✅ What's Working (Technical Excellence)
- **Reliability:** 10/10 — no weird outputs, correct ABV, stock-aware
- **Bartender readability:** Specs are clear and buildable
- **No redundancy:** Fixed double-ingredient bugs
- **Constraint respect:** Aroma preferences, ABV lanes, profile matching all work

### ⚠️ The Gap (Taste & Magic)
- **Ingredients are safe, not inspired** — Heavy grenadine/simple syrup use
- **Balance is rule-based, not craft-tuned** — Mathematically sound but lacks bartender intuition
- **Missing complexity layers** — Most are 4-5 ingredients, missing the "secret weapon"
- **No soul** — Reads like "cocktail by algorithm" because it is
- **Generic across bars** — Every bar makes similar drinks, no signature character

**Bottom line:** Bars will USE it (it works), but won't LOVE it yet. It's a "solid backup" not a "signature serve."

---

## The Path to 10/10 (Within Our Control)

### Phase 1: Technical Reliability ✅ DONE
- Correct ABV (low/medium/strong)
- No ingredient redundancy
- Profile + aroma respect
- Bartender-readable specs

### Phase 2: Taste Intelligence 🎯 CURRENT FOCUS
Get drinks from "functional" to "memorable"

### Phase 3: Signature Character 🚀 NEXT
Each bar has its own flavor identity

---

## 4-Week Action Plan

### **Week 1: Taste Test & Data Capture**

**Goal:** Build the feedback dataset

**Task:**
1. Generate 20 diverse cocktails (different spirits, profiles, bars)
2. Make 10 of them (select randomly for unbiased sample)
3. Score brutally honest:
   - Appearance (1-10)
   - Aroma (1-10)
   - First Sip (1-10)
   - Finish (1-10)
   - **Would I order again?** (YES/NO)
   - **Would I pay £12 for this?** (YES/NO)

4. Document patterns:
   - "All vodka drinks taste the same"
   - "Grenadine makes everything taste like a kid's drink"
   - "The gin drinks are actually good — why?"
   - "Too much juice, not enough spirit character"
   - "Missing complexity — where's the twist?"

**Output:** Scoring sheet + pattern document

**Script to generate test batch:**
```bash
cd recipebuilder
python3 scripts/generate_taste_test_batch.py --count 20 --bars rebel,aviary,level_256
```

---

### **Week 2: Engine Upgrades (Based on Feedback)**

#### **1. Ban Boring Defaults**

Current problem: Grenadine + Simple Syrup make everything taste generic

**Fix:**
```python
# Add to profile_builder.py _choose_sweet_components()

BORING_SWEETENERS = ['grenadine', 'simple syrup']
PREFERRED_SWEETENERS = ['honey syrup', 'agave syrup', 'maple syrup', 'demerara syrup', 'vanilla syrup']

# When selecting sweetener:
# 1. Try preferred sweeteners first
# 2. Only fall back to boring if no alternatives exist
# 3. Weight: 70% preferred, 30% boring (keep some variety)
```

**Expected impact:** +1.0 point (removes "kid's drink" taste)

---

#### **2. Add "Signature Move" Layer**

Current problem: Drinks lack the "what IS that?" factor

**Fix:** After recipe is built, randomly apply ONE upgrade (20% chance):

```python
SIGNATURE_MOVES = {
    'citrus_drinks': [
        'Express lemon peel over drink',
        'Salt rim',
        'Sugar rim with orange zest',
    ],
    'spirit_forward': [
        '2 dashes Angostura bitters',
        '2 dashes orange bitters',
        'Express orange peel',
    ],
    'tropical': [
        'Muddle fresh mint',
        'Muddle fresh basil',
        'Float dark rum (5ml)',
    ],
    'woody': [
        '2 dashes chocolate bitters',
        'Express orange peel',
        'Smoke glass with cinnamon stick',
    ],
}

# Apply AFTER main recipe is built
# Cost: ZERO extra ingredients (techniques, not stock)
# Bartender instruction: Add to method steps
```

**Expected impact:** +1.5 points (adds complexity without complexity)

---

#### **3. Ratio Tuning**

Current problem: Too much juice (45ml), drowns out spirit character

**Fix:**
```python
# Current juice range
juice_ml_range = (30, 45)  # ❌ TOO HIGH

# New juice range
juice_ml_range = (20, 35)  # ✅ Better balance

# Increase modifier use when juice is reduced
# Add 5ml more modifier if juice < 30ml
```

**Expected impact:** +0.5 points (more spirit character)

---

#### **4. Ingredient "Magic Pairs"**

Current problem: Ingredients are compatible but not synergistic

**Fix:** Add small lookup of proven combinations:

```python
MAGIC_PAIRS = {
    'amaretto': ['lemon juice', 'bourbon', 'cherry'],  # Amaretto Sour vibes
    'elderflower': ['gin', 'cucumber', 'prosecco', 'grapefruit'],
    'ginger': ['whisky', 'lemon', 'honey'],
    'basil': ['strawberry', 'gin', 'lemon'],
    'coconut': ['pineapple', 'rum', 'lime'],
    'peach': ['bourbon', 'lemon', 'mint'],
    'spiced rum': ['ginger', 'lime', 'pineapple'],
    'campari': ['grapefruit', 'orange', 'prosecco'],
}

# When one ingredient is selected, boost probability of its pairs by 30%
# Don't force — just nudge the engine toward known-good combos
```

**Expected impact:** +1.0 point (moves from "compatible" to "magical")

---

### **Week 3: Bar-Specific Character**

Current problem: Every bar makes similar drinks — no signature identity

**Fix:** Each bar has 2-3 "hero ingredients" they're known for

```python
BAR_SIGNATURES = {
    'aviary': {
        'hero_ingredients': ['frangelico', 'amaretto', 'disaronno'],
        'vibe': 'nutty/woody sophistication',
        'boost_probability': 40,  # 40% more likely to select these
    },
    'rebel': {
        'hero_ingredients': ['spiced rum', 'passion fruit', 'malibu'],
        'vibe': 'tropical party energy',
        'boost_probability': 40,
    },
    'level_256': {
        'hero_ingredients': ['tequila', 'mezcal', 'jalapeño'],
        'vibe': 'agave-forward boldness',
        'boost_probability': 40,
    },
}

# When building recipe for a bar:
# 1. Try to include at least 1 hero ingredient
# 2. Boost selection probability
# 3. Use bar vibe to inform garnish/glass choices
```

**Expected impact:** +1.0 point (bar identity = memorability)

**Deployment note:** Requires bar owners to define their "signature ingredients" (5-min conversation per bar)

---

### **Week 4: Complexity Boost (Optional 6th Ingredient)**

Current problem: 4-5 ingredients = functional but flat

**Fix:** Add optional 6th ingredient when it has PURPOSE

```python
# After main recipe is built, evaluate:
# - Does this drink need more aroma? → Add bitters/herb
# - Does this need more depth? → Add modifier layer
# - Does this need more texture? → Add egg white/aquafaba

COMPLEXITY_RULES = {
    'spirit_forward_no_citrus': 'Add 2 dashes Angostura bitters',
    'citrus_heavy': 'Add muddled basil or mint',
    'tropical_sweet': 'Add 5ml lime juice + salt rim',
    'dessert_profile': 'Add vanilla extract (2 drops)',
}

# Only add if:
# 1. It solves a balance problem
# 2. It's available in bar stock
# 3. It's simple for bartender (no weird prep)
```

**Expected impact:** +0.5 points (elevates good drinks to great)

---

## Measurement Framework

### Target Metric: "Would I Pay £12 for This?"

After each round of changes:
1. Generate 10 new drinks
2. Make 3 random ones (unbiased sample)
3. Score: **"Would I pay £12 for this?"** (YES/NO)

**Success criteria:**
- **6/10 = Current state** (functional, not special)
- **8/10 = Deployment ready** (good enough for £10k/month)
- **9/10 = Retention driver** (bars brag about it)
- **10/10 = Perfect** (bartenders steal recipes for their own menus)

---

## Key Insights

### What Makes a Cocktail "Woah That's Good"?
1. **Balance** — No single flavor dominates
2. **Complexity** — Layers reveal themselves (first sip ≠ finish)
3. **Surprise** — One unexpected element ("what IS that?")
4. **Craft** — Feels intentional, not algorithmic
5. **Story** — You can explain why these ingredients work together

### What We Control vs Don't Control
**✅ We control:**
- Ingredient selection intelligence
- Ratio tuning
- Signature moves (techniques)
- Bar identity amplification
- Complexity layers

**❌ We don't control:**
- Bar stock quality (cheap vodka = cheap drinks)
- Bartender execution (rushed build = worse drink)
- Guest palate (some people just want vodka Red Bull)

**Focus on what we control.** Make the best recipe possible given stock constraints.

---

## Current Blockers

### 1. No Real-World Feedback Loop Yet
- We're optimizing for test suite scores, not human taste
- Need 20-30 bartender-scored drinks to calibrate

### 2. Sweetener Overuse
- Grenadine/simple syrup are crutches
- Engine defaults to safe/sweet when unsure

### 3. Generic Flavor Profiles
- "Citrus fresh" tastes the same at every bar
- Need bar-specific character

---

## Next Actions (This Week)

1. **Create taste test generation script** → `scripts/generate_taste_test_batch.py`
2. **Generate 20 drinks** → diverse bars, spirits, profiles
3. **Make 10 drinks** → score honestly
4. **Document patterns** → "All vodka drinks taste X" findings
5. **Prioritize fixes** → rank by impact vs effort

**Time commitment:** 2-3 hours (worth it for the data)

---

## Long-Term Vision (Beyond 10/10)

Once drinks are consistently great:
- **Seasonal menus** — Spring/Summer/Autumn/Winter signature serves
- **Guest history** — "You loved the last one, try this similar drink"
- **Bartender collaboration** — "Suggest your own twist on this recipe"
- **Limited editions** — "Only available this month"

But first: **Make drinks people want to order again.**

---

## Success = When Bartenders Say This

*"I made one for myself after shift. It's actually really good."*

That's the benchmark. When bartenders drink CC cocktails off the clock, we've won.

---

**Last updated:** 2 Feb 2026  
**Next review:** After Week 1 taste tests  
