import os

from fitlog.catalog import CatalogStore, load_catalog
from conftest import REPO_DATA, write


def test_loads_foods_and_exercises(catalog_dir):
    catalog = load_catalog(catalog_dir)
    assert catalog.errors == []
    assert catalog.foods["banana"].calories == 105
    assert catalog.foods["banana"].protein == 1.3
    assert catalog.foods["toast"].fat is None
    assert len(catalog.exercises) == 8
    assert catalog.exercises["s0"].steps == ("Do it.",)
    assert catalog.exercises["s0"].minutes is None and catalog.exercises["s0"].duration == 10


def test_shipped_catalog_is_valid():
    catalog = load_catalog(REPO_DATA)
    assert catalog.errors == []
    assert catalog.foods and catalog.exercises


def test_bad_files_are_skipped_and_reported(tmp_path):
    root = tmp_path / "data"
    write(root / "foods" / "ok.yaml", "name: Ok\nserving: 1\ncalories: 10\n")
    write(root / "foods" / "nocal.yaml", "name: No calories\nserving: 1\n")
    write(root / "foods" / "neg.yaml", "name: Negative\nserving: 1\ncalories: -5\n")
    write(root / "foods" / "typo.yaml", "name: Typo\nserving: 1\ncalories: 5\ncalroies: 5\n")
    write(root / "foods" / "broken.yaml", "name: [unclosed\n")
    write(root / "foods" / "sub" / "ok.yaml", "name: Dup\nserving: 1\ncalories: 1\n")
    write(root / "exercises" / "badtype.yaml", "name: X\ntype: yoga-ish\nsteps: [a]\n")
    write(root / "exercises" / "nosteps.yaml", "name: X\ntype: core\nsteps: []\n")
    write(root / "exercises" / "long.yaml", "name: X\ntype: core\nminutes: 500\nsteps: [a]\n")
    catalog = load_catalog(root)
    assert list(catalog.foods) == ["ok"]
    assert catalog.exercises == {}
    joined = "\n".join(catalog.errors)
    for fragment in ("'calories' is required", "non-negative", "unknown keys ['calroies']",
                     "invalid YAML", "duplicate id 'ok'", "'type' must be one of", "at least one step", "'minutes' must be between"):
        assert fragment in joined
    assert len(catalog.errors) == 8


def test_store_reloads_only_on_change(catalog_dir):
    store = CatalogStore(catalog_dir)
    assert store.reload() is False
    write(catalog_dir / "foods" / "apple.yaml", "name: Apple\nserving: 1\ncalories: 95\n")
    assert store.reload() is True
    assert "apple" in store.catalog.foods
    os.remove(catalog_dir / "foods" / "apple.yaml")
    assert store.reload() is True
    assert "apple" not in store.catalog.foods
