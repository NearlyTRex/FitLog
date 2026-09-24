from pathlib import Path

from fitlog.config import Config


def test_defaults():
    config = Config.from_env({})
    assert config.db_path.name == "fitlog.db"
    assert config.catalog_dir.name == "data"
    assert config.catalog_repo is None and config.catalog_url is None
    assert config.catalog_branch == "main"
    assert config.pull_minutes == 10
    assert str(config.timezone) == "America/Los_Angeles"
    assert config.secure_cookies is True and config.trust_proxy is False
    assert config.session_days == 14


def test_from_env():
    config = Config.from_env({
        "FITLOG_DB": "/data/x.db",
        "FITLOG_CATALOG_DIR": "/catalog/data",
        "FITLOG_CATALOG_REPO": "/catalog",
        "FITLOG_CATALOG_URL": "https://example.com/x.git",
        "FITLOG_CATALOG_BRANCH": "dev",
        "FITLOG_PULL_MINUTES": "0",
        "FITLOG_TIMEZONE": "Etc/UTC",
        "FITLOG_SECURE_COOKIES": "0",
        "FITLOG_TRUST_PROXY": " Yes ",
        "FITLOG_SESSION_DAYS": "3",
    })
    assert config.db_path == Path("/data/x.db")
    assert config.catalog_repo == Path("/catalog")
    assert config.catalog_url == "https://example.com/x.git" and config.catalog_branch == "dev"
    assert config.pull_minutes == 0 and config.session_days == 3
    assert str(config.timezone) == "Etc/UTC"
    assert config.secure_cookies is False and config.trust_proxy is True


def test_empty_values_fall_back_to_defaults():
    config = Config.from_env({"FITLOG_SECURE_COOKIES": "", "FITLOG_CATALOG_REPO": "", "FITLOG_CATALOG_URL": ""})
    assert config.secure_cookies is True
    assert config.catalog_repo is None and config.catalog_url is None
