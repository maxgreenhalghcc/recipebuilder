"""Utilities for training the flavour association model from cocktail outcomes."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence
import json

from recipebuilder.recipe_engine import (
    FlavourAssociationModel,
    FlavourAssociationObservation,
)


def _normalize(text: str) -> str:
    return text.strip().lower()


def _ensure_list(value: object) -> List[str]:
    if isinstance(value, str):
        return [segment.strip() for segment in value.split(",") if segment.strip()]
    if isinstance(value, Sequence):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _iter_sample_payloads(raw: object) -> Iterable[Dict[str, object]]:
    """Yield raw sample dictionaries from nested JSON payloads."""

    if isinstance(raw, dict):
        if "samples" in raw and isinstance(raw["samples"], Sequence):
            for entry in raw["samples"]:
                yield from _iter_sample_payloads(entry)
        else:
            yield raw
    elif isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
        for entry in raw:
            yield from _iter_sample_payloads(entry)


@dataclass
class CocktailTrainingIngredient:
    """Ingredient used in a logged cocktail outcome."""

    name: str
    role: str
    amount_ml: Optional[float] = None
    flavour_tags: Sequence[str] = ()

    @classmethod
    def from_dict(cls, data: Dict[str, object]) -> "CocktailTrainingIngredient":
        if "name" not in data or "role" not in data:
            raise ValueError("Training ingredient entries require 'name' and 'role'.")
        tags = data.get("flavour_tags")
        if tags is None:
            tags = data.get("tags")
        tag_list = _ensure_list(tags)
        amount = data.get("amount_ml")
        return cls(
            name=str(data["name"]),
            role=str(data["role"]),
            amount_ml=(float(amount) if amount is not None else None),
            flavour_tags=tag_list,
        )


@dataclass
class CocktailTrainingSample:
    """Represents the logged result of a successful cocktail service."""

    name: str
    success_score: float
    ingredients: Sequence[CocktailTrainingIngredient]
    tags: Sequence[str] = ()
    max_score: float = 5.0
    metadata: Optional[Dict[str, object]] = None

    @classmethod
    def from_dict(cls, data: Dict[str, object]) -> "CocktailTrainingSample":
        if "name" not in data or "success_score" not in data:
            raise ValueError("Training samples require 'name' and 'success_score'.")
        raw_ingredients = data.get("ingredients")
        if not isinstance(raw_ingredients, Sequence):
            raise ValueError("Training samples require an 'ingredients' list.")
        ingredients = [
            CocktailTrainingIngredient.from_dict(entry)
            for entry in raw_ingredients
            if isinstance(entry, dict)
        ]
        if not ingredients:
            raise ValueError("Training samples must include at least one valid ingredient entry.")
        tags = data.get("tags")
        return cls(
            name=str(data["name"]),
            success_score=float(data["success_score"]),
            ingredients=ingredients,
            tags=_ensure_list(tags),
            max_score=float(data.get("max_score", 5.0)),
            metadata=(data.get("metadata") if isinstance(data.get("metadata"), dict) else None),
        )

    def normalized_rating(self) -> float:
        scale = self.max_score if self.max_score and self.max_score > 0 else 5.0
        rating = self.success_score / scale
        return max(0.0, min(1.0, rating))

    def to_observation(self, *, rating_floor: float = 0.0) -> Optional[FlavourAssociationObservation]:
        rating = self.normalized_rating()
        if rating <= rating_floor:
            return None

        tag_bucket: Dict[str, float] = {}
        for ingredient in self.ingredients:
            for tag in ingredient.flavour_tags:
                key = _normalize(tag)
                if not key:
                    continue
                tag_bucket[key] = max(tag_bucket.get(key, 0.0), 1.0)
        for tag in self.tags:
            key = _normalize(tag)
            if not key:
                continue
            tag_bucket[key] = max(tag_bucket.get(key, 0.0), 1.0)

        if not tag_bucket:
            return None

        amount_totals: Dict[str, float] = {}
        amount_total = 0.0
        for ingredient in self.ingredients:
            amount = ingredient.amount_ml
            role_key = _normalize(ingredient.role)
            if amount is not None and amount > 0:
                amount_totals[role_key] = amount_totals.get(role_key, 0.0) + amount
                amount_total += amount

        role_ratios: Optional[Dict[str, float]] = None
        if amount_total > 0:
            role_ratios = {
                role: value / amount_total
                for role, value in amount_totals.items()
                if value > 0
            }
        else:
            counts: Dict[str, int] = {}
            for ingredient in self.ingredients:
                role_key = _normalize(ingredient.role)
                if not role_key:
                    continue
                counts[role_key] = counts.get(role_key, 0) + 1
            total_counts = sum(counts.values())
            if total_counts > 0:
                role_ratios = {role: count / total_counts for role, count in counts.items()}

        return FlavourAssociationObservation(
            tags=list(tag_bucket.keys()),
            rating=rating,
            role_ratios=role_ratios,
        )


def build_observations_from_samples(
    samples: Iterable[CocktailTrainingSample],
    *,
    rating_floor: float = 0.0,
) -> List[FlavourAssociationObservation]:
    """Convert cocktail outcomes into flavour association observations."""

    observations: List[FlavourAssociationObservation] = []
    for sample in samples:
        observation = sample.to_observation(rating_floor=rating_floor)
        if observation is not None:
            observations.append(observation)
    return observations


def train_model_from_samples(
    samples: Iterable[CocktailTrainingSample],
    *,
    base_model: Optional[FlavourAssociationModel] = None,
    rating_floor: float = 0.0,
) -> FlavourAssociationModel:
    """Train a flavour association model using recorded cocktail outcomes."""

    model = base_model or FlavourAssociationModel()
    observations = build_observations_from_samples(samples, rating_floor=rating_floor)
    model.train(observations)
    return model


def load_training_samples(path: Path | str) -> List[CocktailTrainingSample]:
    """Load cocktail training samples from a JSON file or directory."""

    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"Training sample data not found at {file_path!s}.")

    if file_path.is_dir():
        samples: List[CocktailTrainingSample] = []
        for candidate in sorted(file_path.glob("*.json")):
            samples.extend(load_training_samples(candidate))
        return samples

    with file_path.open("r", encoding="utf-8") as handle:
        raw = json.load(handle)

    payloads = list(_iter_sample_payloads(raw))
    if not payloads:
        raise ValueError(
            "Training JSON must contain sample objects or a 'samples' collection."
        )

    samples: List[CocktailTrainingSample] = []
    for entry in payloads:
        samples.append(CocktailTrainingSample.from_dict(entry))
    return samples


def save_observations_to_file(
    observations: Sequence[FlavourAssociationObservation],
    path: Path | str,
) -> None:
    """Persist derived observations so they can be reused for future training."""

    serializable = [
        {
            "tags": list(obs.tags),
            "rating": obs.rating,
            "role_ratios": obs.role_ratios,
        }
        for obs in observations
    ]
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with file_path.open("w", encoding="utf-8") as handle:
        json.dump({"observations": serializable}, handle, indent=2)


def save_model_weights(model: FlavourAssociationModel, path: Path | str) -> None:
    """Persist the trained model's learned weights for fast reuse."""

    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with file_path.open("w", encoding="utf-8") as handle:
        json.dump(model.to_weights(), handle, indent=2)


def load_model_weights(path: Path | str) -> FlavourAssociationModel:
    """Load previously saved model weights."""

    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"Model weights not found at {file_path!s}.")
    with file_path.open("r", encoding="utf-8") as handle:
        raw = json.load(handle)
    return FlavourAssociationModel.from_weights(raw)
