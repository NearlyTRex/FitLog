import re

import pyotp
import pytest
from fastapi.testclient import TestClient

from fitlog import auth
from fitlog.web import create_app
from conftest import make_db

PASSWORD = "correct horse battery"


@pytest.fixture
def app_db(config):
    return make_db(config.db_path)


@pytest.fixture
def secret(app_db):
    return auth.create_user(app_db, "me", PASSWORD)


@pytest.fixture
def client(config, app_db, store):
    with TestClient(create_app(config, app_db, store, sync_in_background=False)) as client:
        yield client


def login(client, secret):
    return client.post("/login", data={
        "username": "me", "password": PASSWORD, "code": pyotp.TOTP(secret).now(),
    }, follow_redirects=False)


@pytest.fixture
def csrf(client, secret):
    assert login(client, secret).status_code == 303
    page = client.get("/").text
    return re.search(r'"X-CSRF-Token": "([^"]+)"', page).group(1)


def test_pages_require_login(client):
    for path in ("/", "/history", "/catalog", "/settings", "/day/2026-01-01"):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 303 and response.headers["location"] == "/login"


def test_htmx_requests_get_hx_redirect(client):
    response = client.get("/foods/search", headers={"HX-Request": "true"})
    assert response.status_code == 204 and response.headers["HX-Redirect"] == "/login"


def test_login_sets_hardened_cookie(client, secret):
    response = login(client, secret)
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie


def test_bad_login(client, secret):
    response = client.post("/login", data={"username": "me", "password": PASSWORD, "code": "000000"})
    assert response.status_code == 401
    assert "Incorrect" in response.text


def test_lockout_page(client, secret):
    for _ in range(auth.MAX_FAILURES_PER_IP):
        client.post("/login", data={"username": "me", "password": "nope nope nope", "code": "000000"})
    response = login(client, secret)
    assert response.status_code == 429


def test_security_headers(client):
    response = client.get("/login")
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["cache-control"] == "no-store"


def test_today_shows_plan_and_foods(client, csrf):
    page = client.get("/").text
    assert page.count('class="exercise') == 4
    assert "Do it." in page
    results = client.get("/foods/search", params={"q": "ban"}).text
    assert "Banana" in results and "Toast" not in results


def test_post_without_csrf_is_rejected(client, csrf):
    response = client.post("/food/custom", data={"day": "2026-01-01", "name": "x", "calories": 1},
                           follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/login"


def test_cross_origin_post_is_refused(client, csrf):
    response = client.post("/food/custom", data={"day": "2026-01-01", "name": "x", "calories": 1},
                           headers={"X-CSRF-Token": csrf, "Origin": "https://evil.example"})
    assert response.status_code == 403


def test_log_food_and_mark_done(client, csrf):
    headers = {"X-CSRF-Token": csrf, "HX-Request": "true"}
    day = client.get("/").text
    day_iso = re.search(r'name="day" value="([\d-]+)"', day).group(1)
    response = client.post("/food/add", data={"day": day_iso, "food_id": "banana", "servings": 2}, headers=headers)
    assert response.status_code == 200 and "210" in response.text
    response = client.post("/food/custom", data={"day": day_iso, "name": "Latte", "calories": 190}, headers=headers)
    assert "400" in response.text and "Latte" in response.text
    response = client.post("/plan/0/done", data={"day": day_iso, "done": "1"}, headers=headers)
    assert "Done ✓" in response.text


def test_settings(client, csrf):
    response = client.post("/settings", data={"csrf": csrf, "calorie_target": 1800, "exercises_per_day": 3,
                                              "minutes_per_day": 45})
    assert "Settings saved." in response.text
    assert 'value="1800"' in response.text and 'value="45"' in response.text
    response = client.post("/settings/sync", data={"csrf": csrf})
    assert "up to date" in response.text


def test_logout(client, csrf):
    client.post("/logout", data={"csrf": csrf})
    assert client.get("/", follow_redirects=False).status_code == 303


def test_exercise_choices(client, csrf, app_db):
    page = client.get("/").text
    planned = re.findall(r"<h3>([^<]+)</h3>", page)
    settings = client.get("/settings").text
    assert settings.count('type="checkbox" name="include"') == 8
    assert settings.count(" checked>") == 8
    ids = [f"s{i}" for i in range(4)] + [f"c{i}" for i in range(4)]
    response = client.post("/settings/exercises", data={
        "csrf": csrf, "shown": ids, "include": [i for i in ids if i.startswith("s")],
    })
    assert "Exercise choices saved" in response.text
    assert response.text.count(" checked>") == 4
    from fitlog import tracker
    assert tracker.get_excluded(app_db) == {f"c{i}" for i in range(4)}
    page = client.get("/").text
    now = re.findall(r"<h3>([^<]+)</h3>", page)
    assert all(name.startswith("Strength") for name in now)
    assert len(now) <= len(planned)


def test_exercise_choices_keep_unshown_exclusions(client, csrf, app_db):
    from fitlog import tracker
    tracker.set_excluded(app_db, {"gone-from-catalog"})
    client.post("/settings/exercises", data={"csrf": csrf, "shown": ["s0", "s1"], "include": ["s1"]})
    assert tracker.get_excluded(app_db) == {"gone-from-catalog", "s0"}
