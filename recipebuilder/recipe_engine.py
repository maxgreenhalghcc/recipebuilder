"""Core recipe engine for generating personalized cocktail recipes."""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path
from statistics import mean
from typing import Dict, Iterable, List, Literal, Optional, Sequence, Set, Tuple
import json
import re
import os
import random
import time


from recipebuilder.preferences import PreferencePlan, build_preference_plan, collect_profile_tags
from recipebuilder.flavour_context import (
    FlavourKnowledgeBase,
    FlavourVector,
    PourTemplate,
    compute_recipe_similarity,
    evaluate_template_constraints,
)


logger = logging.getLogger(__name__)

# -----------------------------
# Variety layer (safe, minimal)
# -----------------------------
def _variety_enabled_for_bar(bar_id: str) -> bool:
    """
    Safety-first rollout:
    - Enabled for Aviary by default (tight blast radius)
    - Or globally via env VARIETY_LAYER=1
    """
    flag = os.getenv("VARIETY_LAYER", "0").strip()
    if flag == "1":
        return True
    return _normalize(bar_id) in {"aviary"}


def _variety_temperature() -> float:
    """Lower = safer (more like argmax)."""
    try:
        value = float(os.getenv("VARIETY_TEMP", "0.75"))
    except (TypeError, ValueError):
        value = 0.75
    return max(0.25, min(1.5, value))


def _weighted_choice_from_ranked(
    ranked: Sequence[Tuple["Ingredient", float]],
    *,
    rng: random.Random,
    k: int = 4,
    min_score_ratio: float = 0.75,
    temperature: float = 0.75,
) -> Optional["Ingredient"]:
    """
    Pick from top-k with guardrails:
    - only items within min_score_ratio of best score
    - weighted by score^(1/temperature)
    """
    if not ranked:
        return None

    top = ranked[0][1]
    try:
        top = float(top)
    except (TypeError, ValueError):
        return ranked[0][0]

    if top <= 0:
        return ranked[0][0]

    cutoff = top * float(min_score_ratio)
    shortlist: List[Tuple["Ingredient", float]] = []
    for ing, score in ranked[: max(1, int(k))]:
        try:
            s = float(score)
        except (TypeError, ValueError):
            continue
        if s >= cutoff:
            shortlist.append((ing, s))

    if not shortlist:
        return ranked[0][0]

    temp = max(0.25, float(temperature))
    weights: List[float] = []
    for _, s in shortlist:
        weights.append(max(1e-6, s) ** (1.0 / temp))

    total = sum(weights)
    if total <= 0:
        return shortlist[0][0]

    pick = rng.random() * total
    running = 0.0
    for (ing, _), w in zip(shortlist, weights):
        running += w
        if pick <= running:
            return ing
    return shortlist[-1][0]


def _apply_variety_pass_to_profile_recipe(
    recipe: object,
    *,
    repository: "StockRepository",
    bar_id: str,
    responses: Dict[str, Optional[str]],
    profile_name: str,
    association_model: Optional["FlavourAssociationModel"] = None,
) -> None:
    """
    Post-pass to increase 'wow' personalization safely.
    Key change: prioritize taste-impacting swaps (sweetener/modifier) BEFORE garnish.
    """
    if not hasattr(recipe, "ingredients"):
        return
    suggestions = getattr(recipe, "ingredients", None)
    if not isinstance(suggestions, list) or not suggestions:
        return

    plan = build_preference_plan(responses)
    profile = collect_profile_tags(responses, plan)

    knowledge = _load_flavour_knowledge()
    for item in repository._all_items:
        try:
            knowledge.enrich_ingredient(item)
        except Exception:
            continue
    target_vector = knowledge.build_target_vector(responses, plan=plan)

    existing_tags = _collect_tags(suggestions)
    used_names = {
        _normalize(getattr(s.ingredient, "name", ""))
        for s in suggestions
        if getattr(s, "ingredient", None)
    }

    rng = random.Random(time.time_ns() & 0xFFFFFFFF)
    temp = _variety_temperature()

    def _norm_resp(key: str) -> str:
        return _normalize(str(responses.get(key) or ""))

    sweet_q = _norm_resp("sweetener_question")
    aroma = _norm_resp("aroma_preference")
    music = _norm_resp("music_preference")
    bitterness = _norm_resp("bitterness_tolerance")
    dining = _norm_resp("dining_style")

    is_candy = any(x in sweet_q for x in ("candy", "fun")) or "pop" in music
    is_floral = "floral" in aroma
    is_berry = "berry" in sweet_q or "berry" in aroma
    is_classic = ("classic" in sweet_q) or ("jazz" in music) or ("fine dining" in dining)
    is_bitter_high = any(x in bitterness for x in ("high", "strong"))
    is_citrus = ("citrus" in aroma) or ("zesty" in sweet_q)

    def _has_name(substr: str) -> bool:
        s = substr.lower()
        for sug in suggestions:
            name = _normalize(getattr(getattr(sug, "ingredient", None), "name", ""))
            if s and s in name:
                return True
        return False

    has_lime_cordial = _has_name("lime cordial")
    has_citrus_sour = _has_name("funkin lime") or _has_name("funkin lemon")
    squash_loop = has_lime_cordial and has_citrus_sour

    SWEETENER_PREFER: Set[str] = set()
    SWEETENER_AVOID: Set[str] = set()
    MODIFIER_PREFER: Set[str] = set()
    MODIFIER_AVOID: Set[str] = set()
    GARNISH_PREFER: Set[str] = set()

    if is_berry:
        SWEETENER_PREFER.update({"grenadine", "raspberry", "blackberry", "cherry", "blackcurrant"})
        GARNISH_PREFER.update({"raspberries", "cherries", "maraschino"})
    if is_floral:
        SWEETENER_PREFER.update({"elderflower", "violet"})
        GARNISH_PREFER.update({"lemon", "lime", "mint"})
    if is_candy:
        SWEETENER_PREFER.update({"bubblegum", "watermelon", "melon", "lychee", "kiwi"})
        MODIFIER_PREFER.update({"midori", "archers", "blue cura", "limoncello", "passoa"})
        GARNISH_PREFER.update({"pineapple", "mint", "raspberries"})
    if is_classic:
        MODIFIER_PREFER.update({"cointreau", "cherry heering"})
        if is_bitter_high:
            MODIFIER_PREFER.add("vermouth")
        else:
            MODIFIER_AVOID.add("vermouth")

    # If we're in the squash loop and the user isn't explicitly citrus/zesty,
    # force the sweetener away from lime cordial.
    if squash_loop and not is_citrus:
        SWEETENER_AVOID.add("lime cordial")

    def _matches_any(name: str, keywords: Set[str]) -> bool:
        if not keywords:
            return True
        n = _normalize(name)
        return any(k in n for k in keywords if k)

    def _swap_role(
        role: str,
        *,
        chance: float,
        k: int,
        min_ratio: float,
        prefer_keywords: Optional[Set[str]] = None,
        avoid_keywords: Optional[Set[str]] = None,
        force: bool = False,
    ) -> bool:
        nonlocal existing_tags, used_names

        current_idx = None
        current_ing = None
        for idx, s in enumerate(suggestions):
            if _normalize(getattr(s, "role", "")) == _normalize(role):
                current_idx = idx
                current_ing = getattr(s, "ingredient", None)
                break
        if current_idx is None or current_ing is None:
            return False

        if (not force) and (rng.random() > chance):
            return False

        current_name = _normalize(getattr(current_ing, "name", ""))

        pool: List[Ingredient] = []
        for item in repository._all_items:
            if _normalize(getattr(item, "role", "")) != _normalize(role):
                continue
            name = _normalize(getattr(item, "name", ""))
            if not name or name == current_name or name in used_names:
                continue
            if getattr(item, "dessert_only", False) and _normalize(profile_name) != "dessert":
                continue
            if avoid_keywords and any(k in name for k in avoid_keywords if k):
                continue
            pool.append(item)

        if not pool:
            return False

        filtered_pool = pool
        if prefer_keywords:
            preferred = [it for it in pool if _matches_any(getattr(it, "name", ""), prefer_keywords)]
            if preferred:
                filtered_pool = preferred

        ranked = _rank_ingredients(
            filtered_pool,
            profile,
            association_model=association_model,
            existing_tags=existing_tags,
            role=role,
            keyword_hints=None,
            knowledge_base=knowledge,
            target_vector=target_vector,
            plan=plan,
        )

        chosen = _weighted_choice_from_ranked(
            ranked,
            rng=rng,
            k=k,
            min_score_ratio=min_ratio,
            temperature=temp,
        )
        if chosen is None:
            return False

        # Extra guard: don't swap INTO lime cordial when we're avoiding it
        if role == "sweetener" and avoid_keywords and "lime cordial" in avoid_keywords:
            if "lime cordial" in _normalize(getattr(chosen, "name", "")):
                return False

        suggestions[current_idx].ingredient = chosen  # type: ignore[attr-defined]
        used_names.add(_normalize(getattr(chosen, "name", "")))
        existing_tags = _collect_tags(suggestions)

        if role == "garnish" and hasattr(recipe, "garnish"):
            try:
                setattr(recipe, "garnish", getattr(chosen, "name", None))
            except Exception:
                pass

        return True

    # -----------------------------
    # NEW ORDER: taste first
    # -----------------------------

    # 1) Sweetener swap (biggest "wow" + colour driver)
    sweet_force = bool(SWEETENER_AVOID)  # squash loop forces
    swapped_sweet = _swap_role(
        "sweetener",
        chance=0.85,
        k=6,
        min_ratio=0.72,
        prefer_keywords=(SWEETENER_PREFER if SWEETENER_PREFER else None),
        avoid_keywords=(SWEETENER_AVOID if SWEETENER_AVOID else None),
        force=sweet_force,
    )

    # 2) Modifier swap (identity)
    _swap_role(
        "modifier",
        chance=0.55 if (is_candy or is_berry) else 0.40,
        k=5,
        min_ratio=0.76,
        prefer_keywords=(MODIFIER_PREFER if MODIFIER_PREFER else None),
        avoid_keywords=(MODIFIER_AVOID if MODIFIER_AVOID else None),
        force=False,
    )

    # 3) Garnish swap (cosmetic, do last)
    _swap_role(
        "garnish",
        chance=0.65,
        k=6,
        min_ratio=0.70,
        prefer_keywords=(GARNISH_PREFER if GARNISH_PREFER else None),
        avoid_keywords=None,
        force=False,
    )

def _apply_serving_style_pass(recipe: object, *, responses: Dict[str, Optional[str]]) -> None:
    """
    Cosmetic-only pass:
    - Adds controlled glassware variety while respecting house_type profiling.
    - Fixes owner pain points: gin+tonic -> gin glass, and orange garnish -> pineapple when pineapple present.
    Does NOT change ingredients or amounts.
    """
    if not hasattr(recipe, "glassware") or not hasattr(recipe, "ingredients"):
        return

    ings = getattr(recipe, "ingredients", []) or []
    names = " ".join(_normalize(getattr(getattr(s, "ingredient", None), "name", "")) for s in ings)

    base = _normalize(str(responses.get("base_spirit") or ""))
    carbonation = _normalize(str(responses.get("carbonation_texture") or ""))
    music = _normalize(str(responses.get("music_preference") or ""))
    house = _normalize(str(responses.get("house_type") or ""))
    current_garnish = _normalize(str(getattr(recipe, "garnish", "") or ""))

    # deterministic-ish randomness per request (uses provided seed if present)
    seed_raw = responses.get("seed")
    try:
        seed = int(seed_raw) if seed_raw is not None else None
    except (TypeError, ValueError):
        seed = None
    rng = random.Random(seed if seed is not None else time.time_ns() & 0xFFFFFFFF)

    # --- House-type base mapping (your profiling points) ---
    def _base_glass_for_house(h: str) -> str:
        if "haunted" in h:
            return "skull glass"
        if "modern" in h:
            return "martini glass"
        if "tree" in h:
            return "gin glass"
        if "beach" in h:
            return "long glass"
        return "long glass"

    base_glass = _base_glass_for_house(house)

    # --- Controlled variety (small, safe alternates) ---
    # We keep the base mapping most of the time, but allow alternates to avoid monotony.
    # Haunted house stays skull glass always.
    if "haunted" not in house:
        # Ingredient-driven flags
        has_tonic = ("tonic" in names) or ("top with tonic" in names)
        has_soda = ("soda water" in names) or ("top with soda" in names)
        has_lemonade = ("lemonade" in names) or ("top with lemonade" in names)

        # Start with base glass most of the time
        pick = base_glass

        if base == "gin" and has_tonic:
            # Owner expectation: G&T should be in a gin glass
            pick = "gin glass"
        else:
            r = rng.random()

            if base_glass == "long glass":
                # beach house tends to dominate, so give it the most variety
                if r < 0.70:
                    pick = "long glass"
                elif r < 0.85:
                    pick = "gin glass"  # still fine for fizzy serves
                else:
                    pick = "chilled coupe" if carbonation in {"still & silky", ""} else "rocks glass"

            elif base_glass == "martini glass":
                # modern house: mostly martini, sometimes coupe/nick&nora
                if r < 0.80:
                    pick = "martini glass"
                else:
                    pick = "chilled coupe"

            elif base_glass == "gin glass":
                # tree house: mostly gin glass, sometimes rocks for woody/darker vibe
                if r < 0.80:
                    pick = "gin glass"
                else:
                    pick = "rocks glass"

        # Apply (only if recipe currently looks generic long/highball-ish OR empty)
        current_glass = _normalize(str(getattr(recipe, "glassware", "") or ""))
        if (not current_glass) or ("long glass" in current_glass) or ("highball" in current_glass):
            recipe.glassware = pick

    # --- Garnish fix: pineapple beats orange when pineapple is already in the build ---
    has_pineapple = "pineapple" in names
    if has_pineapple and ("orange" in current_garnish):
        recipe.garnish = "Pineapple"


