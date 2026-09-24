import os

import pytest

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


def git(*args, cwd=None):
    import subprocess
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@t", "HOME": "/nonexistent", "PATH": os.environ["PATH"]})


def test_store_clones_then_pulls(tmp_path):
    origin = tmp_path / "origin"
    write(origin / "data" / "foods" / "apple.yaml", "name: Apple\nserving: 1\ncalories: 95\n")
    git("init", "-q", "-b", "main", cwd=origin)
    git("add", ".", cwd=origin)
    git("commit", "-qm", "seed", cwd=origin)

    repo = tmp_path / "catalog"
    repo.mkdir()
    store = CatalogStore(repo / "data", repo, url=str(origin), branch="main")
    assert store.catalog.foods == {}
    assert store.sync() is True
    assert store.last_pull == (True, "")
    assert "apple" in store.catalog.foods

    write(origin / "data" / "foods" / "pear.yaml", "name: Pear\nserving: 1\ncalories: 100\n")
    git("add", ".", cwd=origin)
    git("commit", "-qm", "pear", cwd=origin)
    assert store.sync() is True
    assert set(store.catalog.foods) == {"apple", "pear"}
    assert store.sync() is False


def test_single_string_becomes_a_list(tmp_path):
    root = tmp_path / "data"
    write(root / "foods" / "a.yaml", "name: A\nserving: 1\ncalories: 1\ntags: fruit\n")
    assert load_catalog(root).foods["a"].tags == ("fruit",)


@pytest.mark.parametrize("text,error", [
    ("name: A\nserving: 1\ncalories: 1\ntags: [1]\n", "'tags' must be a list"),
    ("name: A\nserving: 1\ncalories: 1\ntags: ['']\n", "'tags' must be a list"),
    ("serving: 1\ncalories: 1\n", "'name' is required"),
    ("name: ''\nserving: 1\ncalories: 1\n", "'name' is required"),
    ("name: [A]\nserving: 1\ncalories: 1\n", "'name' is required"),
    ("- name: A\n", "expected a mapping"),
    ("", "expected a mapping"),
])
def test_malformed_food(tmp_path, text, error):
    root = tmp_path / "data"
    write(root / "foods" / "a.yaml", text)
    catalog = load_catalog(root)
    assert catalog.foods == {}
    assert error in catalog.errors[0]
