# Route to 10/10 perfect match cocktails

## Definition: “10/10 match”
A 10/10 match is when the customer says some version of:
- “This is exactly my taste.”
- “I would order this again.”

So we optimize for:
- **first-hit success** (reduce early mismatch), and
- **fast convergence** (learn quickly when we miss).

---

## The core idea
Treat preference as a small number of **latent flavour dimensions**.

### Suggested latent dimensions (cocktail version)
1) Sweetness (dry ↔ sweet)
2) Bitterness (avoid ↔ loves)
3) Sourness/acid (low ↔ high)
4) Burn/intensity (low ↔ high) — includes spice and ethanol “heat”
5) Aromatic family (fruity ↔ herbal ↔ smoky/roasty)
6) Body/texture (crisp ↔ silky/rich)
7) Complexity (simple ↔ layered)
8) Familiarity/novelty (safe ↔ adventurous)

Personality research mainly helps with #4 and #8, and partly #1/#2.

Source support: `docs/research/personality-flavour-preferences.md`

---

## Cold-start: get to a decent first drink
We need a short set of questions that maps onto the dimensions.

### Minimum viable questions (fast, high signal)
1) Sweet vs dry?
2) Bitter tolerance? (coffee/IPA/Negroni)
3) Citrus/sour tolerance?
4) Adventurousness?
5) Spice/heat?
6) Fruity vs herbal vs smoky?
7) Preferred base spirit (or “surprise me”)

### Convert answers → numeric profile
Represent each dimension as a number (e.g., -2…+2). Example:
- sweetness: -2 (very dry) … +2 (very sweet)
- bitter: -2 (avoid) … +2 (love)
- adventurous: -2 (safe) … +2 (explore)

Also capture **confidence** (how sure we are), which starts low in cold start.

---

## Generation: pick candidates to maximize both love + learning
Instead of generating one drink, generate:
- **A (safe)**: highest predicted match
- **B (learning)**: one-step exploration on the most uncertain dimension
- Optional **C (wildcard)** if adventurousness is high

This is basically exploration/exploitation:
- If the user is cautious, explore gently.
- If the user is adventurous, explore harder.

---

## Feedback: 2 questions that update the whole profile
After serving/trying, ask something like:
1) “What would you change?” (sweeter/drier, more/less bitter, more/less citrus, stronger/weaker)
2) “What did you like most?” (flavour family + texture)

These answers update the numeric profile.

### Update rule (simple)
- If user says “too bitter” → shift bitter dimension down.
- If “loved the herbal notes” → increase preference weight for herbal family.
- If “too strong” → reduce ABV target and burn/intensity.

Over time, increase confidence and reduce exploration.

---

## Route to 10/10 (practical steps)

### Stage 1: Good default priors
- Use questionnaire answers to set starting profile.
- Use personality-linked answers (adventurousness, spice tolerance) to avoid the most common mismatches.

### Stage 2: Fast convergence loop (2–3 iterations)
- Deliver A (safe) + B (learning).
- Collect minimal feedback.
- Update dimensions.

### Stage 3: Personal “signature”
Once the customer has 2–5 ratings:
- Learn their signature build preferences (e.g., “stirred, spirit-forward, orange oils, low sugar”).
- Generate variants within that signature.

### Stage 4: Long-term retention (optional)
- Seasonal rotation within their signature.
- “Occasion mode”: celebratory, date night, low-cal, etc.

---

## What we should build next (implementation plan stub)
- Define profile schema (dimensions + confidence)
- Define question → dimension mapping
- Define recipe scoring function vs profile
- Add feedback ingestion and update function

(We’ll create these as tasks under the tracker once you confirm the questionnaire fields you want locked.)