def _coerce_string_list(value: Optional[object]) -> List[str]:
    """Convert free-form JSON fields into a clean list of strings."""

    if value is None:
        return []
    if isinstance(value, str):
        parts = [segment.strip() for segment in value.split(",")]
    elif isinstance(value, (list, tuple, set)):
        parts = [str(item).strip() for item in value]
    else:
        parts = [str(value).strip()]
    return [part for part in parts if part]


def _ingredient_families(ingredient: "Ingredient") -> Set[str]:
    families: Set[str] = set()
    name = _normalize(ingredient.name)
    category = _normalize(ingredient.category)
    if category:
        families.add(category)
    tags = {_normalize(tag) for tag in ingredient.flavour_tags}
    if "amaro" in name or "amaro" in tags:
        families.add("amaro")
    if "aperitivo" in name or "aperitivo" in tags:
        families.add("aperitivo")
    if "tonic" in name:
        families.add("tonic_water")
    if "soda" in name or "sparkling" in name or "club" in name:
        families.add("carbonated")
    if "grapefruit" in name or "grapefruit" in tags:
        families.add("grapefruit")
    if "bitters" in name or "bitter" in tags:
        if "orange" in name or "orange" in tags:
            families.add("bitters_orange")
        families.add("bitters_aromatic")
    if "egg white" in name or "aquafaba" in name:
        families.update({"egg_white", "aquafaba", "foam"})
    if "vermouth" in name and "dry" in name:
        families.add("dry_vermouth")
    if "lemonade" in name:
        families.add("lemonade")
    return {family for family in families if family}


@dataclass
class Ingredient:
    """Legacy ingredient representation (kept for backward compatibility)."""

    name: str
    category: str
    flavour_tags: Sequence[str]
    sub_category: Optional[str] = None
    default_measure_ml: Optional[float] = None
    preparation: Optional[str] = None
    notes: Optional[str] = None
    seasons: Sequence[str] = ()
    aromas: Sequence[str] = ()
    profiles: Sequence[str] = ()
    pairing_spirits: Sequence[str] = ()
    taste_vector: Dict[str, float] = field(default_factory=dict)
    aroma_vector: Dict[str, float] = field(default_factory=dict)
    structure_vector: Dict[str, float] = field(default_factory=dict)
    flags: Sequence[str] = ()
    pairing_prior: Dict[str, float] = field(default_factory=dict)
    compounds: Sequence[str] = ()

    @classmethod
    def from_dict(cls, data: Dict[str, object]) -> "Ingredient":
        tags = _coerce_string_list(data.get("flavour_tags"))
        profiles = _coerce_string_list(data.get("profiles"))
        aromas = _coerce_string_list(data.get("aromas"))
        seasons = _coerce_string_list(data.get("seasons"))

        if profiles:
            tags.extend(profiles)
        if aromas:
            tags.extend(aromas)
        if seasons:
            tags.extend(seasons)

        if tags:
            unique_tags: List[str] = []
            seen = set()
            for tag in tags:
                key = tag.lower()
                if key in seen:
                    continue
                seen.add(key)
                unique_tags.append(tag)
            tags = unique_tags

        return cls(
            name=str(data["name"]),
            category=str(data.get("category") or data.get("type") or "other"),
            flavour_tags=list(tags),
            sub_category=(str(data["sub_category"]) if data.get("sub_category") else None),
            default_measure_ml=(float(data["default_measure_ml"]) if data.get("default_measure_ml") else None),
            preparation=(str(data["preparation"]) if data.get("preparation") else None),
            notes=(str(data["notes"]) if data.get("notes") else None),
            seasons=seasons,
            aromas=aromas,
            profiles=profiles,
            pairing_spirits=_coerce_string_list(data.get("spirits")),
            taste_vector={},
            aroma_vector={},
            structure_vector={},
            flags=tuple(),
            pairing_prior={},
            compounds=tuple(),
        )


@dataclass
class StockItem:
    """Profile-aware stock entry used by the rule-based recipe builder."""

    name: str
    category: Literal["spirit", "syrup", "juice", "mixer", "sour", "modifier", "garnish"]
    role: Literal["base", "sweetener", "juice", "mixer", "sour", "modifier", "garnish"]

    profiles: Set[str] = field(default_factory=set)
    avoid_profiles: Set[str] = field(default_factory=set)

    neutral: bool = False
    flavour_tags: Sequence[str] = field(default_factory=list)
    default_measure_ml: float = 0.0
    spirit_subtype: Optional[str] = None
    dessert_only: bool = False

    @classmethod
    def from_dict(cls, data: Dict[str, object]) -> "StockItem":
        name = str(data.get("name") or "").strip()
        category = str(data.get("category") or data.get("type") or "other")
        role = str(data.get("role") or "").strip().lower()
        if not role:
            role = {
                "spirit": "base",
                "syrup": "sweetener",
                "juice": "juice",
                "mixer": "mixer",
                "sour": "sour",
                "modifier": "modifier",
                "garnish": "garnish",
            }.get(_normalize(category), "modifier")

        flavour_tags = _coerce_string_list(data.get("flavour_tags"))
        profiles = set(_coerce_string_list(data.get("profiles")))
        avoid_profiles = set(_coerce_string_list(data.get("avoid_profiles")))
        neutral = bool(data.get("neutral", False))
        default_measure_ml = float(data.get("default_measure_ml", 0.0) or 0.0)
        spirit_subtype = str(data.get("spirit_subtype") or "").strip().lower() or None
        dessert_only = bool(data.get("dessert_only", False))

        return cls(
            name=name,
            category=category,  # type: ignore[arg-type]
            role=role,  # type: ignore[arg-type]
            profiles=profiles,
            avoid_profiles=avoid_profiles,
            neutral=neutral,
            flavour_tags=flavour_tags,
            default_measure_ml=default_measure_ml,
            spirit_subtype=spirit_subtype,
            dessert_only=dessert_only,
        )


@dataclass
class IngredientSuggestion:
    """Ingredient used within the resulting recipe."""

    ingredient: Ingredient
    amount_ml: float
    role: str


@dataclass
class FlavourAssociationObservation:
    """Training datum describing a successful flavour combination."""

    tags: Sequence[str]
    rating: float
    role_ratios: Optional[Dict[str, float]] = None

    @classmethod
    def from_dict(cls, data: Dict[str, object]) -> "FlavourAssociationObservation":
        tags_data = data.get("tags") or []
        if isinstance(tags_data, str):
            tags = [segment.strip() for segment in tags_data.split(",") if segment.strip()]
        else:
            tags = list(tags_data)

        rating = float(data.get("rating", 0.0))
        role_ratios = data.get("role_ratios")
        if isinstance(role_ratios, dict):
            ratios = {str(key): float(value) for key, value in role_ratios.items()}
        else:
            ratios = None
        return cls(tags=tags, rating=rating, role_ratios=ratios)


class FlavourAssociationModel:
    """Learns how flavours interplay and how ratios should be adjusted."""

    DEFAULT_ROLE_RATIOS: Dict[str, float] = {
        "base": 0.5,
        "modifier": 0.25,
        "sweetener": 0.15,
        "juice": 0.1,
    }

    def __init__(self) -> None:
        self._tag_bias: Dict[str, float] = defaultdict(float)
        self._pair_bias: Dict[Tuple[str, str], float] = defaultdict(float)
        self._role_ratio_bias: Dict[str, List[float]] = defaultdict(list)
        self._role_presence_bias: Dict[str, float] = defaultdict(float)
        self._ratio_signatures: List[Tuple[frozenset[str], Dict[str, float], float]] = []

    @staticmethod
    def _normalize_tag(tag: str) -> str:
        return _normalize(tag)

    def add_observation(self, observation: FlavourAssociationObservation) -> None:
        if not observation.tags:
            return
        weight = float(observation.rating)
        if weight == 0:
            return
        tags = {self._normalize_tag(tag) for tag in observation.tags if tag}
        if not tags:
            return
        for tag in tags:
            self._tag_bias[tag] += weight

        if len(tags) > 1:
            for first, second in combinations(sorted(tags), 2):
                self._pair_bias[(first, second)] += weight / (len(tags) - 1)

        normalized_role_map: Dict[str, float] = {}
        if observation.role_ratios:
            total = sum(value for value in observation.role_ratios.values() if value > 0)
            if total > 0:
                for role, ratio in observation.role_ratios.items():
                    if ratio <= 0:
                        continue
                    normalized_ratio = ratio / total
                    key = role.strip().lower()
                    normalized_role_map[key] = normalized_ratio
                    self._role_ratio_bias[key].append(normalized_ratio)
                    self._role_presence_bias[key] += weight * normalized_ratio

        if normalized_role_map:
            self._ratio_signatures.append((frozenset(tags), dict(normalized_role_map), weight))

    def train(self, observations: Iterable[FlavourAssociationObservation]) -> None:
        for observation in observations:
            self.add_observation(observation)

    def to_weights(self) -> Dict[str, object]:
        """Return a JSON-serialisable snapshot of the learned weights."""

        pair_bias = {"||".join(pair): value for pair, value in self._pair_bias.items()}
        role_ratio_bias = {role: list(values) for role, values in self._role_ratio_bias.items()}
        ratio_signatures = [
            {
                "tags": sorted(tags),
                "role_ratios": dict(ratio_map),
                "weight": weight,
            }
            for tags, ratio_map, weight in self._ratio_signatures
        ]
        return {
            "tag_bias": dict(self._tag_bias),
            "pair_bias": pair_bias,
            "role_ratio_bias": role_ratio_bias,
            "role_presence_bias": dict(self._role_presence_bias),
            "ratio_signatures": ratio_signatures,
        }

    def save_weights(self, path: Path | str) -> None:
        """Persist learned weights for future reuse without retraining."""

        file_path = Path(path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        with file_path.open("w", encoding="utf-8") as handle:
            json.dump(self.to_weights(), handle, indent=2)

    @classmethod
    def from_weights(cls, data: Dict[str, object]) -> "FlavourAssociationModel":
        """Rehydrate a model instance from a previously saved weight snapshot."""

        model = cls()
        tag_bias = data.get("tag_bias", {})
        if isinstance(tag_bias, dict):
            for tag, value in tag_bias.items():
                model._tag_bias[str(tag)] = float(value)

        pair_bias = data.get("pair_bias", {})
        if isinstance(pair_bias, dict):
            for key, value in pair_bias.items():
                parts = [segment.strip() for segment in str(key).split("||") if segment.strip()]
                if len(parts) != 2:
                    continue
                model._pair_bias[tuple(sorted(parts))] = float(value)

        role_ratio_bias = data.get("role_ratio_bias", {})
        if isinstance(role_ratio_bias, dict):
            for role, values in role_ratio_bias.items():
                role_key = str(role)
                if isinstance(values, Sequence):
                    model._role_ratio_bias[role_key] = [float(item) for item in values]

        role_presence_bias = data.get("role_presence_bias", {})
        if isinstance(role_presence_bias, dict):
            for role, value in role_presence_bias.items():
                model._role_presence_bias[str(role)] = float(value)

        ratio_signatures = data.get("ratio_signatures", {})
        if isinstance(ratio_signatures, Sequence):
            for entry in ratio_signatures:
                if not isinstance(entry, dict):
                    continue
                tags_raw = entry.get("tags")
                ratios_raw = entry.get("role_ratios")
                if not isinstance(ratios_raw, dict):
                    continue
                normalized_map: Dict[str, float] = {}
                total = 0.0
                for role, value in ratios_raw.items():
                    try:
                        ratio_value = float(value)
                    except (TypeError, ValueError):
                        continue
                    if ratio_value <= 0:
                        continue
                    key = str(role).strip().lower()
                    normalized_map[key] = ratio_value
                    total += ratio_value
                if not normalized_map:
                    continue
                if total > 0 and abs(total - 1.0) > 1e-9:
                    normalized_map = {
                        role: value / total for role, value in normalized_map.items()
                    }
                tags: Set[str] = set()
                if isinstance(tags_raw, Sequence) and not isinstance(tags_raw, (str, bytes)):
                    for tag in tags_raw:
                        normalized_tag = cls._normalize_tag(str(tag))
                        if normalized_tag:
                            tags.add(normalized_tag)
                weight_value = entry.get("weight", 0.0)
                try:
                    weight = float(weight_value)
                except (TypeError, ValueError):
                    weight = 0.0
                model._ratio_signatures.append((frozenset(tags), dict(normalized_map), weight))

        return model

    @classmethod
    def from_weights_file(cls, path: Path | str) -> "FlavourAssociationModel":
        file_path = Path(path)
        if not file_path.exists():
            raise FileNotFoundError(f"Model weights not found at {file_path!s}.")
        with file_path.open("r", encoding="utf-8") as handle:
            raw = json.load(handle)
        if not isinstance(raw, dict):
            raise ValueError("Model weight file must contain a JSON object.")
        return cls.from_weights(raw)

    @classmethod
    def from_file(cls, path: Path | str) -> "FlavourAssociationModel":
        model = cls()
        file_path = Path(path)
        if not file_path.exists():
            raise FileNotFoundError(f"Flavour association data not found at {file_path!s}.")
        with file_path.open("r", encoding="utf-8") as handle:
            raw = json.load(handle)
        observations: List[FlavourAssociationObservation] = []
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, dict):
                    observations.append(FlavourAssociationObservation.from_dict(item))
        elif isinstance(raw, dict) and "observations" in raw:
            for item in raw["observations"]:
                if isinstance(item, dict):
                    observations.append(FlavourAssociationObservation.from_dict(item))
        model.train(observations)
        return model

    def _role_weight(self, role: str) -> float:
        role_key = role.strip().lower()
        presence = self._role_presence_bias.get(role_key, 0.0)
        if presence:
            return presence / max(1.0, sum(self._role_presence_bias.values()))
        return self.DEFAULT_ROLE_RATIOS.get(role_key, 0.0)

    def score_ingredient(
        self,
        ingredient: Ingredient,
        base_score: float,
        *,
        existing_tags: Optional[Sequence[str]] = None,
        role: Optional[str] = None,
    ) -> float:
        score = base_score
        tags = {self._normalize_tag(tag) for tag in ingredient.flavour_tags}
        for tag in tags:
            score += self._tag_bias.get(tag, 0.0) * 0.1

        if existing_tags:
            for tag in tags:
                for existing in existing_tags:
                    pair_key = tuple(sorted((tag, existing)))
                    score += self._pair_bias.get(pair_key, 0.0) * 0.1

        if role:
            score += self._role_weight(role) * 0.5

        return score

    def _collect_suggestion_tags(
        self, suggestions: Sequence[IngredientSuggestion]
    ) -> Set[str]:
        tags: Set[str] = set()
        for suggestion in suggestions:
            for tag in suggestion.ingredient.flavour_tags:
                normalized = self._normalize_tag(tag)
                if normalized:
                    tags.add(normalized)
        return tags

    def _find_ratio_signature(
        self, suggestion_tags: Set[str]
    ) -> Optional[Tuple[Dict[str, float], float]]:
        if not suggestion_tags or not self._ratio_signatures:
            return None

        best_match: Optional[Tuple[Dict[str, float], float]] = None
        best_score = 0.0
        for tags, ratio_map, weight in self._ratio_signatures:
            if not tags:
                continue
            overlap = len(suggestion_tags & tags)
            if overlap <= 0:
                continue
            coverage = overlap / len(tags)
            presence = overlap / len(suggestion_tags)
            score = (coverage * 0.6 + presence * 0.4) * max(weight, 0.0)
            if score > best_score:
                best_score = score
                best_match = (dict(ratio_map), score)
        return best_match

    def estimate_role_ratios(
        self, suggestions: Sequence[IngredientSuggestion]
    ) -> Dict[str, float]:
        if not suggestions:
            return {}

        baseline: Dict[str, float] = {}
        for role, samples in self._role_ratio_bias.items():
            if samples:
                baseline[role] = mean(samples)

        if not baseline:
            baseline = dict(self.DEFAULT_ROLE_RATIOS)

        suggestion_tags = self._collect_suggestion_tags(suggestions)
        suggestion_roles = {_normalize(s.role) for s in suggestions if s.role}
        ratios: Dict[str, float] = {}
        for suggestion in suggestions:
            if suggestion.amount_ml <= 0:
                continue
            role = suggestion.role.strip().lower()
            base_ratio = baseline.get(role, 0.0)
            tag_bonus = 0.0
            tags = {self._normalize_tag(tag) for tag in suggestion.ingredient.flavour_tags}
            for tag in tags:
                tag_bonus += self._tag_bias.get(tag, 0.0)

            pair_bonus = 0.0
            for other in suggestions:
                if other is suggestion:
                    continue
                other_tags = {self._normalize_tag(tag) for tag in other.ingredient.flavour_tags}
                for first in tags:
                    for second in other_tags:
                        pair_key = tuple(sorted((first, second)))
                        pair_bonus += self._pair_bias.get(pair_key, 0.0)

            ratios[role] = ratios.get(role, 0.0) + base_ratio + 0.03 * tag_bonus + 0.02 * pair_bonus

        total = sum(value for value in ratios.values() if value > 0)
        if total <= 0:
            return dict(baseline)

        normalized = {
            role: max(value / total, 0.0)
            for role, value in ratios.items()
            if value > 0
        }

        signature = self._find_ratio_signature(suggestion_tags)
        if signature:
            signature_map, score = signature
            relevant_signature = {
                role: value for role, value in signature_map.items() if role in suggestion_roles
            }
            if relevant_signature:
                blend = min(0.5, max(0.0, score))
                signature_roles = set(relevant_signature)
                for role in set(normalized) | signature_roles:
                    base_value = normalized.get(role, 0.0)
                    target_value = relevant_signature.get(role, base_value)
                    normalized[role] = base_value * (1 - blend) + target_value * blend
                total = sum(normalized.values())
                if total > 0:
                    normalized = {
                        role: value / total for role, value in normalized.items() if value > 0
                    }

        missing_roles = set(baseline) - set(normalized)
        if missing_roles:
            remainder = sum(normalized.values())
            leftover = max(0.0, 1.0 - remainder)
            if leftover > 0:
                share = leftover / len(missing_roles)
                for role in missing_roles:
                    normalized[role] = share
        return normalized

    def rebalance_amounts(self, suggestions: Sequence[IngredientSuggestion]) -> None:
        ratios = self.estimate_role_ratios(suggestions)
        if not ratios:
            return
        adjustable = [s for s in suggestions if s.amount_ml > 0]
        if not adjustable:
            return
        total_amount = sum(s.amount_ml for s in adjustable)
        if total_amount <= 0:
            return

        for suggestion in adjustable:
            role = suggestion.role.strip().lower()
            target_ratio = ratios.get(role)
            if target_ratio is None or target_ratio <= 0:
                continue
            minimum = 30.0 if role == "base" else 5.0
            suggestion.amount_ml = max(minimum, total_amount * target_ratio)

        new_total = sum(s.amount_ml for s in adjustable)
        if new_total <= 0:
            return
        scale = total_amount / new_total
        for suggestion in adjustable:
            suggestion.amount_ml *= scale


