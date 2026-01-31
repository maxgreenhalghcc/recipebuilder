# Project Tracker

This is the single place to see **what we’re doing**, **why**, and **what’s next**.

## Dashboard (quick view)

| Area | Project | Status | Next action | Owner | Links |
|---|---|---:|---|---|---|
| Recipe generation | Personality → flavour profile research | In progress | Finish evidence summary + map to questionnaire fields | Claw | docs/research/personality-flavour-preferences.md |
| Recipe generation | 10/10 match roadmap (cold start → iterative perfection) | In progress | Convert research → model assumptions + iteration plan | Claw | docs/recipe_generation/10-10-match-roadmap.md |

---

## Recipe generation (projects)

### 1) Personality → flavour profile research
**Goal:** justify (with evidence) why asking certain questions improves cocktail matching.

**Deliverables**
- Research brief: `docs/research/personality-flavour-preferences.md`
- Mapping: *question → latent trait → cocktail design choice*
- Gaps: what we don’t know yet + how we’ll learn it from feedback

**Fields (for later dashboard expansion)**
- Platform: (e.g., Recipebuilder, CC Platform, POS, WhatsApp)
- Strategy: (e.g., cold-start quiz, upsell prompt, retention)
- Stage: (idea, research, build, test, ship)
- KPI: (e.g., “first drink loved”, reorder rate, NPS, refund)

### 2) Route to 10/10 perfect match cocktails
**Goal:** a repeatable loop that reliably gets to “this was made for me.”

**Deliverables**
- Roadmap doc: `docs/recipe_generation/10-10-match-roadmap.md`
- Implementation plan (later): schema + scoring + exploration/exploitation + feedback prompts

---

## Backlog (parking lot)
- Add “platform / strategy / KPI / stage” columns to dashboard view
- Create per-project subfolders with `notes.md`, `data/`, `experiments/`
- Add a lightweight `docs/projects/DASHBOARD.md` landing page
