"""Rule-based profile recipe builder."""

from __future__ import annotations

import random
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple, Literal

from recipebuilder.recipe_engine import (
    CocktailRecipe,
    IngredientSuggestion,
    StockItem,
    _expand_avoid_terms,
    _extract_avoid_terms,
    _normalize,
    extract_flavour_keywords,
    extract_spirit_family,
)

from recipebuilder.preferences import _NON_RESPONSE_TOKENS, _tokenise_values

TemplateId = Literal[
    "SOUR_FOAMY",
    "SOUR",
    "COLLINS",
    "HIGHBALL",
    "SPRITZ",
    "MARTINI_UP",
    "OLD_FASHIONED",
    "TIKI_SHAKEN",
]

def select_template(responses: Dict[str, Any]) -> TemplateId:
    carb = (responses.get("carbonation_texture") or "").strip().lower()
    foam = (responses.get("foam_toggle") or "").strip().lower()
    house = (responses.get("house_type") or "").strip().lower()

    still = "still" in carb
    lightly = "light" in carb
    properly = "proper" in carb or "spark" in carb

    if still:
        # no carbonated tops
        if foam == "yes":
            return "SOUR_FOAMY"
        # modern house biases "up" drinks
        if "modern" in house:
            return "MARTINI_UP"
        return "SOUR"

    if properly:
        # big top
        return "SPRITZ"

    # default fizzy = lightly / unknown
    if foam == "yes":
        # foamy highballs are rare; best safe template is still a sour style,
        # but since carbonation isn't "still", we keep it as a shaken sour served long without top
        return "SOUR_FOAMY"
    return "HIGHBALL"
    
TEMPLATE_SPECS: Dict[str, Dict[str, Any]] = {
    "SOUR_FOAMY": {"needs_sour": True, "needs_mixer": False, "max_juices": 1, "allow_foam": True},
    "SOUR": {"needs_sour": True, "needs_mixer": False, "max_juices": 1, "allow_foam": False},
    "MARTINI_UP": {"needs_sour": False, "needs_mixer": False, "max_juices": 0, "allow_foam": False},
    "OLD_FASHIONED": {"needs_sour": False, "needs_mixer": False, "max_juices": 0, "allow_foam": False},
    "HIGHBALL": {"needs_sour": True, "needs_mixer": True, "max_juices": 2, "allow_foam": False},
    "COLLINS": {"needs_sour": True, "needs_mixer": True, "max_juices": 2, "allow_foam": False},
    "SPRITZ": {"needs_sour": False, "needs_mixer": True, "max_juices": 2, "allow_foam": False},
    "TIKI_SHAKEN": {"needs_sour": True, "needs_mixer": False, "max_juices": 3, "allow_foam": False},
}



@dataclass
class Glass:
    name: str
    capacity_ml: int
    sparkling: bool


PROFILES = [
    "tropical",
    "citrus_fresh",
    "berry",
    "classic_boozy",
    "candy_fun",
    "dessert",
]

PROFILE_FLAVOUR_WORDS: Dict[str, List[str]] = {
    "tropical": ["passion", "pineapple", "mango", "coconut", "orange"],
    "citrus_fresh": ["lemon", "lime", "orange", "grapefruit"],
    "berry": ["raspberry", "strawberry", "berry", "cranberry"],
    "classic_boozy": ["orange", "grapefruit"],
    "candy_fun": ["blue", "raspberry", "strawberry", "cherry", "berry"],
    "dessert": ["vanilla", "caramel", "toffee", "passion", "pineapple"],
}


MAX_SHARED_INGREDIENT_RATIO = 0.7

class GuardrailReject(Exception):
    def __init__(self, reasons: Sequence[str], fixes: Sequence[str] = ()):
        super().__init__(", ".join(reasons))
        self.reasons = list(reasons)
        self.fixes = list(fixes)



def _is_sour(item: StockItem) -> bool:
    name = item.name.lower()
    return item.role == "sour" or "lemon" in name or "lime" in name


def _is_mixer(item: StockItem) -> bool:
    name = item.name.lower()
    mixer_tokens = ("lemonade", "soda", "tonic", "ginger", "fizz", "sparkling")
    return item.role == "mixer" or any(token in name for token in mixer_tokens)


def _is_core_juice(item: StockItem) -> bool:
    return item.role == "juice" and not _is_sour(item) and not _is_mixer(item)


def _is_citrus_juice(item: StockItem) -> bool:
    name = item.name.lower()
    return any(token in name for token in ("orange", "cranberry", "lemon", "lime", "grapefruit"))


def _is_thick_juice(item: StockItem) -> bool:
    name = item.name.lower()
    return any(token in name for token in ("passion", "mango", "puree", "banana", "guava"))


def _ingredient_key_set(recipe: CocktailRecipe) -> Set[str]:
    return {s.ingredient.name.lower() for s in recipe.ingredients}


def _ingredient_overlap_ratio(a: CocktailRecipe, b: CocktailRecipe) -> float:
    a_keys = _ingredient_key_set(a)
    b_keys = _ingredient_key_set(b)
    if not a_keys and not b_keys:
        return 0.0
    return len(a_keys & b_keys) / len(a_keys | b_keys)