_DEFAULT_ASSOCIATION_PATH = Path("data/flavour_associations.json")
_DEFAULT_ASSOCIATION_WEIGHTS_PATH = Path("data/training/latest_weights.json")
_DEFAULT_SUCCESSFUL_SAMPLE_PATH = Path("data/training/successful_cocktails")
_DEFAULT_ASSOCIATION_MODEL: Optional[FlavourAssociationModel] = None
_DEFAULT_FLAVOUR_KNOWLEDGE: Optional[FlavourKnowledgeBase] = None


def _load_default_association_model() -> Optional[FlavourAssociationModel]:
    global _DEFAULT_ASSOCIATION_MODEL
    if _DEFAULT_ASSOCIATION_MODEL is not None:
        return _DEFAULT_ASSOCIATION_MODEL
    if _DEFAULT_ASSOCIATION_WEIGHTS_PATH.exists():
        try:
            _DEFAULT_ASSOCIATION_MODEL = FlavourAssociationModel.from_weights_file(
                _DEFAULT_ASSOCIATION_WEIGHTS_PATH
            )
        except (FileNotFoundError, ValueError, json.JSONDecodeError):
            _DEFAULT_ASSOCIATION_MODEL = None
    if _DEFAULT_ASSOCIATION_MODEL is None:
        if _DEFAULT_ASSOCIATION_PATH.exists():
            _DEFAULT_ASSOCIATION_MODEL = FlavourAssociationModel.from_file(
                _DEFAULT_ASSOCIATION_PATH
            )
        else:
            _DEFAULT_ASSOCIATION_MODEL = FlavourAssociationModel()

    if _DEFAULT_SUCCESSFUL_SAMPLE_PATH.exists():
        try:
            from recipebuilder.training import load_training_samples, train_model_from_samples

            samples = load_training_samples(_DEFAULT_SUCCESSFUL_SAMPLE_PATH)
        except (FileNotFoundError, ValueError, json.JSONDecodeError):
            samples = []
        if samples:
            _DEFAULT_ASSOCIATION_MODEL = train_model_from_samples(
                samples,
                base_model=_DEFAULT_ASSOCIATION_MODEL,
                rating_floor=0.1,
            )
    return _DEFAULT_ASSOCIATION_MODEL


def _load_flavour_knowledge() -> FlavourKnowledgeBase:
    global _DEFAULT_FLAVOUR_KNOWLEDGE
    if _DEFAULT_FLAVOUR_KNOWLEDGE is None:
        _DEFAULT_FLAVOUR_KNOWLEDGE = FlavourKnowledgeBase()
    return _DEFAULT_FLAVOUR_KNOWLEDGE


@dataclass
class CocktailRecipe:
    """Personalized cocktail recipe result."""

    name: str
    glassware: str
    ice: str
    ingredients: List[IngredientSuggestion]
    steps: List[str]
    flavour_profile: List[Tuple[str, float]]
    garnish: Optional[str] = None
    notes: Optional[str] = None
    explanations: Sequence[str] = field(default_factory=list)
    meta: Dict[str, object] = field(default_factory=dict)


class UnknownBarError(FileNotFoundError):
    """Raised when the requested bar stock list cannot be located."""


class StockRepository:
    """Loads the stock list for a given bar and exposes profile-aware items."""

    def __init__(self, stock_root: Path | str = Path("data/bars")) -> None:
        self.stock_root = Path(stock_root)

    def load_bar_stock(self, bar_id: str) -> List[StockItem]:
        token = _normalize_identifier(bar_id)
        candidates = [self.stock_root / f"{token}.json"]
        if token != bar_id:
            candidates.append(self.stock_root / f"{bar_id}.json")
        if "_" in token:
            candidates.append(self.stock_root / f"{token.replace('_', '')}.json")

        path = next((candidate for candidate in candidates if candidate.exists()), None)
        if path is None:
            raise UnknownBarError(
                f"Stock list for bar '{bar_id}' not found at any of {[str(c) for c in candidates]}"
            )

        logger.info("Loading stock for bar '%s' from %s", bar_id, path)

        with path.open("r", encoding="utf-8") as handle:
            raw = json.load(handle)

        items: object = raw
        if isinstance(raw, dict):
            if isinstance(raw.get("ingredients"), list):
                items = raw["ingredients"]
            elif isinstance(raw.get("flavour_matrix"), list):
                items = raw["flavour_matrix"]
            elif len(raw) == 1:
                only_value = next(iter(raw.values()))
                if isinstance(only_value, dict):
                    if isinstance(only_value.get("ingredients"), list):
                        items = only_value["ingredients"]
                    elif isinstance(only_value.get("flavour_matrix"), list):
                        items = only_value["flavour_matrix"]
                    else:
                        items = only_value
                else:
                    items = only_value

        if not isinstance(items, list):
            raise ValueError(
                "Stock list JSON must be a list or include an 'ingredients' or 'flavour_matrix' list."
            )

        stock_items = []
        for entry in items:
            if not isinstance(entry, dict):
                continue
            name_value = entry.get("name")
            if not name_value or not str(name_value).strip():
                continue
            item = StockItem.from_dict(entry)
            item = self._enrich_stock_item(item)
            stock_items.append(item)
        self._all_items_cache = stock_items
        cache: Dict[str, List[StockItem]] = defaultdict(list)
        for item in stock_items:
            profiles = set(item.profiles)
            if item.neutral:
                profiles.add("neutral")
            if not profiles:
                continue
            for profile in profiles:
                cache[profile].append(item)
        self._profile_cache = cache
        return stock_items

    def _enrich_stock_item(self, item: StockItem) -> StockItem:
        """Normalise profile data, fill sensible defaults, and enforce bans."""

        name = item.name
        normalized_name = _normalize(name)
        category = _normalize(item.category)
        role = _normalize(item.role)
        spirit_subtype = item.spirit_subtype or _infer_spirit_subtype(normalized_name)
        if not role:
            role = {
                "spirit": "base",
                "syrup": "sweetener",
                "juice": "juice",
                "mixer": "mixer",
                "sour": "sour",
                "modifier": "modifier",
                "garnish": "garnish",
            }.get(category, "modifier")

        default_measure = item.default_measure_ml
        if not default_measure:
            default_measure = {
                "spirit": 50.0,
                "syrup": 15.0,
                "juice": 25.0,
                "sour": 20.0,
                "mixer": 75.0,
                "modifier": 20.0,
            }.get(category, 0.0)

        profiles = {p.lower() for p in item.profiles}
        avoid_profiles = {p.lower() for p in item.avoid_profiles}
        neutral = bool(item.neutral)

        family = extract_spirit_family(name)
        if family == "rum" and spirit_subtype == "light":
            profiles.add("citrus_fresh")
            avoid_profiles.discard("citrus_fresh")

        # Normalise schnapps as modifiers (not bases)
        if "schnapps" in normalized_name:
            category = "modifier"
            role = "modifier"
            default_measure = 20.0

        # Neutral staples
        if normalized_name in {"lemon juice", "lime juice", "soda", "soda water", "club soda"}:
            neutral = True

        # Role tweak for sour juices
        if category == "juice" and normalized_name in {"lemon juice", "lime juice"}:
            category = "sour"
            role = "sour"
            default_measure = 20.0

        # If profiles are missing, use heuristics similar to the stock migration rules.
        dessert_only = bool(item.dessert_only)

        if not profiles:
            profiles, avoid_profiles, inferred_neutral = _assign_profile_defaults(normalized_name, category)
            neutral = neutral or inferred_neutral
        else:
            # Apply guardrails from heuristics without overriding declared compatibility.
            extra_profiles, extra_avoids, inferred_neutral = _assign_profile_defaults(normalized_name, category)
            profiles.update(extra_profiles)
            avoid_profiles.update(extra_avoids)
            neutral = neutral or inferred_neutral

        if not dessert_only and _is_dessert_only_name(normalized_name):
            dessert_only = True

        if family == "rum" and spirit_subtype == "light":
            profiles.add("citrus_fresh")
            avoid_profiles.discard("citrus_fresh")

        return StockItem(
            name=name,
            category=category,  # type: ignore[arg-type]
            role=role,  # type: ignore[arg-type]
            profiles=profiles,
            avoid_profiles=avoid_profiles,
            neutral=neutral,
            flavour_tags=item.flavour_tags,
            default_measure_ml=default_measure,
            spirit_subtype=spirit_subtype,
            dessert_only=dessert_only,
        )

    def items_for_profile(self, profile: str, *, role: str | None = None) -> List[StockItem]:
        items = [item for item in self._cached_items.get(profile, []) if (profile in item.profiles or item.neutral) and profile not in item.avoid_profiles]
        if role:
            items = [item for item in items if item.role == role]
        return items

    def neutral_items(self, *, role: str | None = None) -> List[StockItem]:
        items = [item for item in self._all_items if item.neutral]
        if role:
            items = [item for item in items if item.role == role]
        return items

    def find_flavoured_spirits(
        self,
        base_family: str,
        flavour: str,
        profile: str | None = None,
    ) -> List[StockItem]:
        """Return flavoured spirits matching the family/flavour and profile compatibility."""

        flavour = _normalize(flavour)
        results: List[StockItem] = []
        for item in self._all_items:
            if item.category != "spirit":
                continue
            if _is_creamy_name(item.name):
                continue
            if extract_spirit_family(item.name) != base_family:
                continue
            if flavour not in extract_flavour_keywords(item.name):
                continue
            if profile and (profile in item.avoid_profiles or (profile not in item.profiles and not item.neutral)):
                continue
            results.append(item)
        return results

    @property
    def _all_items(self) -> List[StockItem]:  # pragma: no cover - simple helper
        return getattr(self, "_all_items_cache", [])

    @property
    def _cached_items(self) -> Dict[str, List[StockItem]]:  # pragma: no cover - simple helper
        return getattr(self, "_profile_cache", {})

    def prime_cache(self, bar_id: str) -> List[StockItem]:
        """Load stock for a bar and cache profile buckets."""

        items = self.load_bar_stock(bar_id)
        self._all_items_cache = items
        cache: Dict[str, List[StockItem]] = defaultdict(list)
        for item in items:
            if not item.profiles and not item.neutral:
                continue
            for profile in item.profiles or {"neutral"}:
                cache[profile].append(item)
            if item.neutral:
                cache.setdefault("neutral", []).append(item)
        self._profile_cache = cache
        return items


