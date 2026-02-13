# Custom Cocktails Recipe Engine

A bar revenue uplift system that generates bartender-respectable, stock-aware, quiz-personalised cocktails that feel "spooky accurate" to the guest's inputs.

## How It Works

1. Guest scans a QR code and completes a 9-question personality quiz
2. The engine generates a bespoke cocktail recipe tailored to their preferences
3. Staff see the order on a tablet dashboard and serve it fast

Commercial model: 15% commission on cocktail sales (revenue share).

## Architecture Overview

### Core Files

| File | Purpose |
|------|---------|
| `recipebuilder/recipe_engine.py` | Core models, stock repository, flavour association model |
| `recipebuilder/profile_builder.py` | Template selection, glass choice, ingredient selection, family gating |
| `recipebuilder/bartender_score.py` | Scoring loop (0-10 quality + bespoke), repair actions |
| `recipebuilder/flavour_context.py` | Flavour vectors, knowledge base, cosine similarity |
| `recipebuilder/preferences.py` | Profile mapping from quiz responses, allergen logic |
| `service.py` | Flask HTTP endpoint `/generate`, orchestration pipeline |

### Generation Pipeline

```
1. service.py /generate endpoint
   |
2. ProfileRecipeBuilder.build_candidates()
   |- select_template() -> SOUR, HIGHBALL, SPRITZ, etc.
   |- determine_flavor_family() -> DARK_SPICED_WOODY, FRESH_CITRUS_ZESTY, etc.
   |- _build_single_recipe() x3 candidates
   |  |- _choose_glass()
   |  |- _choose_base(), _choose_juices(), _choose_sweet_components()
   |  |- Modifier, sour, mixer selection
   |- _apply_serving_style_pass()
   |- _apply_variety_pass_to_profile_recipe()
   |
3. service.py candidate scoring + coherence
   |- normalise_measurements_and_cap_alcohol()
   |- _score_candidate() (soft preference scoring)
   |- _apply_serving_coherence()
   |
4. bartender_score.iterate_until_pass() (quality gate)
   |- score_recipe() -> quality (0-10) + bespoke (0-10)
   |- improve_recipe() with targeted fixes
   |- Iterates up to 4 times until both scores >= 8
   |
5. Return structured JSON recipe
```

### Key Design Decisions

- **Template-first**: quiz inputs map to a cocktail template (SOUR, HIGHBALL, SPRITZ, MARTINI_UP, etc.) which constrains the build structure
- **Flavour family gating**: ingredients are filtered by family (DARK_SPICED_WOODY, FRESH_CITRUS_ZESTY, etc.) to prevent palate-wrong choices
- **Score + repair loop**: recipes are scored and iteratively improved rather than regenerated from scratch
- **Stock-aware**: every ingredient must exist in the bar's inventory or be substituted from the same category/role
- **Never error**: always returns a recipe, including graceful degradation for missing/malformed inputs

## Quiz Inputs (Payload)

| Field | Description |
|-------|-------------|
| `bar` / `bar_id` | Bar identifier (scopes inventory) |
| `base_spirit` | Primary spirit family (gin/vodka/rum/tequila/whisky) |
| `season` | Affects fruit, warmth, spice, freshness |
| `house_type` | Vibe (beach/modern/haunted/tree) -> glass + style |
| `dining_style` | Semantic intent (fresh/bright/balanced/indulgent) |
| `music_preference` | Mood signal (pop/rock/jazz) -> profile tendencies |
| `aroma_preference` | Main axis (citrus/floral/woody/sweet) |
| `bitterness_tolerance` | low/medium/high |
| `sweetener_question` | Sweet style (classic/zesty/floral) |
| `carbonation_texture` | still & silky / lightly fizzy / properly sparkling |
| `foam_toggle` | yes/no (foam contract) |
| `abv_lane` | low/medium/strong |
| `allergens` | Parsed safely for exclusions |
| `seed` | Optional, for deterministic reproducibility |

## Output Schema

| Field | Description |
|-------|-------------|
| `glassware` | Staff-facing glass (must match serve style + volume) |
| `garnish` | Must be in stock or substituted |
| `ingredients[]` | Measured lines + optional "Top with X" |
| `method` | Build steps (shake/build/stir/strain; dry shake if foam) |
| `warnings[]` | Internal debug flags |

## What's Working Well

- **Reliability**: returns HTTP 200 consistently, never errors even on weird/empty inputs
- **Template system**: 8 template specs with proper constraint enforcement
- **Stock-awareness**: bar-scoped inventory, allergen handling, profile filtering
- **Flavour family gating**: 7 families with ingredient ban lists
- **Scoring loop**: iterates up to 4x with targeted repairs (sour injection, sugar reduction, neon swaps)
- **Carbonation coherence**: still & silky correctly blocked from fizzy tops
- **Foam toggle**: dry shake method, foaming agent enforcement
- **ABV lane**: base spirit volume gated by low/medium/strong

## Known Gaps

### Critical

**1. No MVC (Minimum Viable Cocktail) enforcement**
The spec requires at least 4 ingredients (base + sweet + sour + complexity) but there's no gate or auto-fill. The scoring loop penalizes thin builds indirectly, but nothing prevents a 3-ingredient "spiked mixer" from slipping through.

**2. Fill-to-glass volume checks missing**
Glass objects carry `capacity_ml` but there's no post-generation check that final liquid volume fits the glass. No logic to switch to a smaller glass if volume is too low, or add a lengthener if underfilled.

**3. Serve-style logic fragmented across 3 locations**
Glass/ice/top-up decisions are made in `profile_builder._choose_glass()`, then potentially overwritten by `profile_builder._apply_serving_style_pass()`, then again by `service._apply_serving_coherence()`. These can conflict with no single source of truth.

### Significant

**4. Clash matrix barely exists**
The spec calls for 4-6 hard clash rules (toffee+lime, neon+non-tropical, etc.). Only one is implemented (dessert spirit + lime) as a soft scoring penalty.

**5. Bitterness enforcement is soft only**
High bitterness should guarantee a bitter component (tonic/amaro/bitters/grapefruit). Low should exclude tonic. Currently keyword-weighted and soft-scored only.

**6. Flavour vectors underutilized**
`flavour_context.py` has a full vector scoring system with cosine similarity, but core ingredient selection uses keyword heuristics. Vectors only drive post-generation variety swaps.

**7. Scoring loop can undo family gating**
`FAMILY_BANS` gate ingredients during generation, but `improve_recipe()` can swap ingredients back in from outside the family during repair iterations.

### Test Coverage Gaps

- No test for >= 4 ingredients (MVC)
- No test for volume vs glass capacity
- No test for `dry_shake` in method when `foam_toggle=yes`
- No test that family gating blocks tropical ingredients in DARK_SPICED_WOODY
- Only 1 clash scenario tested out of 4-6 specified

## Next Sprint (Minimum Effective Work)

1. **Unify serve-style** to a single decision point
2. **Add MVC enforcement** with auto-fill for missing roles
3. **Add fill-to-glass volume check** with glass-switch fallback
4. **Implement clash matrix** as scoring penalties + repair actions
5. **Hard-enforce bitterness rules**

## Running Tests

```bash
python3 test_recipes.py -c -v
```

## Data

- `/data/bars/*.json` — bar-specific inventory (15+ bars)
- `/data/flavour/` — flavour profiles, templates, vectors, training weights
- `/data/training/` — successful cocktail samples for model training
