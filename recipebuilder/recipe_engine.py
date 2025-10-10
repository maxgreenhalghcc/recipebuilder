"""Core recipe engine for generating personalized cocktail recipes."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from itertools import combinations
from statistics import mean
from typing import Dict, Iterable, List, Optional, Sequence, Tuple
import json

from recipebuilder.preferences import build_preference_plan, collect_profile_tags


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


@dataclass
class Ingredient:
    """Represents a single stock ingredient."""

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

        if observation.role_ratios:
            total = sum(value for value in observation.role_ratios.values() if value > 0)
            if total > 0:
                for role, ratio in observation.role_ratios.items():
                    if ratio <= 0:
                        continue
                    normalized_ratio = ratio / total
                    key = role.strip().lower()
                    self._role_ratio_bias[key].append(normalized_ratio)
                    self._role_presence_bias[key] += weight * normalized_ratio

    def train(self, observations: Iterable[FlavourAssociationObservation]) -> None:
        for observation in observations:
            self.add_observation(observation)

    def to_weights(self) -> Dict[str, object]:
        """Return a JSON-serialisable snapshot of the learned weights."""

        pair_bias = {"||".join(pair): value for pair, value in self._pair_bias.items()}
        role_ratio_bias = {role: list(values) for role, values in self._role_ratio_bias.items()}
        return {
            "tag_bias": dict(self._tag_bias),
            "pair_bias": pair_bias,
            "role_ratio_bias": role_ratio_bias,
            "role_presence_bias": dict(self._role_presence_bias),
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
            return baseline

        normalized = {role: max(value / total, 0.0) for role, value in ratios.items()}
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
_DEFAULT_ASSOCIATION_MODEL: Optional[FlavourAssociationModel] = None


def _load_default_association_model() -> Optional[FlavourAssociationModel]:
    global _DEFAULT_ASSOCIATION_MODEL
    if _DEFAULT_ASSOCIATION_MODEL is not None:
        return _DEFAULT_ASSOCIATION_MODEL
    if _DEFAULT_ASSOCIATION_PATH.exists():
        _DEFAULT_ASSOCIATION_MODEL = FlavourAssociationModel.from_file(_DEFAULT_ASSOCIATION_PATH)
    else:
        _DEFAULT_ASSOCIATION_MODEL = None
    return _DEFAULT_ASSOCIATION_MODEL


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


class UnknownBarError(FileNotFoundError):
    """Raised when the requested bar stock list cannot be located."""


class StockRepository:
    """Loads the stock list for a given bar."""

    def __init__(self, stock_root: Path | str = Path("data/bars")) -> None:
        self.stock_root = Path(stock_root)

    def load_bar_stock(self, bar_id: str) -> List[Ingredient]:
        token = _normalize_identifier(bar_id)
        candidates = [self.stock_root / f"{token}.json"]
        if token != bar_id:
            candidates.append(self.stock_root / f"{bar_id}.json")

        path = next((candidate for candidate in candidates if candidate.exists()), None)
        if path is None:
            raise UnknownBarError(
                f"Stock list for bar '{bar_id}' not found at any of {[str(c) for c in candidates]}"
            )

        with path.open("r", encoding="utf-8") as handle:
            raw = json.load(handle)

        if isinstance(raw, dict) and "ingredients" in raw:
            items = raw["ingredients"]
        else:
            items = raw

        if not isinstance(items, list):
            raise ValueError("Stock list JSON must be a list or contain an 'ingredients' list.")

        ingredients = []
        for entry in items:
            if not isinstance(entry, dict):
                continue
            try:
                ingredient = Ingredient.from_dict(entry)
            except KeyError as exc:  # missing required fields
                raise ValueError(f"Ingredient entry missing required field: {exc} -> {entry}") from exc
            ingredients.append(ingredient)
        return ingredients


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


def _extract_avoid_terms(notes: Optional[str]) -> List[str]:
    if not notes:
        return []
    lower = notes.lower()
    terms: List[str] = []
    if "no coconut" in lower:
        terms.append("coconut")
    if "no dairy" in lower:
        terms.extend(["cream", "milk", "dairy"])
    if "no egg" in lower:
        terms.extend(["egg", "albumen"])
    if "no sugar" in lower:
        terms.extend(["sugar", "syrup"])
    if "no spice" in lower:
        terms.append("spice")
    if "no citrus" in lower:
        terms.append("citrus")
    if "allergic to nuts" in lower or "no nuts" in lower:
        terms.extend(["nut", "amaretto", "frangelico", "hazelnut"])
    return list(dict.fromkeys(terms))


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
) -> float:
    base_score = 0.0
    for tag in ingredient.flavour_tags:
        base_score += profile.get(_normalize(tag), 0.0)
    if association_model:
        return association_model.score_ingredient(
            ingredient,
            base_score,
            existing_tags=existing_tags,
            role=role,
        )
    score = base_score
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
    )
    return ranked[0][0] if ranked else None


def _match_base_spirit(
    ingredients: Sequence[Ingredient],
    profile: Dict[str, float],
    base_spirit: str,
    *,
    association_model: Optional[FlavourAssociationModel] = None,
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
    )
    if spirit:
        return spirit
    return _select_best_match(
        ingredients,
        profile,
        category="spirit",
        association_model=association_model,
        role="base",
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
) -> Optional[Ingredient]:
    garnishes = _find_candidates_by_category(ingredients, "garnish")
    ranked = _rank_ingredients(
        garnishes,
        profile,
        association_model=association_model,
        existing_tags=existing_tags,
        role="garnish",
        keyword_hints=keyword_hints,
    )
    return ranked[0][0] if ranked else None


def generate_cocktail_recipe(
    responses: Dict[str, Optional[str]],
    *,
    bar_id: str,
    repository: Optional[StockRepository] = None,
    association_model: Optional[FlavourAssociationModel] = None,
    recipe_name: str = "Signature Serve",
) -> CocktailRecipe:
    """Generate a personalized cocktail recipe for the specified bar stock."""

    if repository is None:
        repository = StockRepository()

    if association_model is None:
        association_model = _load_default_association_model()

    ingredients = repository.load_bar_stock(bar_id)

    plan = build_preference_plan(responses)
    profile = collect_profile_tags(responses, plan)

    avoid_terms = _extract_avoid_terms(responses.get("notes"))
    if avoid_terms:
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
        )

    if base is None:
        base = _select_best_match(
            ingredients,
            profile,
            category="spirit",
            association_model=association_model,
            role="base",
        )
    if base is None:
        raise ValueError("No base spirit available in the bar stock list.")

    suggestions: List[IngredientSuggestion] = []

    base_amount = plan.base_ml if plan.base_ml > 0 else _default_amount(base.category, base, "base")
    suggestions.append(IngredientSuggestion(base, base_amount, "base"))

    modifiers_pool = [
        ing for ing in _find_candidates_by_category(ingredients, "liqueur", "modifier") if ing != base
    ]
    sweeteners_pool = [
        ing for ing in _find_candidates_by_category(ingredients, "syrup", "sweetener") if ing != base
    ]
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
    )

    if plan.sweetener_ml > 0 and sweet_ranked:
        sweetener, _ = sweet_ranked[0]
        suggestions.append(IngredientSuggestion(sweetener, plan.sweetener_ml, "sweetener"))
        existing_tags = _collect_tags(suggestions)

    juice_hints: List[str] = list(plan.juice_tags)
    if plan.juice_focus:
        juice_hints.append(plan.juice_focus)

    juices_ranked = _rank_ingredients(
        juices_pool,
        profile,
        association_model=association_model,
        existing_tags=existing_tags,
        role="juice",
        keyword_hints=juice_hints,
    )

    wants_juice = bool(juice_hints)
    if wants_juice and juices_ranked:
        juice, _ = juices_ranked[0]
        juice_amount = _default_amount(juice.category, juice, "juice")
        suggestions.append(IngredientSuggestion(juice, juice_amount, "juice"))

    if association_model:
        association_model.rebalance_amounts(suggestions)
        if plan.base_ml > 0 and suggestions and suggestions[0].amount_ml > 0:
            scale = plan.base_ml / suggestions[0].amount_ml
            for suggestion in suggestions:
                suggestion.amount_ml *= scale

    existing_tags = _collect_tags(suggestions)

    garnish_item = None
    if plan.garnish_hint:
        garnish_item = _match_named_ingredient(
            ingredients,
            plan.garnish_hint,
            category="garnish",
        )
    if garnish_item is None:
        garnish_item = _select_garnish(
            ingredients,
            profile,
            association_model=association_model,
            existing_tags=existing_tags,
            keyword_hints=[plan.garnish_hint] if plan.garnish_hint else None,
        )
    garnish_name = garnish_item.name if garnish_item else plan.garnish_hint

    glassware = plan.glass_type or _determine_glassware(responses)
    ice = _determine_ice(responses)

    total_ml = sum(s.amount_ml for s in suggestions if s.amount_ml > 0)
    top_up_needed = max(plan.glass_min_ml - total_ml, 0.0)
    lengthener_note = None
    if top_up_needed > 15:
        if plan.juice_focus:
            lengthener_note = (
                f"Top up with {top_up_needed:.0f} ml chilled {plan.juice_focus} juice to finish the serve."
            )
        else:
            lengthener_note = (
                f"Top up with {top_up_needed:.0f} ml chilled soda or filtered water to finish the serve."
            )

    steps = _build_steps(recipe_name, suggestions, glassware, ice, garnish_name, lengthener_note)

    sorted_profile = sorted(profile.items(), key=lambda item: item[1], reverse=True)

    return CocktailRecipe(
        name=recipe_name,
        glassware=glassware,
        ice=ice,
        ingredients=suggestions,
        steps=steps,
        garnish=garnish_name,
        flavour_profile=sorted_profile,
        notes=responses.get("notes"),
    )


def _build_steps(
    recipe_name: str,
    suggestions: Sequence[IngredientSuggestion],
    glassware: str,
    ice: str,
    garnish: Optional[str],
    lengthener_note: Optional[str],
) -> List[str]:
    steps: List[str] = []
    mixing_vessel = "shaker"
    base = suggestions[0] if suggestions else None
    if base and base.ingredient.category.lower() == "spirit":
        steps.append(
            f"Chill the {glassware.lower()} and ready a {mixing_vessel}."
        )
    else:
        steps.append(f"Prepare the {glassware.lower()} with {ice.lower()}.")

    for suggestion in suggestions:
        steps.append(
            f"Add {suggestion.amount_ml:.0f} ml {suggestion.ingredient.name}"
            + (f" ({suggestion.role})" if suggestion.role != "base" else "")
            + " to the shaker with ice."
        )

    steps.append("Shake vigorously for 10-12 seconds and fine strain into the chilled glass.")
    steps.append(f"Serve over {ice.lower()} in the {glassware.lower()}.")

    if garnish:
        steps.append(f"Garnish with {garnish.lower()} just before serving.")
    if lengthener_note:
        steps.append(lengthener_note)
    steps.append("Present immediately and share the flavour story with the guest.")
    return steps