def choose_profile(responses: Dict[str, Any]) -> str:
    explicit = (responses.get("flavour_profile") or responses.get("profile") or "").strip().lower()
    if explicit in PROFILES:
        return explicit

    scores = {profile: 0.0 for profile in PROFILES}

    season = (responses.get("season") or "").lower()
    if "summer" in season:
        scores["tropical"] += 3
        scores["candy_fun"] += 2
    if "spring" in season:
        scores["berry"] += 2
        scores["citrus_fresh"] += 2
    if "autumn" in season:
        scores["classic_boozy"] += 2
    if "winter" in season:
        scores["dessert"] += 3
        scores["classic_boozy"] += 1

    house = (responses.get("house_type") or "").lower()
    if "beach" in house:
        scores["tropical"] += 3
    if "tree" in house:
        scores["tropical"] += 2
        scores["citrus_fresh"] += 1
    if "modern" in house:
        scores["citrus_fresh"] += 2
        scores["classic_boozy"] += 1
    if "haunted" in house:
        scores["classic_boozy"] += 2
        scores["berry"] += 1

    dining = (responses.get("dining_style") or "").lower()
    if "balanced blend" in dining:
        scores["citrus_fresh"] += 2
        scores["berry"] += 2
        scores["dessert"] -= 1
    if "subtle" in dining or "fresh" in dining:
        scores["citrus_fresh"] += 3
        scores["dessert"] -= 1
    if "refreshing" in dining or "vibrant" in dining or "bright" in dining or "zesty" in dining:
        scores["tropical"] += 3
        scores["citrus_fresh"] += 2
    if "sweet tooth" in dining or "dessert" in dining or "indulging in rich flavours" in dining:
        scores["dessert"] += 6
        scores["candy_fun"] += 2

    music = (responses.get("music_preference") or "").lower()
    if "jazz" in music or "blues" in music:
        scores["classic_boozy"] += 2
    if "pop" in music:
        scores["candy_fun"] += 2
        scores["berry"] += 1
    if "rock" in music:
        scores["classic_boozy"] += 2
        scores["tropical"] += 1
    if "rap" in music:
        scores["candy_fun"] += 2
        scores["tropical"] += 1

    aroma = (responses.get("aroma_preference") or "").lower()
    if "citrus" in aroma:
        scores["citrus_fresh"] += 3
        scores["tropical"] += 1
    if "campfire" in aroma or "wood" in aroma:
        scores["classic_boozy"] += 3
    if "floral" in aroma:
        scores["berry"] += 3
        scores["citrus_fresh"] += 1
    if "sweet" in aroma:
        scores["candy_fun"] += 2
        scores["dessert"] += 2

    base = (responses.get("base_spirit") or "").lower()
    if "rum" in base:
        scores["tropical"] += 3
        scores["candy_fun"] += 1
    if "gin" in base:
        scores["berry"] += 2
        scores["citrus_fresh"] += 2
        scores["candy_fun"] += 1
    if "vodka" in base:
        for profile in PROFILES:
            if profile != "dessert":
                scores[profile] += 1
    if "tequila" in base:
        scores["classic_boozy"] += 3
        scores["citrus_fresh"] += 1

    bitter = (responses.get("bitterness_tolerance") or "").lower()
    if "low" in bitter:
        scores["candy_fun"] += 2
        scores["tropical"] += 2
        scores["dessert"] += 1
    if "medium" in bitter:
        scores["citrus_fresh"] += 1
        scores["berry"] += 1
    if "high" in bitter:
        scores["classic_boozy"] += 3
        scores["citrus_fresh"] += 1

    sweet_style = (responses.get("sweetener_question") or "").lower()
    if "classic" in sweet_style:
        scores["classic_boozy"] += 3
        scores["citrus_fresh"] += 1
    if "floral" in sweet_style:
        scores["berry"] += 3
        scores["dessert"] += 3
    if "rich" in sweet_style:
        scores["dessert"] += 4
        scores["candy_fun"] += 2
    if "zesty" in sweet_style:
        scores["citrus_fresh"] += 3
        scores["tropical"] += 2

    abv = (responses.get("abv_lane") or "").lower()
    if "strong" in abv:
        scores["classic_boozy"] += 3
        scores["tropical"] += 1
    if "medium" in abv:
        scores["tropical"] += 1
        scores["citrus_fresh"] += 1
        scores["berry"] += 1
    if "low" in abv:
        scores["candy_fun"] += 2
        scores["citrus_fresh"] += 2

    carbonation = (responses.get("carbonation_texture") or "").lower()
    if "sparkling" in carbonation or "fizzy" in carbonation:
        scores["tropical"] += 2
        scores["citrus_fresh"] += 2
        scores["candy_fun"] += 2
        scores["classic_boozy"] -= 1
    if "still" in carbonation:
        scores["classic_boozy"] += 2
        scores["dessert"] += 1

    if all(score == 0 for score in scores.values()):
        if "rum" in base:
            return "tropical"
        if "tequila" in base:
            return "classic_boozy"
        if "gin" in base:
            return "citrus_fresh"
        return "tropical"

    return max(scores.items(), key=lambda kv: kv[1])[0]

def _has_any_term(items: Sequence[StockItem], terms: Sequence[str]) -> bool:
    terms_l = [t.lower() for t in terms]
    for it in items:
        name = (it.name or "").lower()
        if any(t in name for t in terms_l):
            return True
    return False

def _pick_first_matching(candidates: Sequence[StockItem], keywords: Iterable[str]) -> Optional[StockItem]:
    lowered = [kw.lower() for kw in keywords]
    for cand in candidates:
        name = cand.name.lower()
        if any(kw in name for kw in lowered):
            return cand
    return candidates[0] if candidates else None


def is_creamy(item: StockItem) -> bool:
    name = item.name.lower()
    creamy_keywords = [
        "cream",
        "baileys",
        "irish cream",
        "milk",
        "custard",
        "egg white",
        "eggwhite",
        "egg ",
    ]
    return any(keyword in name for keyword in creamy_keywords)


def _is_sour(item: StockItem) -> bool:
    name = item.name.lower()
    return item.role == "sour" or "lemon" in name or "lime" in name or "sour" in name


def _is_mixer(item: StockItem) -> bool:
    name = item.name.lower()
    return item.role == "mixer" or "lemonade" in name or "soda" in name or "tonic" in name or "mixer" in name


def _is_core_juice(item: StockItem) -> bool:
    if item.role != "juice":
        return False
    name = item.name.lower()
    if _is_sour(item) or _is_mixer(item):
        return False
    return "juice" in name or item.category == "juice"


def _should_exclude_stock(item: StockItem, avoid_terms: Set[str]) -> bool:
    if not avoid_terms:
        return False
    name = item.name.lower()
    tags = {str(tag).lower() for tag in getattr(item, "flavour_tags", [])}
    for term in avoid_terms:
        key = _normalize(term)
        if not key:
            continue
        if key in name or any(key in tag for tag in tags):
            return True
    return False




