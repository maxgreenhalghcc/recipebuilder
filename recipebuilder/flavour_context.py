"""Flavour knowledge base that drives context-aware recipe scoring."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, MutableMapping, Optional, Sequence, Tuple, TYPE_CHECKING
import json
import math

if TYPE_CHECKING:  # pragma: no cover - runtime import guard
    from recipebuilder.preferences import PreferencePlan


def _normalize(text: str) -> str:
    return text.strip().lower()


def _safe_sum(values: Iterable[float]) -> float:
    total = 0.0
    for value in values:
        if not isinstance(value, (int, float)):
            continue
        total += float(value)
    return total


@dataclass
class FlavourAxes:
    taste: Sequence[str]
    aroma: Sequence[str]
    structure: Sequence[str]
    affect: Sequence[str]

    @classmethod
    def from_dict(cls, data: Mapping[str, Sequence[str]]) -> "FlavourAxes":
        flavour_axes = data.get("flavour_axes") if "flavour_axes" in data else data
        return cls(
            taste=list(flavour_axes.get("taste", [])),
            aroma=list(flavour_axes.get("aroma", [])),
            structure=list(flavour_axes.get("structure", [])),
            affect=list(flavour_axes.get("affect", [])),
        )


@dataclass
class FlavourVector:
    taste: Dict[str, float] = field(default_factory=dict)
    aroma: Dict[str, float] = field(default_factory=dict)
    structure: Dict[str, float] = field(default_factory=dict)
    affect: Dict[str, float] = field(default_factory=dict)

    def normalized(self) -> "FlavourVector":
        return FlavourVector(
            taste=_normalize_distribution(self.taste),
            aroma=_normalize_distribution(self.aroma),
            structure=_normalize_distribution(self.structure),
            affect=_normalize_distribution(self.affect),
        )

    def blend(self, other: "FlavourVector", weight: float) -> "FlavourVector":
        weight = max(0.0, min(1.0, weight))
        inv = 1.0 - weight
        return FlavourVector(
            taste=_blend_maps(self.taste, other.taste, inv, weight),
            aroma=_blend_maps(self.aroma, other.aroma, inv, weight),
            structure=_blend_maps(self.structure, other.structure, inv, weight),
            affect=_blend_maps(self.affect, other.affect, inv, weight),
        )

    def dot(self, other: "FlavourVector", *, weights: Optional[Dict[str, float]] = None) -> float:
        taste_weight = 1.0
        aroma_weight = 1.0
        structure_weight = 0.6
        affect_weight = 0.8
        if weights:
            taste_weight = weights.get("taste", taste_weight)
            aroma_weight = weights.get("aroma", aroma_weight)
            structure_weight = weights.get("structure", structure_weight)
            affect_weight = weights.get("affect", affect_weight)

        return (
            taste_weight * _dot_maps(self.taste, other.taste)
            + aroma_weight * _dot_maps(self.aroma, other.aroma)
            + structure_weight * _dot_maps(self.structure, other.structure)
            + affect_weight * _dot_maps(self.affect, other.affect)
        )

    def cosine_similarity(self, other: "FlavourVector") -> float:
        numerator = self.dot(other)
        denominator = math.sqrt(self.dot(self)) * math.sqrt(other.dot(other))
        if denominator == 0:
            return 0.0
        return numerator / denominator


def _normalize_distribution(values: Mapping[str, float]) -> Dict[str, float]:
    cleaned: Dict[str, float] = {}
    for key, value in values.items():
        normalized_key = _normalize(key)
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        if numeric <= 0:
            continue
        cleaned[normalized_key] = cleaned.get(normalized_key, 0.0) + numeric
    total = _safe_sum(cleaned.values())
    if total <= 0:
        return dict(cleaned)
    return {key: value / total for key, value in cleaned.items()}


def _blend_maps(
    left: Mapping[str, float],
    right: Mapping[str, float],
    left_weight: float,
    right_weight: float,
) -> Dict[str, float]:
    result: Dict[str, float] = {}
    for key, value in left.items():
        normalized = _normalize(key)
        result[normalized] = result.get(normalized, 0.0) + value * left_weight
    for key, value in right.items():
        normalized = _normalize(key)
        result[normalized] = result.get(normalized, 0.0) + value * right_weight
    return result


def _dot_maps(left: Mapping[str, float], right: Mapping[str, float]) -> float:
    score = 0.0
    for key, value in left.items():
        score += value * right.get(key, 0.0)
    return score


@dataclass
class IngredientVector:
    id: str
    name: str
    category: str
    taste_per_30ml: Dict[str, float]
    aroma: Dict[str, float]
    structure: Dict[str, float]
    flags: Sequence[str] = ()
    pairing_prior: Dict[str, float] = field(default_factory=dict)
    compounds: Sequence[str] = ()

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> "IngredientVector":
        return cls(
            id=str(data.get("id") or _normalize(str(data["name"]))),
            name=str(data["name"]),
            category=str(data.get("category", "other")),
            taste_per_30ml={
                _normalize(key): float(value)
                for key, value in (data.get("taste_per_30ml") or {}).items()
            },
            aroma={
                _normalize(key): float(value)
                for key, value in (data.get("aroma") or {}).items()
            },
            structure={
                _normalize(key): float(value)
                for key, value in (data.get("structure") or {}).items()
            },
            flags=[_normalize(flag) for flag in data.get("flags", []) if flag],
            pairing_prior={
                _normalize(key): float(value)
                for key, value in (data.get("pairing_prior", {}).get("with", {})).items()
            },
            compounds=[_normalize(compound) for compound in data.get("compounds", []) if compound],
        )

    def flavour_vector(self, *, ml: float = 30.0) -> FlavourVector:
        scale = ml / 30.0 if ml else 0.0
        taste = {axis: value * scale for axis, value in self.taste_per_30ml.items()}
        aroma = {axis: value for axis, value in self.aroma.items()}
        structure = {axis: value * scale for axis, value in self.structure.items()}
        affect: Dict[str, float] = {}
        if self.pairing_prior:
            affect = {
                axis: value for axis, value in self.pairing_prior.items() if axis in {"refreshing", "indulgent", "elegant", "bold"}
            }
        return FlavourVector(taste=taste, aroma=aroma, structure=structure, affect=affect)


@dataclass
class QuestionnaireMapping:
    field_weights: Dict[str, float]
    answers_to_tags: Dict[str, Sequence[str]]
    notes_keywords: Dict[str, Sequence[str]]

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> "QuestionnaireMapping":
        return cls(
            field_weights={
                _normalize(field): float(weight)
                for field, weight in (data.get("question_field_weights") or {}).items()
            },
            answers_to_tags={
                _normalize(key): [
                    _normalize(tag) for tag in value if isinstance(tag, str) and tag
                ]
                for key, value in (data.get("answers_to_tags") or {}).items()
            },
            notes_keywords={
                _normalize(key): [
                    _normalize(tag) for tag in value if isinstance(tag, str) and tag
                ]
                for key, value in (data.get("notes_keywords") or {}).items()
            },
        )

    def target_tags_from_responses(self, responses: Mapping[str, Optional[str]]) -> Dict[str, float]:
        weighted: Dict[str, float] = {}
        for field, weight in self.field_weights.items():
            answer = responses.get(field)
            if not answer:
                continue
            key = f"{field}::{_normalize(str(answer))}"
            tags = self.answers_to_tags.get(_normalize(key))
            if not tags:
                continue
            for tag in tags:
                weighted[tag] = weighted.get(tag, 0.0) + weight

        notes = _normalize(str(responses.get("notes") or ""))
        if notes:
            for keyword, tags in self.notes_keywords.items():
                if keyword in notes:
                    for tag in tags:
                        weighted[tag] = weighted.get(tag, 0.0) + self.field_weights.get("notes_keyword_bonus", 0.5)

        total = _safe_sum(weighted.values())
        if total <= 0:
            return weighted
        return {tag: value / total for tag, value in weighted.items()}


@dataclass
class PourTemplate:
    id: str
    name: str
    role_ratios: Dict[str, float]
    constraints: Dict[str, object] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> "PourTemplate":
        ratios = {
            _normalize(role): float(value)
            for role, value in (data.get("role_ratios") or {}).items()
            if float(value) > 0
        }
        total = _safe_sum(ratios.values())
        if total > 0:
            ratios = {role: value / total for role, value in ratios.items()}
        return cls(
            id=str(data.get("id") or data.get("name")),
            name=str(data.get("name") or data.get("id")),
            role_ratios=ratios,
            constraints=dict(data.get("constraints") or {}),
        )

    def role_ratio(self, role: str) -> float:
        return self.role_ratios.get(_normalize(role), 0.0)


class FlavourKnowledgeBase:
    """Centralised loader for flavour space metadata."""

    def __init__(
        self,
        *,
        axes_path: Path | str = Path("data/flavour/flavour_space.json"),
        mapping_path: Path | str = Path("data/flavour/question_mapping.json"),
        ingredient_vector_path: Path | str = Path("data/flavour/ingredient_vectors.json"),
        template_path: Path | str = Path("data/flavour/templates.json"),
        adjective_training_path: Path | str = Path("data/training/human_adjective_training.json"),
    ) -> None:
        self.axes = self._load_axes(axes_path)
        self.mapping = self._load_mapping(mapping_path)
        self.ingredient_vectors = self._load_ingredient_vectors(ingredient_vector_path)
        self.templates = self._load_templates(template_path)
        self.adjective_preferences = self._load_adjective_preferences(adjective_training_path)

    @staticmethod
    def _read_json(path: Path | str) -> object:
        file_path = Path(path)
        with file_path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def _load_axes(self, path: Path | str) -> FlavourAxes:
        raw = self._read_json(path)
        if not isinstance(raw, Mapping):
            raise ValueError("Flavour axes JSON must be an object.")
        return FlavourAxes.from_dict(raw)

    def _load_mapping(self, path: Path | str) -> QuestionnaireMapping:
        raw = self._read_json(path)
        if not isinstance(raw, Mapping):
            raise ValueError("Question mapping JSON must be an object.")
        return QuestionnaireMapping.from_dict(raw)

    def _load_ingredient_vectors(self, path: Path | str) -> Dict[str, IngredientVector]:
        raw = self._read_json(path)
        if not isinstance(raw, Sequence):
            raise ValueError("Ingredient vector JSON must be a list of ingredient entries.")
        vectors: Dict[str, IngredientVector] = {}
        for entry in raw:
            if not isinstance(entry, Mapping):
                continue
            vector = IngredientVector.from_dict(entry)
            vectors[_normalize(vector.name)] = vector
        return vectors

    def _load_templates(self, path: Path | str) -> List[PourTemplate]:
        raw = self._read_json(path)
        if not isinstance(raw, Sequence):
            raise ValueError("Template JSON must be a list of template definitions.")
        templates: List[PourTemplate] = []
        for entry in raw:
            if isinstance(entry, Mapping):
                templates.append(PourTemplate.from_dict(entry))
        return templates

    def _load_adjective_preferences(self, path: Path | str) -> Dict[str, Dict[str, object]]:
        file_path = Path(path)
        if not file_path.exists():
            return {"templates": {}, "adjectives": {}}
        raw = self._read_json(file_path)
        if isinstance(raw, Mapping) and "samples" in raw:
            samples = raw.get("samples", [])
        elif isinstance(raw, Sequence):
            samples = raw
        else:
            samples = []
        templates: Dict[str, Dict[str, object]] = {}
        adjectives: Dict[str, Dict[str, object]] = {}
        for entry in samples:
            if not isinstance(entry, Mapping):
                continue
            adjectives_raw = entry.get("adjectives") or []
            if isinstance(adjectives_raw, str):
                adjective_list = [segment.strip() for segment in adjectives_raw.split(",") if segment.strip()]
            else:
                adjective_list = [str(item).strip() for item in adjectives_raw if str(item).strip()]
            template_id = _normalize(str(entry.get("target_template") or ""))
            if not template_id:
                continue
            weight = float(entry.get("success_score", 1.0))
            ratio_hint = entry.get("ratio_hint") or {}
            recommended_roles = entry.get("recommended_roles") or {}
            template_entry = templates.setdefault(template_id, {"weight": 0.0, "ratios": defaultdict(float)})
            template_entry["weight"] += weight
            if isinstance(ratio_hint, Mapping):
                for role, value in ratio_hint.items():
                    try:
                        template_entry["ratios"][ _normalize(role) ] += float(value) * weight
                    except (TypeError, ValueError):
                        continue
            for adjective in adjective_list:
                key = _normalize(adjective)
                if not key:
                    continue
                adj_entry = adjectives.setdefault(
                    key,
                    {
                        "templates": defaultdict(float),
                        "ratios": defaultdict(float),
                        "roles": defaultdict(float),
                    },
                )
                adj_entry["templates"][template_id] += weight
                if isinstance(ratio_hint, Mapping):
                    for role, value in ratio_hint.items():
                        try:
                            adj_entry["ratios"][ _normalize(role) ] += float(value) * weight
                        except (TypeError, ValueError):
                            continue
                if isinstance(recommended_roles, Mapping):
                    for role, ingredient in recommended_roles.items():
                        if ingredient:
                            adj_entry["roles"][ _normalize(role) ] += weight

        for template_id, info in templates.items():
            weight = info.get("weight", 0.0)
            ratios = info.get("ratios", {})
            if weight and isinstance(ratios, Mapping):
                info["ratios"] = {
                    role: value / weight for role, value in ratios.items() if weight
                }
            else:
                info["ratios"] = dict(ratios)
        for adjective, info in adjectives.items():
            ratios = info.get("ratios", {})
            template_weights = info.get("templates", {})
            total_weight = sum(template_weights.values()) or 1.0
            info["ratios"] = {
                role: value / total_weight for role, value in ratios.items() if total_weight
            }
            info["templates"] = dict(template_weights)
            info["roles"] = dict(info.get("roles", {}))
        return {"templates": templates, "adjectives": adjectives}

    def build_target_vector(
        self,
        responses: Mapping[str, Optional[str]],
        plan: Optional["PreferencePlan"] = None,
    ) -> FlavourVector:
        tag_weights = self.mapping.target_tags_from_responses(responses)
        taste: MutableMapping[str, float] = {}
        aroma: MutableMapping[str, float] = {}
        structure: MutableMapping[str, float] = {}
        affect: MutableMapping[str, float] = {}

        for tag, weight in tag_weights.items():
            if tag in self.axes.taste:
                taste[tag] = taste.get(tag, 0.0) + weight
            elif tag in self.axes.aroma:
                aroma[tag] = aroma.get(tag, 0.0) + weight
            elif tag in self.axes.structure:
                structure[tag] = structure.get(tag, 0.0) + weight
            elif tag in self.axes.affect:
                affect[tag] = affect.get(tag, 0.0) + weight
            else:
                if tag in {"zesty", "citrus", "tart", "bright"}:
                    aroma["citrus"] = aroma.get("citrus", 0.0) + weight
                    taste["sour"] = taste.get("sour", 0.0) + 0.5 * weight
                elif tag in {"sweet", "indulgent", "rich"}:
                    taste["sweet"] = taste.get("sweet", 0.0) + weight
                    affect["indulgent"] = affect.get("indulgent", 0.0) + 0.4 * weight
                elif tag in {"herbal", "fresh"}:
                    aroma["herbal"] = aroma.get("herbal", 0.0) + weight
                    affect["refreshing"] = affect.get("refreshing", 0.0) + 0.3 * weight
                elif tag in {"tropical", "fruit", "berry"}:
                    aroma["tropical"] = aroma.get("tropical", 0.0) + weight
                    affect["refreshing"] = affect.get("refreshing", 0.0) + 0.2 * weight

        if plan is not None:
            for key, value in plan.taste_bias.items():
                normalized = _normalize(key)
                taste[normalized] = taste.get(normalized, 0.0) + float(value)
            for key, value in plan.aroma_bias.items():
                normalized = _normalize(key)
                aroma[normalized] = aroma.get(normalized, 0.0) + float(value)
            for key, value in plan.structure_bias.items():
                normalized = _normalize(key)
                structure[normalized] = structure.get(normalized, 0.0) + float(value)
            if plan.sparkle_bias is not None:
                structure["sparkle"] = structure.get("sparkle", 0.0) + float(plan.sparkle_bias)
            for key, value in plan.affect_bias.items():
                normalized = _normalize(key)
                affect[normalized] = affect.get(normalized, 0.0) + float(value)

        return FlavourVector(
            taste=dict(taste),
            aroma=dict(aroma),
            structure=dict(structure),
            affect=dict(affect),
        ).normalized()

    def ingredient_vector_for(self, name: str) -> Optional[IngredientVector]:
        return self.ingredient_vectors.get(_normalize(name))

    def enrich_ingredient(self, ingredient: object) -> None:
        vector = self.ingredient_vector_for(getattr(ingredient, "name", ""))
        if not vector:
            return
        setattr(ingredient, "taste_vector", dict(vector.taste_per_30ml))
        setattr(ingredient, "aroma_vector", dict(vector.aroma))
        setattr(ingredient, "structure_vector", dict(vector.structure))
        setattr(ingredient, "flags", tuple(vector.flags))
        setattr(ingredient, "pairing_prior", dict(vector.pairing_prior))
        setattr(ingredient, "compounds", tuple(vector.compounds))

    def flavour_vector_for_ingredient(self, ingredient: object, ml: float) -> FlavourVector:
        vector = self.ingredient_vector_for(getattr(ingredient, "name", ""))
        if not vector:
            return FlavourVector()
        return vector.flavour_vector(ml=ml)

    def select_templates_for_target(
        self,
        target: FlavourVector,
        target_tags: Optional[Mapping[str, float]] = None,
        *,
        plan: Optional["PreferencePlan"] = None,
        limit: int = 3,
    ) -> List[PourTemplate]:
        scored: List[Tuple[float, PourTemplate]] = []
        tag_map: Dict[str, float] = {}
        if target_tags:
            for key, value in target_tags.items():
                try:
                    tag_map[_normalize(key)] = float(value)
                except (TypeError, ValueError):
                    continue
        adjective_preferences = self.adjective_preferences.get("adjectives", {})
        template_preferences = self.adjective_preferences.get("templates", {})
        plan_weights: Dict[str, float] = {}
        if plan is not None and plan.template_weights:
            plan_weights = {
                _normalize(key): float(value)
                for key, value in plan.template_weights.items()
            }
        for template in self.templates:
            template_id = _normalize(template.id or template.name)
            affect_bias = template.constraints.get("affect_bias")
            if isinstance(affect_bias, Mapping):
                pseudo_vector = FlavourVector(affect={_normalize(k): float(v) for k, v in affect_bias.items()})
                similarity = target.cosine_similarity(pseudo_vector)
            else:
                similarity = target.dot(FlavourVector())
            preference_bonus = 0.0
            if tag_map and adjective_preferences:
                for tag, weight in tag_map.items():
                    pref = adjective_preferences.get(tag)
                    if not pref:
                        continue
                    template_weights = pref.get("templates", {})
                    preference_bonus += template_weights.get(template_id, 0.0) * weight
            template_info = template_preferences.get(template_id)
            if template_info:
                preference_bonus += 0.05 * template_info.get("weight", 0.0)
            if plan_weights:
                preference_bonus += 0.1 * plan_weights.get(template_id, 0.0)
            scored.append((similarity + 0.05 * preference_bonus, template))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [template for _, template in scored[:limit]]

    def score_ingredient_against_target(
        self,
        ingredient: object,
        target: FlavourVector,
        *,
        role: Optional[str] = None,
    ) -> float:
        vector = self.ingredient_vector_for(getattr(ingredient, "name", ""))
        if not vector:
            return 0.0
        ingredient_vector = vector.flavour_vector()
        score = ingredient_vector.dot(target)
        if role:
            role_key = _normalize(role)
            if role_key in {"modifier", "sweetener"}:
                score += 0.05 * ingredient_vector.aroma.get("citrus", 0.0)
            if role_key == "juice":
                score += 0.05 * ingredient_vector.taste.get("sour", 0.0)
        pairing_bonus = 0.0
        if target.affect:
            for affect_tag, weight in target.affect.items():
                pairing_bonus += vector.pairing_prior.get(affect_tag, 0.0) * weight
        return score + pairing_bonus


def _recipe_vector(
    knowledge: FlavourKnowledgeBase,
    ingredients: Sequence[Tuple[object, float]],
) -> FlavourVector:
    taste: Dict[str, float] = {}
    aroma: Dict[str, float] = {}
    structure: Dict[str, float] = {}
    affect: Dict[str, float] = {}
    for ingredient, amount in ingredients:
        vector = knowledge.flavour_vector_for_ingredient(ingredient, amount)
        for key, value in vector.taste.items():
            taste[key] = taste.get(key, 0.0) + value
        for key, value in vector.aroma.items():
            aroma[key] = aroma.get(key, 0.0) + value
        for key, value in vector.structure.items():
            structure[key] = structure.get(key, 0.0) + value
        for key, value in vector.affect.items():
            affect[key] = affect.get(key, 0.0) + value
    return FlavourVector(taste=taste, aroma=aroma, structure=structure, affect=affect)


def compute_recipe_similarity(
    knowledge: FlavourKnowledgeBase,
    target: FlavourVector,
    ingredients: Sequence[Tuple[object, float]],
) -> float:
    if not ingredients:
        return 0.0
    vector = _recipe_vector(knowledge, ingredients)
    return vector.cosine_similarity(target)


def evaluate_template_constraints(
    knowledge: FlavourKnowledgeBase,
    template: PourTemplate,
    ingredients: Sequence[Tuple[object, float]],
) -> Dict[str, float]:
    results: Dict[str, float] = {}
    recipe_vector = _recipe_vector(knowledge, ingredients)
    total_ml = _safe_sum(amount for _, amount in ingredients)
    abv = 0.0
    if total_ml > 0:
        abv = recipe_vector.structure.get("strength_abv", 0.0) / total_ml
    constraints = template.constraints

    abv_range = constraints.get("abv_range")
    if isinstance(abv_range, Sequence) and len(abv_range) == 2:
        low, high = float(abv_range[0]) / 100.0, float(abv_range[1]) / 100.0
        if abv < low:
            results["abv_under"] = low - abv
        elif abv > high:
            results["abv_over"] = abv - high

    taste_targets = constraints.get("taste_targets") or {}
    if "sweet:acid_ratio" in taste_targets:
        window = taste_targets["sweet:acid_ratio"]
        if isinstance(window, Sequence) and len(window) == 2:
            sweet = recipe_vector.taste.get("sweet", 0.0)
            acid = recipe_vector.taste.get("sour", 0.0)
            ratio = sweet / acid if acid > 0 else float("inf")
            low, high = float(window[0]), float(window[1])
            if ratio < low:
                results["sweetness_low"] = low - ratio
            elif ratio > high:
                results["sweetness_high"] = ratio - high

    taste_caps = constraints.get("taste_caps") or {}
    if isinstance(taste_caps, Mapping):
        for taste_key, limit in taste_caps.items():
            try:
                threshold = float(limit)
            except (TypeError, ValueError):
                continue
            normalized = _normalize(str(taste_key))
            current = recipe_vector.taste.get(normalized, recipe_vector.taste.get(str(taste_key), 0.0))
            if current > threshold:
                results[f"{normalized}_over"] = current - threshold

    return results

