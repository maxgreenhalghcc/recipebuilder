"""Core recipe engine for generating personalized cocktail recipes."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from itertools import combinations
from statistics import mean
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple
import json
import re

from recipebuilder.preferences import PreferencePlan, build_preference_plan, collect_profile_tags
from recipebuilder.flavour_context import (
    FlavourKnowledgeBase,
    FlavourVector,
    PourTemplate,
    compute_recipe_similarity,
    evaluate_template_constraints,
)


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
        if "_" in token:
            candidates.append(self.stock_root / f"{token.replace('_', '')}.json")

        path = next((candidate for candidate in candidates if candidate.exists()), None)
        if path is None:
            raise UnknownBarError(
                f"Stock list for bar '{bar_id}' not found at any of {[str(c) for c in candidates]}"
            )

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

        ingredients = []
        for entry in items:
            if not isinstance(entry, dict):
                continue
            name_value = entry.get("name")
            if not name_value or not str(name_value).strip():
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
    if association_model:
        return association_model.score_ingredient(
            ingredient,
            base_score + bonus - penalty,
            existing_tags=existing_tags,
            role=role,
        )
    score = base_score + bonus - penalty
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
    keyword_sequences: List[Optional[Sequence[str]]] = []
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

    if repository is None:
        repository = StockRepository()

    if association_model is None:
        association_model = _load_default_association_model()

    ingredients = repository.load_bar_stock(bar_id)

    flavour_knowledge = _load_flavour_knowledge()
    for item in ingredients:
        flavour_knowledge.enrich_ingredient(item)

    plan = build_preference_plan(responses)
    profile = collect_profile_tags(responses, plan)
    target_vector = flavour_knowledge.build_target_vector(responses, plan=plan)
    target_tag_weights = flavour_knowledge.mapping.target_tags_from_responses(responses)
    control_set = {_normalize(tag) for tag in target_tag_weights.keys()}

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
    if secondary_focus and not _ingredient_matches_keywords(primary_juice, [secondary_focus]):
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

    if glass_min and glass_min >= 240:
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
    if top_up_needed > 15 and (plan.lengthener_allowed is not False):
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
            used_juice_names.add(_normalize(candidate.name))
            suggestions.append(
                IngredientSuggestion(candidate, max(top_up_needed, 40.0), "juice")
            )
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
        if not fallback_name:
            fallback_name = "seasonal juice"
        lengthener_note = (
            f"Top up with {top_up_needed:.0f} ml chilled {fallback_name.lower()} to finish the serve."
        )

    final_payload = [(s.ingredient, s.amount_ml) for s in suggestions if s.amount_ml > 0]
    final_similarity = compute_recipe_similarity(
        flavour_knowledge,
        target_vector,
        final_payload,
    )
    if selected_template is not None:
        template_feedback["similarity"] = final_similarity

    explanations: List[str] = []
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
        explanations=explanations,
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
