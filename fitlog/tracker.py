"""Daily food log and exercise plan."""

import json
import random
from collections import defaultdict
from datetime import date, timedelta


class TrackerError(ValueError):
    pass


# Food log

def add_food(db, catalog, day, food_id, servings):
    food = catalog.foods.get(food_id)
    if food is None:
        raise TrackerError(f"Unknown food '{food_id}'")
    if not servings > 0:
        raise TrackerError("Servings must be greater than zero")

    def scaled(value):
        return None if value is None else round(value * servings, 1)

    with db.connect() as conn:
        conn.execute(
            "INSERT INTO food_log (day, food_id, name, servings, calories, protein, carbs, fat) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (day.isoformat(), food.id, food.name, servings, scaled(food.calories),
             scaled(food.protein), scaled(food.carbs), scaled(food.fat)),
        )


def add_custom(db, day, name, calories):
    name = name.strip()
    if not name:
        raise TrackerError("Name is required")
    if not calories >= 0:
        raise TrackerError("Calories must be zero or more")
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO food_log (day, name, servings, calories) VALUES (?, ?, 1, ?)",
            (day.isoformat(), name, round(calories, 1)),
        )


def delete_entry(db, entry_id):
    with db.connect() as conn:
        conn.execute("DELETE FROM food_log WHERE id = ?", (entry_id,))


def day_entries(db, day):
    with db.connect() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM food_log WHERE day = ? ORDER BY id", (day.isoformat(),)
        )]


def totals(entries):
    result = {"calories": 0.0, "protein": 0.0, "carbs": 0.0, "fat": 0.0}
    for entry in entries:
        for key in result:
            result[key] += entry[key] or 0
    return {k: round(v, 1) for k, v in result.items()}


def history(db, end, days):
    start = end - timedelta(days=days - 1)
    with db.connect() as conn:
        calories = {r["day"]: r["total"] for r in conn.execute(
            "SELECT day, SUM(calories) AS total FROM food_log WHERE day BETWEEN ? AND ? GROUP BY day",
            (start.isoformat(), end.isoformat()),
        )}
        workouts = {r["day"]: (r["done"], r["planned"]) for r in conn.execute(
            "SELECT day, SUM(done) AS done, COUNT(*) AS planned FROM plans "
            "WHERE day BETWEEN ? AND ? GROUP BY day",
            (start.isoformat(), end.isoformat()),
        )}
    rows = []
    for offset in range(days):
        d = end - timedelta(days=offset)
        done, planned = workouts.get(d.isoformat(), (0, 0))
        rows.append({
            "day": d,
            "calories": round(calories.get(d.isoformat(), 0), 1),
            "done": done,
            "planned": planned,
        })
    return rows


# Exercise plan

def _plan_rows(conn, day):
    return [dict(r) for r in conn.execute(
        "SELECT position, exercise_id, done FROM plans WHERE day = ? ORDER BY position",
        (day.isoformat(),),
    )]


def _previous_ids(conn, day):
    """Exercise ids from the most recent plan before this day."""
    row = conn.execute("SELECT MAX(day) AS day FROM plans WHERE day < ?", (day.isoformat(),)).fetchone()
    if row["day"] is None:
        return set()
    return {r["exercise_id"] for r in _plan_rows(conn, date.fromisoformat(row["day"]))}


def get_excluded(db):
    return set(json.loads(db.get_setting("excluded_exercises")))


def set_excluded(db, ids):
    db.set_setting("excluded_exercises", json.dumps(sorted(ids)))


def _available(db, catalog):
    excluded = get_excluded(db)
    return [e for e in catalog.exercises.values() if e.id not in excluded]


