# Personality → flavour preferences (research brief)

## Why this matters
We ask users questions to build a flavour profile. The point of bringing in **personality** is not “psychology for vibes” — it’s a way to set better **priors** when the user is new (cold start), and to predict where they’ll sit on key cocktail dimensions like **sweet vs dry**, **bitter tolerance**, **heat/spice tolerance**, and **novelty/adventurousness**.

In practice:
- Personality-linked traits help predict *acceptance of intensity/novelty* (e.g., bitter, spicy, funky, smoky).
- This reduces early mismatches and speeds up the learning loop toward “perfect match”.

---

## Strongest (most actionable) empirical links

### 1) Sensation seeking / reward sensitivity ↔ liking & intake of spicy foods
**What it supports in our model:**
- A user who reports “adventurous”, “likes intense sensations”, “chases novelty” is more likely to enjoy high-impact cocktails (heat, bold bitterness, smoky, high-ABV, unusual ingredients).
- Conversely, low sensation-seeking users are more likely to prefer smoother, familiar, lower-burn options.

**Evidence:**
- *Personality factors predict spicy food liking and intake* (Food Quality and Preference, 2013; PMID: **23538555**, DOI: **10.1016/j.foodqual.2012.09.008**)
  - Found positive correlations between **Sensation Seeking** and liking of spicy foods (and chili intake).
  - Chili intake also associated with **Sensitivity to Reward**.
  - Not explained by reduced perceived burn (i.e., not just desensitization); supports a *personality driver*.

- *Gender differences in the influence of personality traits on spicy food liking and intake* (Food Quality and Preference, 2015; PMID: **25663751**, DOI: **10.1016/j.foodqual.2015.01.002**)
  - Suggests different pathways by gender: in men reward sensitivity related more strongly; in women sensation seeking more strongly.

**How to use this:**
- Our “adventurousness / intensity” questions are justified.
- We can use these answers to pick:
  - spice/heat, smoke, bitterness, “funk”, ABV, and complexity.

---

### 2) Bitter liking ↔ antisocial / “dark” traits (robust link)
**What it supports in our model:**
- Bitter tolerance/preference is not random; it can track stable individual differences.
- Not saying customers are “sadistic” if they like Negronis — but bitter preference is a reliable dimension and correlates with certain personality constructs.

**Evidence:**
- *Individual differences in bitter taste preferences are associated with antisocial personality traits* (Appetite, 2016; PMID: **26431683**, DOI: **10.1016/j.appet.2015.09.031**)
  - Across two studies (N≈953), bitter preference positively associated with malevolent traits (esp. everyday sadism/psychopathy).
  - Bitter preference remained predictive **controlling for** sweet/sour/salty preferences.

**How to use this:**
- Reinforces “bitter vs sweet/dry” as a core axis.
- We should treat **bitterness** separately from “strength” and “sourness”.

---

### 3) Sweet preference ↔ more neurotic traits (context: obesity sample)
**What it supports in our model:**
- Sweet preference can be linked to affect regulation / stress coping for some segments.
- For cocktails: sweet/comfort profiles may be safer for users who dislike harshness and seek soothing flavours.

**Evidence:**
- *Sweet and fat taste preference in obesity have different associations with personality and eating behavior* (Physiology & Behavior, 2006; PMID: **16624348**, DOI: **10.1016/j.physbeh.2006.03.006**)
  - Strong sweet preference associated with more neurotic traits (e.g., lack of assertiveness/embitterment) in this sample.

**How to use this:**
- Don’t overclaim generality; still useful as directional support.
- Suggests “comfort vs challenging” is a meaningful framing.

---

## What this implies for *our* questionnaire (the parts we ask)

### The cocktail-relevant latent dimensions (what we’re really measuring)
1) **Novelty / adventurousness** (exploration vs safety)
2) **Intensity tolerance** (burn/heat/ABV/bitterness)
3) **Sweetness preference** (dessert-like vs dry)
4) **Bitter tolerance** (Negroni/IPA drinker vs avoids bitterness)
5) **Sour tolerance** (sharp/citrus vs mellow)
6) **Aromatic preference** (fruity vs herbal vs smoky)
7) **Texture preference** (silky/creamy vs crisp/refreshing)

Personality research mainly boosts (1) and (2), and partly (3)/(4).

### Map: question → signal → design choice
Below is the practical mapping to justify why the questions exist.

- **“How adventurous are you?”**
  - Signal: sensation seeking / novelty preference
  - Design: choose unusual modifiers, bolder bitters, smoke, funky flavours; allow higher complexity.

- **“Do you like spicy/heat?”**
  - Signal: sensation seeking + tolerance for trigeminal burn
  - Design: chili/ginger/pepper; higher impact drinks; don’t default to “smooth”.

- **“Do you like bitter drinks (coffee/IPA/Negroni)?”**
  - Signal: bitter preference axis (stable trait-like differences)
  - Design: bitters-forward builds (amaro, tonic-like quinine notes, coffee/chocolate bitter) vs avoid.

- **“Sweet vs dry?”**
  - Signal: sweet preference / comfort-seeking (sometimes stress-linked)
  - Design: sugar level, liqueur choice, fruit-forward vs spirit-forward.

- **“Fruity vs herbal vs smoky?”**
  - Signal: aromatic family preference (often learned/cultural)
  - Design: botanical selection, garnish direction, top-notes.

- **“Refreshing vs rich/creamy?”**
  - Signal: texture/mouthfeel preference
  - Design: shaken high-acid vs stirred; egg white/cream; dilution targets.

---

## Limits / what we should *not* claim
- Many personality–taste links are **correlational** and context-dependent.
- Effects can be small-to-moderate; best used as **priors**, not deterministic rules.
- Culture, exposure, and availability drive a lot of preference (esp. aromatics).

---

## Next research additions (optional)
If we want to deepen this later, we should pull in:
- Food neophobia ↔ Big Five (often: lower openness → higher neophobia)
- Alcohol taste acquisition (learned liking for bitterness)
- Sensory genetics (PROP “supertaster” sensitivity) as a separate axis

---

## How we translate this into a better recipe model
This brief supports a model where we:
1) Use questionnaire answers to set priors on the axes above.
2) Generate 2–3 candidate cocktails balanced between:
   - exploitation (safe match) and
   - exploration (learn quickly).
3) Ask for lightweight feedback (1–2 questions) and update profile.

Implementation roadmap: see `docs/recipe_generation/10-10-match-roadmap.md`.
