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
        "favourite_dessert": "tangfastics",
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
    assert 0.78 < high < 0.83

    assert "base" in plan.ratio_targets
    base_low, base_high = plan.ratio_targets["base"]
    assert math.isclose(base_low, 0.375, rel_tol=0.05)
    assert math.isclose(base_high, 0.425, rel_tol=0.05)

    assert plan.candidate_pools["juice"]
    assert any("pineapple" in value.lower() for value in plan.candidate_pools["juice"])

    assert plan.lengthener_rules.get("allow_carbonated") is True
    assert plan.role_bounds["juice"][0] >= 0.28
    assert math.isclose(plan.role_bounds["base"][1], 0.38, rel_tol=0.05)
    assert plan.taste_caps["bitter"] == pytest.approx(0.35, rel=1e-3)
    assert plan.candidate_item_bias.get("soda water") and plan.candidate_family_bias.get("foam")


def test_collect_profile_tags_includes_biases(sample_responses):
    plan = build_preference_plan(sample_responses)
    tags = collect_profile_tags(sample_responses, plan)

    assert tags["citrus"] > 0
    assert tags["tropical"] > 0
    assert tags["refreshing"] > 0


def test_load_profile_map_handles_non_mapping(monkeypatch):
    from recipebuilder import preferences

    monkeypatch.setattr(preferences, "_read_json", lambda path: ["not", "a", "mapping"])
    result = _load_profile_map("season_profiles.json")
    assert result == {}
