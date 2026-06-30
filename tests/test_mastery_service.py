from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from backend.app.services.mastery_service import MasteryService


def test_mastery_service_rewards_good_answers():
    service = MasteryService()
    updated = service.update_mastery(0.5, 92, "correct")
    assert updated > 0.5


def test_mastery_service_penalizes_weak_answers_and_clamps():
    service = MasteryService()
    updated = service.update_mastery(0.02, 20, "concept_missing")
    assert updated == 0.0

    updated_high = service.update_mastery(0.97, 96, "correct")
    assert updated_high == 1.0
if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__]))