def _normalize(text: str) -> str:
    return text.strip().lower()


def _tokenize(text: str) -> List[str]:
    return [
        token
        for token in (_normalize(part) for part in text.replace("&", " ").replace("-", " ").split())
        if token
    ]


def _normalize_identifier(value: str) -> str:
    normalized = _normalize(value)
    filtered = [ch if ch.isalnum() or ch == "_" else "_" for ch in normalized]
    token = "".join(filtered).strip("_")
    return token.replace("__", "_")


# Flavoured spirit helpers
FLAVOUR_KEYWORDS: Set[str] = {
    "raspberry",
    "strawberry",
    "berry",
    "passion",
    "pineapple",
    "coconut",
    "mango",
    "vanilla",
    "caramel",
    "toffee",
    "apple",
    "peach",
    "cherry",
    "orange",
}

BASE_SPIRIT_FAMILIES = ["vodka", "gin", "rum", "tequila"]


def extract_flavour_keywords(name: str) -> Set[str]:
    lower = _normalize(name)
    return {kw for kw in FLAVOUR_KEYWORDS if kw in lower}


def extract_spirit_family(name: str) -> str | None:
    lower = _normalize(name)
    for fam in BASE_SPIRIT_FAMILIES:
        if fam in lower:
            return fam
    return None


def _infer_spirit_subtype(name: str) -> Optional[str]:
    """Lightweight spirit subtype inference for rum/tequila variants."""

    lower = _normalize(name)
    if "spiced" in lower:
        return "spiced"
    if any(token in lower for token in ["dark", "anejo", "aged", "gold"]):
        return "dark"
    if any(token in lower for token in ["white", "light", "silver", "blanco"]):
        return "light"
    if "reposado" in lower:
        return "anejo"
    return None


def _is_creamy_name(name: str) -> bool:
    lowered = _normalize(name)
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
    return any(keyword in lowered for keyword in creamy_keywords)


def _is_dessert_only_name(name: str) -> bool:
    lowered = _normalize(name)
    dessert_markers = [
        "vanilla",
        "caramel",
        "toffee",
        "butterscotch",
        "amaretto",
    ]
    return any(marker in lowered for marker in dessert_markers)


def _assign_profile_defaults(name: str, category: str) -> tuple[Set[str], Set[str], bool]:
    """Apply consistent profile/avoid/neutral heuristics for stock items."""

    profiles: Set[str] = set()
    avoids: Set[str] = set()
    neutral = False

    # Spirits
    spirit_rules = {
        "vodka": (set(["tropical", "citrus_fresh", "berry", "classic_boozy", "candy_fun", "dessert"]), set(), True),
        "gin": (set(["citrus_fresh", "berry", "candy_fun"]), set(["dessert"]), False),
        "rum": (set(["tropical", "candy_fun", "dessert"]), set(["citrus_fresh", "classic_boozy"]), False),
        "tequila": (set(["classic_boozy", "tropical"]), set(["candy_fun", "dessert"]), False),
    }
    for key, (prof, avoid, maybe_neutral) in spirit_rules.items():
        if key in name:
            profiles.update(prof)
            avoids.update(avoid)
            if maybe_neutral and "premium" not in name:
                neutral = True

    # Juices
    juice_rules = {
        "orange juice": (set(["tropical", "citrus_fresh"]), set(["dessert", "candy_fun"])),
        "pineapple juice": (set(["tropical", "candy_fun"]), set(["classic_boozy"])),
        "cranberry juice": (set(["berry", "citrus_fresh", "candy_fun"]), set(["dessert"])),
        "passion fruit juice": (set(["tropical", "dessert"]), set(["classic_boozy"])),
        "apple juice": (set(["dessert", "classic_boozy"]), set()),
    }
    for key, (prof, avoid) in juice_rules.items():
        if key in name:
            profiles.update(prof)
            avoids.update(avoid)

    # Syrups and sweeteners
    if "vanilla" in name:
        profiles.update(["dessert", "tropical"])
        avoids.update(["citrus_fresh", "classic_boozy", "candy_fun"])
    if "caramel" in name:
        profiles.update(["dessert", "candy_fun"])
        avoids.update(["citrus_fresh", "classic_boozy", "tropical"])
    if any(kw in name for kw in ["raspberry", "strawberry", "berry"]):
        profiles.update(["berry", "candy_fun"])
        avoids.update(["classic_boozy"])
    if "blue" in name:
        profiles.update(["candy_fun"])
        avoids.update(["classic_boozy", "dessert"])
    if "maple" in name:
        profiles.update(["dessert", "tropical"])
        avoids.update(["candy_fun"])

    if "amaretto" in name:
        profiles.update(["dessert", "tropical"])
        avoids.update(["citrus_fresh", "candy_fun"])
    if "grenadine" in name:
        profiles.update(["tropical", "berry", "candy_fun", "classic_boozy"])
        avoids.update(["dessert"])
    if "aperol" in name:
        profiles.update(["classic_boozy", "citrus_fresh"])
    if "triple sec" in name:
        profiles.update(["classic_boozy", "tropical"])
    if "bitters" in name:
        profiles.update(["classic_boozy", "citrus_fresh"])
    if "elderflower" in name:
        profiles.update(["citrus_fresh", "berry"])
    if "coconut" in name:
        profiles.update(["tropical", "dessert"])
    if "coffee" in name:
        profiles.update(["dessert", "classic_boozy"])
        avoids.update(["citrus_fresh"])

    if "lemonade" in name:
        profiles.update(["tropical", "berry", "candy_fun", "citrus_fresh", "classic_boozy"])
        avoids.update(["dessert"])

    if not profiles and category == "syrup":
        profiles.update(["tropical", "citrus_fresh", "berry", "classic_boozy"])
    if not profiles and category == "juice":
        profiles.update(["tropical", "citrus_fresh"])
    if not profiles:
        profiles.update(["tropical", "citrus_fresh"])

    return profiles, avoids, neutral


def _is_flavoured_spirit(ingredient: Ingredient) -> bool:
    if _normalize(ingredient.category) != "spirit":
        return False
    name = _normalize(ingredient.name)
    tags = {_normalize(tag) for tag in ingredient.flavour_tags}
    flavour_markers = {
        "flavoured",
        "flavored",
        "fruit",
        "fruity",
        "berry",
        "tropical",
        "vanilla",
        "caramel",
        "candy",
        "sweet",
        "coconut",
        "spiced",
        "peach",
        "passion",
        "orange",
        "lemon",
        "lime",
    }
    return "flavour" in name or "flavor" in name or bool(tags & flavour_markers)


def _find_flavoured_spirits(
    ingredients: Sequence[Ingredient],
    *,
    exclude: Optional[Sequence[Ingredient]] = None,
) -> List[Ingredient]:
    excluded = {_normalize(item.name) for item in exclude or ()}
    return [
        ing
        for ing in ingredients
        if _is_flavoured_spirit(ing) and _normalize(ing.name) not in excluded
    ]


_NEGATIVE_NOTE_PATTERN = re.compile(
    r"(?:no|avoid|without|allergic to|allergy to|can't have|cannot have|intolerant to|sensitive to)\s+([a-z0-9 ,/&-]+)"
)

_LISTED_ALLERGEN_PATTERN = re.compile(r"allerg(?:y|ies)\s*[:\-]\s*([a-z0-9 ,/&-]+)")

_FREE_PATTERN = re.compile(r"([a-z0-9]+)-free")

_AVOID_SYNONYMS: Dict[str, Set[str]] = {
    "nuts": {"nut", "nuts", "nutty", "amaretto", "hazelnut", "almond", "orgeat", "praline", "pecan", "walnut"},
    "dairy": {"dairy", "milk", "cream", "creme", "cream", "baileys", "yogurt", "yoghurt"},
    "egg": {"egg", "albumen", "eggwhite", "egg-white"},
    "coconut": {"coconut", "malibu"},
    "pineapple": {"pineapple", "pineapple juice"},
    "passion fruit": {"passion fruit", "passionfruit", "passion-fruit", "passion"},
    "orange": {"orange", "orange juice", "clementine", "mandarin"},
    "cranberry": {"cranberry", "cranberry juice"},
    "citrus": {"citrus", "lemon", "lime", "grapefruit"},
    "gluten": {"gluten", "wheat", "barley", "beer"},
    "ginger": {"ginger", "ginger beer"},
    "sugar": {"sugar", "syrup", "simple syrup"},
    "spice": {"spice", "spices", "spiced", "cinnamon", "nutmeg"},
}


def _expand_avoid_terms(raw_terms: Iterable[str]) -> List[str]:
    expanded: Set[str] = set()
    for term in raw_terms:
        key = _normalize(term)
        if not key:
            continue
        expanded.add(key)
        for canonical, synonyms in _AVOID_SYNONYMS.items():
            normalized_canonical = _normalize(canonical)
            normalized_synonyms = {_normalize(value) for value in synonyms}
            if key == normalized_canonical or key in normalized_synonyms:
                expanded.add(normalized_canonical)
                expanded.update(normalized_synonyms)
    return sorted(expanded)


def _extract_avoid_terms(notes: Optional[str]) -> List[str]:
    if not notes:
        return []

    lower = notes.lower()
    extracted: List[str] = []

    for pattern in (_NEGATIVE_NOTE_PATTERN, _LISTED_ALLERGEN_PATTERN):
        for match in pattern.finditer(lower):
            raw = match.group(1)
            raw = raw.split(".")[0]
            for part in re.split(r"[,&/]| and ", raw):
                token = part.strip()
                if not token:
                    continue
                extracted.append(token)

    for match in _FREE_PATTERN.finditer(lower):
        extracted.append(match.group(1))

    if "allergic to" in lower and "and" in lower:
        for segment in lower.split("allergic to"):
            for part in re.split(r"[,&/]| and ", segment):
                token = part.strip()
                if token:
                    extracted.append(token)

    if "no" in lower:
        for part in re.split(r"no ", lower):
            if not part:
                continue
            candidate = part.split()[0]
            if candidate:
                extracted.append(candidate)

    return _expand_avoid_terms(extracted)


def _should_exclude(ingredient: Ingredient, avoid_terms: Sequence[str]) -> bool:
    if not avoid_terms:
        return False
    name_tokens = set(_tokenize(ingredient.name))
    tag_tokens = {_normalize(tag) for tag in ingredient.flavour_tags}
    for term in avoid_terms:
        key = _normalize(term)
        if not key:
            continue
        if key in name_tokens or any(key in tag for tag in tag_tokens):
            return True
    return False


_BASE_JUICE_KEYWORDS: Set[str] = {
    "pineapple",
    "pineapple juice",
    "passion fruit",
    "passionfruit",
    "orange",
    "orange juice",
    "cranberry",
    "cranberry juice",
}

