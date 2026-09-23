import hmac
import threading
from contextlib import asynccontextmanager
from datetime import date, datetime
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import auth, tracker
from .catalog import EXERCISE_TYPES, CatalogStore
from .config import Config
from .db import Database

HERE = Path(__file__).parent
SESSION_COOKIE = "fitlog_session"
UNSAFE_METHODS = ("POST", "PUT", "PATCH", "DELETE")
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "form-action 'self'; frame-ancestors 'none'; base-uri 'none'; object-src 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
}


class LoginRequired(Exception):
    pass


def _fmt(value):
    if value is None:
        return ""
    return f"{value:.0f}" if float(value).is_integer() else f"{value:.1f}"


def create_app(config=None, db=None, store=None, sync_in_background=True):
    config = config or Config.from_env()
    db = db or Database(config.db_path)
    store = store or CatalogStore(config.catalog_dir, config.catalog_repo)
    templates = Jinja2Templates(directory=HERE / "templates")
    templates.env.filters["fmt"] = _fmt

    @asynccontextmanager
    async def lifespan(app):
        stop = threading.Event()
        if sync_in_background and config.pull_minutes > 0:
            def loop():
                while not stop.wait(config.pull_minutes * 60):
                    try:
                        store.sync()
                    except Exception as e:
                        store.last_pull = (False, str(e))
            threading.Thread(target=loop, daemon=True, name="catalog-sync").start()
        yield
        stop.set()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.db = db
    app.state.store = store
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")

    def today():
        return datetime.now(config.timezone).date()

    def client_ip(request):
        if config.trust_proxy and request.headers.get("x-real-ip"):
            return request.headers["x-real-ip"]
        return request.client.host if request.client else "unknown"

    def parse_day(value):
        try:
            day = date.fromisoformat(value)
        except (TypeError, ValueError):
            return today()
        return min(day, today())

    def render(request, name, session=None, status_code=200, **context):
        context.update(request=request, session=session, today=today())
        return templates.TemplateResponse(request, name, context, status_code=status_code)

    async def require_session(request):
        session = auth.get_session(db, request.cookies.get(SESSION_COOKIE))
        if session is None:
            raise LoginRequired()
        if request.method in UNSAFE_METHODS:
            form = await request.form()
            sent = request.headers.get("x-csrf-token") or form.get("csrf") or ""
            if not hmac.compare_digest(str(sent), session["csrf_token"]):
                raise LoginRequired()
        return session

    @app.exception_handler(LoginRequired)
    async def login_required(request, exc):
        if request.headers.get("hx-request"):
            return Response(status_code=204, headers={"HX-Redirect": "/login"})
        return RedirectResponse("/login", status_code=303)

    @app.middleware("http")
    async def security(request, call_next):
        if request.method in UNSAFE_METHODS:
            origin = request.headers.get("origin")
            if origin and origin.split("://", 1)[-1] != request.headers.get("host"):
                return PlainTextResponse("Cross-origin request refused", status_code=403)
        response = await call_next(request)
        for key, value in SECURITY_HEADERS.items():
            response.headers.setdefault(key, value)
        if not request.url.path.startswith("/static/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    # Login

    @app.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request):
        return render(request, "login.html")

    @app.post("/login")
    async def login(request: Request, username: str = Form(""), password: str = Form(""),
                    code: str = Form("")):
        ip = client_ip(request)
        if auth.is_locked(db, ip):
            return render(request, "login.html", status_code=429,
                          error="Too many failed attempts. Try again in 15 minutes.")
        user_id = auth.authenticate(db, username, password, code, ip)
        if user_id is None:
            return render(request, "login.html", status_code=401,
                          error="Incorrect username, password, or code.", username=username)
        token, _ = auth.create_session(db, user_id, config.session_days)
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(
            SESSION_COOKIE, token, max_age=config.session_days * 86400, httponly=True,
            secure=config.secure_cookies, samesite="strict", path="/",
        )
        return response

    @app.post("/logout")
    async def logout(request: Request):
        await require_session(request)
        auth.delete_session(db, request.cookies.get(SESSION_COOKIE))
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(SESSION_COOKIE, path="/")
        return response

    # Day view

    def day_context(day):
        entries = tracker.day_entries(db, day)
        plan = tracker.get_plan(db, store.catalog, day, create=(day == today()))
        return {
            "day": day,
            "entries": entries,
            "totals": tracker.totals(entries),
            "target": float(db.get_setting("calorie_target")),
            "plan": plan,
            "plan_minutes": tracker.plan_minutes(plan),
            "minutes_per_day": int(db.get_setting("minutes_per_day")),
        }

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        session = await require_session(request)
        return render(request, "day.html", session, **day_context(today()))

    @app.get("/day/{value}", response_class=HTMLResponse)
    async def day_page(request: Request, value: str):
        session = await require_session(request)
        return render(request, "day.html", session, **day_context(parse_day(value)))

    def food_partial(request, session, day, error=None):
        return render(request, "_food_log.html", session, error=error, **day_context(day))

    @app.get("/foods/search", response_class=HTMLResponse)
    async def food_search(request: Request, q: str = "", day: str = ""):
        session = await require_session(request)
        terms = q.lower().split()
        foods = sorted(store.catalog.foods.values(), key=lambda f: f.name.lower())
        if terms:
            foods = [f for f in foods
                     if all(t in f" {f.name} {f.id} {' '.join(f.tags)}".lower() for t in terms)]
        return render(request, "_food_results.html", session, foods=foods[:30], day=parse_day(day))

    @app.post("/food/add", response_class=HTMLResponse)
    async def food_add(request: Request, day: str = Form(...), food_id: str = Form(...),
                       servings: float = Form(1.0)):
        session = await require_session(request)
        day = parse_day(day)
        try:
            tracker.add_food(db, store.catalog, day, food_id, servings)
        except tracker.TrackerError as e:
            return food_partial(request, session, day, str(e))
        return food_partial(request, session, day)

    @app.post("/food/custom", response_class=HTMLResponse)
    async def food_custom(request: Request, day: str = Form(...), name: str = Form(""),
                          calories: float = Form(0.0)):
        session = await require_session(request)
        day = parse_day(day)
        try:
            tracker.add_custom(db, day, name, calories)
        except tracker.TrackerError as e:
            return food_partial(request, session, day, str(e))
        return food_partial(request, session, day)

    @app.post("/food/{entry_id}/delete", response_class=HTMLResponse)
    async def food_delete(request: Request, entry_id: int, day: str = Form(...)):
        session = await require_session(request)
        tracker.delete_entry(db, entry_id)
        return food_partial(request, session, parse_day(day))

    def plan_partial(request, session, day, error=None):
        return render(request, "_plan.html", session, error=error, **day_context(day))

    @app.post("/plan/{position}/done", response_class=HTMLResponse)
    async def plan_done(request: Request, position: int, day: str = Form(...), done: str = Form("")):
        session = await require_session(request)
        day = parse_day(day)
        tracker.set_done(db, day, position, done == "1")
        return plan_partial(request, session, day)

    @app.post("/plan/{position}/reroll", response_class=HTMLResponse)
    async def plan_reroll(request: Request, position: int, day: str = Form(...)):
        session = await require_session(request)
        day = parse_day(day)
        try:
            tracker.reroll(db, store.catalog, day, position)
        except tracker.TrackerError as e:
            return plan_partial(request, session, day, str(e))
        return plan_partial(request, session, day)

    # Other pages

    @app.get("/history", response_class=HTMLResponse)
    async def history_page(request: Request):
        session = await require_session(request)
        return render(request, "history.html", session,
                      rows=tracker.history(db, today(), 30),
                      target=float(db.get_setting("calorie_target")))

    @app.get("/catalog", response_class=HTMLResponse)
    async def catalog_page(request: Request):
        session = await require_session(request)
        catalog = store.catalog
        return render(request, "catalog.html", session,
                      foods=sorted(catalog.foods.values(), key=lambda f: f.name.lower()),
                      exercises=sorted(catalog.exercises.values(), key=lambda e: (e.type, e.name.lower())))

    def settings_context(message=None):
        groups = {}
        for exercise in sorted(store.catalog.exercises.values(), key=lambda e: e.name.lower()):
            groups.setdefault(exercise.type, []).append(exercise)
        return {
            "calorie_target": db.get_setting("calorie_target"),
            "exercises_per_day": db.get_setting("exercises_per_day"),
            "minutes_per_day": db.get_setting("minutes_per_day"),
            "groups": [(t, groups[t]) for t in EXERCISE_TYPES if t in groups],
            "excluded": tracker.get_excluded(db),
            "catalog": store.catalog,
            "last_pull": store.last_pull,
            "has_repo": store.repo is not None,
            "message": message,
        }

    @app.get("/settings", response_class=HTMLResponse)
    async def settings_page(request: Request):
        session = await require_session(request)
        return render(request, "settings.html", session, **settings_context())

    @app.post("/settings", response_class=HTMLResponse)
    async def settings_save(request: Request, calorie_target: int = Form(...),
                            exercises_per_day: int = Form(...), minutes_per_day: int = Form(...)):
        session = await require_session(request)
        db.set_setting("calorie_target", max(0, calorie_target))
        db.set_setting("exercises_per_day", min(max(1, exercises_per_day), 20))
        db.set_setting("minutes_per_day", min(max(5, minutes_per_day), 240))
        return render(request, "settings.html", session, **settings_context("Settings saved."))

    @app.post("/settings/exercises", response_class=HTMLResponse)
    async def settings_exercises(request: Request):
        session = await require_session(request)
        form = await request.form()
        shown = set(form.getlist("shown"))
        included = set(form.getlist("include"))
        excluded = (tracker.get_excluded(db) - shown) | (shown - included)
        tracker.set_excluded(db, excluded)
        tracker.replace_excluded(db, store.catalog, today())
        return render(request, "settings.html", session,
                      **settings_context("Exercise choices saved. Today's plan was updated."))

    @app.post("/settings/sync", response_class=HTMLResponse)
    async def settings_sync(request: Request):
        session = await require_session(request)
        changed = store.sync()
        message = "Catalog reloaded." if changed else "Catalog is already up to date."
        return render(request, "settings.html", session, **settings_context(message))

    return app
