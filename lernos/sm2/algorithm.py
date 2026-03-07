"""LernOS — SM-2 Algorithm (User-Version mit Recovery-Boost)"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lernos.db.topics import Topic

from lernos.db.topics import (
    STATE_FROZEN,
    STATE_LEARNING,
    STATE_MASTERED,
    STATE_NEW,
    STATE_REVIEW,
)

# ── Konfiguration ─────────────────────────────────────────────────────────────
MASTERED_INTERVAL  = 21
MASTERED_EF_MIN    = 2.0
FROZEN_DAYS        = 6
LEARNING_MIN_REPS  = 2
EF_HARD_FLOOR      = 1.3
EF_EASE_HELL_FLOOR = 1.5
EF_CEILING         = 2.5   # hartes Maximum — Grade 5 hebt EF an,
                            # kann aber 2.5 nicht überschreiten.


@dataclass
class SMResult:
    new_ef:       float
    new_interval: int
    new_state:    str
    new_due_date: str
    new_reps:     int
    grade_used:   int
    correct:      int


def adjust_grade(grade: int, confidence: int, correct: int = -1) -> int:
    """
    Passt die Rohbewertung anhand Konfidenz an.

    Args:
        grade:      Rohbewertung 0-5
        confidence: Konfidenz 1-5
        correct:    Explizit (0=falsch, 1=richtig). Falls -1: aus grade>=3 abgeleitet.
    """
    is_correct = (grade >= 3) if correct == -1 else bool(correct)
    if not is_correct:
        return grade - 2 if confidence >= 4 else grade - 1
    return grade


def next_state(topic, grade: int) -> str:
    s = topic.state
    if s == STATE_FROZEN:
        return STATE_FROZEN
    if s == STATE_NEW:
        return STATE_REVIEW if grade >= 3 else STATE_LEARNING
    if s == STATE_LEARNING:
        if grade >= 3 and topic.repetitions >= LEARNING_MIN_REPS:
            return STATE_REVIEW
        return STATE_LEARNING
    if s == STATE_REVIEW:
        if grade < 3:
            return STATE_LEARNING
        if topic.interval_d >= MASTERED_INTERVAL and topic.ef >= MASTERED_EF_MIN:
            return STATE_MASTERED
        return STATE_REVIEW
    if s == STATE_MASTERED:
        return STATE_LEARNING if grade < 3 else STATE_MASTERED
    return STATE_NEW


def calc_ef(current_ef: float, grade: int, learning_resets: int = 0) -> float:
    new_ef = current_ef + (0.1 - (5 - grade) * (0.08 + (5 - grade) * 0.02))
    if grade == 5 and current_ef < 2.0:
        new_ef += 0.05
    floor = EF_EASE_HELL_FLOOR if learning_resets >= 3 else EF_HARD_FLOOR
    return round(max(floor, min(EF_CEILING, new_ef)), ndigits=3)


def calc_interval(topic, grade: int, new_ef: float) -> int:
    if grade < 3:
        return 1
    reps = topic.repetitions
    if reps == 0:
        return 1
    elif reps == 1:
        return 6
    else:
        return max(1, int(round(topic.interval_d * new_ef)))


def calculate(topic, grade: int, confidence: int, correct: int = -1) -> SMResult:
    adj_grade       = max(0, min(5, adjust_grade(grade, confidence, correct)))
    correct_derived = 1 if adj_grade >= 3 else 0
    learning_resets = getattr(topic, "learning_resets", 0) or 0
    new_ef          = calc_ef(topic.ef, adj_grade, learning_resets)
    new_interval    = calc_interval(topic, adj_grade, new_ef)
    new_reps        = 0 if adj_grade < 3 else topic.repetitions + 1

    class _T:
        state       = topic.state
        repetitions = new_reps
        interval_d  = new_interval
        ef          = new_ef

    new_state = next_state(_T(), adj_grade)
    new_due   = (date.today() + timedelta(days=new_interval)).isoformat()
    return SMResult(
        new_ef       = new_ef,
        new_interval = new_interval,
        new_state    = new_state,
        new_due_date = new_due,
        new_reps     = new_reps,
        grade_used   = adj_grade,
        correct      = correct_derived,
    )


GRADE_DESCRIPTIONS = {
    0: "Totaler Blackout",
    1: "Falsch, Antwort war bekannt",
    2: "Falsch, beim Sehen erkannt",
    3: "Richtig, aber sehr mühsam",
    4: "Richtig mit kurzem Zögern",
    5: "Sofort und sicher korrekt",
}

CONFIDENCE_DESCRIPTIONS = {
    1: "Gar nicht sicher",
    2: "Kaum sicher",
    3: "Mittel sicher",
    4: "Ziemlich sicher",
    5: "Völlig sicher",
}
