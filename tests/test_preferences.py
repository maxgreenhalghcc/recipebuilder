import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from recipebuilder.preferences import (  # noqa: E402
    _load_profile_map,
    build_preference_plan,
    collect_profile_tags,
)


@pytest.fixture
def sample_responses():
    return {
        "season": "summer",
        "house_type": "beach house",
        "dining_style": "refreshing and vibrant flavours which awaken my senses",
        "music_preference": "pop",
        "aroma_preference": "citrus",
        "sweetener_question": "zesty",
        "bitterness_tolerance": "medium",
        "carbonation_texture": "lightly fizzy",
        "abv_lane": "low",
        "base_spirit": "rum",
        "foam_toggle": "yes",
    }


def test_preference_plan_combines_ratio_targets_and_windows(sample_responses):
    plan = build_preference_plan(sample_responses)

    assert plan.sweet_acid_window is not None
    low, high = plan.sweet_acid_window
    assert 0.54 < low < 0.59
    assert 0.83 < high < 0.86

    assert "base" in plan.ratio_targets
    base_low, base_high = plan.ratio_targets["base"]
    assert math.isclose(base_low, 0.375, rel_tol=0.05)
    assert math.isclose(base_high, 0.425, rel_tol=0.05)

    assert plan.candidate_pools["juice"]
    assert any("pineapple" in value.lower() for value in plan.candidate_pools["juice"])

    assert plan.lengthener_rules.get("allow_carbonated") is True
    assert plan.role_bounds["juice"][0] >= 0.28
    assert math.isclose(plan.role_bounds["base"][1], 0.38, rel_tol=0.05)
    sweet_min, sweet_max = plan.role_bounds["sweetener"]
    assert math.isclose(sweet_min, 0.1, rel_tol=0.1)
    assert math.isclose(sweet_max, 0.22, rel_tol=0.1)
    assert plan.taste_caps["bitter"] == pytest.approx(0.35, rel=1e-3)
    assert plan.candidate_item_bias.get("soda water") and plan.candidate_family_bias.get("foam")


def test_collect_profile_tags_includes_biases(sample_responses):
    plan = build_preference_plan(sample_responses)
    tags = collect_profile_tags(sample_responses, plan)

    assert tags["citrus"] > 0
    assert tags["tropical"] > 0
    assert tags["refreshing"] > 0


def test_dessert_proxy_rich_winter_bias(sample_responses):
    responses = sample_responses | {
        "season": "winter",
        "sweetener_question": "rich",
        "aroma_preference": "sweet",
        "house_type": "modern house",
        "abv_lane": "medium",
    }
    plan = build_preference_plan(responses)
    assert plan.sweet_acid_window is not None
    low, high = plan.sweet_acid_window
    assert low > 0.68
    assert high > 0.95
    assert plan.candidate_family_bias.get("vanilla", 1.0) > 1.1
    tags = collect_profile_tags(responses, plan)
    assert tags["vanilla"] > tags["citrus"]


def test_dessert_proxy_zesty_summer_offsets(sample_responses):
    responses = sample_responses | {
        "season": "summer",
        "sweetener_question": "zesty",
        "aroma_preference": "citrus",
    }
    plan = build_preference_plan(responses)
    assert plan.sweet_acid_window is not None
    low, high = plan.sweet_acid_window
    assert low < 0.6
    assert high < 0.86
    assert plan.candidate_family_bias.get("citrus_liqueur", 0) > 1.0
    tags = collect_profile_tags(responses, plan)
    assert tags["citrus"] >= tags["tropical"]


def test_dessert_proxy_floral_alignment(sample_responses):
    responses = sample_responses | {
        "season": "spring",
        "sweetener_question": "floral",
        "aroma_preference": "floral",
        "house_type": "tree house",
    }
    plan = build_preference_plan(responses)
    assert plan.candidate_family_bias.get("elderflower", 1.0) > 1.1
    tags = collect_profile_tags(responses, plan)
    assert tags["floral"] >= tags["citrus"]
    assert plan.taste_caps.get("bitter", 0.5) == pytest.approx(0.35, rel=1e-3)


def test_load_profile_map_handles_non_mapping(monkeypatch):
    from recipebuilder import preferences

    monkeypatch.setattr(preferences, "_read_json", lambda path: ["not", "a", "mapping"])
    result = _load_profile_map("season_profiles.json")
    assert result == {}
