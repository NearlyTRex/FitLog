import random
from datetime import date, timedelta

import pytest

from fitlog import tracker
from fitlog.catalog import CatalogStore, load_catalog
from conftest import exercise_yaml, write

DAY = date(2026, 9, 23)


def ids(plan):
    return {row["exercise_id"] for row in plan}


def test_plan_is_created_once_and_balanced(db, store):
    plan = tracker.get_plan(db, store.catalog, DAY, create=True, rng=random.Random(1))
    assert len(plan) == 4
    assert sorted(row["exercise"].type for row in plan) == ["cardio", "cardio", "strength", "strength"]
    again = tracker.get_plan(db, store.catalog, DAY, create=True, rng=random.Random(99))
    assert ids(again) == ids(plan)


def test_past_day_without_plan_is_not_created(db, store):
    assert tracker.get_plan(db, store.catalog, DAY) == []


def test_no_repeats_from_previous_plan(db, store):
    for seed in range(20):
        day = DAY + timedelta(days=seed)
        yesterday = ids(tracker.get_plan(db, store.catalog, day - timedelta(days=1)))
        today = ids(tracker.get_plan(db, store.catalog, day, create=True, rng=random.Random(seed)))
        assert not today & yesterday


def test_previous_plan_is_excluded_across_a_gap(db, store):
    first = ids(tracker.get_plan(db, store.catalog, DAY, create=True, rng=random.Random(3)))
    later = ids(tracker.get_plan(db, store.catalog, DAY + timedelta(days=3), create=True, rng=random.Random(4)))
    assert not first & later


def test_small_catalog_falls_back_to_repeats(db, tmp_path):
    root = tmp_path / "small"
    for i in range(5):
        write(root / "exercises" / f"e{i}.yaml", exercise_yaml(f"E{i}", "core"))
    catalog = load_catalog(root)
    first = ids(tracker.get_plan(db, catalog, DAY, create=True, rng=random.Random(0)))
    second = ids(tracker.get_plan(db, catalog, DAY + timedelta(days=1), create=True, rng=random.Random(0)))
    assert len(second) == 4
    assert len(second & first) == 3


def test_reroll_prefers_same_type_and_avoids_recent(db, catalog_dir):
    for i in range(4, 8):
        write(catalog_dir / "exercises" / f"s{i}.yaml", exercise_yaml(f"Strength {i}", "strength"))
        write(catalog_dir / "exercises" / f"c{i}.yaml", exercise_yaml(f"Cardio {i}", "cardio"))
    store = CatalogStore(catalog_dir)
    yesterday = ids(tracker.get_plan(db, store.catalog, DAY - timedelta(days=1), create=True, rng=random.Random(5)))
    plan = tracker.get_plan(db, store.catalog, DAY, create=True, rng=random.Random(6))
    tracker.set_done(db, DAY, 0, True)
    old = plan[0]["exercise"]
    tracker.reroll(db, store.catalog, DAY, 0, rng=random.Random(7))
    new_plan = tracker.get_plan(db, store.catalog, DAY)
    new = new_plan[0]
    assert new["exercise"].type == old.type
    assert new["exercise_id"] not in ids(plan) | yesterday
    assert new["done"] == 0


def test_reroll_falls_back_to_previous_plan(db, store):
    yesterday = ids(tracker.get_plan(db, store.catalog, DAY - timedelta(days=1), create=True, rng=random.Random(5)))
    plan = tracker.get_plan(db, store.catalog, DAY, create=True, rng=random.Random(6))
    tracker.reroll(db, store.catalog, DAY, 0, rng=random.Random(7))
    new = tracker.get_plan(db, store.catalog, DAY)[0]
    assert new["exercise_id"] in yesterday
    assert new["exercise"].type == plan[0]["exercise"].type


def test_reroll_with_nothing_left(db, tmp_path):
    root = tmp_path / "tiny"
    write(root / "exercises" / "only.yaml", exercise_yaml("Only", "core"))
    catalog = load_catalog(root)
    tracker.get_plan(db, catalog, DAY, create=True)
    with pytest.raises(tracker.TrackerError):
        tracker.reroll(db, catalog, DAY, 0)


def test_food_log_scales_and_totals(db, store):
    tracker.add_food(db, store.catalog, DAY, "banana", 2)
    tracker.add_food(db, store.catalog, DAY, "toast", 0.5)
    tracker.add_custom(db, DAY, "Latte", 190)
    entries = tracker.day_entries(db, DAY)
    assert [e["calories"] for e in entries] == [210, 40, 190]
    assert tracker.totals(entries) == {"calories": 440, "protein": 2.6, "carbs": 0, "fat": 0}
    tracker.delete_entry(db, entries[0]["id"])
    assert tracker.totals(tracker.day_entries(db, DAY))["calories"] == 230


def test_food_log_rejects_bad_input(db, store):
    with pytest.raises(tracker.TrackerError):
        tracker.add_food(db, store.catalog, DAY, "nope", 1)
    with pytest.raises(tracker.TrackerError):
        tracker.add_food(db, store.catalog, DAY, "banana", 0)
    with pytest.raises(tracker.TrackerError):
        tracker.add_custom(db, DAY, "  ", 10)


