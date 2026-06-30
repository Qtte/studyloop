"""Mastery score updates for study interactions."""

from __future__ import annotations


class MasteryService:
    """Apply simple mastery updates from grades and mistake categories."""

    GRADE_ADJUSTMENTS = [
        (90, 0.12),
        (75, 0.07),
        (60, 0.03),
        (40, -0.03),
        (0, -0.08),
    ]

    MISTAKE_ADJUSTMENTS = {
        "correct": 0.03,
        "concept_confusion": -0.05,
        "concept_missing": -0.05,
        "shallow_answer": -0.02,
        "missing_evidence": -0.02,
        "application_weak": -0.03,
    }

    def update_mastery(self, old_score: float, grade_score: float, mistake_type: str) -> float:
        delta = self._grade_delta(grade_score) + self.MISTAKE_ADJUSTMENTS.get(mistake_type, 0.0)
        return max(0.0, min(1.0, round(old_score + delta, 4)))

    def _grade_delta(self, grade_score: float) -> float:
        for threshold, delta in self.GRADE_ADJUSTMENTS:
            if grade_score >= threshold:
                return delta
        return 0.0
