import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo


def _bool(value, default):
    if value is None or value == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Config:
    db_path: Path
    catalog_dir: Path
    catalog_repo: Path | None
    pull_minutes: int
    timezone: ZoneInfo
    secure_cookies: bool
    trust_proxy: bool
    session_days: int

    @classmethod
    def from_env(cls, env=None):
        env = os.environ if env is None else env
        root = Path(__file__).resolve().parent.parent
        repo = env.get("FITLOG_CATALOG_REPO")
        return cls(
            db_path=Path(env.get("FITLOG_DB", root / "var" / "fitlog.db")),
            catalog_dir=Path(env.get("FITLOG_CATALOG_DIR", root / "data")),
            catalog_repo=Path(repo) if repo else None,
            pull_minutes=int(env.get("FITLOG_PULL_MINUTES", "10")),
            timezone=ZoneInfo(env.get("FITLOG_TIMEZONE", "America/Los_Angeles")),
            secure_cookies=_bool(env.get("FITLOG_SECURE_COOKIES"), True),
            trust_proxy=_bool(env.get("FITLOG_TRUST_PROXY"), False),
            session_days=int(env.get("FITLOG_SESSION_DAYS", "14")),
        )
