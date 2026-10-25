"""Template environment and helpers shared by public and admin routes."""
from __future__ import annotations

import json
from datetime import date, datetime

from fastapi import Request
from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from .config import BASE_DIR, PUBLIC_BASE_URL
from .db import SessionLocal
from .services import settings as cfg
from .services.bookings import STATUS_EN, STATUS_TH
from .services.security import fmt_phone
from .timeutil import EN_DOW, TH_DOW, month_label, thai_date, today

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

CHANNELS = {
    "website": ("เว็บไซต์", "Website"), "facebook": ("Facebook", "Facebook"), "line": ("LINE OA", "LINE OA"),
    "airbnb": ("Airbnb", "Airbnb"), "phone": ("โทรศัพท์", "Phone"), "other": ("อื่นๆ", "Other"),
}
KINDS = {"big_front": ("หลังใหญ่หน้าสุด", "Big house, sea front"), "big": ("หลังใหญ่", "Big house"),
         "small": ("หลังเล็ก", "Small house")}
AMENITIES = {
    "aircon": ("ห้องแอร์", "Air-con"), "wifi": ("Wi-Fi ฟรี", "Free Wi-Fi"), "tv": ("ทีวี", "TV"),
    "fridge": ("ตู้เย็น", "Fridge"), "water_heater": ("เครื่องทำน้ำอุ่น", "Hot shower"),
    "living_room": ("ห้องนั่งเล่น", "Living room"), "sofa": ("โซฟา", "Sofa"),
    "kitchenette": ("มุมครัวเล็ก", "Kitchenette"), "microwave": ("ไมโครเวฟ", "Microwave"),
    "porch_bench": ("ระเบียงไม้มีม้านั่ง", "Wooden porch with bench"),
    "linen": ("เครื่องนอนและผ้าเช็ดตัวครบ", "Bedding and towels"),
}


def money(v) -> str:
    try:
        f = float(v or 0)
    except (TypeError, ValueError):
        return "0"
    return f"{f:,.0f}" if abs(f - round(f)) < 0.005 else f"{f:,.2f}"


def get_lang(request: Request) -> str:
    """Language comes from the URL (/en/...), never from a cookie, so a URL always
    shows the same language to people and to search engines. API calls pass ?lang=."""
    if request.scope.get("state", {}).get("lang") == "en":
        return "en"
    if request.url.path.startswith("/api") and request.query_params.get("lang") == "en":
        return "en"
    return "th"


def site_url(request: Request) -> str:
    """Absolute origin used in canonical/OG/sitemap. PUBLIC_BASE_URL wins in production."""
    if PUBLIC_BASE_URL and "localhost" not in PUBLIC_BASE_URL:
        return PUBLIC_BASE_URL.rstrip("/")
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    return f"{proto}://{host}"


def lang_path(lang: str, path: str) -> str:
    if lang != "en":
        return path
    return "/en" + (path if path != "/" else "")


def render(request: Request, name: str, status_code: int = 200, **ctx):
    lang = ctx.pop("lang", None) or get_lang(request)
    db = SessionLocal()
    try:
        conf = cfg.get_all(db)
    finally:
        db.close()

    def tt(th: str, en: str = "") -> str:
        return en if (lang == "en" and en) else th

    path = request.url.path            # path without the /en prefix
    origin = site_url(request)
    base = dict(
        request=request, lang=lang, tt=tt, conf=conf, money=money, today=today(),
        thai_date=lambda d, **k: thai_date(d, lang, **k), month_label=lambda y, m: month_label(y, m, lang),
        dow=EN_DOW if lang == "en" else TH_DOW, CHANNELS=CHANNELS, KINDS=KINDS, AMENITIES=AMENITIES,
        status_label=lambda s: (STATUS_EN if lang == "en" else STATUS_TH).get(s, s),
        STATUS_TH=STATUS_TH, fmt_phone=fmt_phone, path=path,
        L="/en" if lang == "en" else "",
        origin=origin,
        canonical=origin + lang_path(lang, path),
        url_th=origin + path, url_en=origin + lang_path("en", path),
        alt_lang_href=lang_path("th" if lang == "en" else "en", path),
        flash=request.session.pop("flash", None) if "session" in request.scope else None,
        user=getattr(request.state, "user", None),
    )
    if name.startswith("public/") and "resort_ld" not in ctx:
        from .services import seo
        db = SessionLocal()
        try:
            base["resort_ld"] = [seo.resort_ld(db, origin, lang), seo.website_ld(db, origin, lang)]
        finally:
            db.close()
    base.update(ctx)
    return templates.TemplateResponse(request, name, base, status_code=status_code)


def flash(request: Request, msg: str, kind: str = "ok"):
    request.session["flash"] = {"msg": msg, "kind": kind}


def tojson(v) -> Markup:
    def d(o):
        if isinstance(o, (date, datetime)):
            return o.isoformat()
        return float(o)
    return Markup(json.dumps(v, default=d, ensure_ascii=False).replace("</", "<\\/"))


def srcset(path: str) -> str:
    """Responsive candidates for the bundled photos (/static/img/x.jpg)."""
    if not path.startswith("/static/img/"):
        return path
    b = path[:-4].removesuffix("-m").removesuffix("-s")
    return f"{b}-s.jpg 480w, {b}-m.jpg 900w, {b}.jpg 1800w"


templates.env.filters["money"] = money
templates.env.filters["srcset"] = srcset
templates.env.filters["tojs"] = tojson
