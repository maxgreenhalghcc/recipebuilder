# Claude.md — Custom Cocktails Recipe Engine Context (Spooky-Good Spec)

This file is the **single source of truth** for the Custom Cocktails recipe generator: what exists today, what’s broken, what “spooky good” means, and the **rules + implementation plan** to get there fast without destabilising reliability.

---

# Business + product context (why this exists)

## What Custom Cocktails is
Custom Cocktails is a **bar revenue uplift system**:
- Guest scans a **QR**
- Completes a **short “personality quiz” (9 questions)**
- Engine generates a **bespoke cocktail recipe** that feels “spooky accurate”
- Staff see the order on a **tablet dashboard** (service workflow)
- Bar serves it fast, consistently, and it should taste great
- Platform is **stock-aware per bar** (must only output what the bar can actually make)

Commercial model:
- We win when the bar wins: **15% commission on cocktail sales** (revenue share)
- No “fragile novelty” allowed — if recipes are weird/weak, staff lose trust and usage dies.

## Product experience constraints (the most important “hidden” requirements)
The engine is not an art project — it’s a **live service system in a real bar**:
- Must be **fast** (ordering shouldn’t slow service)
- Must be **consistent** (staff must trust it)
- Must be **buildable** (realistic measures, steps, glass, ice)
- Must be **stock-valid** (bar-specific inventory + substitutions)
- Must be **high perceived value** (crafted cocktails, not “spirit + syrup + lime”)

The recipe quality drives:
- staff adoption
- customer delight
- repeat orders
- perceived “AI magic”
- revenue uplift (the business proof)

---

# How the system works end-to-end (high level)

## Flow
1) Quiz UI collects answers → creates a JSON payload
2) API `/generate` receives payload
3) Engine loads bar stock (bar_id scoped)
4) Engine chooses a template/family and fills slots
5) Validation + repair loop runs
6) Response returned as a structured recipe for staff and guest

## Critical invariants
- **Never error**: always return a recipe
- **Never contradict the guest’s key inputs** (especially carbonation + bitterness + foam)
- **Never output non-stock ingredients** (unless explicit substitution policy applies)
- **Never output under-built drinks** (thin, underfilled, not crafted)

---

# Inputs (payload) — expected fields & meanings

This is what the engine should assume the product is sending (common live payload):

- `bar` / `bar_id`: bar identifier (inventory + rules)
- `base_spirit`: primary spirit family (gin/vodka/rum/tequila/whisky etc)
- `season`: affects fruit, warmth, spice, freshness
- `house_type`: “vibe” (beach/modern/haunted/tree) → influences glass + style family
- `dining_style`: semantic intent text (fresh/bright/balanced/indulgent etc)
- `music_preference`: mood signal (pop/rock/jazz etc) → profile tendencies
- `aroma_preference`: main axis (citrus/floral/woody/sweet etc)
- `bitterness_tolerance`: low/medium/high
- `sweetener_question`: classic/zesty/floral/etc (sweet style)
- `carbonation_texture`: still & silky / lightly fizzy / properly sparkling
- `foam_toggle`: yes/no (foam contract)
- `abv_lane`: low/medium/strong (controls base total)
- `allergens`: may be messy (true allergens vs dislikes) — must parse safely
- `seed`: optional (for deterministic repeatability in testing)

**Important:** some fields can be missing/blank; engine must degrade gracefully.

---

# Output (response schema) — what the platform and staff actually need

The engine returns:

- `glassware`: staff-facing, must match serve style + volume
- `garnish`: must be in stock or substituted
- `ingredients[]`: list of measured lines; may include “Top with X” OR a measured top-up line
- `method`: clear build steps (shake/build/stir/strain; dry shake when foam)
- `warnings[]`: internal flagging for debug (should not break service)
- optional: `abvEstimate`, `description`, `name`

**Do not** mix schema fields (garnish/serve_over inside ingredients, etc).

---

# Stock-awareness model (what “stock aware” means in practice)

Each bar has a stock list (JSON) where ingredients typically carry:
- `name`
- `category` (spirit / modifier / sweetener / sour / juice / mixer / garnish etc)
- `role` (what it’s used as)
- `default_measure_ml`
- flavour signals: `flavour_tags`, `profiles`, `aromas`, `seasons`
- possibly flags like `neutral`