_SECONDARY_JUICE_KEYWORDS: Set[str] = {"lemonade", "citrus blend", "tropical juice"}

_TART_JUICE_KEYWORDS: Set[str] = {
    "cranberry",
    "cranberry juice",
    "passion",
    "citrus",
    "lime",
    "lemon",
    "grapefruit",
}


def _ingredient_matches_keywords(ingredient: Ingredient, keywords: Sequence[str]) -> bool:
    if not keywords:
        return False
    normalized_keywords = {_normalize(keyword) for keyword in keywords if keyword}
    if not normalized_keywords:
        return False
    name_normalized = _normalize(ingredient.name)
    condensed_name = name_normalized.replace(" ", "")
    tokens = set(_tokenize(ingredient.name))
    tokens.update(_normalize(tag) for tag in ingredient.flavour_tags)
    condensed_tokens = {token.replace(" ", "") for token in tokens}
    for keyword in normalized_keywords:
        condensed_keyword = keyword.replace(" ", "")
        if keyword in tokens or condensed_keyword in condensed_tokens:
            return True
        if keyword and (keyword in name_normalized or condensed_keyword in condensed_name):
            return True
    return False


def _is_carbonated_lengthener(name: str) -> bool:
    tokens = _normalize(name)
    return any(
        keyword in tokens
        for keyword in (
            "soda",
            "tonic",
            "sparkling",
            "lemonade",
            "prosecco",
            "champagne",
            "fizz",
            "ginger beer",
            "cola",
        )
    )


def _is_tart_juice(ingredient: Ingredient) -> bool:
    return _ingredient_matches_keywords(ingredient, _TART_JUICE_KEYWORDS)


def _match_named_ingredient(
    ingredients: Sequence[Ingredient],
    target: Optional[str],
    *,
    category: Optional[str] = None,
) -> Optional[Ingredient]:
    if not target:
        return None
    normalized_target = _normalize(target)
    if not normalized_target:
        return None
    for ingredient in ingredients:
        if category and _normalize(ingredient.category) != _normalize(category):
            continue
        name = _normalize(ingredient.name)
        if normalized_target in name or name in normalized_target:
            return ingredient
    return None



def _score_ingredient(
    ingredient: Ingredient,
    profile: Dict[str, float],
    *,
    association_model: Optional[FlavourAssociationModel] = None,
    existing_tags: Optional[Sequence[str]] = None,
    role: Optional[str] = None,
    keyword_hints: Optional[Sequence[str]] = None,
    knowledge_base: Optional[FlavourKnowledgeBase] = None,
    target_vector: Optional[FlavourVector] = None,
    plan: Optional[PreferencePlan] = None,
) -> float:
    base_score = 0.0
    if knowledge_base is not None and target_vector is not None:
        base_score += knowledge_base.score_ingredient_against_target(
            ingredient,
            target_vector,
            role=role,
        )
    for tag in ingredient.flavour_tags:
        base_score += profile.get(_normalize(tag), 0.0)
    bonus = 0.0
    penalty = 0.0
    multiplier = 1.0
    if plan is not None:
        normalized_name = _normalize(ingredient.name)
        if role == "base" and plan.base_spirit_bias:
            for spirit, weight in plan.base_spirit_bias.items():
                spirit_key = _normalize(spirit)
                if spirit_key in normalized_name or normalized_name in spirit_key:
                    bonus += float(weight)
        pool_map = {
            "modifier": "modifier",
            "sweetener": "sweetener",
            "juice": "juice",
            "garnish": "garnish",
        }
        if role in pool_map and plan.candidate_pools.get(pool_map[role]):
            normalized_pool = {
                _normalize(value) for value in plan.candidate_pools.get(pool_map[role], ())
            }
            if normalized_name in normalized_pool:
                bonus += 0.6
        if plan.lengtheners and role == "juice":
            normalized_lengtheners = {_normalize(value) for value in plan.lengtheners}
            if normalized_name in normalized_lengtheners:
                bonus += 0.4
        if plan.avoid_or_reduce:
            lowered = normalized_name
            for term in plan.avoid_or_reduce:
                keyword = _normalize(term)
                if keyword and keyword in lowered:
                    penalty += 0.5
        if plan.bitterness_tolerance is not None:
            bitter_tokens = {
                tag
                for tag in (_normalize(tag) for tag in ingredient.flavour_tags)
                if "bitter" in tag
            }
            if bitter_tokens:
                bias = plan.bitterness_tolerance - 0.5
                bonus += 0.4 * bias
        if plan.candidate_family_bias:
            families = _ingredient_families(ingredient)
            for family in families:
                weight = plan.candidate_family_bias.get(family)
                if weight is not None:
                    multiplier *= float(weight)
        if plan.candidate_item_bias:
            item_weight = plan.candidate_item_bias.get(normalized_name)
            if item_weight is not None:
                multiplier *= float(item_weight)
        bitter_cap = plan.taste_caps.get("bitter") if plan.taste_caps else None
        if bitter_cap is not None:
            tag_set = {_normalize(tag) for tag in ingredient.flavour_tags}
            if "bitters" in normalized_name or "bitters" in tag_set:
                multiplier *= max(0.1, min(1.0, bitter_cap / 0.4))
    if association_model:
        adjusted_score = (base_score + bonus - penalty) * multiplier
        return association_model.score_ingredient(
            ingredient,
            adjusted_score,
            existing_tags=existing_tags,
            role=role,
        )
    score = (base_score + bonus - penalty) * multiplier
    if keyword_hints:
        normalized_hints = {_normalize(hint) for hint in keyword_hints if hint}
        name_tokens = set(_tokenize(ingredient.name))
        tag_tokens = {_normalize(tag) for tag in ingredient.flavour_tags}
        for hint in normalized_hints:
            if hint in name_tokens or hint in tag_tokens:
                score += 0.3
    return score


def _rank_ingredients(
    ingredients: Iterable[Ingredient],
    profile: Dict[str, float],
    *,
    association_model: Optional[FlavourAssociationModel] = None,
    existing_tags: Optional[Sequence[str]] = None,
    role: Optional[str] = None,
    keyword_hints: Optional[Sequence[str]] = None,
    knowledge_base: Optional[FlavourKnowledgeBase] = None,
    target_vector: Optional[FlavourVector] = None,
    plan: Optional[PreferencePlan] = None,
) -> List[Tuple[Ingredient, float]]:
    ranked = [
        (
            ingredient,
            _score_ingredient(
                ingredient,
                profile,
                association_model=association_model,
                existing_tags=existing_tags,
                role=role,
                keyword_hints=keyword_hints,
                knowledge_base=knowledge_base,
                target_vector=target_vector,
                plan=plan,
            ),
        )
        for ingredient in ingredients
    ]
    ranked.sort(key=lambda item: item[1], reverse=True)
    return ranked


def _select_best_match(
    ingredients: Sequence[Ingredient],
    profile: Dict[str, float],
    *,
    category: Optional[str] = None,
    predicate: Optional[callable] = None,
    association_model: Optional[FlavourAssociationModel] = None,
    existing_tags: Optional[Sequence[str]] = None,
    role: Optional[str] = None,
    keyword_hints: Optional[Sequence[str]] = None,
    knowledge_base: Optional[FlavourKnowledgeBase] = None,
    target_vector: Optional[FlavourVector] = None,
    plan: Optional[PreferencePlan] = None,
) -> Optional[Ingredient]:
    candidates: List[Ingredient] = []
    for ingredient in ingredients:
        if category and _normalize(ingredient.category) != _normalize(category):
            continue
        if predicate and not predicate(ingredient):
            continue
        candidates.append(ingredient)
    ranked = _rank_ingredients(
        candidates,
        profile,
        association_model=association_model,
        existing_tags=existing_tags,
        role=role,
        keyword_hints=keyword_hints,
        knowledge_base=knowledge_base,
        target_vector=target_vector,
        plan=plan,
    )
    return ranked[0][0] if ranked else None


def _select_juice_candidate(
    juices: Sequence[Ingredient],
    profile: Dict[str, float],
    *,
    association_model: Optional[FlavourAssociationModel],
    existing_tags: Optional[Sequence[str]],
    used_names: Set[str],
    keyword_hints: Optional[Sequence[str]] = None,
    required_keywords: Optional[Sequence[str]] = None,
    knowledge_base: Optional[FlavourKnowledgeBase] = None,
    target_vector: Optional[FlavourVector] = None,
    plan: Optional[PreferencePlan] = None,
) -> Optional[Ingredient]:
    candidates: List[Ingredient] = []
    normalized_used = {_normalize(name) for name in used_names}
    for ingredient in juices:
        if _normalize(ingredient.name) in normalized_used:
            continue
        if required_keywords and not _ingredient_matches_keywords(ingredient, required_keywords):
            continue
        candidates.append(ingredient)
    if not candidates:
        return None
    ranked = _rank_ingredients(
        candidates,
        profile,
        association_model=association_model,
        existing_tags=existing_tags,
        role="juice",
        keyword_hints=keyword_hints,
        knowledge_base=knowledge_base,
        target_vector=target_vector,
        plan=plan,
    )
    return ranked[0][0] if ranked else None


def _select_lengthener_candidate(
    juices: Sequence[Ingredient],
    profile: Dict[str, float],
    *,
    association_model: Optional[FlavourAssociationModel],
    existing_tags: Optional[Sequence[str]],
    used_names: Set[str],
    keyword_hints: Optional[Sequence[str]] = None,
    knowledge_base: Optional[FlavourKnowledgeBase] = None,
    target_vector: Optional[FlavourVector] = None,
    plan: Optional[PreferencePlan] = None,
) -> Optional[Ingredient]:
    allow_carbonated = True
    require_carbonated = False
    preferred_exact: Sequence[str] = ()
    if plan is not None and getattr(plan, "lengthener_rules", None):
        allow_carbonated = plan.lengthener_rules.get("allow_carbonated", True)
        require_carbonated = bool(plan.lengthener_rules.get("require_carbonated", False))
        preferred = plan.lengthener_rules.get("preferred")
        if isinstance(preferred, (list, tuple, set)):
            preferred_exact = list(preferred)

    keyword_sequences: List[Optional[Sequence[str]]] = []
    priority_sequences: List[Sequence[str]] = []

    def _append_priority(sequence: Sequence[str]) -> None:
        if sequence and sequence not in priority_sequences:
            priority_sequences.append(sequence)

    strategy = getattr(plan, "juice_strategy", None) if plan is not None else None
    preference_flag = getattr(plan, "lengthener_preference", None) if plan is not None else None

    def _normalize_flag(value: Optional[str]) -> Optional[str]:
        if isinstance(value, str) and value.strip():
            return _normalize(value)
        return None

    normalized_strategy = _normalize_flag(strategy)
    normalized_preference = _normalize_flag(preference_flag)

    def _has_flag(*flags: str) -> bool:
        return any(flag in {normalized_strategy, normalized_preference} for flag in flags)

    if _has_flag("juice_only"):
        for ingredient in juices:
            normalized_name = _normalize(ingredient.name)
            if normalized_name in used_names:
                _append_priority([ingredient.name])

    if _has_flag("lemonade_combo"):
        for value in ("lemonade", "cloudy lemonade", "sparkling lemonade"):
            _append_priority([value])

    if _has_flag("sparkling_lengthener", "sparkling_core"):
        for value in ("soda water", "lemonade", "tonic water"):
            _append_priority([value])

    if priority_sequences:
        keyword_sequences.extend(priority_sequences)
    if preferred_exact:
        keyword_sequences.append(list(preferred_exact))
    if plan is not None and plan.lengtheners:
        keyword_sequences.append(list(plan.lengtheners))
    keyword_sequences.extend(
        [
            list(_BASE_JUICE_KEYWORDS),
            list(_SECONDARY_JUICE_KEYWORDS),
            None,
        ]
    )
    for keyword_pool in keyword_sequences:
        candidate = _select_juice_candidate(
            juices,
            profile,
            association_model=association_model,
            existing_tags=existing_tags,
            used_names=used_names,
            keyword_hints=keyword_hints,
            required_keywords=keyword_pool,
            knowledge_base=knowledge_base,
            target_vector=target_vector,
            plan=plan,
        )
        if candidate:
            carbonated = _is_carbonated_lengthener(candidate.name)
            if not allow_carbonated and carbonated:
                continue
            if require_carbonated and not carbonated:
                continue
            return candidate
    return None


def _match_base_spirit(
    ingredients: Sequence[Ingredient],
    profile: Dict[str, float],
    base_spirit: str,
    *,
    association_model: Optional[FlavourAssociationModel] = None,
    knowledge_base: Optional[FlavourKnowledgeBase] = None,
    target_vector: Optional[FlavourVector] = None,
    plan: Optional[PreferencePlan] = None,
) -> Optional[Ingredient]:
    target = _normalize(base_spirit)

    def _predicate(ing: Ingredient) -> bool:
        values = [ing.name, ing.category, ing.sub_category or ""]
        normalized_values = [_normalize(value) for value in values]
        return any(target in value or value in target for value in normalized_values)

    spirit = _select_best_match(
        ingredients,
        profile,
        category="spirit",
        predicate=_predicate,
        association_model=association_model,
        role="base",
        knowledge_base=knowledge_base,
        target_vector=target_vector,
        plan=plan,
    )
    if spirit:
        return spirit
    return _select_best_match(
        ingredients,
        profile,
        category="spirit",
        association_model=association_model,
        role="base",
        knowledge_base=knowledge_base,
        target_vector=target_vector,
        plan=plan,
    )