def test_history(db, store):
    tracker.add_food(db, store.catalog, DAY, "banana", 1)
    tracker.get_plan(db, store.catalog, DAY, create=True)
    tracker.set_done(db, DAY, 1, True)
    rows = tracker.history(db, DAY, 3)
    assert [r["day"] for r in rows] == [DAY, DAY - timedelta(days=1), DAY - timedelta(days=2)]
    assert rows[0] == {"day": DAY, "calories": 105, "done": 1, "planned": 4}
    assert rows[1]["calories"] == 0 and rows[1]["planned"] == 0


def test_budget_caps_plan_minutes(db, tmp_path):
    root = tmp_path / "timed"
    write(root / "exercises" / "game.yaml", exercise_yaml("Game", "cardio", 30))
    write(root / "exercises" / "bike.yaml", exercise_yaml("Bike", "cardio", 30))
    for i in range(6):
        write(root / "exercises" / f"s{i}.yaml", exercise_yaml(f"S{i}", "strength", 8))
    catalog = load_catalog(root)
    db.set_setting("exercises_per_day", 10)
    for seed in range(20):
        day = DAY + timedelta(days=seed)
        plan = tracker.get_plan(db, catalog, day, create=True, rng=random.Random(seed))
        assert tracker.plan_minutes(plan) <= 60
        assert sum(1 for r in plan if r["exercise"].duration == 30) <= 1


def test_pick_skips_what_no_longer_fits():
    catalog_types = [type("E", (), {"id": str(i), "type": "core", "duration": d})() for i, d in enumerate([50, 20, 5])]
    picked = tracker.pick_balanced(catalog_types, 5, random.Random(0), budget=25)
    assert sum(e.duration for e in picked) <= 25


def test_excluded_exercises_are_never_picked(db, store):
    tracker.set_excluded(db, {f"c{i}" for i in range(4)})
    for seed in range(5):
        plan = tracker.get_plan(db, store.catalog, DAY + timedelta(days=seed), create=True, rng=random.Random(seed))
        assert plan and all(r["exercise"].type == "strength" for r in plan)


def test_everything_excluded_gives_empty_plan(db, store):
    tracker.set_excluded(db, set(store.catalog.exercises))
    assert tracker.get_plan(db, store.catalog, DAY, create=True) == []


def test_reroll_respects_budget_and_exclusions(db, tmp_path):
    root = tmp_path / "timed"
    write(root / "exercises" / "long.yaml", exercise_yaml("Long", "cardio", 40))
    write(root / "exercises" / "short.yaml", exercise_yaml("Short", "cardio", 5))
    write(root / "exercises" / "other.yaml", exercise_yaml("Other", "cardio", 5))
    write(root / "exercises" / "skip.yaml", exercise_yaml("Skip", "cardio", 5))
    catalog = load_catalog(root)
    db.set_setting("minutes_per_day", 45)
    db.set_setting("exercises_per_day", 2)
    tracker.set_excluded(db, {"skip"})
    with db.connect() as conn:
        conn.executemany("INSERT INTO plans (day, position, exercise_id) VALUES (?, ?, ?)",
                         [(DAY.isoformat(), 0, "long"), (DAY.isoformat(), 1, "short")])
    tracker.reroll(db, catalog, DAY, 1, rng=random.Random(0))
    assert ids(tracker.get_plan(db, catalog, DAY)) == {"long", "other"}
    tracker.set_excluded(db, {"skip", "short"})
    with pytest.raises(tracker.TrackerError):
        tracker.reroll(db, catalog, DAY, 1)


def test_replace_excluded_swaps_or_drops_undone(db, store):
    plan = tracker.get_plan(db, store.catalog, DAY, create=True, rng=random.Random(2))
    done = plan[0]
    tracker.set_done(db, DAY, done["position"], True)
    tracker.set_excluded(db, {r["exercise_id"] for r in plan})
    tracker.replace_excluded(db, store.catalog, DAY, rng=random.Random(3))
    after = tracker.get_plan(db, store.catalog, DAY)
    by_pos = {r["position"]: r for r in after}
    assert by_pos[done["position"]]["exercise_id"] == done["exercise_id"]
    others = [r for r in after if r["position"] != done["position"]]
    assert len(others) == 3
    assert not {r["exercise_id"] for r in others} & ids(plan)


def test_budget_leftover_is_not_filled_with_repeats(db, tmp_path):
    root = tmp_path / "timed"
    for i in range(6):
        write(root / "exercises" / f"long{i}.yaml", exercise_yaml(f"Long {i}", "cardio", 25))
        write(root / "exercises" / f"tiny{i}.yaml", exercise_yaml(f"Tiny {i}", "core", 3))
    catalog = load_catalog(root)
    db.set_setting("exercises_per_day", 6)
    for seed in range(15):
        day = DAY + timedelta(days=seed)
        yesterday = ids(tracker.get_plan(db, catalog, day - timedelta(days=1)))
        today = ids(tracker.get_plan(db, catalog, day, create=True, rng=random.Random(seed)))
        assert not today & yesterday