**Hard rule:** if ingredient is not in stock, do not use it (unless substitution policy exists).
Substitution policy should be:
- same category/role
- same flavour family/profile
- minimal surprise (elderflower ↔ floral liqueur, etc)

---

# Definition of “good recipes” (operational, not vibes)

A “good” output must satisfy **both**:

## 1) Cocktail quality
- coherent flavour family (not random candy leakage)
- balanced sweet/sour/body
- not thin / not cloying
- not overloaded (too many juices)
- credible bartender recipe (measures, steps, glass, ice)

## 2) Bespoke match
The guest should immediately see their preferences reflected:
- still vs sparkling correctly
- bitterness correctly
- aroma correctly
- season/vibe correctly
- ABV lane correctly
- foam toggle correctly (real foam contract)

## Practical “minimum crafted” standard (service-ready baseline)
Unless a template explicitly defines a 3-ingredient classic (rare and deliberate), **the generator must output a crafted structure**:
- base spirit total consistent with ABV lane
- at least one complexity contributor (modifier OR structured mixer strategy)
- proper body/length plan matching glass selection

---

# Safety + reliability rules for Claude working in this repo

When implementing changes:
- Prefer **small, testable diffs** over big refactors.
- Add constraints in a way that preserves “never error”.
- Keep logic single-source-of-truth (serve style, glass, ice).
- Avoid conflicting mutators and late-stage overrides.
- Where possible: enforce rules via **scoring + rerank** first, then hard constraints.

Testing expectations:
- Add targeted fixtures for new rules
- Ensure deterministic behavior when seed is provided
- Ensure output schema remains stable

---

# How to use this file in Claude Code sessions
Claude Code must:
1) Read `CLAUDE.md` first
2) Work in priority order from the roadmap
3) Keep changes minimal and safe
4) Run tests after changes (`python3 test_recipes.py -c -v`)
5) Fix the top failure patterns before adding new complexity

---

## Project goal

Generate **bartender-respectable**, **stock-aware**, **quiz-personalised** cocktails that feel “**spooky accurate**” to the guest’s inputs (season/aroma/bitterness/sweetness/carbonation/house vibe/ABV/foam).

**Non-negotiable:** engine must remain **bulletproof** (always returns a recipe, never errors).

---

## Current state (where the engine is now)

### What’s working
- **Reliability is strong**: returns HTTP 200 consistently, including weird/empty inputs (has a “never error” fallback).
- **Template-first direction exists**: `select_template`, `TEMPLATE_SPECS`, `TEMPLATE_RULES`.
- **Critic/repair loop exists**: validate → repair → validate.
- **Minimum viable cocktail guardrails** exist partially (reduced “spiked mixer” cases).
- **Foam logic clarified**: foam means **build style**, not “needs foamer stock”; steps builder treats:
  - fizzy + foam=yes → shake (except mixer) then top
  - fizzy + foam=no → build/stir then top
  - still → shake + strain (no top)
- **Glass mapping exists** primarily in `ProfileRecipeBuilder._choose_glass()`:
  - beach → long glass
  - modern → martini/coupe variation
  - haunted → should be mason jar (skull was wrong)
  - tree → gin glass (+ small random to rocks)

### Known architecture risk
There are multiple “serve style” mutators that can fight each other:
- `service.py _apply_serving_coherence` (adds “Serve over cubed”, changes glassware)
- `recipe_engine.py _apply_serving_style_pass / _determine_glassware` (can overwrite glassware)

**Direction:** move toward **single source of truth** for glass/ice/serve-style.


## Key problem (root cause of “valid but random”)

The engine currently behaves more like a **constraint satisfier** than a **flavour-family engine**.

It can often satisfy “has sweet/sour/mixer” while still choosing **palate-wrong** items (bubblegum/melon/banana in zesty/woody/classic lanes).

---

## “Spooky good” definition

### Target outcome
A guest should feel:  
> “That’s exactly what I’d want”  
…because the drink clearly reflects:
- season
- aroma preference
- bitterness tolerance
- sweetness style
- carbonation texture
- “house vibe”
- ABV lane
- foam toggle

