# Recipe Improvement Notes

Quick reference for ongoing recipe quality work.

---

## Current State (2 Feb 2026)

### Engine Score: 10.0/10 (Technical)
- All test cases pass
- ABV lanes work correctly (low/medium/strong)
- No ingredient redundancy
- Aroma preferences respected
- Profile constraints working

### Drink Score: 6-7/10 (Taste)
- Reliable but generic
- Bartender-buildable
- Stock-aware
- Missing "magic" factor

---

## The 3 Phases of Recipe Quality

### Phase 1: Don't Break ✅ DONE
- Correct ABV
- No weird outputs
- Stock compatibility
- Bartender readability

### Phase 2: Taste Good 🎯 CURRENT
- Ingredient intelligence
- Ratio tuning
- Complexity layers
- Signature moves

### Phase 3: Be Memorable 🚀 NEXT
- Bar identity
- Unexpected twists
- Craft storytelling
- "Steal-worthy" recipes

---

## Quick Wins (High Impact, Low Effort)

### 1. Ban Grenadine (Unless Specifically Requested)
- Makes everything taste like a kid's drink
- Replace with: honey, agave, maple, demerara
- **Impact:** +1.0 point
- **Effort:** 10 lines of code

### 2. Reduce Juice Amounts
- Current: 30-45ml (too much)
- Target: 20-35ml (better balance)
- **Impact:** +0.5 point
- **Effort:** Change one constant

### 3. Add Bitters Layer (20% of drinks)
- Costs nothing (2-3 dashes)
- Adds complexity instantly
- **Impact:** +1.0 point
- **Effort:** 20 lines of code

---

## Common Patterns from Testing

### Vodka Drinks
- **Problem:** All taste the same (neutral spirit = relies on mixers)
- **Fix:** Increase modifier use (15ml → 20ml), add herb garnish
- **Target:** Make vodka drinks interesting without making them "not vodka"

### Gin Drinks
- **Status:** Actually pretty good already
- **Why:** Gin has character, botanicals work with citrus
- **Opportunity:** Lean into this — make gin our showcase

### Rum Drinks
- **Problem:** Over-reliant on tropical clichés (pineapple/coconut/passion fruit)
- **Fix:** Explore spiced rum + woody ingredients (cinnamon, maple, ginger)
- **Opportunity:** Rum + autumn vibes = unexplored territory

### Tequila Drinks
- **Status:** Limited bar stock (only level_256 has good tequila)
- **Opportunity:** When tequila is available, go bold (jalapeño, mezcal smoke, lime)

---

## Ingredient Intelligence