def _find_candidates_by_category(ingredients: Sequence[Ingredient], *categories: str) -> List[Ingredient]:
    normalized = {_normalize(category) for category in categories}
    if not normalized:
        return list(ingredients)
    return [ingredient for ingredient in ingredients if _normalize(ingredient.category) in normalized]


def _collect_tags(suggestions: Sequence[IngredientSuggestion]) -> List[str]:
    tags: List[str] = []
    for suggestion in suggestions:
        tags.extend(_normalize(tag) for tag in suggestion.ingredient.flavour_tags)
    return tags


def _count_unique_juices(suggestions: Sequence[IngredientSuggestion]) -> int:
    return len(
        {
            _normalize(suggestion.ingredient.name)
            for suggestion in suggestions
            if suggestion.role == "juice"
        }
    )


def _scale_role_total(
    suggestions: Sequence[IngredientSuggestion],
    role: str,
    target_total: float,
    *,
    minimum: float = 4.0,
) -> None:
    if target_total <= 0:
        return
    items = [s for s in suggestions if _normalize(s.role) == _normalize(role)]
    if not items:
        return
    current = sum(item.amount_ml for item in items if item.amount_ml > 0)
    if current <= 0:
        share = target_total / len(items)
        for item in items:
            item.amount_ml = max(minimum, share)
        return
    factor = target_total / current
    for item in items:
        item.amount_ml = max(minimum, item.amount_ml * factor)


def _quantize_spirit_total(
    suggestions: Sequence[IngredientSuggestion], target_spirit_ml: float
) -> None:
    spirits = [s for s in suggestions if _normalize(s.ingredient.category) == "spirit"]
    if not spirits or target_spirit_ml <= 0:
        return
    current = sum(s.amount_ml for s in spirits if s.amount_ml > 0)
    if current <= 0:
        spirits[0].amount_ml = target_spirit_ml
        return
    scale = target_spirit_ml / current
    if scale <= 0:
        return
    for spirit in spirits:
        spirit.amount_ml = max(10.0, spirit.amount_ml * scale)
    adjusted_total = sum(s.amount_ml for s in spirits if s.amount_ml > 0)
    if adjusted_total <= 0:
        return
    correction = target_spirit_ml - adjusted_total
    if abs(correction) > 0.5:
        share = correction / len(spirits)
        for spirit in spirits:
            spirit.amount_ml = max(10.0, spirit.amount_ml + share)


def _cap_citrus_acidity(suggestions: Sequence[IngredientSuggestion]) -> None:
    citrus_terms = {"lemon", "lime"}
    citrus_juices = [
        s
        for s in suggestions
        if s.role == "juice" and any(term in _normalize(s.ingredient.name) for term in citrus_terms)
    ]
    if not citrus_juices:
        return
    multiple_juices = _count_unique_juices(suggestions) > 1
    for juice in citrus_juices:
        if multiple_juices:
            juice.amount_ml = min(juice.amount_ml, 15.0)


def _default_amount(category: str, ingredient: Ingredient, role: str) -> float:
    category = _normalize(category)
    if ingredient.default_measure_ml:
        return ingredient.default_measure_ml
    if role == "base":
        return 50.0
    if category in {"liqueur", "modifier"}:
        return 25.0
    if category in {"syrup", "sweetener"}:
        return 15.0
    if category in {"juice", "citrus"}:
        return 20.0
    if category in {"garnish"}:
        return 0.0
    return 10.0


def _determine_glassware(responses: Dict[str, str]) -> str:
    style = _normalize(responses.get("dining_style", ""))
    house = _normalize(responses.get("house_type", ""))
    season = _normalize(responses.get("season", ""))

    if style in {"fine dining", "tasting menu"}:
        return "Nick & Nora glass"
    if season in {"summer", "spring", "al fresco"}:
        return "Chilled coupe"
    if house in {"rustic"}:
        return "Rocks glass"
    return "Double old fashioned"


def _determine_ice(responses: Dict[str, str]) -> str:
    style = _normalize(responses.get("dining_style", ""))
    season = _normalize(responses.get("season", ""))
    if season in {"winter"}:
        return "Large clear cube"
    if style in {"al fresco"}:
        return "Crushed ice"
    return "Standard cubed ice"


def _select_garnish(
    ingredients: Sequence[Ingredient],
    profile: Dict[str, float],
    *,
    association_model: Optional[FlavourAssociationModel] = None,
    existing_tags: Optional[Sequence[str]] = None,
    keyword_hints: Optional[Sequence[str]] = None,
    knowledge_base: Optional[FlavourKnowledgeBase] = None,
    target_vector: Optional[FlavourVector] = None,
    plan: Optional[PreferencePlan] = None,
) -> Optional[Ingredient]:
    garnishes = _find_candidates_by_category(ingredients, "garnish")
    ranked = _rank_ingredients(
        garnishes,
        profile,
        association_model=association_model,
        existing_tags=existing_tags,
        role="garnish",
        keyword_hints=keyword_hints,
        knowledge_base=knowledge_base,
        target_vector=target_vector,
        plan=plan,
    )
    return ranked[0][0] if ranked else None


def _ensure_palate_balance(
    suggestions: Sequence[IngredientSuggestion],
    plan,
) -> None:
    if not suggestions:
        return

    role_groups: Dict[str, List[IngredientSuggestion]] = defaultdict(list)
    for suggestion in suggestions:
        role = suggestion.role.strip().lower()
        role_groups[role].append(suggestion)

    def _total(role: str) -> float:
        return sum(max(s.amount_ml, 0.0) for s in role_groups.get(role, []))

    ratio_targets = getattr(plan, "ratio_targets", {}) or {}
    if ratio_targets:
        considered_roles = {role for role in ratio_targets.keys() if role in role_groups}
        total_core = sum(_total(role) for role in considered_roles)
        if total_core <= 0:
            total_core = sum(_total(role) for role in ("base", "modifier", "sweetener", "juice"))
        for role, window in ratio_targets.items():
            items = role_groups.get(role)
            if not items or not window:
                continue
            low, high = float(window[0]), float(window[1])
            if total_core <= 0:
                continue
            current = _total(role)
            target_min = max(0.0, low * total_core)
            target_max = max(target_min + 1.0, high * total_core)
            if current < target_min:
                deficit = target_min - current
                share = deficit / len(items)
                for suggestion in items:
                    suggestion.amount_ml += share
            elif current > target_max and current > 0:
                scale = target_max / current
                for suggestion in items:
                    suggestion.amount_ml = max(3.0, suggestion.amount_ml * scale)

    base_total = _total("base")
    if role_groups.get("base"):
        base_target_min = 35.0
        base_target_max = 60.0
        primary_base = role_groups["base"][0]
        if base_total < base_target_min:
            primary_base.amount_ml += base_target_min - base_total
            base_total = _total("base")
        elif base_total > base_target_max:
            scale = base_target_max / base_total if base_total > 0 else 1.0
            for suggestion in role_groups["base"]:
                suggestion.amount_ml = max(5.0, suggestion.amount_ml * scale)
            base_total = _total("base")

    juice_total = _total("juice")
    if role_groups.get("juice"):
        min_juice = max(base_total * 0.85, 40.0)
        try:
            glass_min = float(getattr(plan, "glass_min_ml", 0.0) or 0.0)
        except (TypeError, ValueError):
            glass_min = 0.0
        if glass_min:
            min_juice = max(min_juice, glass_min * 0.35)
        if juice_total < min_juice:
            deficit = min_juice - juice_total
            share = deficit / len(role_groups["juice"])
            for suggestion in role_groups["juice"]:
                suggestion.amount_ml += share
            juice_total = _total("juice")

    sweet_total = _total("sweetener")
    sweet_window = getattr(plan, "sweet_acid_window", None)
    if role_groups.get("sweetener") and juice_total > 0:
        if sweet_window:
            low, high = float(sweet_window[0]), float(sweet_window[1])
            if juice_total > 0:
                ratio = sweet_total / juice_total if juice_total > 0 else float("inf")
                if ratio < low:
                    target = low * juice_total
                    deficit = target - sweet_total
                    share = deficit / len(role_groups["sweetener"])
                    for suggestion in role_groups["sweetener"]:
                        suggestion.amount_ml += share
                    sweet_total = _total("sweetener")
                elif ratio > high and sweet_total > 0:
                    target = high * juice_total
                    scale = target / sweet_total if sweet_total > 0 else 1.0
                    for suggestion in role_groups["sweetener"]:
                        suggestion.amount_ml = max(3.0, suggestion.amount_ml * scale)
                    sweet_total = _total("sweetener")
        else:
            min_sweet = max(juice_total * 0.12, 5.0)
            max_sweet = max(juice_total * 0.6, min_sweet)
            if sweet_total < min_sweet:
                deficit = min_sweet - sweet_total
                share = deficit / len(role_groups["sweetener"])
                for suggestion in role_groups["sweetener"]:
                    suggestion.amount_ml += share
                sweet_total = _total("sweetener")
            elif sweet_total > max_sweet and sweet_total > 0:
                scale = max_sweet / sweet_total
                for suggestion in role_groups["sweetener"]:
                    suggestion.amount_ml = max(3.0, suggestion.amount_ml * scale)

    modifier_total = _total("modifier")
    if role_groups.get("modifier") and base_total > 0:
        max_modifier = max(base_total * 0.6, 15.0)
        if modifier_total > max_modifier and modifier_total > 0:
            scale = max_modifier / modifier_total
            for suggestion in role_groups["modifier"]:
                suggestion.amount_ml = max(5.0, suggestion.amount_ml * scale)


def _apply_template_guidance(
    suggestions: Sequence[IngredientSuggestion],
    template,
    *,
    knowledge: FlavourKnowledgeBase,
    target_vector: Optional[FlavourVector] = None,
    control_tags: Optional[Sequence[str]] = None,
    plan: Optional[PreferencePlan] = None,
) -> Dict[str, float]:
    if template is None or not suggestions:
        return {}

    active_template = template
    role_ratios = getattr(template, "role_ratios", {})
    constraints = dict(getattr(template, "constraints", {}))

    if plan is not None:
        adjusted_ratios = dict(role_ratios)
        if plan.ratio_targets:
            for role, window in plan.ratio_targets.items():
                if not window:
                    continue
                low, high = window
                center = max(0.0, (float(low) + float(high)) / 2.0)
                role_key = _normalize(role)
                current = adjusted_ratios.get(role_key, center)
                adjusted_ratios[role_key] = 0.7 * current + 0.3 * center
            total = sum(adjusted_ratios.values())
            if total > 0:
                adjusted_ratios = {role: value / total for role, value in adjusted_ratios.items()}
        role_ratios = adjusted_ratios

        if plan.sweet_acid_window:
            taste_targets = dict(constraints.get("taste_targets") or {})
            taste_targets["sweet:acid_ratio"] = [float(plan.sweet_acid_window[0]), float(plan.sweet_acid_window[1])]
            constraints["taste_targets"] = taste_targets
        if getattr(plan, "taste_caps", None):
            taste_caps = dict(constraints.get("taste_caps") or {})
            for key, value in plan.taste_caps.items():
                try:
                    taste_caps[_normalize(key)] = float(value)
                except (TypeError, ValueError):
                    continue
            if taste_caps:
                constraints["taste_caps"] = taste_caps
        if plan.abv_range:
            constraints["abv_range"] = [float(plan.abv_range[0]), float(plan.abv_range[1])]
        if plan.ingredient_max is not None:
            constraints["ingredient_count_max"] = int(plan.ingredient_max)
        active_template = PourTemplate(
            id=getattr(template, "id", "template"),
            name=getattr(template, "name", "Template"),
            role_ratios=role_ratios,
            constraints=constraints,
        )

    if not role_ratios:
        return {}

    role_groups: Dict[str, List[IngredientSuggestion]] = defaultdict(list)
    for suggestion in suggestions:
        role_groups[suggestion.role.strip().lower()].append(suggestion)

    total_core = sum(
        suggestion.amount_ml
        for suggestion in suggestions
        if suggestion.role.strip().lower() in role_ratios
    )
    if total_core <= 0:
        total_core = 100.0

    for role, ratio in role_ratios.items():
        items = role_groups.get(role)
        if not items:
            continue
        target_amount = max(0.0, ratio * total_core)
        share = target_amount / len(items)
        minimum = 30.0 if role == "base" else 5.0
        for suggestion in items:
            suggestion.amount_ml = max(minimum, share)

    control_set = {(_normalize(tag)) for tag in control_tags or []}
    if "reduce_sweet" in control_set:
        for suggestion in role_groups.get("sweetener", []):
            suggestion.amount_ml *= 0.85
    if "reduce_strength" in control_set:
        for suggestion in role_groups.get("base", []):
            suggestion.amount_ml *= 0.9
        for suggestion in role_groups.get("juice", []):
            suggestion.amount_ml *= 1.05

    if plan is not None and getattr(plan, "role_bounds", None):
        core_roles = ("base", "modifier", "sweetener", "juice")
        total_core = sum(
            sum(s.amount_ml for s in role_groups.get(role, [])) for role in core_roles
        )
        if total_core <= 0:
            total_core = sum(s.amount_ml for group in role_groups.values() for s in group)
        if total_core > 0:
            for role, bounds in plan.role_bounds.items():
                items = role_groups.get(role)
                if not items:
                    continue
                min_share, max_share = bounds
                current_total = sum(s.amount_ml for s in items)
                if min_share is not None:
                    target_min = max(0.0, float(min_share) * total_core)
                    if current_total < target_min:
                        deficit = target_min - current_total
                        share = deficit / len(items)
                        for suggestion in items:
                            suggestion.amount_ml += share
                        current_total = sum(s.amount_ml for s in items)
                if max_share is not None and current_total > 0:
                    target_max = max(0.0, float(max_share) * total_core)
                    if target_max < current_total:
                        scale = target_max / current_total if current_total > 0 else 1.0
                        for suggestion in items:
                            suggestion.amount_ml = max(3.0, suggestion.amount_ml * scale)

    ingredient_payload = [(s.ingredient, s.amount_ml) for s in suggestions]
    issues = evaluate_template_constraints(knowledge, active_template, ingredient_payload)

    if "sweetness_high" in issues and role_groups.get("sweetener"):
        factor = max(0.6, 1.0 - issues["sweetness_high"])
        for suggestion in role_groups["sweetener"]:
            suggestion.amount_ml = max(4.0, suggestion.amount_ml * factor)
    elif "sweetness_low" in issues and role_groups.get("sweetener"):
        factor = min(1.4, 1.0 + issues["sweetness_low"])
        for suggestion in role_groups["sweetener"]:
            suggestion.amount_ml = max(5.0, suggestion.amount_ml * factor)

    if "abv_over" in issues and role_groups.get("base") and role_groups.get("juice"):
        base_factor = max(0.6, 1.0 - issues["abv_over"])
        for suggestion in role_groups["base"]:
            suggestion.amount_ml = max(25.0, suggestion.amount_ml * base_factor)
        for suggestion in role_groups["juice"]:
            suggestion.amount_ml *= 1.1
    elif "abv_under" in issues and role_groups.get("base"):
        boost = min(1.3, 1.0 + issues["abv_under"])
        for suggestion in role_groups["base"]:
            suggestion.amount_ml = max(30.0, suggestion.amount_ml * boost)

    ingredient_payload = [(s.ingredient, s.amount_ml) for s in suggestions]
    final_issues = evaluate_template_constraints(knowledge, active_template, ingredient_payload)
    similarity = 0.0
    if target_vector is not None:
        similarity = compute_recipe_similarity(knowledge, target_vector, ingredient_payload)
    final_issues["similarity"] = similarity
    return final_issues