### Scoring rubric (use for debugging)
- **Cocktail quality (0–10):** balance, not thin, not cloying, coherent family
- **Bespoke match (0–10):** does it clearly reflect the quiz inputs (and avoid contradictions)?
- **Completeness (0–10):**
  - 0–3 = spiked mixer (bad)
  - 4–6 = simple highball (okay)
  - 7–10 = full crafted cocktail (good)
- **Consistency (pass/fail):**
  - still & silky must not be topped with fizz
  - garnish must be in stock and match the drink
  - glass/ice must match top-up / serve style


## Output schema expectations (do not break)

DO NOT CHANGE PAYLOAD SHAPE

Keep a clean separation:
- `ingredients[]` = liquid measures + optional “Top with X” (or a measured top-up line)
- `garnish` = garnish only (must exist in stock or be substituted)
- `glassware` = final glass decision (must match serve style)
- `method` = includes shake/build instructions, includes dry shake if foam
- `serve_over` (if used) = cubed/crushed/none — **never** string-inject into ingredients

**Never allow** strings like:
- “Garnish: …” inside ingredients[]
- “Serve over …” inside ingredients[]

---

## Acceptance tests (add fixtures / unit tests)

1) If `carbonation_texture == "still & silky"` → output contains **no carbonated top**  
2) If `foam_toggle == "yes"` → includes foam agent + method contains **dry shake**  
3) If `bitterness_tolerance == "high"` → includes **bitter component**  
4) Autumn + woody + tree house + classic → **no tropical trio** (coconut/passionfruit/pineapple) unless summer+beach also true  
5) Sour/collins templates → **exactly one sour source** and sour exists  
6) Juice count ≤ 2 (unless tiki family explicitly chosen)  
7) Martini/coupe glass → **no top-up lines**  
8) If “Top with …” exists → long glass + cubed ice

---

## Notes on file hotspots (names seen in work so far)

- `ProfileRecipeBuilder._choose_glass()` — glass mapping by house_type + seed
- `service.py _apply_serving_coherence` — may mutate serve/glass/ice (risk of conflict)
- `recipe_engine.py _apply_serving_style_pass / _determine_glassware` — may overwrite glassware (risk)
- `flavour_context.py` / vector scoring — foundations exist; not fully driving slot selection yet

**Guiding principle:** reduce duplicated logic; enforce one place decides serve style.

---

## Immediate “next sprint” (minimum effective work)

If only 4 tasks happen next:
1) Add **family lanes** (choose_family + allow/bans + slot fill gating)
2) Add **clash matrix** (top 4–6 bans)
3) Add **fill-to-glass measured top-ups**
4) Add **bitterness enforcement** (tonic/bitters/amaro rules)

Everything else is incremental polish.

---

# Additions — “service-ready” hard constraints (do not remove, implement safely)

## Minimum Viable Cocktail (MVC) enforcement (prevents 3-ingredient underbuilt drinks)
Unless a template is explicitly marked as a deliberate 3-ingredient classic, enforce:
- at least **4 ingredients** (excluding garnish)
- must include: base + sweet + sour + (modifier OR juice OR lengthener/top depending on carbonation)
- if output is short of MVC, the engine must **auto-fill** missing role(s) using best-fit stock ingredients inside the chosen family.

## Volume/glass consistency (prevents tiny drinks in gin/long glass)
If final volume is too low for the selected glass:
- either (A) **switch glass** to coupe/martini/rocks (short serve)
- or (B) **add a still lengthener** (still & silky) or **sparkling lengthener** (fizzy lanes)
Target:
- long/gin glass: **~120–200ml** final liquid
- coupe/martini: **~90–130ml** final liquid (no tops)

## Determinism contract (for tests + reproducibility)
- If payload includes `seed`, generator behavior must be reproducible.
- Randomness should be seeded per-request, not global.

## Implementation preference order (least risky)
1) Score + rerank candidates (soft enforcement)
2) Repair pass to meet MVC + serve coherence
3) Only then: change template selection / slot filling strategy

EOF


