from __future__ import annotations

import logging
import os
import threading
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.sessions import SessionMiddleware

from .config import BASE_DIR, COOKIE_SECURE, CRON_TOKEN_ENV, DISABLE_BACKGROUND, PUBLIC_BASE_URL, SECRET_KEY
from .db import SessionLocal
from .models import House, User
from .routes import admin, public
from .seed import run as seed_run
from .services import bookings as svc
from .services import automation, ical, storage

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
log = logging.getLogger("app")


def run_jobs(sync_ical: bool = True) -> dict:
    """Release unpaid holds, run the automations (reminders, summaries, auto check-out...)
    and pull Airbnb calendars. Called by the background thread and by /cron (free hosts
    that sleep need an outside ping)."""
    out = {"expired": 0, "auto": {}, "ical": {}}
    db = SessionLocal()
    try:
        out["expired"] = svc.expire_holds(db)
        out["auto"] = automation.run_all(db, PUBLIC_BASE_URL.rstrip("/"))
        if sync_ical:
            for h in db.query(House).filter(House.ical_import_url != ""):
                try:
                    out["ical"][h.slug] = ical.sync(db, h)
                except Exception as e:  # noqa: BLE001
                    log.warning("iCal sync %s failed: %s", h.slug, e)
                    out["ical"][h.slug] = str(e)
    finally:
        db.close()
    return out


def _loop():
    tick = 0
    while True:
        try:
            run_jobs(sync_ical=tick % 24 == 0)
        except Exception as e:  # noqa: BLE001
            log.warning("background jobs: %s", e)
        tick += 1
        time.sleep(300)


@asynccontextmanager
async def lifespan(app: FastAPI):
    seed_run()
    if not DISABLE_BACKGROUND:
        threading.Thread(target=_loop, daemon=True).start()
    yield


class LangPrefix:
    """/en/... serves the English site from the same routes, so every page has a
    stable, crawlable URL per language (needed for hreflang)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            path = scope["path"]
            state = scope.setdefault("state", {})
            state["orig_path"] = path
            if path == "/en" or path.startswith("/en/"):
                new = path[3:] or "/"
                scope["path"] = new
                scope["raw_path"] = new.encode()
                state["lang"] = "en"
        await self.app(scope, receive, send)


class CachedStatic(StaticFiles):
    """Long cache for versioned assets and WebP when the browser accepts it."""

    async def get_response(self, path, scope):
        accept = dict(scope.get("headers") or []).get(b"accept", b"").decode()
        if path.endswith(".jpg") and "image/webp" in accept:
            webp = path[:-4] + ".webp"
            if os.path.exists(os.path.join(self.directory, webp)):
                resp = await super().get_response(webp, scope)
                resp.headers["Vary"] = "Accept"
                resp.headers["Cache-Control"] = "public, max-age=2592000"
                return resp
        resp = await super().get_response(path, scope)
        if resp.status_code == 200:
            # fonts never change; css/js are versioned with ?v= in the templates
            long = path.replace("\\", "/").startswith(("fonts/", "css/", "js/"))
            resp.headers["Cache-Control"] = "public, max-age=31536000, immutable" if long else "public, max-age=2592000"
            if path.endswith(".jpg"):
                resp.headers["Vary"] = "Accept"
        return resp


app = FastAPI(title="Golden Beach Resort", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(GZipMiddleware, minimum_size=800)
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, same_site="lax", https_only=COOKIE_SECURE,
                   max_age=60 * 60 * 24 * 30, session_cookie="gbr")
app.mount("/static", CachedStatic(directory=str(BASE_DIR / "static")), name="static")


@app.middleware("http")
async def headers_and_legacy_lang(request: Request, call_next):
    # old ?lang=en links → the canonical /en URL
    q = request.query_params.get("lang")
    p = request.url.path
    if request.method == "GET" and q in ("th", "en") and not p.startswith(("/api", "/admin", "/static")):
        rest = "&".join(f"{k}={v}" for k, v in request.query_params.multi_items() if k != "lang")
        target = ("/en" + (p if p != "/" else "") if q == "en" else p) + (f"?{rest}" if rest else "")
        return RedirectResponse(target or "/", 301)
    resp = await call_next(request)
    if p.startswith("/admin") or p.startswith("/booking/") or p.startswith("/api"):
        resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    return resp


def _image(data: bytes | None, private: bool):
    if data is None:
        return PlainTextResponse("not found", 404)
    return Response(data, media_type="image/jpeg",
                    headers={"Cache-Control": "private, no-store" if private else "public, max-age=2592000"})


@app.get("/uploads/photos/{name}")
def photo_file(name: str):
    return _image(storage.load("photos", name), False)


@app.get("/uploads/slips/{name}")
def slip_file(request: Request, name: str):
    """Payment slips contain bank details, so only signed-in staff can open them."""
    uid = request.session.get("uid")
    db = SessionLocal()
    try:
        u = db.get(User, uid) if uid else None
    finally:
        db.close()
    if not u or not u.active:
        return PlainTextResponse("not found", 404)
    return _image(storage.load("slips", name), True)


@app.get("/cron")
def cron(token: str = ""):
    """Point a free scheduler (cron-job.org, UptimeRobot, GitHub Actions) here every
    10 minutes: it keeps a sleeping free host awake and runs the timed jobs."""
    expected = os.environ.get(CRON_TOKEN_ENV, "")
    if not expected or token != expected:
        return JSONResponse({"error": "forbidden"}, 403)
    return run_jobs()


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404 and not request.url.path.startswith(("/api", "/static", "/uploads")):
        from .web import render
        return render(request, "public/404.html", status_code=404)
    return JSONResponse({"detail": exc.detail}, exc.status_code)


@app.exception_handler(admin.LoginRequired)
def _login_required(request: Request, exc):
    uid = request.session.get("uid")
    if not uid:
        return RedirectResponse("/admin/login", 303)
    db = SessionLocal()
    try:
        u = db.get(User, uid)
    finally:
        db.close()
    if not u or not u.active:
        request.session.clear()
        return RedirectResponse("/admin/login", 303)
    home = "/admin/housekeeping" if u.role == "housekeeping" else "/admin"
    if request.url.path.rstrip("/") == home.rstrip("/"):
        return PlainTextResponse("forbidden", 403)
    request.session["flash"] = {"msg": "บัญชีนี้ไม่มีสิทธิ์เข้าหน้านั้น", "kind": "error"}
    return RedirectResponse(home, 303)


app.include_router(public.router)
app.include_router(admin.router, prefix="/admin")
# outermost: rewrite /en before anything else sees the path
app.add_middleware(LangPrefix)
