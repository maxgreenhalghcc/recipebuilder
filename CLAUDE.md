## North Star
Generate cocktails that are:
1) **Objectively good** (balanced, buildable)
2) **Spooky accurate** to quiz answers
3) **Service-ready** for real bars (correct glass/volume/ingredients)

## Hard acceptance criteria (MUST)
### A) Minimum viable cocktail (MVC)
Never return a cocktail with fewer than **4 ingredients** (excluding garnish).
Target **5–7** ingredients typically.

Required roles (must be present):
- **base spirit**
- **sweetener**
- **sour**
- plus at least one of:
  - **modifier** (liqueur/amaro/fortified wine)
  - **juice**
  - **mixer/top** (carbonation-dependent)

If any role is missing, **add a sensible fallback ingredient from bar stock** that matches flavour/profile/season.

### B) Volume & glass match
- If glass is **gin glass / long glass / mason jar**: total liquid should be **~120–200ml** (after top/lengthener).
- If total liquid < **90ml**, it’s not a long drink → switch to **coupe/martini** OR add a still lengthener/top (depending on carbonation).
- Glass choice must consider **final volume + carbonation**.

### C) Carbonation rules (strict)
- **still & silky**: MUST NOT include fizzy tops (lemonade/soda/prosecco/etc).
  - Use still lengtheners: apple juice, pineapple, cranberry, grapefruit, tea, water, coconut water, etc (stock-dependent).
- **lightly fizzy**: OK to use a gentle top (soda/lemonade) but keep it reasonable.
- **properly sparkling**: SHOULD include a sparkling top (soda/lemonade/sparkling wine) unless stock prevents.

### D) Balance heuristics (guardrails)
- Typical sour: **10–25ml** depending on style
- Sweet should be balanced to sour (avoid >20ml syrup unless drink is intentionally candy/dessert and has enough dilution/length).
- Avoid >2 juices unless explicitly tiki-style.
- Never duplicate the exact same ingredient or redundant duplicates by category unless intentional.

### E) Bitterness preference
- If bitterness = **high**: include at least one bitter element (amaro/aperitif/bitters/grapefruit etc) if available.
- If bitterness = **low**: avoid bitter modifiers.

## Known failure modes to eliminate
- Underfilled recipes (3 ingredients / too low total ml)
- Glass mismatch (small drink in gin glass)
- Still drinks topped with fizzy mixers
- No garnish
- Duplicate ingredients

## Engineering priorities (in order)
1) Add MVC enforcement (ingredient count + required roles)
2) Add volume/glass validation (auto-switch glass or auto-add lengthener)
3) Seeded determinism for tests
4) Add `--help` + CI non-zero exit threshold

## How to test quickly
- Local test harness:
  - `python3 test_recipes.py -v`
  - `python3 test_recipes.py -c -v`
- Live endpoint smoke test (fixed seed should be stable)
