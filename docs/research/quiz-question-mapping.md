# Mapping: CC Platform quiz → flavour profile → recipe decisions

This maps the current CCPLATFORM1 quiz fields to the latent flavour dimensions we use for recipe generation.

Source of questions in CCPLATFORM1:
- `CCPLATFORM1/web/app/lib/questions.ts`

## Latent dimensions (internal)
These are the “real knobs” our generator should reason about.

1) **Sweetness** (dry ↔ sweet)
2) **Bitterness tolerance** (avoid ↔ loves)
3) **Acidity/sour** (low ↔ high)
4) **Burn/intensity** (low ↔ high) — ethanol heat + spice + punch
5) **Aromatic family** (citrus ↔ floral ↔ woody/smoky ↔ sweet/vanilla)
6) **Body/texture** (crisp ↔ rich/silky) *(not asked explicitly yet in the app quiz)*
7) **Complexity** (simple ↔ layered)
8) **Novelty/adventurousness** (safe ↔ explore)

Personality research supports using stable individual differences as **priors** especially for #4 and #8.

---

## Current quiz questions → mapping

### `season` (Spring/Summer/Autumn/Winter)
**Best interpreted as:** temperature/comfort context + flavour mood.

**Suggested mapping**
- spring → higher floral/fresh notes, medium sweetness, crisp texture
- summer → bright citrus, higher acidity, lower perceived heaviness
- autumn → spice/wood, warmer aromatics, slightly richer body
- winter → rich/comforting, higher sweetness/body, lower acidity

**Recipe decisions affected**
- aromatic family (floral/citrus/woody)
- sweetness target
- garnish/top-notes

**Note:** this is more “occasion preference” than personality.

---

### `house_type` (Beach/Modern/Haunted/Tree)
**Best interpreted as:** vibe/novelty preference proxy.

**Suggested mapping**
- beach house → tropical/fruity, lower bitterness, brighter acids
- modern house → clean, crisp, spirit-forward, restrained sweetness
- haunted house → smoky/woody/bitters, higher bitterness tolerance, more intensity
- tree house → herbal/green, fresh, moderate novelty

**Recipe decisions affected**
- aromatic family
- bitterness/intensity priors
- complexity/novelty priors

**Validation needed:** treat as a soft prior; confirm with explicit taste feedback quickly.

---

### `dining_style`
Options:
- balanced blend
- subtle/fresh
- refreshing/vibrant
- sweet tooth/rich

**Best interpreted as:** direct preference signal across sweetness/acidity/body.

**Suggested mapping**
- balanced blend → moderate across dimensions; avoid extremes
- subtle/fresh → lower sweetness, lower intensity, crisp
- refreshing/vibrant → higher acidity, crisp, possibly carbonation
- sweet tooth/rich → higher sweetness + richer modifiers

**Recipe decisions affected**
- sweetness target
- acid target
- build style (shaken/highball vs stirred)

---

### `music_preference` (Jazz/Pop/Rock/Rap)
**Best interpreted as:** arousal/intensity proxy + vibe.

**Suggested mapping**
- jazz/blues → smooth, classic, spirit-forward, restrained sweetness
- pop → bright, approachable, fruity
- rock → bolder, higher intensity, more bitter/smoky acceptable
- rap → punchy flavours, sweeter + higher impact acceptable

**Validation needed:** soft prior.

---

### `aroma_preference` (Citrus/Floral/Woody/Sweet)
**Best interpreted as:** aromatic family (high signal).

**Recipe decisions affected**
- primary modifier family (citrus, floral liqueur, smoke/wood notes, vanilla/caramel)
- garnish and top-note selection

---

### `base_spirit` (Gin/Vodka/Rum/Tequila)
**Best interpreted as:** base constraint + known familiarity.

**Recipe decisions affected**
- spirit choice
- bitterness ceiling (e.g., gin can carry bitterness/herbal; vodka often wants cleaner builds)

---

### `bitterness_tolerance` (Low/Medium/High)
**Best interpreted as:** core axis.

**Evidence support**
- Bitter preference correlates with stable individual differences and predicts behaviour even controlling for other tastes.
  - Appetite (2016) PMID 26431683, DOI 10.1016/j.appet.2015.09.031

**Recipe decisions affected**
- amaro/bitters load
- tonic/quinine notes
- coffee/chocolate bitter notes

---

### `sweetener_question` (Classic/Rich/Floral/Zesty)
**Best interpreted as:** sweetness *style* and flavour family.

**Suggested mapping**
- classic → neutral sweetener profile, balanced
- rich → higher sweetness + deeper notes (demerara/vanilla/caramel)
- floral → lower/medium sweetness + floral top notes
- zesty → medium sweetness + citrus-forward

**Recipe decisions affected**
- sweetener type
- citrus selection
- garnish

---

### `abv_lane` (Low/Medium/Strong)
**Best interpreted as:** intensity/ethanol heat preference + occasion.

**Evidence support (adjacent)**
- Sensation seeking / reward sensitivity predicts liking & intake of capsaicin/spicy foods, i.e., acceptance of intense sensations.
  - Food Quality and Preference (2013) PMID 23538555, DOI 10.1016/j.foodqual.2012.09.008

**Recipe decisions affected**
- ABV target and style (highball vs stirred)
- dilution targets
- perceived burn management (sugar/acid balance)

---

## Gaps (what the current quiz does NOT capture well)
If we want “10/10 matches”, we should eventually capture:
- **Acidity/sour tolerance** explicitly (some users hate sharp citrus)
- **Texture preference** (creamy/foamy vs crisp)
- **Spice/heat tolerance** explicitly (validated by research)
- **Allergens / dietary constraints** (already present in older quiz code, not currently asked in the app flow)

---

## Recommendation: make the “vibe” questions safe
Keep vibe questions (season/house/music) for engagement, but:
- treat them as **soft priors**
- confirm quickly via a direct taste axis question or early feedback

Next doc to connect this into convergence loop:
- `docs/recipe_generation/10-10-match-roadmap.md`