def pick_balanced(exercises, count, rng, budget=None):
    """Pick up to count exercises, spreading picks across types in random order.

    With a budget, only exercises that still fit in the remaining minutes are picked.
    """
    by_type = defaultdict(list)
    for exercise in exercises:
        by_type[exercise.type].append(exercise)
    for group in by_type.values():
        rng.shuffle(group)
    types = list(by_type)
    rng.shuffle(types)
    picked = []
    remaining = budget
    while len(picked) < count and any(by_type.values()):
        for type in types:
            group = by_type[type]
            if remaining is not None:
                group[:] = [e for e in group if e.duration <= remaining]
            if group and len(picked) < count:
                choice = group.pop()
                picked.append(choice)
                if remaining is not None:
                    remaining -= choice.duration
    return picked


def get_plan(db, catalog, day, create=False, rng=None):
    """Return the plan for a day, creating it when asked and none exists yet.

    Unchecked exercises are never picked. Exercises from the previous plan are
    left out, and only used again when too few others are checked to fill the day.
    """
    rng = rng or random.Random()
    count = int(db.get_setting("exercises_per_day"))
    budget = int(db.get_setting("minutes_per_day"))
    with db.connect() as conn:
        rows = _plan_rows(conn, day)
        available = _available(db, catalog)
        if not rows and create and available:
            previous = _previous_ids(conn, day)
            fresh = [e for e in available if e.id not in previous]
            picked = pick_balanced(fresh, count, rng, budget)
            if len(fresh) < count:
                used = sum(e.duration for e in picked)
                repeats = [e for e in available if e.id in previous]
                picked += pick_balanced(repeats, count - len(picked), rng, budget - used)
            conn.executemany(
                "INSERT INTO plans (day, position, exercise_id) VALUES (?, ?, ?)",
                [(day.isoformat(), i, e.id) for i, e in enumerate(picked)],
            )
            rows = _plan_rows(conn, day)
    for row in rows:
        row["exercise"] = catalog.exercises.get(row["exercise_id"])
    return rows


def plan_minutes(plan):
    return sum(row["exercise"].duration for row in plan if row["exercise"])


def reroll(db, catalog, day, position, rng=None):
    """Swap one planned exercise for another that fits the time left, preferring the same type.

    Exercises from the previous plan are only considered when nothing else fits.
    """
    rng = rng or random.Random()
    budget = int(db.get_setting("minutes_per_day"))
    available = _available(db, catalog)
    with db.connect() as conn:
        rows = _plan_rows(conn, day)
        current = next((r for r in rows if r["position"] == position), None)
        if current is None:
            raise TrackerError("No such exercise in the plan")
        planned = {r["exercise_id"] for r in rows}
        others = sum(catalog.exercises[r["exercise_id"]].duration for r in rows
                     if r["position"] != position and r["exercise_id"] in catalog.exercises)
        fits = [e for e in available if e.id not in planned and e.duration <= budget - others]
        previous = _previous_ids(conn, day)
        candidates = [e for e in fits if e.id not in previous] or fits
        if not candidates:
            raise TrackerError("No other checked exercise fits in the time left")
        old = catalog.exercises.get(current["exercise_id"])
        same_type = [e for e in candidates if old and e.type == old.type]
        choice = rng.choice(same_type or candidates)
        conn.execute(
            "UPDATE plans SET exercise_id = ?, done = 0 WHERE day = ? AND position = ?",
            (choice.id, day.isoformat(), position),
        )


def replace_excluded(db, catalog, day, rng=None):
    """Swap out unchecked exercises from a day's plan that are not done yet, or drop them."""
    excluded = get_excluded(db)
    for row in get_plan(db, catalog, day):
        if row["exercise_id"] in excluded and not row["done"]:
            try:
                reroll(db, catalog, day, row["position"], rng)
            except TrackerError:
                with db.connect() as conn:
                    conn.execute("DELETE FROM plans WHERE day = ? AND position = ?",
                                 (day.isoformat(), row["position"]))


def set_done(db, day, position, done):
    with db.connect() as conn:
        conn.execute(
            "UPDATE plans SET done = ? WHERE day = ? AND position = ?",
            (1 if done else 0, day.isoformat(), position),
        )
