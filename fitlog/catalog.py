"""Food and exercise catalog loaded from YAML files, one entry per file.

The id of an entry is its filename stem, so it must be unique across
subdirectories. Invalid files are reported and skipped; valid ones still load.
"""

import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path

import yaml

EXERCISE_TYPES = ("strength", "cardio", "mobility", "core", "balance")
DEFAULT_MINUTES = 10


class CatalogError(ValueError):
    pass


@dataclass(frozen=True)
class Food:
    id: str
    name: str
    serving: str
    calories: float
    protein: float | None = None
    carbs: float | None = None
    fat: float | None = None
    tags: tuple = ()


@dataclass(frozen=True)
class Exercise:
    id: str
    name: str
    type: str
    steps: tuple
    muscles: tuple = ()
    equipment: tuple = ()
    prescription: str | None = None
    tips: tuple = ()
    minutes: float | None = None

    @property
    def duration(self):
        return self.minutes if self.minutes is not None else DEFAULT_MINUTES


@dataclass
class Catalog:
    foods: dict = field(default_factory=dict)
    exercises: dict = field(default_factory=dict)
    errors: list = field(default_factory=list)


def _str_list(data, key, path):
    value = data.get(key, [])
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        raise CatalogError(f"{path}: '{key}' must be a list of non-empty strings")
    return tuple(v.strip() for v in value)


def _number(data, key, path, required=False):
    value = data.get(key)
    if value is None:
        if required:
            raise CatalogError(f"{path}: '{key}' is required")
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise CatalogError(f"{path}: '{key}' must be a non-negative number")
    return float(value)


def _text(data, key, path, required=True):
    value = data.get(key)
    if value is None and not required:
        return None
    if not isinstance(value, (str, int, float)) or isinstance(value, bool) or not str(value).strip():
        raise CatalogError(f"{path}: '{key}' is required")
    return str(value).strip()


def _check_keys(data, allowed, path):
    unknown = set(data) - set(allowed)
    if unknown:
        raise CatalogError(f"{path}: unknown keys {sorted(unknown)}")


def parse_food(id, data, path):
    _check_keys(data, ("name", "serving", "calories", "protein", "carbs", "fat", "tags"), path)
    return Food(
        id=id,
        name=_text(data, "name", path),
        serving=_text(data, "serving", path),
        calories=_number(data, "calories", path, required=True),
        protein=_number(data, "protein", path),
        carbs=_number(data, "carbs", path),
        fat=_number(data, "fat", path),
        tags=_str_list(data, "tags", path),
    )


def parse_exercise(id, data, path):
    _check_keys(data, ("name", "type", "steps", "muscles", "equipment", "prescription", "tips", "minutes"),
                path)
    type = _text(data, "type", path).lower()
    if type not in EXERCISE_TYPES:
        raise CatalogError(f"{path}: 'type' must be one of {', '.join(EXERCISE_TYPES)}")
    steps = _str_list(data, "steps", path)
    if not steps:
        raise CatalogError(f"{path}: 'steps' needs at least one step")
    return Exercise(
        id=id,
        name=_text(data, "name", path),
        type=type,
        steps=steps,
        muscles=_str_list(data, "muscles", path),
        equipment=_str_list(data, "equipment", path),
        prescription=_text(data, "prescription", path, required=False),
        tips=_str_list(data, "tips", path),
        minutes=_minutes(data, path),
    )


def _minutes(data, path):
    minutes = _number(data, "minutes", path)
    if minutes is not None and not 0 < minutes <= 240:
        raise CatalogError(f"{path}: 'minutes' must be between 1 and 240")
    return minutes


def _load_kind(directory, parse, errors, root):
    entries = {}
    if not directory.is_dir():
        return entries
    for path in sorted(directory.rglob("*")):
        if path.suffix not in (".yaml", ".yml") or not path.is_file():
            continue
        rel = path.relative_to(root)
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise CatalogError(f"{rel}: expected a mapping")
            if path.stem in entries:
                raise CatalogError(f"{rel}: duplicate id '{path.stem}'")
            entries[path.stem] = parse(path.stem, data, rel)
        except yaml.YAMLError as e:
            errors.append(f"{rel}: invalid YAML ({e.__class__.__name__})")
        except CatalogError as e:
            errors.append(str(e))
    return entries


def load_catalog(root):
    root = Path(root)
    catalog = Catalog()
    catalog.foods = _load_kind(root / "foods", parse_food, catalog.errors, root)
    catalog.exercises = _load_kind(root / "exercises", parse_exercise, catalog.errors, root)
    return catalog


def _signature(root):
    root = Path(root)
    if not root.is_dir():
        return ()
    return tuple(sorted(
        (str(p), p.stat().st_mtime_ns, p.stat().st_size)
        for p in root.rglob("*") if p.suffix in (".yaml", ".yml") and p.is_file()
    ))


class CatalogStore:
    """Holds the current catalog and swaps it when the files change."""

    def __init__(self, root, repo=None):
        self.root = Path(root)
        self.repo = Path(repo) if repo else None
        self._lock = threading.Lock()
        self._signature = None
        self.catalog = Catalog()
        self.last_pull = None
        self.reload()

    def reload(self):
        with self._lock:
            signature = _signature(self.root)
            if signature != self._signature:
                self.catalog = load_catalog(self.root)
                self._signature = signature
                return True
            return False

    def pull(self):
        if self.repo is None:
            return None
        result = subprocess.run(
            ["git", "-C", str(self.repo), "pull", "--ff-only", "--quiet"],
            capture_output=True, text=True, timeout=120,
        )
        self.last_pull = (result.returncode == 0, (result.stderr or result.stdout).strip())
        return self.last_pull

    def sync(self):
        self.pull()
        return self.reload()