def generate_cocktail_recipe(
    responses: Dict[str, Optional[str]],
    *,
    bar_id: str,
    repository: Optional[StockRepository] = None,
    association_model: Optional[FlavourAssociationModel] = None,
    recipe_name: str = "Signature Serve",
) -> CocktailRecipe:
    """Generate a personalized cocktail recipe for the specified bar stock."""

    logger.info("Starting recipe generation for bar '%s'", bar_id)

    if repository is None:
        repository = StockRepository()

    if association_model is None:
        association_model = _load_default_association_model()

    ingredients = repository.load_bar_stock(bar_id)

    flavour_knowledge = _load_flavour_knowledge()
    for item in ingredients:
        flavour_knowledge.enrich_ingredient(item)

    plan = build_preference_plan(responses)
    carbonation_choice = _normalize(responses.get("carbonation_texture") or "")
    abv_lane_choice = _normalize(responses.get("abv_lane") or "")
    target_spirit_ml = 40.0
    if abv_lane_choice in {"strong", "high"}:
        target_spirit_ml = 50.0
    elif abv_lane_choice == "low":
        target_spirit_ml = 25.0
    plan.strength_oz = target_spirit_ml / 29.5735
    lengthener_rules = dict(getattr(plan, "lengthener_rules", {}) or {})
    if carbonation_choice == "still & silky":
        lengthener_rules["allow_carbonated"] = False
        lengthener_rules["require_carbonated"] = False
        plan.lengthener_allowed = True
        plan.juice_max_count = min(plan.juice_max_count, 2)
    elif carbonation_choice == "lightly fizzy":
        lengthener_rules["allow_carbonated"] = True
        plan.lengthener_allowed = True
        preferred: List[str] = []
        bitterness_level = plan.bitterness_tolerance or 0.0
        if bitterness_level >= 0.55:
            preferred = ["tonic water", "lemonade", "soda water"]
        else:
            preferred = ["lemonade", "soda water", "tonic water"]
        lengthener_rules["preferred"] = tuple(preferred)
        plan.juice_max_count = min(plan.juice_max_count, 2)
    elif carbonation_choice == "properly sparkling":
        lengthener_rules["allow_carbonated"] = True
        lengthener_rules["require_carbonated"] = True
        plan.lengthener_allowed = True
        lengthener_rules["preferred"] = ("lemonade", "soda water", "tonic water")
        plan.juice_max_count = min(plan.juice_max_count, 2)
    plan.lengthener_rules = lengthener_rules
    logger.debug(
        "Built preference plan: glass=%s, templates=%s, juice_max=%s, lengthener=%s",
        plan.glass_type,
        sorted(plan.template_weights.keys()),
        plan.juice_max_count,
        plan.lengtheners,
    )
    profile = collect_profile_tags(responses, plan)
    target_vector = flavour_knowledge.build_target_vector(responses, plan=plan)
    target_tag_weights = flavour_knowledge.mapping.target_tags_from_responses(responses)
    control_set = {_normalize(tag) for tag in target_tag_weights.keys()}

    avoid_terms = _extract_avoid_terms(responses.get("notes"))
    if avoid_terms:
        logger.info("Applying %d avoidance terms from notes", len(avoid_terms))
        filtered = [ing for ing in ingredients if not _should_exclude(ing, avoid_terms)]
        if not filtered:
            raise ValueError("No available ingredients after applying guest restrictions.")
        ingredients = filtered

    base: Optional[Ingredient] = None
    if responses.get("base_spirit"):
        base = _match_base_spirit(
            ingredients,
            profile,
            responses["base_spirit"],
            association_model=association_model,
            knowledge_base=flavour_knowledge,
            target_vector=target_vector,
            plan=plan,
        )

    if base is None:
        base = _select_best_match(
            ingredients,
            profile,
            category="spirit",
            association_model=association_model,
            role="base",
            knowledge_base=flavour_knowledge,
            target_vector=target_vector,
            plan=plan,
        )
    if base is None:
        raise ValueError("No base spirit available in the bar stock list.")

    logger.info("Selected base spirit: %s", base.name)

    suggestions: List[IngredientSuggestion] = []

    base_amount = plan.base_ml if plan.base_ml > 0 else _default_amount(base.category, base, "base")
    suggestions.append(IngredientSuggestion(base, base_amount, "base"))

    modifiers_pool = [
        ing for ing in _find_candidates_by_category(ingredients, "liqueur", "modifier") if ing != base
    ]
    sweeteners_pool = [
        ing for ing in _find_candidates_by_category(ingredients, "syrup", "sweetener") if ing != base
    ]
    if not sweeteners_pool:
        sweeteners_pool = _find_flavoured_spirits(ingredients, exclude=[base])
    juices_pool = [
        ing for ing in _find_candidates_by_category(ingredients, "juice", "citrus") if ing != base
    ]

    existing_tags = _collect_tags(suggestions)

    modifiers_ranked = _rank_ingredients(
        modifiers_pool,
        profile,
        association_model=association_model,
        existing_tags=existing_tags,
        role="modifier",
        keyword_hints=plan.modifier_tags,
        knowledge_base=flavour_knowledge,
        target_vector=target_vector,
        plan=plan,
    )

    if modifiers_ranked and plan.modifier_ml > 0:
        modifier, _ = modifiers_ranked[0]
        suggestions.append(IngredientSuggestion(modifier, plan.modifier_ml, "modifier"))
        existing_tags = _collect_tags(suggestions)

    sweet_ranked = _rank_ingredients(
        sweeteners_pool,
        profile,
        association_model=association_model,
        existing_tags=existing_tags,
        role="sweetener",
        keyword_hints=plan.sweetener_tags,
        knowledge_base=flavour_knowledge,
        target_vector=target_vector,
        plan=plan,
    )

    sweetener_added = False
    if plan.sweetener_ml > 0 and sweet_ranked:
        sweetener, _ = sweet_ranked[0]
        suggestions.append(IngredientSuggestion(sweetener, plan.sweetener_ml, "sweetener"))
        sweetener_added = True
        existing_tags = _collect_tags(suggestions)

    juice_hints: List[str] = [hint for hint in list(plan.juice_tags) if hint]
    if plan.juice_focus:
        juice_hints.append(plan.juice_focus)
    for candidate in plan.candidate_pools.get("juice", ()):  # prefer persona juices
        if candidate not in juice_hints:
            juice_hints.append(candidate)
    for candidate in plan.lengtheners:
        if candidate not in juice_hints:
            juice_hints.append(candidate)

    if not juices_pool:
        raise ValueError("Bar stock does not include suitable juices for this serve.")

    used_juice_names: Set[str] = set()
    max_juices = min(getattr(plan, "juice_max_count", 3), 2)

    primary_juice = _select_juice_candidate(
        juices_pool,
        profile,
        association_model=association_model,
        existing_tags=existing_tags,
        used_names=used_juice_names,
        keyword_hints=juice_hints,
        required_keywords=list(_BASE_JUICE_KEYWORDS),
        knowledge_base=flavour_knowledge,
        target_vector=target_vector,
        plan=plan,
    )

    if primary_juice is None:
        raise ValueError(
            "Bar stock must include pineapple, passion fruit, orange, or cranberry juice for this serve."
        )

    used_juice_names.add(_normalize(primary_juice.name))
    base_reference_ml = plan.base_ml if plan.base_ml > 0 else _default_amount(base.category, base, "base")
    primary_amount = max(
        _default_amount(primary_juice.category, primary_juice, "juice"),
        base_reference_ml * 0.8,
    )
    if carbonation_choice == "properly sparkling":
        primary_amount = min(primary_amount, max(30.0, target_spirit_ml * 0.6))
    elif carbonation_choice == "still & silky":
        primary_amount = min(primary_amount, max(25.0, target_spirit_ml * 0.5))
    primary_amount = min(primary_amount, 90.0)
    suggestions.append(IngredientSuggestion(primary_juice, primary_amount, "juice"))
    primary_juice_name = primary_juice.name
    juice_hints.append(primary_juice.name)
    existing_tags = _collect_tags(suggestions)

    if not sweetener_added and _is_tart_juice(primary_juice) and sweet_ranked:
        fallback_sweetener, _ = sweet_ranked[0]
        fallback_amount = 12.0
        suggestions.append(IngredientSuggestion(fallback_sweetener, fallback_amount, "sweetener"))
        sweetener_added = True
        existing_tags = _collect_tags(suggestions)

    secondary_focus = plan.juice_focus or ""
    if (
        secondary_focus
        and not _ingredient_matches_keywords(primary_juice, [secondary_focus])
        and _count_unique_juices(suggestions) < max_juices
    ):
        focus_candidate = _select_juice_candidate(
            juices_pool,
            profile,
            association_model=association_model,
            existing_tags=existing_tags,
            used_names=used_juice_names,
            keyword_hints=juice_hints,
            required_keywords=[secondary_focus],
            knowledge_base=flavour_knowledge,
            target_vector=target_vector,
            plan=plan,
        )
        if focus_candidate:
            used_juice_names.add(_normalize(focus_candidate.name))
            focus_amount = min(max(base_reference_ml * 0.5, 25.0), 60.0)
            suggestions.append(IngredientSuggestion(focus_candidate, focus_amount, "juice"))
            juice_hints.append(focus_candidate.name)
            existing_tags = _collect_tags(suggestions)

    try:
        glass_min = float(getattr(plan, "glass_min_ml", 0.0) or 0.0)
    except (TypeError, ValueError):
        glass_min = 0.0

    if glass_min and glass_min >= 240 and _count_unique_juices(suggestions) < max_juices:
        additional_candidate = _select_juice_candidate(
            juices_pool,
            profile,
            association_model=association_model,
            existing_tags=existing_tags,
            used_names=used_juice_names,
            keyword_hints=juice_hints,
            required_keywords=list(_BASE_JUICE_KEYWORDS),
            knowledge_base=flavour_knowledge,
            target_vector=target_vector,
            plan=plan,
        )
        if additional_candidate:
            used_juice_names.add(_normalize(additional_candidate.name))
            additional_amount = min(max(glass_min * 0.25, 30.0), 70.0)
            suggestions.append(IngredientSuggestion(additional_candidate, additional_amount, "juice"))
            juice_hints.append(additional_candidate.name)
            existing_tags = _collect_tags(suggestions)

    if association_model:
        association_model.rebalance_amounts(suggestions)
        if plan.base_ml > 0 and suggestions and suggestions[0].amount_ml > 0:
            scale = plan.base_ml / suggestions[0].amount_ml
            for suggestion in suggestions:
                suggestion.amount_ml *= scale

    juice_total = sum(s.amount_ml for s in suggestions if s.role == "juice")
    if carbonation_choice == "still & silky":
        _scale_role_total(suggestions, "juice", target_spirit_ml * 0.5, minimum=10.0)
        _scale_role_total(suggestions, "sweetener", target_spirit_ml * 0.5, minimum=8.0)
        _scale_role_total(suggestions, "modifier", target_spirit_ml * 0.5, minimum=8.0)
    elif carbonation_choice == "lightly fizzy":
        desired_juice = max(target_spirit_ml * 0.7, juice_total)
        _scale_role_total(suggestions, "juice", desired_juice, minimum=12.0)
    elif carbonation_choice == "properly sparkling":
        desired_juice = min(juice_total, max(25.0, target_spirit_ml * 0.6))
        _scale_role_total(suggestions, "juice", desired_juice, minimum=10.0)

    _quantize_spirit_total(suggestions, target_spirit_ml)
    _cap_citrus_acidity(suggestions)

    template_candidates = flavour_knowledge.select_templates_for_target(
        target_vector,
        target_tags=target_tag_weights,
        plan=plan,
    )
    selected_template = template_candidates[0] if template_candidates else None
    template_feedback = {}
    if selected_template is not None:
        template_feedback = _apply_template_guidance(
            suggestions,
            selected_template,
            knowledge=flavour_knowledge,
            target_vector=target_vector,
            control_tags=control_set,
            plan=plan,
        )

    _ensure_palate_balance(suggestions, plan)

    existing_tags = _collect_tags(suggestions)

    garnish_item = None
    if plan.garnish_hint:
        garnish_item = _match_named_ingredient(
            ingredients,
            plan.garnish_hint,
            category="garnish",
        )
    garnish_hints: List[str] = []
    if plan.garnish_hint:
        garnish_hints.append(plan.garnish_hint)
    garnish_hints.extend(value for value in plan.garnish_pool if value)
    if garnish_item is None:
        garnish_item = _select_garnish(
            ingredients,
            profile,
            association_model=association_model,
            existing_tags=existing_tags,
            keyword_hints=garnish_hints or None,
            knowledge_base=flavour_knowledge,
            target_vector=target_vector,
            plan=plan,
        )
    garnish_name = garnish_item.name if garnish_item else plan.garnish_hint

    glassware = plan.glass_type or _determine_glassware(responses)
    ice = plan.ice_program or _determine_ice(responses)

    total_ml = sum(s.amount_ml for s in suggestions if s.amount_ml > 0)
    top_up_needed = max(plan.glass_min_ml - total_ml, 0.0)
    lengthener_rules = getattr(plan, "lengthener_rules", {}) or {}
    preferred_lengthener_override: Optional[str] = None
    if carbonation_choice == "lightly fizzy":
        preferred_lengthener_override = (
            "tonic water" if (plan.bitterness_tolerance or 0.0) >= 0.55 else "lemonade"
        )
    elif carbonation_choice == "properly sparkling":
        preferred_lengthener_override = "lemonade"
    if top_up_needed > 15 and (plan.lengthener_allowed is not False):
        require_carbonated = bool(lengthener_rules.get("require_carbonated"))
        unique_juices = _count_unique_juices(suggestions)
        allow_new_unique = unique_juices < max_juices or require_carbonated
        candidate: Optional[Ingredient] = None
        reused_suggestion: Optional[IngredientSuggestion] = None
        if not require_carbonated and carbonation_choice == "still & silky":
            for suggestion in suggestions:
                if suggestion.role == "juice":
                    candidate = suggestion.ingredient
                    reused_suggestion = suggestion
                    break
        if candidate is None and preferred_lengthener_override:
            matched = _match_named_ingredient(
                juices_pool,
                preferred_lengthener_override,
            )
            if matched:
                candidate = matched
        if candidate is None and allow_new_unique:
            candidate = _select_lengthener_candidate(
                juices_pool,
                profile,
                association_model=association_model,
                existing_tags=existing_tags,
                used_names=used_juice_names,
                keyword_hints=juice_hints,
                knowledge_base=flavour_knowledge,
                target_vector=target_vector,
                plan=plan,
            )
        if candidate:
            addition_amount = max(top_up_needed, 40.0)
            if carbonation_choice == "properly sparkling":
                addition_amount = max(addition_amount, max(plan.glass_min_ml * 0.5, 90.0))
            elif carbonation_choice == "lightly fizzy":
                addition_amount = max(addition_amount, 60.0)
            normalized_name = _normalize(candidate.name)
            if reused_suggestion is not None:
                reused_suggestion.amount_ml += addition_amount
            else:
                if normalized_name not in used_juice_names:
                    used_juice_names.add(normalized_name)
                suggestions.append(IngredientSuggestion(candidate, addition_amount, "juice"))
                juice_hints.append(candidate.name)
            _ensure_palate_balance(suggestions, plan)
            existing_tags = _collect_tags(suggestions)
            total_ml = sum(s.amount_ml for s in suggestions if s.amount_ml > 0)
            top_up_needed = max(plan.glass_min_ml - total_ml, 0.0)

    lengthener_note = None
    if top_up_needed > 15 and (plan.lengthener_allowed is not False):
        fallback_name = primary_juice_name
        if not fallback_name and plan.juice_focus:
            focus = plan.juice_focus.strip()
            fallback_name = focus if "juice" in focus.lower() else f"{focus} juice"
        if not fallback_name and getattr(plan, "lengthener_rules", None):
            preferred = plan.lengthener_rules.get("preferred")
            if preferred:
                fallback_name = str(preferred[0])
        if preferred_lengthener_override:
            fallback_name = preferred_lengthener_override
        if not fallback_name:
            fallback_name = "seasonal juice"
        lengthener_note = (
            f"Top up with {top_up_needed:.0f} ml chilled {fallback_name.lower()} to finish the serve."
        )

    _quantize_spirit_total(suggestions, target_spirit_ml)
    _cap_citrus_acidity(suggestions)

    final_payload = [(s.ingredient, s.amount_ml) for s in suggestions if s.amount_ml > 0]
    final_similarity = compute_recipe_similarity(
        flavour_knowledge,
        target_vector,
        final_payload,
    )
    if selected_template is not None:
        template_feedback["similarity"] = final_similarity

    explanations: List[str] = []
    if getattr(plan, "explanations", None):
        explanations.extend(plan.explanations)
    if selected_template is not None:
        explanations.append(
            f"Applied {selected_template.name} template to keep base/modifier/juice ratios bartender friendly."
        )
    if final_similarity:
        explanations.append(f"Flavour alignment score: {final_similarity:.2f} (cosine match).")
    if "reduce_sweet" in control_set:
        explanations.append("Sweetness tempered in line with guest notes.")
    if "reduce_strength" in control_set:
        explanations.append("ABV moderated for a lighter sipping profile.")
    if plan.juice_focus:
        explanations.append(f"Juice emphasis: {plan.juice_focus.title()} leads the palate.")
    if lengthener_note:
        explanations.append("Lengthener guidance included to reach glass volume without diluting balance.")

   steps = _build_steps(
        name, suggestions, glassware, ice, garnish, lengthener_note,
        carbonation_texture=(responses.get("carbonation_texture") or ""),
        foam_toggle=(responses.get("foam_toggle") or "no"),
    )


    sorted_profile = sorted(profile.items(), key=lambda item: item[1], reverse=True)

    logger.info(
        "Built recipe '%s' for bar '%s' using template '%s' with %d ingredients (similarity=%.2f)",
        recipe_name,
        bar_id,
        selected_template.name if selected_template else "custom-balance",
        len(suggestions),
        final_similarity if isinstance(final_similarity, (int, float)) else 0.0,
    )
    logger.debug(
        "Ingredients: %s",
        [f"{s.amount_ml:.0f}ml {s.ingredient.name} ({s.role})" for s in suggestions],
    )

    return CocktailRecipe(
        name=recipe_name,
        glassware=glassware,
        ice=ice,
        ingredients=suggestions,
        steps=steps,
        garnish=garnish_name,
        flavour_profile=sorted_profile,
        notes=responses.get("notes"),
        explanations=explanations,
    )


    def _build_steps(
        recipe_name: str,
        suggestions: Sequence[IngredientSuggestion],
        glassware: str,
        ice: str,
        garnish: Optional[str],
        lengthener_note: Optional[str],
        carbonation_texture: str = "",
        foam_toggle: str = "no",
    ) -> List[str]:
        """
        Build method steps based on:
        - carbonation_texture (still vs fizzy)
        - foam_toggle (YES => shaken build, but no foaming agent required)
        - glassware (martini/coupe => up serve)
        - mixer presence / lengthener_note
        """
        steps: List[str] = []
    
        g = (glassware or "").lower()
        carb = (carbonation_texture or "").strip().lower()
        foam = (foam_toggle or "").strip().lower()
    
        is_up = ("martini" in g) or ("coupe" in g)
        is_still = carb.startswith("still")
        wants_fizzy = carb.startswith("light") or carb.startswith("proper") or "spark" in carb
    
        # Identify mixer suggestion (if any)
        mixer = next((s for s in suggestions if s.role == "mixer"), None)
    
        # If we have a mixer, treat as fizzy unless explicitly still
        if mixer and not is_still:
            wants_fizzy = True
    
        # Helper: separate "shake set" ingredients (everything except mixer)
        shake_set = [s for s in suggestions if s.role != "mixer"]
        # If caller uses lengthener_note for top-up, keep it as additional instruction
    
        # --- UP / MARTINI STYLE ---
        if is_up:
            steps.append(f"Chill the {glassware.lower()}.")
            steps.append("Add all ingredients to a shaker with cubed ice and shake hard (10–12 seconds).")
            steps.append(f"Fine strain into the chilled {glassware.lower()}.")
            if garnish:
                steps.append(f"Garnish with {garnish.lower()}.")
            if lengthener_note:
                steps.append(lengthener_note)
            steps.append("Serve immediately.")
            return steps

    # --- STILL & SILKY (always shaken, no top) ---
    if is_still:
        steps.append(f"Fill a {glassware.lower()} with {ice.lower()}.")
        steps.append("Add all ingredients to a shaker with cubed ice and shake hard (10–12 seconds).")
        steps.append(f"Strain into the ice-filled {glassware.lower()}.")
        if garnish:
            steps.append(f"Garnish with {garnish.lower()}.")
        if lengthener_note:
            steps.append(lengthener_note)
        steps.append("Serve immediately.")
        return steps

    # --- FIZZY DRINKS ---
    steps.append(f"Fill a {glassware.lower()} with {ice.lower()}.")

    # Foam toggle = shaken build (but no foaming agent required)
    if foam == "yes":
        steps.append("Add all ingredients (except the mixer) to a shaker with cubed ice and shake hard (10–12 seconds).")
        steps.append(f"Strain into the ice-filled {glassware.lower()}.")
    else:
        # Default fizzy build: build in glass (more service-friendly)
        steps.append("Add spirits, syrups, juices and sour to the glass. Give a brief stir.")

    # Top-up if we have a mixer
    if mixer:
        steps.append(f"Top with {mixer.ingredient.name}.")
    elif wants_fizzy and lengthener_note:
        # If caller passed a lengthener note but no mixer object exists
        steps.append(lengthener_note)

    if garnish:
        steps.append(f"Garnish with {garnish.lower()}.")
    steps.append("Serve immediately.")
    return steps


