"""
LernOS — Unit Tests
"""

import os
import sqlite3
import sys
import tempfile

import pytest

from lernos.db.schema import get_connection, migrate
from lernos.db.topics import (
    STATE_FROZEN,
    STATE_LEARNING,
    STATE_MASTERED,
    STATE_NEW,
    STATE_REVIEW,
    create_edge,
    create_topic,
    freeze_topic,
    get_all_topics,
    get_due_topics,
    get_edges_for_topic,
    get_topic_by_id,
    get_topic_by_name,
    log_session,
    thaw_expired_frozen,
    unfreeze_topic,
    update_topic_sm2,
)
from lernos.fuzzy.resolve import fuzzy_score, resolve_topic
from lernos.graph.topo import build_exam_plan, topo_sort
from lernos.sm2.algorithm import adjust_grade, calc_ef, calc_interval, calculate
from lernos.sm2.cascade import cascade_review


@pytest.fixture
def db():
    """Frische In-Memory-SQLite-Datenbank für jeden Test."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    conn = get_connection(path)
    migrate(conn)
    yield conn
    conn.close()
    os.unlink(path)


# ── Topic CRUD Tests ──────────────────────────────────────────────────────────


class TestTopicCRUD:
    def test_create_topic(self, db):
        t = create_topic(db, "Grenzwerte", "Analysis I", "Basiskonzept")
        assert t.id is not None
        assert t.name == "Grenzwerte"
        assert t.module == "Analysis I"
        assert t.state == STATE_NEW
        assert t.ef == 2.5
        assert t.interval_d == 1
        assert t.repetitions == 0

    def test_get_by_name(self, db):
        create_topic(db, "Stetigkeit", "Analysis I")
        t = get_topic_by_name(db, "Stetigkeit")
        assert t is not None
        assert t.name == "Stetigkeit"

    def test_get_by_name_missing(self, db):
        assert get_topic_by_name(db, "NichtExistent") is None

    def test_unique_name_constraint(self, db):
        create_topic(db, "Duplicat")
        with pytest.raises(Exception):
            create_topic(db, "Duplicat")

    def test_get_all_topics_state_filter(self, db):
        t1 = create_topic(db, "T1")
        t2 = create_topic(db, "T2")
        db.execute("UPDATE topics SET state='REVIEW' WHERE id=?", (t2.id,))
        db.commit()
        review = get_all_topics(db, state=STATE_REVIEW)
        assert len(review) == 1
        assert review[0].name == "T2"

    def test_get_due_topics(self, db):
        t1 = create_topic(db, "Fällig")
        t2 = create_topic(db, "Nicht fällig")
        db.execute(
            "UPDATE topics SET due_date=date('now', '+5 days') WHERE id=?", (t2.id,)
        )
        db.commit()
        due = get_due_topics(db)
        names = [t.name for t in due]
        assert "Fällig" in names
        assert "Nicht fällig" not in names


# ── Edge Tests ────────────────────────────────────────────────────────────────


class TestEdges:
    def test_create_edge(self, db):
        t1 = create_topic(db, "A")
        t2 = create_topic(db, "B")
        e = create_edge(db, t1.id, t2.id, weight=0.8, confirmed=True)
        assert e.from_id == t1.id
        assert e.to_id == t2.id
        assert e.weight == 0.8
        assert e.confirmed is True

    def test_get_edges_for_topic(self, db):
        t1 = create_topic(db, "Root")
        t2 = create_topic(db, "Child")
        t3 = create_topic(db, "GrandChild")
        create_edge(db, t1.id, t2.id, weight=0.7)
        create_edge(db, t2.id, t3.id, weight=0.9)

        edges_t2 = get_edges_for_topic(db, t2.id)
        assert len(edges_t2["incoming"]) == 1  # von t1
        assert len(edges_t2["outgoing"]) == 1  # zu t3
        assert edges_t2["incoming"][0].from_id == t1.id

    def test_edge_unique_constraint(self, db):
        t1 = create_topic(db, "X")
        t2 = create_topic(db, "Y")
        create_edge(db, t1.id, t2.id)
        # Nochmals anlegen überschreibt (INSERT OR REPLACE)
        e2 = create_edge(db, t1.id, t2.id, weight=0.9)
        assert e2.weight == 0.9


# ── Freeze Tests ──────────────────────────────────────────────────────────────


class TestFreeze:
    def test_freeze_topic(self, db):
        t = create_topic(db, "FreezMe")
        db.execute("UPDATE topics SET state='MASTERED' WHERE id=?", (t.id,))
        db.commit()
        freeze_topic(db, t.id, days=6)
        frozen = get_topic_by_id(db, t.id)
        assert frozen.state == STATE_FROZEN
        assert frozen.frozen_until is not None

    def test_unfreeze_topic(self, db):
        t = create_topic(db, "UnfreezeMe")
        freeze_topic(db, t.id, days=6)
        unfreeze_topic(db, t.id)
        t2 = get_topic_by_id(db, t.id)
        assert t2.state == STATE_REVIEW
        assert t2.frozen_until is None

    def test_thaw_expired(self, db):
        t = create_topic(db, "ExpiredFreeze")
        # Direkt mit abgelaufenem Datum einfrieren
        db.execute(
            "UPDATE topics SET state='FROZEN', frozen_until=date('now', '-1 day') WHERE id=?",
            (t.id,),
        )
        db.commit()
        count = thaw_expired_frozen(db)
        assert count == 1
        t2 = get_topic_by_id(db, t.id)
        assert t2.state == STATE_REVIEW

    def test_active_freeze_not_thawed(self, db):
        t = create_topic(db, "ActiveFreeze")
        freeze_topic(db, t.id, days=6)  # 6 Tage in der Zukunft
        count = thaw_expired_frozen(db)
        assert count == 0
        t2 = get_topic_by_id(db, t.id)
        assert t2.state == STATE_FROZEN


# ── SM-2 Algorithm Tests ──────────────────────────────────────────────────────


class TestSM2:
    def test_adjust_grade_overconfidence(self):
        """Hohe Konfidenz + falsch = -2 Strafe"""
        adj = adjust_grade(grade=3, confidence=5, correct=0)
        assert adj == 1  # 3 - 2 = 1

    def test_adjust_grade_low_confidence_wrong(self):
        """Niedrige Konfidenz + falsch = -1"""
        adj = adjust_grade(grade=2, confidence=2, correct=0)
        assert adj == 1  # 2 - 1 = 1

    def test_adjust_grade_correct_no_bonus(self):
        """Richtig = kein Grade-Modifier"""
        adj = adjust_grade(grade=4, confidence=5, correct=1)
        assert adj == 4

    def test_ef_increases_on_good_grade(self):
        new_ef = calc_ef(2.0, 5)
        assert new_ef > 2.0

    def test_ef_decreases_on_bad_grade(self):
        new_ef = calc_ef(2.5, 0)
        assert new_ef < 2.5

    def test_ef_clamped(self):
        """EF bleibt zwischen 1.3 und 2.5"""
        # Sehr viele schlechte Bewertungen
        ef = 1.5
        for _ in range(20):
            ef = calc_ef(ef, 0)
        assert ef >= 1.3

        ef = 2.4
        for _ in range(20):
            ef = calc_ef(ef, 5)
        assert ef <= 2.5

    def test_calculate_correct_transitions(self, db):
        t = create_topic(db, "TestSM2")
        # Erstes Review: state=NEW → grade 5 → REVIEW
        result = calculate(t, grade=5, confidence=3, correct=1)
        assert result.new_state == STATE_REVIEW

    def test_calculate_to_mastered(self, db):
        """Nach vielen korrekten Reviews → MASTERED"""
        t = create_topic(db, "Mastery")
        db.execute(
            "UPDATE topics SET state='REVIEW', interval_d=22, ef=2.2, repetitions=10 WHERE id=?",
            (t.id,),
        )
        db.commit()
        t2 = get_topic_by_id(db, t.id)
        result = calculate(t2, grade=5, confidence=4, correct=1)
        assert result.new_state == STATE_MASTERED

    def test_calculate_back_to_learning(self, db):
        """Falsch im REVIEW → LEARNING"""
        t = create_topic(db, "Regress")
        db.execute("UPDATE topics SET state='REVIEW' WHERE id=?", (t.id,))
        db.commit()
        t2 = get_topic_by_id(db, t.id)
        result = calculate(t2, grade=0, confidence=2, correct=0)
        assert result.new_state == STATE_LEARNING

    def test_interval_first_reps(self, db):
        """Erste zwei Intervalle sind 1 und 6"""
        t = create_topic(db, "Intervals")
        r1 = calculate(t, grade=5, confidence=3, correct=1)
        assert r1.new_interval == 1  # Erste Wiederholung

        db.execute(
            "UPDATE topics SET repetitions=1, interval_d=1, state='REVIEW' WHERE id=?",
            (t.id,),
        )
        db.commit()
        t2 = get_topic_by_id(db, t.id)
        r2 = calculate(t2, grade=5, confidence=3, correct=1)
        assert r2.new_interval == 6  # Zweite Wiederholung


# ── Cascade Tests ──────────────────────────────────────────────────────────────


class TestCascade:
    def test_cascade_triggers_on_high_weight(self, db):
        t1 = create_topic(db, "Root")
        t2 = create_topic(db, "Dependent")
        create_edge(db, t1.id, t2.id, weight=0.8)  # ≥ 0.6 → soll triggern

        # t2 auf REVIEW setzen
        db.execute("UPDATE topics SET state='REVIEW' WHERE id=?", (t2.id,))
        db.commit()

        affected = cascade_review(db, t1.id)
        assert len(affected) == 1
        assert affected[0]["name"] == "Dependent"
        assert affected[0]["new"] == STATE_REVIEW

    def test_cascade_hard_triggers_learning(self, db):
        """Gewicht ≥ 0.8 + MASTERED → LEARNING"""
        t1 = create_topic(db, "Prereq")
        t2 = create_topic(db, "Mastered")
        create_edge(db, t1.id, t2.id, weight=0.85)

        db.execute("UPDATE topics SET state='MASTERED' WHERE id=?", (t2.id,))
        db.commit()

        affected = cascade_review(db, t1.id)
        assert len(affected) == 1
        assert affected[0]["new"] == STATE_LEARNING

    def test_cascade_skips_frozen(self, db):
        """FROZEN Topics werden von Kaskade ignoriert"""
        t1 = create_topic(db, "Trigger")
        t2 = create_topic(db, "FrozenDep")
        create_edge(db, t1.id, t2.id, weight=0.9)

        db.execute("UPDATE topics SET state='FROZEN' WHERE id=?", (t2.id,))
        db.commit()

        affected = cascade_review(db, t1.id)
        assert len(affected) == 0

    def test_cascade_skips_low_weight(self, db):
        """Gewicht < 0.6 → kein Trigger"""
        t1 = create_topic(db, "Weak")
        t2 = create_topic(db, "WeakDep")
        create_edge(db, t1.id, t2.id, weight=0.3)

        db.execute("UPDATE topics SET state='REVIEW' WHERE id=?", (t2.id,))
        db.commit()

        affected = cascade_review(db, t1.id)
        assert len(affected) == 0


# ── Graph / Topo Tests ─────────────────────────────────────────────────────────


class TestTopo:
    def test_topo_sort_linear(self, db):
        """A → B → C soll in Reihenfolge A, B, C kommen"""
        t1 = create_topic(db, "A")
        t2 = create_topic(db, "B")
        t3 = create_topic(db, "C")
        create_edge(db, t1.id, t2.id, weight=0.9)
        create_edge(db, t2.id, t3.id, weight=0.9)

        sorted_topics, had_cycle = topo_sort(db)
        names = [t.name for t in sorted_topics]
        assert names.index("A") < names.index("B")
        assert names.index("B") < names.index("C")
        assert not had_cycle

    def test_topo_sort_cycle_detection(self, db):
        """Zykel wird erkannt ohne Crash"""
        t1 = create_topic(db, "X")
        t2 = create_topic(db, "Y")
        create_edge(db, t1.id, t2.id)
        create_edge(db, t2.id, t1.id)  # Zykel!

        _, had_cycle = topo_sort(db)
        assert had_cycle

    def test_exam_plan_priorities(self, db):
        t1 = create_topic(db, "Basis")
        t2 = create_topic(db, "Aufbau")
        create_edge(db, t1.id, t2.id, weight=0.9)
        db.execute("UPDATE topics SET state='LEARNING' WHERE id=?", (t2.id,))
        db.commit()

        plan = build_exam_plan(db, days=14)
        # LEARNING hat Priorität SEHR HOCH → sollte früher kommen
        labels = [item["label"] for item in plan]
        assert "SEHR HOCH" in labels


# ── Fuzzy Tests ───────────────────────────────────────────────────────────────


class TestFuzzy:
    def test_exact_match(self):
        assert fuzzy_score("Kettenregel", "Kettenregel") == 1000

    def test_prefix_match(self):
        score = fuzzy_score("Ketten", "Kettenregel")
        assert score >= 400

    def test_contains_match(self):
        score = fuzzy_score("regel", "Kettenregel")
        assert score >= 200

    def test_subsequence(self):
        score = fuzzy_score("ktnrl", "Kettenregel")
        assert score > 0

    def test_no_match(self):
        score = fuzzy_score("xyz999", "Kettenregel")
        assert score == 0

    def test_resolve_exact(self, db):
        create_topic(db, "Kettenregel")
        t = resolve_topic(db, "Kettenregel")
        assert t is not None
        assert t.name == "Kettenregel"

    def test_resolve_prefix(self, db):
        create_topic(db, "Differenzierbarkeit")
        t = resolve_topic(db, "Diff")
        assert t is not None
        assert t.name == "Differenzierbarkeit"

    def test_resolve_abbreviation(self, db):
        create_topic(db, "Taylorreihen")
        t = resolve_topic(db, "taylr")
        assert t is not None

    def test_resolve_no_match(self, db):
        create_topic(db, "Analysis")
        t = resolve_topic(db, "XXXXXX99")
        assert t is None


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