### Boring Ingredients (Overused)
- Grenadine
- Simple syrup
- Orange juice (when it's the only juice)
- Lemonade as default mixer

### Magic Ingredients (Underused)
- Honey syrup
- Maple syrup
- Ginger (fresh or syrup)
- Basil/rosemary (fresh herbs)
- Bitters (any kind)
- Salt/sugar rims

### Hero Ingredients (Bar Signatures)
- **Aviary:** Frangelico, Amaretto, Disaronno
- **Rebel:** Spiced rum, Malibu, tropical fruits
- **Level 256:** Tequila, mezcal
- **Demo Bar:** (To be defined)

---

## Ratio Guidelines (From Bartender Feedback)

### Spirit-Forward (Martini/Manhattan Style)
- 50-60ml spirit
- 10-15ml modifier
- 0-10ml sweetener
- No juice or minimal (10ml max)

### Sours (Daiquiri/Margarita Style)
- 50ml spirit
- 20-25ml citrus juice
- 10-15ml sweetener
- Optional: 10ml modifier

### Highballs (Collins/Mojito Style)
- 50ml spirit
- 20-30ml juice
- 10ml sweetener
- Top with mixer (soda/tonic)
- Fresh herbs recommended

### Tropical (Piña Colada/Mai Tai Style)
- 40-50ml spirit (can split 25+25 for complexity)
- 40-50ml juice (mix of 2-3)
- 15ml sweetener
- 10-15ml modifier
- Top with minimal mixer

---

## Signature Moves Library

### Citrus Drinks
- Express lemon/orange peel over drink
- Salt rim (savory twist)
- Sugar rim with orange zest
- Add 2 dashes orange bitters

### Spirit-Forward
- 2 dashes Angostura bitters
- Express orange peel
- Stir (don't shake) for clarity
- Large ice cube (slow dilution)

### Tropical
- Muddle fresh mint
- Muddle fresh basil
- Float dark rum (5ml on top)
- Burnt cinnamon stick garnish

### Woody/Autumn
- 2 dashes chocolate bitters
- Smoke glass with cinnamon
- Maple syrup instead of simple
- Expressed orange peel

### Refreshing/Fizz
- Add cucumber slice
- Muddle mint
- Add elderflower
- Salt rim (unexpected)

---

## Testing Protocol

### Before Making Changes
1. Generate 5 drinks with current engine
2. Note common patterns/issues
3. Make hypothesis about fix

### After Making Changes
1. Generate 5 drinks with new engine
2. Make 2-3 of them
3. Score: Better/Same/Worse?
4. Commit if better, revert if worse

### Quality Gates
- **Functional:** Builds without error, stock-aware
- **Readable:** Bartender can follow spec
- **Balanced:** No single flavor dominates
- **Interesting:** Has at least one "that's cool" element
- **Orderable:** Would I pay £12 for this?

---

## Red Flags (Auto-Fail Drinks)

- More than 50ml juice (drowns spirit)
- More than 20ml sweetener (too sugary)
- Redundant ingredients (double elderflower)
- No spirit character (tastes like juice)
- Boring garnish only ("orange slice" for everything)
- No complexity (4 ingredients, all obvious)

---

## Golden Rules

1. **Respect the spirit** — Every drink should taste like its base spirit
2. **Balance, not bland** — Balanced ≠ boring
3. **One surprise** — Not everything, just one unexpected thing
4. **Bartender-first** — If it's annoying to make, they won't make it
5. **Guest-second** — If it tastes algorithmic, they won't order again

---

## Next Experiments to Try

### A. Sweetener Downgrade
- Reduce all sweetener amounts by 20%
- Hypothesis: Less sugar = more spirit character
- Test: 10 drinks, compare to current

### B. Modifier Upgrade
- Increase modifier use by 5ml
- Hypothesis: More complexity without more sugar
- Test: 10 drinks, focus on vodka (needs help)

### C. Bitters Layer
- Add bitters to 50% of drinks (random)
- Hypothesis: Instant complexity boost
- Test: Same recipe with/without bitters, blind taste

### D. Herb Garnish
- Force fresh herb garnish on 30% of drinks
- Hypothesis: Aroma elevates perception
- Test: Same drink, garnish vs no garnish

---

## Feedback Collection Template

```markdown
## Drink Test: [Name/Date]

**Recipe:**
- [ingredients list]

**Appearance:** [1-10]
**Aroma:** [1-10]
**First Sip:** [1-10]
**Finish:** [1-10]
**Balance:** [1-10]

**Would order again?** [YES/NO]
**Would pay £12?** [YES/NO]

**What worked:**
- [list]

**What didn't:**
- [list]

**Suggested fix:**
- [specific change]
```

---

## Success Metrics (Weekly Check-in)

### Week 1 Target
- 20 drinks generated
- 10 drinks made
- Patterns documented
- 3 fixes prioritized

### Week 2 Target
- 3 fixes implemented
- 10 new drinks tested
- Score improvement: +1.5 points

### Week 3 Target
- Bar signatures added
- Magic pairs working
- Score improvement: +2.5 points total

### Week 4 Target
- Complexity layer added
- 8/10 "would pay £12" rate
- Deploy to production

---

## Long-Term Quality Roadmap

### Q1 2026 (Now)
- Technical reliability ✅
- Taste intelligence 🎯

### Q2 2026
- Bar signature identity
- Seasonal menu generation
- Bartender feedback loop

### Q3 2026
- Guest preference learning
- Advanced complexity layers
- Regional ingredient discovery

### Q4 2026
- Multi-bar portfolio menus
- Limited edition specials
- Competition-worthy cocktails

---

**Last updated:** 2 Feb 2026