def generate_cocktail_recipe(
    responses: Dict[str, Optional[str]],
    *,
    bar_id: str,
    repository: Optional[StockRepository] = None,
    association_model: Optional[FlavourAssociationModel] = None,
    recipe_name: str = "Signature Serve",
) -> CocktailRecipe:
    """Generate a profile-guarded cocktail recipe for the specified bar stock."""

    logger.info("Starting recipe generation for bar '%s'", bar_id)

    if repository is None:
        repository = StockRepository()

    # Association model is only used for the variety post-pass scoring
    if association_model is None and _variety_enabled_for_bar(bar_id):
        association_model = _load_default_association_model()

    responses_with_bar = dict(responses)
    responses_with_bar["bar_id"] = bar_id

    from recipebuilder.profile_builder import ProfileRecipeBuilder, choose_profile

    profile_name = choose_profile(responses_with_bar)

    repository.prime_cache(bar_id)
    builder = ProfileRecipeBuilder(repository)

    # Build a few candidates and choose the most "service-ready" one (safe fallback)
    seed_raw = responses_with_bar.get("seed")
    try:
        seed_i = int(seed_raw) if seed_raw is not None else None
    except (TypeError, ValueError):
        seed_i = None

    candidates = []
    try:
        candidates = builder.build_candidates(
            responses_with_bar,
            profile_name,
            seed=seed_i,
            num_candidates=3,
            max_attempts=8,
        ) or []
    except Exception:
        # Never break service because candidate generation failed
        logger.exception("Candidate build failed; falling back to single recipe.")
        candidates = []

    def _score_candidate(r):
        ings = getattr(r, "ingredients", None) or []
        roles = [getattr(s, "role", "").lower() for s in ings]
        n = len(ings)

        has_juice = "juice" in roles
        has_sour = "sour" in roles
        has_mixer = "mixer" in roles

        # Prefer: (1) fuller recipes, (2) real structure, (3) sour/mixer presence, (4) component count
        return (
            1 if n >= 6 else 0,
            1 if (has_juice and (has_sour or has_mixer)) else 0,
            1 if has_sour else 0,
            1 if has_mixer else 0,
            n,
        )

    try:
        recipe = max(candidates, key=_score_candidate) if candidates else None
    except Exception:
        logger.exception("Candidate scoring failed; falling back to single recipe.")
        recipe = None

    if recipe is None:
        # Absolute fallback path (always works)
        recipe = builder.build_recipe(responses_with_bar, profile_name)

    try:
        _apply_serving_style_pass(recipe, responses=responses_with_bar)
    except Exception:
        logger.exception("Serving style pass failed; leaving glassware/garnish unchanged.")

    # Safe, minimal variety layer (Aviary-only by default, or enable via VARIETY_LAYER=1)
    if _variety_enabled_for_bar(bar_id):
        try:
            _apply_variety_pass_to_profile_recipe(
                recipe,
                repository=repository,
                bar_id=bar_id,
                responses=responses_with_bar,
                profile_name=str(profile_name),
                association_model=association_model,
            )
        except Exception:
            logger.exception("Variety pass failed; serving original recipe.")

    recipe.name = recipe_name
    return recipe
