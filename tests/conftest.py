from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from fitlog.catalog import CatalogStore
from fitlog.config import Config
from fitlog.db import Database

REPO_DATA = Path(__file__).resolve().parent.parent / "data"


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def exercise_yaml(name, type, minutes=None):
    extra = f"minutes: {minutes}\n" if minutes else ""
    return f"name: {name}\ntype: {type}\n{extra}steps:\n  - Do it.\n"


def make_db(path):
    db = Database(path)
    db.set_setting("exercises_per_day", 4)
    return db


@pytest.fixture
def db(tmp_path):
    return make_db(tmp_path / "fitlog.db")


@pytest.fixture
def catalog_dir(tmp_path):
    root = tmp_path / "data"
    write(root / "foods" / "banana.yaml", "name: Banana\nserving: 1 medium\ncalories: 105\nprotein: 1.3\n")
    write(root / "foods" / "toast.yaml", "name: Toast\nserving: 1 slice\ncalories: 80\n")
    for i in range(4):
        write(root / "exercises" / f"s{i}.yaml", exercise_yaml(f"Strength {i}", "strength"))
        write(root / "exercises" / f"c{i}.yaml", exercise_yaml(f"Cardio {i}", "cardio"))
    return root


@pytest.fixture
def store(catalog_dir):
    return CatalogStore(catalog_dir)


@pytest.fixture
def config(tmp_path, catalog_dir):
    return Config(
        db_path=tmp_path / "fitlog.db",
        catalog_dir=catalog_dir,
        catalog_repo=None,
        pull_minutes=0,
        timezone=ZoneInfo("UTC"),
        secure_cookies=False,
        trust_proxy=False,
        session_days=14,
    )
