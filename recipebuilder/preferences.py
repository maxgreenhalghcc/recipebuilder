"""Preference planning based on questionnaire-driven persona profiles."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, Mapping, Optional, Sequence, Tuple
import json

OZ_TO_ML = 29.5735


def oz_to_ml(oz: float) -> float:
    """Convert fluid ounces to millilitres."""

    return oz * OZ_TO_ML


def _normalize(text: str) -> str:
    return text.strip().lower()


def _read_json(path: Path) -> object:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _load_profile_map(filename: str) -> Dict[str, Mapping[str, object]]:
    path = Path(__file__).resolve().parent.parent / "data" / "flavour" / filename
    if not path.exists():
        return {}
    data = _read_json(path)
    if not isinstance(data, Mapping):
        return {}
    return { _normalize(key): value for key, value in data.items() if isinstance(value, Mapping) }


_SEASON_PROFILES = _load_profile_map("season_profiles.json")
_GLASS_PROFILES = _load_profile_map("glass_profiles.json")
_DINING_PROFILES = _load_profile_map("dining_profiles.json")
_MUSIC_PROFILES = _load_profile_map("music_profiles.json")
_AROMA_PROFILES = _load_profile_map("aroma_profiles.json")
_DESSERT_PROFILES = _load_profile_map("dessert_profiles.json")
_COLOUR_PROFILES = _load_profile_map("colour_profiles.json")
_SWEETENER_STYLES = _load_profile_map("sweetener_styles.json")

_STRENGTH_SCALE = {
    "low": 0.2,
    "low_mid": 0.35,
    "mid": 0.5,
    "mid_high": 0.65,
    "high": 0.8,
}

_TEMPERATURE_SCALE = {
    "warm": 0.3,
    "cold": 0.7,
    "very_cold": 0.9,
    "cold_or_warm": 0.5,
}

_BASE_JUICE_PRIORITY = (
    "pineapple juice",
    "passion fruit juice",
    "orange juice",
    "cranberry juice",
)


@dataclass
class PreferencePlan:
    """Pre-computed pour and flavour information for a guest."""

    strength_oz: float
    modifier_oz: float
    sweetener_oz: float
    glass_type: str
    glass_min_ml: float
    garnish_hint: Optional[str]
    juice_focus: Optional[str]

    modifier_tags: Sequence[str] = field(default_factory=tuple)
    sweetener_tags: Sequence[str] = field(default_factory=tuple)
    juice_tags: Sequence[str] = field(default_factory=tuple)
    seasonal_tags: Sequence[str] = field(default_factory=tuple)

    template_weights: Dict[str, float] = field(default_factory=dict)
    ratio_targets: Dict[str, Tuple[float, float]] = field(default_factory=dict)
    sweet_acid_window: Optional[Tuple[float, float]] = None
    base_spirit_bias: Dict[str, float] = field(default_factory=dict)
    taste_bias: Dict[str, float] = field(default_factory=dict)
    aroma_bias: Dict[str, float] = field(default_factory=dict)
    structure_bias: Dict[str, float] = field(default_factory=dict)
    affect_bias: Dict[str, float] = field(default_factory=dict)
    tag_weights: Dict[str, float] = field(default_factory=dict)

    candidate_pools: Dict[str, Sequence[str]] = field(default_factory=dict)
    avoid_or_reduce: Sequence[str] = field(default_factory=tuple)
    garnish_pool: Sequence[str] = field(default_factory=tuple)
    lengtheners: Sequence[str] = field(default_factory=tuple)

    sparkle_bias: Optional[float] = None
    sparkle_allowed: Optional[bool] = None
    lengthener_allowed: Optional[bool] = None
    abv_range: Optional[Tuple[float, float]] = None
    ingredient_max: Optional[int] = None
    ice_program: Optional[str] = None
    bitterness_tolerance: Optional[float] = None

    @property
    def base_ml(self) -> float:
        return oz_to_ml(self.strength_oz)

    @property
    def modifier_ml(self) -> float:
        return oz_to_ml(self.modifier_oz)

    @property
    def sweetener_ml(self) -> float:
        return oz_to_ml(self.sweetener_oz)

    def total_core_ml(self) -> float:
        return self.base_ml + self.modifier_ml + self.sweetener_ml


def _merge_numeric_map(target: Dict[str, float], source: Mapping[str, object], weight: float = 1.0) -> None:
    for key, value in source.items():
        normalized_key = _normalize(str(key))
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        target[normalized_key] = target.get(normalized_key, 0.0) + numeric * weight


def _merge_ratio_ranges(
    accumulator: Dict[str, list[Tuple[float, float]]],
    ranges: Mapping[str, Sequence[object]],
) -> None:
    for role, window in ranges.items():
        if not isinstance(window, Sequence) or len(window) != 2:
            continue
        try:
            low = float(window[0])
            high = float(window[1])
        except (TypeError, ValueError):
            continue
        if low <= 0 and high <= 0:
            continue
        accumulator[_normalize(role)].append((low, high))


def _merge_candidate_pool(store: Dict[str, set[str]], key: str, values: Iterable[str]) -> None:
    bucket = store.setdefault(key, set())
    for value in values:
        cleaned = value.strip()
        if cleaned:
            bucket.add(cleaned)


def _structure_value(key: str, value: object) -> Optional[float]:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        normalized = _normalize(value)
        if key == "temperature":
            return _TEMPERATURE_SCALE.get(normalized)
        if key == "strength_abv":
            return _STRENGTH_SCALE.get(normalized)
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _compute_average_window(windows: Sequence[Tuple[float, float]] | None) -> Optional[Tuple[float, float]]:
    if not windows:
        return None
    lows = [window[0] for window in windows if window[0] >= 0]
    highs = [window[1] for window in windows if window[1] >= 0]
    if not lows or not highs:
        return None
    return (sum(lows) / len(lows), sum(highs) / len(highs))


def _apply_shift(window: Optional[Tuple[float, float]], shift: Tuple[float, float]) -> Optional[Tuple[float, float]]:
    if window is None:
        return None
    low, high = window
    return (max(0.0, low + shift[0]), max(low + 0.05, high + shift[1]))


def build_preference_plan(responses: Dict[str, Optional[str]]) -> PreferencePlan:
    season_key = _normalize(responses.get("season") or "")
    house_key = _normalize(responses.get("house_type") or "")
    dining_key = _normalize(responses.get("dining_style") or "")
    music_key = _normalize(responses.get("music_preference") or "")
    aroma_key = _normalize(responses.get("aroma_preference") or "")
    dessert_key = _normalize(responses.get("favourite_dessert") or "")
    colour_key = _normalize(responses.get("modifier_question") or "")
    sweetener_key = _normalize(responses.get("sweetener_question") or "")

    profiles = [
        _SEASON_PROFILES.get(season_key, {}),
        _GLASS_PROFILES.get(house_key, {}),
        _DINING_PROFILES.get(dining_key, {}),
        _MUSIC_PROFILES.get(music_key, {}),
        _AROMA_PROFILES.get(aroma_key, {}),
        _DESSERT_PROFILES.get(dessert_key, {}),
        _COLOUR_PROFILES.get(colour_key, {}),
        _SWEETENER_STYLES.get(sweetener_key, {}),
    ]

    template_weights: Dict[str, float] = defaultdict(float)
    ratio_ranges: Dict[str, list[Tuple[float, float]]] = defaultdict(list)
    sweet_acid_windows: list[Tuple[float, float]] = []
    sweet_acid_shifts: list[Tuple[float, float]] = []
    base_bias: Dict[str, float] = defaultdict(float)
    taste_bias: Dict[str, float] = defaultdict(float)
    aroma_bias: Dict[str, float] = defaultdict(float)
    structure_bias: Dict[str, float] = defaultdict(float)
    affect_bias: Dict[str, float] = defaultdict(float)
    tag_weights: Dict[str, float] = defaultdict(float)
    candidate_sets: Dict[str, set[str]] = defaultdict(set)
    garnish_candidates: set[str] = set()
    lengtheners: set[str] = set()
    avoid_set: set[str] = set()
    seasonal_tags: set[str] = set()
    sweetness_windows = []
    abv_ranges: list[Tuple[float, float]] = []
    sparkle_biases: list[float] = []
    sparkle_allowances: list[bool] = []
    ingredient_caps: list[int] = []
    ice_programs: list[str] = []
    bitterness_levels: list[float] = []

    for profile in profiles:
        if not profile:
            continue
        tags = profile.get("tags") or []
        aroma_tags = profile.get("aroma_tags") or []
        for tag in tags:
            tag_weights[_normalize(tag)] += 1.0
        for tag in aroma_tags:
            normalized = _normalize(tag)
            tag_weights[normalized] += 1.0
            aroma_bias[normalized] += 1.0
            seasonal_tags.add(normalized)

        taste = profile.get("taste_bias") or {}
        if isinstance(taste, Mapping):
            _merge_numeric_map(taste_bias, taste)

        structure = profile.get("structure_bias") or {}
        if isinstance(structure, Mapping):
            for key, value in structure.items():
                numeric = _structure_value(_normalize(key), value)
                if numeric is not None:
                    structure_bias[_normalize(key)] += numeric
            sparkle = structure.get("sparkle")
            sparkle_numeric = _structure_value("sparkle", sparkle)
            if sparkle_numeric is not None:
                sparkle_biases.append(sparkle_numeric)

        affect = profile.get("affect_tags") or []
        for tag in affect:
            affect_bias[_normalize(tag)] += 1.0

        preferred_templates = profile.get("preferred_templates") or {}
        if isinstance(preferred_templates, Mapping):
            for template_id, weight in preferred_templates.items():
                try:
                    template_weights[_normalize(template_id)] += float(weight)
                except (TypeError, ValueError):
                    continue

        ratio_nudge = profile.get("ratio_nudge") or {}
        if isinstance(ratio_nudge, Mapping):
            _merge_ratio_ranges(ratio_ranges, ratio_nudge)

        window = profile.get("sweet_acid_window")
        if isinstance(window, Sequence) and len(window) == 2:
            try:
                sweet_acid_windows.append((float(window[0]), float(window[1])))
            except (TypeError, ValueError):
                pass

        shift = profile.get("sweet_acid_window_shift")
        if isinstance(shift, Sequence) and len(shift) == 2:
            try:
                sweet_acid_shifts.append((float(shift[0]), float(shift[1])))
            except (TypeError, ValueError):
                pass

        base_pref = profile.get("base_spirit_bias") or {}
        if isinstance(base_pref, Mapping):
            for spirit, weight in base_pref.items():
                try:
                    base_bias[_normalize(spirit)] += float(weight)
                except (TypeError, ValueError):
                    continue

        _merge_candidate_pool(candidate_sets, "modifier", profile.get("modifiers_pool") or [])
        _merge_candidate_pool(candidate_sets, "sweetener", profile.get("sweeteners_pool") or [])
        _merge_candidate_pool(candidate_sets, "juice", profile.get("juice_pool") or [])
        _merge_candidate_pool(candidate_sets, "herb", profile.get("herbs_spices") or [])
        _merge_candidate_pool(candidate_sets, "juice", profile.get("juice_synergy") or [])
        garnish_candidates.update(profile.get("garnishes") or [])
        lengtheners.update(profile.get("lengtheners") or [])
        avoid_set.update(_normalize(value) for value in profile.get("avoid_or_reduce") or [])

        if "bitterness_tolerance" in profile:
            try:
                bitterness_levels.append(float(profile["bitterness_tolerance"]))
            except (TypeError, ValueError):
                pass

        constraints = profile.get("constraints")
        if isinstance(constraints, Mapping):
            abv_range = constraints.get("abv_range")
            if isinstance(abv_range, Sequence) and len(abv_range) == 2:
                try:
                    abv_ranges.append((float(abv_range[0]), float(abv_range[1])))
                except (TypeError, ValueError):
                    pass
            if "lengthener_allowed" in constraints:
                sparkle_allowances.append(bool(constraints.get("lengthener_allowed")))
            if "sparkle_allowed" in constraints:
                # treat sparkle flag as allow lengthener synonyms as well
                sparkle_allowances.append(bool(constraints.get("sparkle_allowed")))
            if "ingredient_count_max" in constraints:
                try:
                    ingredient_caps.append(int(constraints.get("ingredient_count_max")))
                except (TypeError, ValueError):
                    pass

        glass = profile.get("glass")
        if isinstance(glass, Mapping) and "ice" in profile:
            ice_programs.append(str(profile.get("ice")))

    strength_oz = 2.0
    if music_key in _MUSIC_PROFILES:
        try:
            strength_oz = float(_MUSIC_PROFILES[music_key].get("strength_oz", strength_oz))
        except (TypeError, ValueError):
            pass

    modifier_oz = 0.75
    sweetener_oz = 0.5

    glass_profile = _GLASS_PROFILES.get(house_key, {})
    glass_spec = glass_profile.get("glass") if isinstance(glass_profile.get("glass"), Mapping) else {}
    glass_type = str(glass_spec.get("type") or "double old fashioned")
    try:
        glass_min_ml = float(glass_spec.get("min_ml", 180.0))
    except (TypeError, ValueError):
        glass_min_ml = 180.0
    garnish_hint = None
    if garnish_candidates:
        garnish_hint = sorted(garnish_candidates)[0]

    juice_focus = None
    aggregated_juices = list(candidate_sets.get("juice", set()))
    for candidate in _BASE_JUICE_PRIORITY:
        for juice in aggregated_juices:
            if _normalize(candidate) == _normalize(juice):
                juice_focus = juice
                break
        if juice_focus:
            break
    if not juice_focus and aggregated_juices:
        juice_focus = aggregated_juices[0]

    sweet_acid_window = _compute_average_window(sweet_acid_windows)
    for shift in sweet_acid_shifts:
        sweet_acid_window = _apply_shift(sweet_acid_window, shift)

    ratio_targets = {
        role: _compute_average_window(windows)
        for role, windows in ratio_ranges.items()
        if _compute_average_window(windows) is not None
    }
    ratio_targets = {role: window for role, window in ratio_targets.items() if window is not None}

    if sparkle_allowances:
        sparkle_allowed = any(sparkle_allowances)
    else:
        sparkle_allowed = None

    if sparkle_biases:
        sparkle_bias = sum(sparkle_biases) / len(sparkle_biases)
    else:
        sparkle_bias = None

    if abv_ranges:
        low_avg = sum(r[0] for r in abv_ranges) / len(abv_ranges)
        high_avg = sum(r[1] for r in abv_ranges) / len(abv_ranges)
        abv_range = (low_avg, max(low_avg + 1.0, high_avg))
    else:
        abv_range = None

    ingredient_max = min(ingredient_caps) if ingredient_caps else None

    ice_program = None
    if ice_programs:
        ice_program = ice_programs[-1]
    elif isinstance(glass_profile.get("ice"), str):
        ice_program = str(glass_profile["ice"])

    candidate_pools = {key: tuple(sorted(values)) for key, values in candidate_sets.items() if values}
    garnish_pool = tuple(sorted(garnish_candidates)) if garnish_candidates else tuple()
    lengthener_pool = tuple(sorted(lengtheners)) if lengtheners else tuple()

    if taste_bias:
        total = sum(taste_bias.values())
        if total > 0:
            taste_bias = {key: value / total for key, value in taste_bias.items()}
    if aroma_bias:
        total = sum(aroma_bias.values())
        if total > 0:
            aroma_bias = {key: value / total for key, value in aroma_bias.items()}
    if structure_bias:
        structure_bias = dict(structure_bias)
    if affect_bias:
        total = sum(affect_bias.values())
        if total > 0:
            affect_bias = {key: value / total for key, value in affect_bias.items()}

    if tag_weights:
        total = sum(tag_weights.values())
        tag_weights = {key: value / total for key, value in tag_weights.items() if value > 0}

    modifier_tags = tuple(sorted(candidate_sets.get("modifier", [])))
    sweetener_tags = tuple(sorted(candidate_sets.get("sweetener", [])))
    juice_tags = tuple(sorted(candidate_sets.get("juice", [])))
    seasonal_tags_tuple = tuple(sorted(seasonal_tags)) if seasonal_tags else tuple()

    bitterness_tolerance = None
    if bitterness_levels:
        bitterness_tolerance = sum(bitterness_levels) / len(bitterness_levels)

    return PreferencePlan(
        strength_oz=strength_oz,
        modifier_oz=modifier_oz,
        sweetener_oz=sweetener_oz,
        glass_type=glass_type,
        glass_min_ml=glass_min_ml,
        garnish_hint=garnish_hint,
        juice_focus=juice_focus,
        modifier_tags=modifier_tags,
        sweetener_tags=sweetener_tags,
        juice_tags=juice_tags,
        seasonal_tags=seasonal_tags_tuple,
        template_weights=dict(template_weights),
        ratio_targets=ratio_targets,
        sweet_acid_window=sweet_acid_window,
        base_spirit_bias=dict(base_bias),
        taste_bias=taste_bias,
        aroma_bias=aroma_bias,
        structure_bias=structure_bias,
        affect_bias=affect_bias,
        tag_weights=tag_weights,
        candidate_pools=candidate_pools,
        avoid_or_reduce=tuple(sorted(avoid_set)) if avoid_set else tuple(),
        garnish_pool=garnish_pool,
        lengtheners=lengthener_pool,
        sparkle_bias=sparkle_bias,
        sparkle_allowed=sparkle_allowed,
        lengthener_allowed=sparkle_allowed if sparkle_allowed is not None else None,
        abv_range=abv_range,
        ingredient_max=ingredient_max,
        ice_program=ice_program,
        bitterness_tolerance=bitterness_tolerance,
    )


def collect_profile_tags(
    responses: Dict[str, Optional[str]], plan: PreferencePlan
) -> Dict[str, float]:
    """Aggregate flavour keywords derived from questionnaire responses."""

    weights: Dict[str, float] = defaultdict(float)

    for tag, weight in plan.tag_weights.items():
        weights[_normalize(tag)] += float(weight)

    for tag in plan.modifier_tags:
        weights[_normalize(tag)] += 0.3
    for tag in plan.sweetener_tags:
        weights[_normalize(tag)] += 0.3
    for tag in plan.juice_tags:
        weights[_normalize(tag)] += 0.4
    for tag in plan.seasonal_tags:
        weights[_normalize(tag)] += 0.5

    notes = (responses.get("notes") or "").lower()
    if notes:
        if "smok" in notes:
            weights["smoke"] += 0.5
        if "citrus" in notes:
            weights["citrus"] += 0.6
        if "sweet" in notes:
            weights["sweet"] += 0.5
        if "bitter" in notes:
            weights["bitter"] += 0.4
        if "herb" in notes:
            weights["herbal"] += 0.4
        if "spice" in notes:
            weights["spice"] += 0.4

    total = sum(weights.values())
    if total:
        return {tag: weight / total for tag, weight in weights.items()}
    return dict(weights)