class ProfileRecipeBuilder:
    """Build cocktails using profile-guarded stock items."""

    def __init__(self, repository, glass_logic=None) -> None:
        self.repository = repository
        self.glass_logic = glass_logic

    def build_recipe(self, responses: Dict[str, Any], profile: str, seed: int | None = None) -> CocktailRecipe:
        base_seed = seed if seed is not None else random.getrandbits(32)
        self._load_items(responses)

        last_error: Exception | None = None
        for attempt in range(1, 6):
            rnd = random.Random(base_seed + attempt)  # stable-ish rerolls
            try:
                return self._build_single_recipe(responses, profile, rnd)
            except GuardrailReject as e:
                last_error = e
                continue
            except Exception as e:
                last_error = e
                continue

        if last_error:
            raise last_error
        raise ValueError("Failed to build recipe.")

    def build_candidates(
        self,
        responses: Dict[str, Any],
        profile: str,
        seed: int | None = None,
        num_candidates: int = 3,
        max_attempts: int = 10,
    ) -> List[CocktailRecipe]:
        base_seed = seed if seed is not None else random.getrandbits(32)
        rnd = random.Random(base_seed)
        self._load_items(responses)
        candidates: List[CocktailRecipe] = []
        attempts = 0

        while len(candidates) < num_candidates and attempts < max_attempts:
            attempts += 1
            recipe_seed = rnd.getrandbits(32)
            candidate = self._build_single_recipe(responses, profile, random.Random(recipe_seed))
            if all(_ingredient_overlap_ratio(candidate, existing) <= MAX_SHARED_INGREDIENT_RATIO for existing in candidates):
                candidates.append(candidate)

        return candidates

    def _load_items(self, responses: Dict[str, Any]) -> List[StockItem]:
        stock = (
            self.repository.prime_cache(responses.get("bar_id", ""))
            if hasattr(self.repository, "prime_cache")
            else self.repository.load_bar_stock(responses.get("bar_id", ""))
        )
        raw_items = getattr(self.repository, "_all_items", stock)

        tokenised_allergens = _tokenise_values(responses.get("allergens"))
        filtered_tokens = {token for token in tokenised_allergens if token not in _NON_RESPONSE_TOKENS}
        avoid_terms = set(_expand_avoid_terms(filtered_tokens))

        notes = responses.get("notes") or ""
        if isinstance(notes, str) and notes.strip():
            avoid_terms.update(_extract_avoid_terms(notes))

        filtered_items = [
            item
            for item in raw_items
            if not is_creamy(item) and not _should_exclude_stock(item, avoid_terms)
        ]

        cache: Dict[str, List[StockItem]] = defaultdict(list)
        for item in filtered_items:
            if not item.profiles and not item.neutral:
                continue
            for prof in item.profiles or {"neutral"}:
                cache[prof].append(item)
            if item.neutral:
                cache.setdefault("neutral", []).append(item)

        self.repository._all_items_cache = filtered_items  # type: ignore[attr-defined]
        self.repository._profile_cache = cache  # type: ignore[attr-defined]

        return filtered_items

    def _build_single_recipe(
        self,
        responses: Dict[str, Any],
        profile: str,
        rnd: random.Random,
    ) -> CocktailRecipe:
        abv_lane = (responses.get("abv_lane") or "medium").strip().lower()
        base_target = {"strong": 60.0, "medium": 50.0, "low": 40.0}.get(abv_lane, 50.0)

        carbonation = (responses.get("carbonation_texture") or "still").strip().lower()
        glass = self._choose_glass(responses, carbonation)

        template = select_template(responses)
        spec = TEMPLATE_SPECS[template]

        def profile_items(role: str) -> List[StockItem]:
            items = [item for item in self.repository.items_for_profile(profile, role=role) if not is_creamy(item)]
            if role != "garnish":
                items.extend([i for i in self.repository.neutral_items(role=role) if not is_creamy(i)])
            rnd.shuffle(items)
            unique = []
            seen = set()
            for item in items:
                key = item.name.lower()
                if key in seen:
                    continue
                seen.add(key)
                unique.append(item)
            return unique

        prefs = self._profile_preferences(profile, responses)

        base = self._choose_base(responses, profile, profile_items("base"), prefs["base_keywords"], prefs)
        if base is None:
            raise ValueError("No base spirit available for the selected profile.")

        base_family = extract_spirit_family(base.name) or ""

        juice_pool = profile_items("juice")
        core_juices = [i for i in juice_pool if _is_core_juice(i)]

        base_spirit = (responses.get("base_spirit") or "").lower()
        aroma = (responses.get("aroma_preference") or "").strip().lower()
        sweet_style = (responses.get("sweetener_question") or "").strip().lower()

        # Keep gin + citrus_fresh sharp: avoid pineapple juice in this lane
        if base_spirit == "gin" and profile == "citrus_fresh" and ("citrus" in aroma or "zesty" in sweet_style):
            core_juices = [j for j in core_juices if "pineapple" not in j.name.lower()]

        juices = self._choose_juices(core_juices, prefs["juice_keywords"], prefs.get("juice_priority"), rnd, limit=spec["max_juices"])

        sweetener, sweet_ml, flavoured_spirit, flavoured_ml = self._choose_sweet_components(
            profile,
            base_family,
            profile_items("sweetener"),
            profile_items("base"),
            abv_lane,
            rnd,
            aroma_preference=responses.get("aroma_preference") or "",
        )

        # Modifier keyword adjustments (floral prefer elderflower etc.)
        modifier_keywords = list(prefs.get("modifier_keywords") or [])
        sweetener_keywords = list(prefs.get("sweetener_keywords") or [])

        if aroma == "floral":
            floral_terms = ("elderflower", "st germain", "st-germain", "stgermain")
            modifiers_pool = profile_items("modifier")
            sweeteners_pool = profile_items("sweetener")

            if _has_any_term(modifiers_pool, floral_terms):
                modifier_keywords = ["elderflower", "st germain"] + [
                    k for k in modifier_keywords
                    if "elder" not in k.lower() and "germain" not in k.lower()
                ]
            if _has_any_term(sweeteners_pool, floral_terms):
                sweetener_keywords = ["elderflower", "st germain"] + [
                    k for k in sweetener_keywords
                    if "elder" not in k.lower() and "germain" not in k.lower()
                ]

        modifier = self._choose_modifier(profile_items("modifier"), modifier_keywords)

        available_sours = profile_items("sour") + [j for j in juice_pool if _is_sour(j)]
        sour = self._maybe_add_sour(profile, available_sours, bool(spec["needs_sour"]))

        sour_ml = 0.0 if sour is None else prefs["sour_ml"]
        modifier_ml = 0.0 if modifier is None else 15.0

        base_ml = base_target - modifier_ml
        if flavoured_spirit and flavoured_spirit.category == "spirit":
            base_ml = max(25.0, base_target - flavoured_ml)
        if abv_lane == "low":
            base_ml = max(25.0, min(base_ml, 40.0))
        else:
            base_ml = max(40.0, min(base_ml, 60.0))

        if flavoured_spirit and flavoured_spirit.category == "spirit":
            total_spirits = base_ml + flavoured_ml
            if total_spirits > 60.0:
                excess = total_spirits - 60.0
                base_ml = max(25.0, base_ml - excess)
            if total_spirits < 40.0:
                base_ml = max(25.0, 40.0 - flavoured_ml)

        juice_amounts = self._assign_juice_amounts(juices, carbonation, prefs["juice_ml"])

        sweetness_load = self._estimate_sweetness_load(
            sweetener,
            sweet_ml,
            flavoured_spirit,
            flavoured_ml,
            modifier,
            modifier_ml,
            juices,
            juice_amounts,
        )

        if sweetness_load >= 25.0:
            if sour is None and available_sours:
                sour = available_sours[0]
                sour_ml = prefs["sour_ml"] if prefs.get("needs_sour", True) else 15.0
            if sour:
                sour_ml = max(15.0, min(max(sour_ml, 15.0), 25.0))
        elif sour:
            sour_ml = max(10.0, sour_ml)

        juice_amounts, sour_ml = self._rebalance_juices_and_sour(
            juices, juice_amounts, sour_ml, glass, prefs["juice_ml"]
        )

        juices, juice_amounts, sour_ml = self._apply_citrus_rules(
            profile, juices, juice_amounts, sour_ml, responses
        )

        juice_amounts = self._apply_thickness_guard(carbonation, juices, juice_amounts, sweetness_load)

        spirit_extra = flavoured_ml if flavoured_spirit and flavoured_spirit.category == "spirit" else 0.0
        core_volume = base_ml + modifier_ml + sweet_ml + sour_ml + sum(juice_amounts) + spirit_extra

        bitterness = (responses.get("bitterness_tolerance") or "").strip().lower()
        mixer_keywords = list(prefs.get("mixer_keywords") or [])

        # Tonic only for high bitterness top-up
        if bitterness == "high":
            if "tonic" not in mixer_keywords:
                mixer_keywords.append("tonic")
        else:
            mixer_keywords = [k for k in mixer_keywords if "tonic" not in k.lower()]

        # ----------------------------
        # TEMPLATE-DRIVEN MIXER LOGIC
        # ----------------------------
        mixer_item: Optional[StockItem] = None
        mixer_ml = 0.0

        if spec["needs_mixer"]:
            # Only attempt a top-up when template requires it
            if glass.sparkling:
                mixer_item = self._select_mixer(profile_items("mixer"), mixer_keywords, carbonation)
                space = max(0.0, glass.capacity_ml - core_volume)
                target_min = 80.0 if carbonation.startswith("properly") else 40.0
                if mixer_item:
                    mixer_ml = max(target_min, space)

            elif glass.capacity_ml - core_volume > 40 and carbonation.startswith("lightly"):
                mixer_item = self._select_mixer(profile_items("mixer"), mixer_keywords, carbonation)
                if mixer_item:
                    mixer_ml = max(25.0, min(60.0, glass.capacity_ml - core_volume))

            if carbonation.startswith("properly") and (mixer_item is None or not _is_mixer(mixer_item)):
                fallback_mixers = self.repository.neutral_items(role="mixer") + profile_items("mixer")
                mixer_item = self._select_mixer(fallback_mixers, mixer_keywords, carbonation)
                if mixer_item:
                    space = max(0.0, glass.capacity_ml - core_volume)
                    mixer_ml = max(80.0, space)

            if mixer_item is None and glass.sparkling and not carbonation.startswith("properly"):
                # last fallback: use extra juice as a "lengthener"
                fallback = _pick_first_matching(profile_items("juice"), prefs["juice_keywords"]) if juices else None
                if fallback:
                    mixer_item = fallback
                    mixer_ml = max(25.0, glass.capacity_ml - core_volume)
        else:
            # Template says NO TOP (SOUR/MARTINI_UP/OLD_FASHIONED/TIKI_SHAKEN)
            mixer_item = None
            mixer_ml = 0.0

        suggestions: List[IngredientSuggestion] = [IngredientSuggestion(base, base_ml, "base")]
        if flavoured_spirit:
            role = flavoured_spirit.role if flavoured_spirit.role in {"modifier", "base"} else "modifier"
            suggestions.append(IngredientSuggestion(flavoured_spirit, flavoured_ml, role))
        if modifier:
            suggestions.append(IngredientSuggestion(modifier, modifier_ml, "modifier"))
        if sweetener:
            suggestions.append(IngredientSuggestion(sweetener, sweet_ml, "sweetener"))
        for juice, amount in zip(juices, juice_amounts):
            suggestions.append(IngredientSuggestion(juice, amount, "juice"))
        if sour:
            suggestions.append(IngredientSuggestion(sour, sour_ml, "sour"))
        if mixer_item and mixer_ml > 0:
            suggestions.append(IngredientSuggestion(mixer_item, mixer_ml, "mixer"))

        # Keep your quick MVC safety net (guardrails will also enforce)
        has_juice = any(s.role == "juice" for s in suggestions)
        has_sour = any(s.role == "sour" for s in suggestions)
        if (not has_juice) and (not has_sour):
            fallback_pool = self.repository.neutral_items(role="juice") + profile_items("juice")
            fallback = _pick_first_matching(fallback_pool, ["orange", "apple", "cranberry"])
            if fallback:
                suggestions.append(IngredientSuggestion(fallback, fallback.default_measure_ml or 30.0, "juice"))

        used_fallback = False
        relaxed_profile = profile

        try:
            self._validate(profile, suggestions)
        except ValueError:
            if self._should_relax_base(responses, profile, suggestions):
                relaxed_profile, suggestions, used_fallback = self._apply_base_relaxation(
                    responses, profile, suggestions
                )
                self._validate(relaxed_profile, suggestions, allow_relaxed=True)
            else:
                raise

        # --- GUARDRAILS PASS (fix / validate / reject) ---
        glass, suggestions, garnish, steps, fixes = self._apply_guardrails(
            responses=responses,
            profile=relaxed_profile,
            glass=glass,
            suggestions=suggestions,
        )
        ice = self._ensure_ice_program(glass, responses)

        meta: Dict[str, object] = {"used_fallback": True} if used_fallback else {}
        if fixes:
            meta["guardrail_fixes"] = fixes

        return CocktailRecipe(
            name="Signature Serve",
            glassware=glass.name,
            ice=ice,
            ingredients=suggestions,
            steps=steps,
            flavour_profile=[(relaxed_profile, 1.0)],
            garnish=garnish,
            notes=None,
            explanations=(),
            meta=meta,
        )


    def _profile_preferences(self, profile: str, responses: Dict[str, Any] | None = None) -> Dict[str, Any]:
        defaults = {
            "base_keywords": ["vodka"],
            "juice_keywords": ["orange"],
            "sweetener_keywords": ["simple"],
            "modifier_keywords": [],
            "mixer_keywords": ["lemonade"],
            "garnish_keywords": ["twist"],
            "needs_sour": True,
            "sour_ml": 12.0,
            "juice_ml": (25.0, 40.0),
            "juice_priority": None,
        }
        profiles: Dict[str, Dict[str, Any]] = {
            "tropical": {
                "base_keywords": ["rum", "vodka"],
                "juice_keywords": ["pineapple", "orange", "passion", "cranberry"],
                "sweetener_keywords": ["grenadine", "vanilla", "passion", "coconut"],
                "modifier_keywords": ["liqueur", "schnapps", "falernum"],
                "mixer_keywords": ["lemonade"],
                "garnish_keywords": ["orange", "pineapple", "mint"],
                "juice_priority": ["orange", "cranberry", "passion", "pineapple"],
            },
            "berry": {
                "base_keywords": ["gin", "vodka"],
                "juice_keywords": ["cranberry", "berry"],
                "sweetener_keywords": ["rasp", "straw", "grenadine"],
                "mixer_keywords": ["lemonade"],
                "garnish_keywords": ["berry", "lemon"],
            },
            "citrus_fresh": {
                "base_keywords": ["gin", "vodka"],
                "juice_keywords": ["lemon", "lime", "orange", "cranberry"],
                "sweetener_keywords": ["simple", "sugar"],
                "mixer_keywords": ["soda", "light lemonade"],
                "garnish_keywords": ["lemon", "lime"],
            },
            "classic_boozy": {
                "base_keywords": ["tequila", "bourbon", "whiskey"],
                "juice_keywords": ["orange", "passion"],
                "sweetener_keywords": ["simple", "grenadine"],
                "mixer_keywords": [],
                "garnish_keywords": ["orange", "twist"],
                "needs_sour": False,
                "juice_ml": (15.0, 30.0),
            },
            "candy_fun": {
                "base_keywords": ["vodka", "gin"],
                "juice_keywords": ["cranberry", "berry", "orange"],
                "sweetener_keywords": ["blue", "grenadine", "rasp"],
                "mixer_keywords": ["lemonade"],
                "garnish_keywords": ["berry", "orange"],
            },
            "dessert": {
                "base_keywords": ["vodka", "rum"],
                "juice_keywords": ["passion", "pineapple"],
                "sweetener_keywords": ["vanilla", "caramel", "passion"],
                "modifier_keywords": ["coffee", "cocoa"],
                "mixer_keywords": ["soda", "lemonade"],
                "garnish_keywords": ["orange", "passion", "cherry"],
                "needs_sour": True,
                "sour_ml": 10.0,
                "juice_ml": (20.0, 30.0),
            },
        }
        prefs = {**defaults, **profiles.get(profile, {})}

        house = (responses or {}).get("house_type")
        if house:
            house_lower = house.lower()
            if "beach" in house_lower and profile == "tropical":
                prefs["juice_priority"] = ["orange", "cranberry", "passion", "pineapple"]
            if "tree" in house_lower and not prefs.get("juice_priority"):
                prefs["juice_priority"] = ["orange", "cranberry", "lemon"]

        return prefs

    def _choose_glass(self, responses: Dict[str, Any], carbonation: str) -> Glass:
        mapping = {
            "beach house": Glass("long glass", 400, sparkling=True),
            "modern house": Glass("martini glass", 250, sparkling=False),
            "haunted house": Glass("skull glass", 400, sparkling=False),
            "tree house": Glass("gin glass", 500, sparkling=True),
        }

        house_key = (responses.get("house_type") or "").strip().lower()
        base_glass = mapping.get(house_key)

        if base_glass and house_key == "haunted house":
            return base_glass

        seed_raw = responses.get("seed")
        try:
            seed = int(seed_raw) if seed_raw is not None else random.getrandbits(32)
        except (TypeError, ValueError):
            seed = random.getrandbits(32)
        rnd = random.Random(seed)

        if base_glass:
            if house_key == "beach house":
                if (carbonation.startswith("properly") or carbonation.startswith("light")) and rnd.random() < 0.25:
                    return Glass("gin glass", 500, sparkling=True)
                return base_glass

            if house_key == "modern house":
                if not (carbonation.startswith("properly") or carbonation.startswith("light")) and rnd.random() < 0.25:
                    return Glass("chilled coupe", 250, sparkling=False)
                return base_glass

            if house_key == "tree house":
                if rnd.random() < 0.20:
                    return Glass("rocks glass", 300, sparkling=True)
                return base_glass

            return base_glass

        if carbonation.startswith("properly") or carbonation.startswith("light"):
            return Glass("long glass", 400, sparkling=True)
        return Glass("martini glass", 250, sparkling=False)

    def _choose_base(
        self,
        responses: Dict[str, Any],
        profile: str,
        items: Sequence[StockItem],
        keywords: Sequence[str],
        prefs: Dict[str, Any],
    ) -> Optional[StockItem]:
        desired = (responses.get("base_spirit") or "").lower()

        def subtype_score(item: StockItem) -> float:
            subtype = getattr(item, "spirit_subtype", None) or ""
            family = extract_spirit_family(item.name) or ""
            score = 0.0
            if family == "rum":
                if subtype in {"spiced", "dark", "anejo"}:
                    score += 2.0 if profile in {"tropical", "dessert", "candy_fun", "classic_boozy"} else 1.0
                if subtype in {"light"}:
                    score += 2.0 if profile in {"citrus_fresh", "berry"} else 0.5
            return score

        if desired:
            desired_items = [item for item in items if desired in item.name.lower() or desired in item.category.lower() or desired in item.role]
            if desired_items:
                return max(desired_items, key=subtype_score)
            fallback_pool = [
                item
                for item in getattr(self.repository, "_all_items", [])
                if item.role == "base" and desired in item.name.lower() and not is_creamy(item)
            ]
            if fallback_pool:
                return max(fallback_pool, key=subtype_score)

        ranked = sorted(items, key=subtype_score, reverse=True)
        preferred = _pick_first_matching(ranked, keywords)
        return preferred or (ranked[0] if ranked else None)

    def _choose_juices(
        self,
        items: Sequence[StockItem],
        keywords: Sequence[str],
        priority: Optional[Sequence[str]],
        rnd: random.Random,
        limit: int,
    ) -> List[StockItem]:
        filtered = [i for i in items if not is_creamy(i)]
        picks: List[StockItem] = []
        ordered_keywords = list(priority or []) + [kw for kw in keywords if kw not in (priority or [])]
        for keyword in ordered_keywords:
            pool = [i for i in filtered if i not in picks]
            match = _pick_first_matching(pool, [keyword])
            if match and match not in picks:
                picks.append(match)
            if len(picks) >= limit:
                break
        if len(picks) < limit:
            remainder = [i for i in filtered if i not in picks]
            rnd.shuffle(remainder)
            picks.extend(remainder[: limit - len(picks)])

        pineapples = [p for p in picks if "pineapple" in p.name.lower()]
        if pineapples and len(picks) > 1:
            primary_candidates = [p for p in picks if p not in pineapples]
            if primary_candidates:
                picks = primary_candidates[:1] + pineapples[:1]
        return picks[:limit]

    def _choose_sweet_components(
        self,
        profile: str,
        base_family: str,
        sweeteners: Sequence[StockItem],
        base_pool: Sequence[StockItem],
        abv_lane: str,
        rnd: random.Random,
        aroma_preference: str = "",
    ) -> Tuple[Optional[StockItem], float, Optional[StockItem], float]:
        sweeteners = [s for s in sweeteners if not is_creamy(s)]
        flavour_words = PROFILE_FLAVOUR_WORDS.get(profile, [])
        matching_syrups = [s for s in sweeteners if any(word in s.name.lower() for word in flavour_words)]
        neutral_syrups = [s for s in sweeteners if s not in matching_syrups]

        if (aroma_preference or "").strip().lower() == "floral":
            floral_terms = ("elderflower", "st germain", "st-germain", "stgermain")
            elderflower_syrups = [s for s in sweeteners if any(t in s.name.lower() for t in floral_terms)]
            if elderflower_syrups:
                matching_syrups = elderflower_syrups + [s for s in matching_syrups if s not in elderflower_syrups]
                neutral_syrups = [s for s in neutral_syrups if s not in elderflower_syrups]

        flavoured_spirits: List[StockItem] = []
        if base_family:
            for word in flavour_words:
                flavoured_spirits.extend(self.repository.find_flavoured_spirits(base_family, word, profile))

        unique_spirits: List[StockItem] = []
        seen: set[str] = set()
        for spirit in flavoured_spirits:
            key = spirit.name.lower()
            if key in seen:
                continue
            seen.add(key)
            unique_spirits.append(spirit)
        flavoured_spirits = unique_spirits

        sweetener_pick: Optional[StockItem] = None
        sweet_ml = 0.0
        flavoured_pick: Optional[StockItem] = None
        flavoured_ml = 0.0

        if profile == "tropical":
            grenadine_first = [
                s
                for s in matching_syrups + neutral_syrups
                if s.category == "syrup"
                and ("grenadine" in s.name.lower() or "tropical" in (kw.lower() for kw in s.profiles))
            ]
            if grenadine_first:
                sweetener_pick = grenadine_first[0]
                sweet_ml = rnd.uniform(12, 18)
                matching_syrups = [s for s in matching_syrups if s != sweetener_pick]

        if sweetener_pick is None and matching_syrups:
            sweetener_pick = matching_syrups[0]
            sweet_ml = rnd.uniform(12, 18)
            if flavoured_spirits and abv_lane != "low":
                flavoured_pick = flavoured_spirits[0]
                flavoured_ml = 10.0 if abv_lane == "medium" else 15.0
        elif flavoured_spirits:
            flavoured_pick = flavoured_spirits[0]
            flavoured_ml = 20.0 if abv_lane == "low" else 25.0
        elif neutral_syrups:
            sweetener_pick = neutral_syrups[0]
            sweet_ml = rnd.uniform(10, 16)
        else:
            sweetener_pick = _pick_first_matching(base_pool, flavour_words)
            sweet_ml = rnd.uniform(10, 14) if sweetener_pick else 0.0

        return sweetener_pick, sweet_ml, flavoured_pick, flavoured_ml

    def _choose_modifier(self, modifiers: Sequence[StockItem], keywords: Sequence[str]) -> Optional[StockItem]:
        modifiers = [m for m in modifiers if not is_creamy(m)]
        if not modifiers:
            return None
        return _pick_first_matching(modifiers, keywords) or modifiers[0]

    def _estimate_sweetness_load(
        self,
        sweetener: Optional[StockItem],
        sweet_ml: float,
        flavoured_spirit: Optional[StockItem],
        flavoured_ml: float,
        modifier: Optional[StockItem],
        modifier_ml: float,
        juices: Sequence[StockItem],
        juice_amounts: Sequence[float],
    ) -> float:
        load = 0.0

        def sweetish(item: StockItem) -> bool:
            name = item.name.lower()
            return any(token in name for token in ["sweet", "vanilla", "caramel", "passion", "pineapple", "grenadine", "rasp", "straw", "berry", "blue", "maple", "honey"])

        if sweetener:
            load += sweet_ml
        if flavoured_spirit and sweetish(flavoured_spirit):
            load += flavoured_ml
        if modifier and sweetish(modifier):
            load += modifier_ml
        for juice, amt in zip(juices, juice_amounts):
            if sweetish(juice):
                load += amt * 0.5
        return load

    def _maybe_add_sour(self, profile: str, sours: Sequence[StockItem], needs_sour: bool) -> Optional[StockItem]:
        if not needs_sour:
            return None
        return sours[0] if sours else None

    def _assign_juice_amounts(self, juices: Sequence[StockItem], carbonation: str, ml_range: tuple[float, float]) -> List[float]:
        if not juices:
            return []
        base, high = ml_range
        amounts = []
        for idx, _ in enumerate(juices):
            amt = high - idx * 5
            if carbonation.startswith("properly"):
                amt = max(base, amt - 5)
            amounts.append(max(base, amt))
        return amounts

    def _rebalance_juices_and_sour(
        self,
        juices: Sequence[StockItem],
        juice_amounts: Sequence[float],
        sour_ml: float,
        glass: Glass,
        ml_range: tuple[float, float],
    ) -> tuple[List[float], float]:
        if not juices:
            return list(juice_amounts), sour_ml

        target_low, target_high = 70.0, 100.0
        if glass.capacity_ml < 350:
            target_low, target_high = 40.0, 80.0
        juice_list = list(juice_amounts)
        pre_mixer = sum(juice_list) + sour_ml
        base_min, base_max = ml_range

        if pre_mixer == 0:
            return juice_list, sour_ml

        def clamp(amount: float, minimum: float, maximum: float) -> float:
            return max(minimum, min(maximum, amount))

        if pre_mixer < target_low:
            scale = target_low / pre_mixer
            juice_list = [clamp(amt * scale, base_min, max(base_max, base_min)) for amt in juice_list]
            sour_ml = clamp(sour_ml * scale, 10.0, 25.0)
        elif pre_mixer > target_high:
            scale = target_high / pre_mixer
            juice_list = [clamp(amt * scale, base_min, base_max) for amt in juice_list]
            sour_ml = clamp(sour_ml * scale, 10.0, 25.0)

        juice_list = self._cap_pineapple_ratio(juices, juice_list, sour_ml)
        return juice_list, sour_ml

    def _apply_citrus_rules(
        self,
        profile: str,
        juices: List[StockItem],
        juice_amounts: List[float],
        sour_ml: float,
        responses: Dict[str, Any],
    ) -> tuple[List[StockItem], List[float], float]:
        bitterness = (responses.get("bitterness_tolerance") or "").lower()
        citrus_limit = None
        require_soft = False
        lime_cap = None
        if profile in {"dessert", "candy_fun"}:
            citrus_limit = 45.0
            lime_cap = 10.0 if "high" not in bitterness else None
            require_soft = True
        elif profile == "classic_boozy":
            citrus_limit = 30.0
            lime_cap = 10.0 if "high" not in bitterness else None
        elif profile == "citrus_fresh":
            citrus_limit = 70.0

        def citrus_total(amounts: Sequence[float]) -> float:
            return sum(amount for juice, amount in zip(juices, amounts) if _is_citrus_juice(juice)) + sour_ml

        adjusted_amounts = list(juice_amounts)

        if lime_cap is not None:
            for idx, juice in enumerate(juices):
                if "lime" in juice.name.lower() or "lemon" in juice.name.lower():
                    adjusted_amounts[idx] = min(adjusted_amounts[idx], lime_cap)

        if citrus_limit is not None and citrus_total(adjusted_amounts) > citrus_limit:
            priority = ["cranberry", "orange", "lemon", "lime"]
            for keyword in priority:
                if citrus_total(adjusted_amounts) <= citrus_limit:
                    break
                for idx, juice in enumerate(list(juices)):
                    if keyword in juice.name.lower() and adjusted_amounts[idx] > 0:
                        reduction = min(adjusted_amounts[idx], citrus_total(adjusted_amounts) - citrus_limit)
                        adjusted_amounts[idx] = max(0.0, adjusted_amounts[idx] - reduction)
                        if adjusted_amounts[idx] < 8.0 and len(juices) > 1:
                            juices.pop(idx)
                            adjusted_amounts.pop(idx)
                        break

        if profile == "classic_boozy" and len(juices) >= 2:
            names = [j.name.lower() for j in juices]
            if all(term in " ".join(names) for term in ["orange", "cranberry", "lime"]):
                drop_idx = next((i for i, j in enumerate(juices) if "cranberry" in j.name.lower()), 1)
                juices.pop(drop_idx)
                adjusted_amounts.pop(drop_idx)

        if require_soft:
            soft_keywords = ("pineapple", "passion", "mango", "coconut")
            has_soft = any(any(kw in juice.name.lower() for kw in soft_keywords) for juice in juices)
            if not has_soft:
                pool = [
                    j
                    for j in self.repository.items_for_profile(profile, role="juice")
                    if any(kw in j.name.lower() for kw in soft_keywords)
                ]
                if pool:
                    juices = juices[:1] + [pool[0]] if juices else [pool[0]]
                    adjusted_amounts = adjusted_amounts[:1] + [max(25.0, adjusted_amounts[0] if adjusted_amounts else 25.0)]
        return juices, adjusted_amounts, sour_ml

    def _apply_thickness_guard(
        self,
        carbonation: str,
        juices: List[StockItem],
        juice_amounts: List[float],
        sweet_ml: float,
    ) -> List[float]:
        if carbonation.startswith("still"):
            return juice_amounts
        thick_ml = sum(amount for juice, amount in zip(juices, juice_amounts) if _is_thick_juice(juice))
        if thick_ml + sweet_ml <= 50.0:
            return juice_amounts
        adjusted = list(juice_amounts)
        for idx, juice in enumerate(juices):
            if _is_thick_juice(juice):
                adjusted[idx] = max(10.0, adjusted[idx] - 12.0)
        return adjusted

    def _cap_pineapple_ratio(
        self,
        juices: Sequence[StockItem],
        juice_amounts: Sequence[float],
        sour_ml: float,
        max_ratio: float = 0.25,
    ) -> List[float]:
        pineapple_indices = [idx for idx, juice in enumerate(juices) if "pineapple" in juice.name.lower()]
        if not pineapple_indices:
            return list(juice_amounts)

        total_juice = sum(juice_amounts) + sour_ml
        if total_juice <= 0:
            return list(juice_amounts)

        pineapple_total = sum(juice_amounts[idx] for idx in pineapple_indices)
        allowed = max_ratio * total_juice
        if pineapple_total <= allowed:
            return list(juice_amounts)

        scale = allowed / pineapple_total if pineapple_total > 0 else 1.0
        adjusted = list(juice_amounts)
        reclaimed = 0.0
        for idx in pineapple_indices:
            new_amt = adjusted[idx] * scale
            reclaimed += adjusted[idx] - new_amt
            adjusted[idx] = new_amt

        if reclaimed > 0:
            non_pineapple = [i for i in range(len(adjusted)) if i not in pineapple_indices]
            if non_pineapple:
                share = reclaimed / len(non_pineapple)
                for idx in non_pineapple:
                    adjusted[idx] += share

        return adjusted

    def _select_mixer(self, mixers: Sequence[StockItem], keywords: Sequence[str], carbonation: str) -> Optional[StockItem]:
        fizz_keywords = ("lemonade", "soda", "tonic", "ginger")
        if carbonation.startswith("properly"):
            mixers = [m for m in mixers if any(token in m.name.lower() for token in fizz_keywords)]
        if not mixers and not carbonation.startswith("properly"):
            return None
        target_keywords = list(keywords)
        return _pick_first_matching(list(mixers), target_keywords) if mixers else None

    def _pick_garnish(self, garnishes: Sequence[StockItem], juices: Sequence[StockItem], keywords: Sequence[str]) -> str:
        choices = list(garnishes)
        if not choices:
            return ""

        if juices:
            juice_text = " ".join((j.name or "").lower() for j in juices)
            priority = []
            if "pineapple" in juice_text:
                priority.append("pineapple")
            if "passion" in juice_text:
                priority.append("passion")
            if "orange" in juice_text:
                priority.append("orange")
            if "cranberry" in juice_text:
                priority.append("cranberry")
            if "lime" in juice_text:
                priority.append("lime")
            if "lemon" in juice_text:
                priority.append("lemon")
            for want in priority:
                for cand in choices:
                    if want in cand.name.lower():
                        return cand.name

        lowered_keywords = [k.lower() for k in (keywords or []) if k]
        if lowered_keywords:
            for cand in choices:
                name = cand.name.lower()
                if any(k in name for k in lowered_keywords):
                    return cand.name

        return choices[0].name

    # -----------------------------
    # Guardrails helpers
    # -----------------------------

    def _is_martini_style_glass(self, glass_name: str) -> bool:
        g = (glass_name or "").lower()
        return ("martini" in g) or ("coupe" in g)

    def _count_components(self, suggestions: Sequence[IngredientSuggestion]) -> int:
        return sum(1 for s in suggestions if s.role not in {"garnish"})

    def _remove_mixers(self, suggestions: List[IngredientSuggestion]) -> tuple[List[IngredientSuggestion], bool]:
        before = len(suggestions)
        suggestions = [s for s in suggestions if s.role != "mixer"]
        return suggestions, (len(suggestions) != before)

    def _ensure_ice_program(self, glass: Glass, responses: Dict[str, Any]) -> str:
        if self._is_martini_style_glass(glass.name):
            return "none"
        return "cubed"

    def _pick_garnish_guardrailed(
        self,
        responses: Dict[str, Any],
        profile: str,
        garnishes: Sequence[StockItem],
        suggestions: Sequence[IngredientSuggestion],
    ) -> str:
        if not garnishes:
            return ""

        gchoices = list(garnishes)

        names = " ".join((s.ingredient.name or "").lower() for s in suggestions)

        # colour-ish cues (rough but works well in practice)
        colour_priority: list[str] = []
        if any(t in names for t in ("blue", "curaçao", "curacao")):
            colour_priority.append("blue")
        if any(t in names for t in ("violet", "purple")):
            colour_priority.append("violet")
        if any(t in names for t in ("cranberry", "raspberry", "strawberry", "cherry", "grenadine")):
            colour_priority.append("berry")
        if any(t in names for t in ("mint", "lime", "kiwi")):
            colour_priority.append("mint")
        if any(t in names for t in ("pineapple", "passion", "mango")):
            colour_priority.append("pineapple")
        if "orange" in names:
            colour_priority.append("orange")
        if any(t in names for t in ("lemon", "lime")):
            colour_priority.append("lemon")

        aroma = (responses.get("aroma_preference") or "").strip().lower()
        sweet_style = (responses.get("sweetener_question") or "").strip().lower()
        carbonation = (responses.get("carbonation_texture") or "").strip().lower()

        palette_priority: list[str] = []
        if "fresh" in sweet_style or "zesty" in sweet_style or "citrus" in aroma:
            palette_priority += ["mint", "lemon", "lime"]
        if profile == "tropical":
            palette_priority += ["pineapple", "orange", "mint"]
        if "woody" in aroma or "wood" in aroma:
            palette_priority += ["orange"]

        has_juice = any(s.role == "juice" for s in suggestions)
        if carbonation.startswith("still") and has_juice:
            palette_priority = ["mint"] + palette_priority

        priority: list[str] = []
        for token in (colour_priority + palette_priority):
            if token and token not in priority:
                priority.append(token)

        for tok in priority:
            for cand in gchoices:
                if tok in cand.name.lower():
                    return cand.name

        return gchoices[0].name

    def _build_steps_guardrailed(
        self,
        glass: Glass,
        responses: Dict[str, Any],
        suggestions: Sequence[IngredientSuggestion],
    ) -> List[str]:
        carbonation = (responses.get("carbonation_texture") or "").strip().lower()
        is_martini = self._is_martini_style_glass(glass.name)

        mixer = next((s for s in suggestions if s.role == "mixer"), None)

        if is_martini:
            return [
                "Add ingredients to a shaker with ice and shake hard.",
                f"Strain into a chilled {glass.name}.",
                "Garnish and serve.",
            ]

        if carbonation.startswith("still"):
            return [
                "Add ingredients to a shaker with ice and shake hard.",
                f"Strain into an ice-filled {glass.name}.",
                "Garnish and serve.",
            ]

        steps = [
            f"Fill a {glass.name} with cubed ice.",
            "Add spirits, syrups, juices and sour. Give a brief stir.",
        ]
        if mixer:
            steps.append(f"Top with {mixer.ingredient.name}.")
        steps.append("Garnish and serve.")
        return steps

    def _apply_guardrails(
        self,
        responses: Dict[str, Any],
        profile: str,
        glass: Glass,
        suggestions: List[IngredientSuggestion],
    ) -> tuple[Glass, List[IngredientSuggestion], str, List[str], List[str]]:
        fixes: List[str] = []
        reasons: List[str] = []

        carbonation = (responses.get("carbonation_texture") or "").strip().lower()
        aroma = (responses.get("aroma_preference") or "").strip().lower()
        
        is_martini = self._is_martini_style_glass(glass.name)
        
        # TEMPLATE ENFORCEMENT (template-first)
        template = select_template(responses)
        spec = TEMPLATE_SPECS[template]
        
        # If template says no mixer, strip it
        if not spec["needs_mixer"]:
            suggestions, removed = self._remove_mixers(suggestions)
            if removed:
                fixes.append("REMOVED_MIXER_FOR_TEMPLATE")


        # 1) Still & silky must not have carbonated mixers
        if carbonation.startswith("still"):
            suggestions, removed = self._remove_mixers(suggestions)
            if removed:
                fixes.append("REMOVED_MIXER_FOR_STILL")

        # 2) Martini rules: no top (always shaken)
        if is_martini:
            suggestions, removed = self._remove_mixers(suggestions)
            if removed:
                fixes.append("REMOVED_MIXER_FOR_MARTINI")

        # 3) Minimum viable cocktail (>=5 components) with autofill
        if self._count_components(suggestions) < 5:
            fixes.append("UNDERBUILT_AUTOFILL")

            has_juice = any(s.role == "juice" for s in suggestions)
            has_sour = any(s.role == "sour" for s in suggestions)
            has_sweet = any(s.role == "sweetener" for s in suggestions)

            if not has_juice and not has_sour:
                pool = self.repository.neutral_items(role="juice") + self.repository.items_for_profile(profile, role="juice")
                fallback = _pick_first_matching(pool, ["orange", "apple", "cranberry", "pineapple"])
                if fallback:
                    suggestions.append(IngredientSuggestion(fallback, fallback.default_measure_ml or 30.0, "juice"))
                    fixes.append("ADDED_CORE_JUICE")

            has_sour = any(s.role == "sour" for s in suggestions)
            if has_sweet and not has_sour:
                pool = self.repository.neutral_items(role="sour") + self.repository.items_for_profile(profile, role="sour")
                fallback = _pick_first_matching(pool, ["lemon", "lime"])
                if fallback:
                    suggestions.append(IngredientSuggestion(fallback, fallback.default_measure_ml or 15.0, "sour"))
                    fixes.append("ADDED_CORE_SOUR")

        # 4) Woody coherence (simple ban-list)
        if "wood" in aroma or "woody" in aroma:
            banned = ("malibu", "passion", "pineapple syrup", "bubblegum", "midori")
            if any(b in s.ingredient.name.lower() for s in suggestions for b in banned):
                reasons.append("WOODY_TROPICAL_CONFLICT")

        # Final rejection if still bad
        if self._count_components(suggestions) < 5:
            reasons.append("UNDERBUILT")

        garnish_items = self.repository.items_for_profile(profile, role="garnish")
        garnish = self._pick_garnish_guardrailed(responses, profile, garnish_items, suggestions)
        if garnish == "" and garnish_items:
            garnish = garnish_items[0].name

        steps = self._build_steps_guardrailed(glass, responses, suggestions)

        if reasons:
            raise GuardrailReject(reasons=reasons, fixes=fixes)

        return glass, suggestions, garnish, steps, fixes

    # -----------------------------
    # Existing validator + fallback logic
    # -----------------------------

    def _validate(
        self,
        profile: str,
        suggestions: Sequence[IngredientSuggestion],
        *,
        allow_relaxed: bool = False,
    ) -> None:
        juices = [s for s in suggestions if s.role == "juice"]
        sweeteners = [s for s in suggestions if s.role == "sweetener" and not s.ingredient.neutral]
        if len(juices) > 2:
            raise ValueError("Too many juices selected")
        if len(sweeteners) > 1:
            raise ValueError("Too many flavoured sweeteners selected")
        for suggestion in suggestions:
            item = suggestion.ingredient
            if suggestion.role in {"garnish", "mixer"}:
                continue
            if allow_relaxed:
                if profile in item.avoid_profiles:
                    raise ValueError(f"Ingredient {item.name} avoids profile {profile}")
            else:
                if profile not in item.profiles and not item.neutral:
                    raise ValueError(f"Ingredient {item.name} not compatible with profile {profile}")
                if profile in item.avoid_profiles:
                    raise ValueError(f"Ingredient {item.name} avoids profile {profile}")
            if is_creamy(item):
                raise ValueError(f"Ingredient {item.name} is creamy and not allowed")

    def _should_relax_base(
        self, responses: Dict[str, Any], profile: str, suggestions: Sequence[IngredientSuggestion]
    ) -> bool:
        desired = (responses.get("base_spirit") or "").lower().strip()
        if not desired:
            return False
        base = next((s for s in suggestions if s.role == "base"), None)
        if not base:
            return False
        item = base.ingredient
        incompatible = (profile not in item.profiles and not item.neutral) or (profile in item.avoid_profiles)
        if not incompatible:
            return False
        return desired in item.name.lower() or desired in (extract_spirit_family(item.name) or "")

    def _fallback_sweetener(
        self, profile: str, base_item: StockItem, disallow_dessert: bool
    ) -> Optional[StockItem]:
        candidates: list[StockItem] = []
        candidates.extend(self.repository.neutral_items(role="sweetener"))
        candidates.extend(self.repository.items_for_profile(profile, role="sweetener"))

        for cand in candidates:
            if disallow_dessert and getattr(cand, "dessert_only", False):
                continue
            if profile in cand.avoid_profiles:
                continue
            if is_creamy(cand):
                continue
            return cand
        return None

    def _fallback_juice(self, profile: str) -> Optional[StockItem]:
        candidates: list[StockItem] = []
        candidates.extend(self.repository.neutral_items(role="juice"))
        candidates.extend(self.repository.items_for_profile(profile, role="juice"))
        for cand in candidates:
            if profile in cand.avoid_profiles:
                continue
            if is_creamy(cand):
                continue
            return cand
        return None

    def _apply_base_relaxation(
        self,
        responses: Dict[str, Any],
        profile: str,
        suggestions: Sequence[IngredientSuggestion],
    ) -> tuple[str, List[IngredientSuggestion], bool]:
        base = next((s for s in suggestions if s.role == "base"), None)
        if base is None:
            return profile, list(suggestions), False

        base_item = base.ingredient
        base_family = extract_spirit_family(base_item.name) or ""
        used_fallback = False

        relaxed_profile = profile
        if profile not in base_item.profiles or profile in base_item.avoid_profiles:
            candidates = [p for p in base_item.profiles if p not in base_item.avoid_profiles]
            if candidates:
                relaxed_profile = next(iter(candidates))
                used_fallback = True

        disallow_dessert = "dessert" in base_item.avoid_profiles or base_family == "tequila"

        cleaned: List[IngredientSuggestion] = []
        dessert_candidates: List[IngredientSuggestion] = []

        for suggestion in suggestions:
            item = suggestion.ingredient
            lower_name = item.name.lower()
            if suggestion.role in {"sweetener", "modifier"} and getattr(item, "dessert_only", False):
                dessert_candidates.append(suggestion)
                if disallow_dessert:
                    used_fallback = True
                    continue
            if base_family == "tequila" and "amaretto" in lower_name:
                used_fallback = True
                continue
            cleaned.append(suggestion)

        dessert_only_clean = [s for s in cleaned if getattr(s.ingredient, "dessert_only", False)]
        if len(dessert_only_clean) > 1:
            keep = dessert_only_clean[0]
            cleaned = [s for s in cleaned if s not in dessert_only_clean or s is keep]
            used_fallback = True

        has_sweetener = any(s.role == "sweetener" for s in cleaned)
        if not has_sweetener:
            replacement = self._fallback_sweetener(relaxed_profile, base_item, disallow_dessert)
            if replacement:
                ml = replacement.default_measure_ml or 15.0
                cleaned.append(IngredientSuggestion(replacement, ml, "sweetener"))
                used_fallback = True
            else:
                candidate = None
                if not disallow_dessert:
                    candidate = dessert_candidates[0] if dessert_candidates else None
                else:
                    candidate = next(
                        (
                            cand
                            for cand in dessert_candidates
                            if "amaretto" not in cand.ingredient.name.lower()
                        ),
                        None,
                    )
                if candidate:
                    cleaned.append(candidate)
                    used_fallback = True

        cleaned_no_avoids: List[IngredientSuggestion] = []
        for suggestion in cleaned:
            if suggestion.role != "base" and relaxed_profile in suggestion.ingredient.avoid_profiles:
                used_fallback = True
                continue
            cleaned_no_avoids.append(suggestion)
        cleaned = cleaned_no_avoids

        if not any(s.role == "juice" for s in cleaned):
            juice_replacement = self._fallback_juice(relaxed_profile)
            if juice_replacement:
                ml = juice_replacement.default_measure_ml or 25.0
                cleaned.append(IngredientSuggestion(juice_replacement, ml, "juice"))
                used_fallback = True

        dessert_after = [s for s in cleaned if getattr(s.ingredient, "dessert_only", False)]
        if disallow_dessert and len(dessert_after) > 1:
            keep = dessert_after[0]
            cleaned = [s for s in cleaned if s not in dessert_after or s is keep]
            used_fallback = True
        if base_family == "tequila" and dessert_after:
            non_amaretto = next((s for s in dessert_after if "amaretto" not in s.ingredient.name.lower()), None)
            if non_amaretto and len(dessert_after) > 1:
                cleaned = [s for s in cleaned if s not in dessert_after or s is non_amaretto]
                used_fallback = True
            elif dessert_after and "amaretto" in dessert_after[0].ingredient.name.lower():
                cleaned = [s for s in cleaned if s not in dessert_after[1:]]
                used_fallback = True
        return relaxed_profile, cleaned, used_fallback


