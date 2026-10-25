from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from ..config import PUBLIC_BASE_URL
from ..db import get_db
from ..models import Addon, Booking, BuyoutPackage, Customer, House, Inquiry, OnlineCharge, Photo, Promotion
from ..services import availability as av
from ..services import bookings as svc
from ..services import automation, ical, notify, opn, promptpay, seo
from ..services import settings as cfg
from ..services.pricing import completed_stays, from_price, nightly_prices, quote
from ..services.security import clean_phone
from ..services.storage import UploadError, save_image_fp
from ..timeutil import parse_date, today
from ..web import flash, get_lang, lang_path, render, site_url

router = APIRouter()


def _houses(db: Session) -> list[House]:
    return db.query(House).filter(House.is_active.is_(True)).order_by(House.side, House.position).all()


def _dates(ci: str | None, co: str | None) -> tuple[date, date, bool]:
    d1, d2 = parse_date(ci), parse_date(co)
    chosen = bool(d1 and d2 and d2 > d1 and d1 >= today())
    if not chosen:
        d1 = today()
        d2 = d1 + timedelta(days=1)
    return d1, d2, chosen


def _site_promos(db: Session) -> list[Promotion]:
    t = today()
    return [p for p in db.query(Promotion).filter(Promotion.active.is_(True), Promotion.show_on_site.is_(True))
            .order_by(Promotion.sort) if not p.end_date or p.end_date >= t]


def _plan_data(db: Session, houses: list[House], ci: date, co: date, guests: int, lang: str) -> dict:
    out = {}
    for h in houses:
        free = av.is_free(db, h.id, ci, co)
        q = quote(db, h, ci, co, min(guests, h.max_guests), 0, lang=lang)
        nxt = None if free else av.next_free_date(db, h.id, ci)
        out[h.id] = {"free": free, "fits": h.max_guests >= guests, "total": q.total,
                     "nights": q.n, "next": nxt.isoformat() if nxt else None}
    return out


@router.get("/")
def home(request: Request, ci: str | None = None, co: str | None = None, guests: int = 2,
         db: Session = Depends(get_db)):
    lang = get_lang(request)
    houses = _houses(db)
    d1, d2, chosen = _dates(ci, co)
    return render(request, "public/home.html", houses=houses, ci=d1, co=d2, chosen=chosen,
                  guests=guests, plan=_plan_data(db, houses, d1, d2, guests, lang),
                  promos=_site_promos(db), from_price=min(from_price(db, h) for h in houses) if houses else 0,
                  buyout=db.query(BuyoutPackage).filter(BuyoutPackage.active.is_(True)).first(),
                  gallery=db.query(Photo).filter(Photo.house_id.is_(None)).order_by(Photo.sort).all())


@router.get("/api/availability")
def api_availability(request: Request, ci: str, co: str, guests: int = 2, db: Session = Depends(get_db)):
    d1, d2, chosen = _dates(ci, co)
    if not chosen:
        return JSONResponse({"error": "invalid dates"}, status_code=400)
    return _plan_data(db, _houses(db), d1, d2, guests, get_lang(request))


@router.get("/api/quote")
def api_quote(request: Request, house: int, ci: str, co: str, adults: int = 2, children: int = 0,
              phone: str = "", addons: str = "", db: Session = Depends(get_db)):
    h = db.get(House, house)
    d1, d2 = parse_date(ci), parse_date(co)
    if not h or not d1 or not d2:
        return JSONResponse({"errors": ["ข้อมูลไม่ครบ"]}, status_code=400)
    qty = {}
    for part in addons.split(","):
        if ":" in part:
            a, n = part.split(":", 1)
            if a.isdigit() and n.isdigit():
                qty[int(a)] = int(n)
    lang = get_lang(request)
    q = quote(db, h, d1, d2, adults, children, qty, clean_phone(phone), lang=lang)
    data = q.to_json()
    data["available"] = av.is_free(db, h.id, d1, d2)
    if not data["available"]:
        nxt = av.next_free_date(db, h.id, d1)
        data["next_free"] = nxt.isoformat() if nxt else None
        data["alternatives"] = [{"id": x.id, "name": x.name(lang), "slug": x.slug}
                                for x in av.alternatives(db, d1, d2, adults + children, h.id)]
    return data


@router.get("/houses")
def houses_page(request: Request, db: Session = Depends(get_db)):
    houses = _houses(db)
    return render(request, "public/houses.html", houses=houses)


@router.get("/houses/{slug}")
def house_page(request: Request, slug: str, db: Session = Depends(get_db)):
    h = db.query(House).filter(House.slug == slug, House.is_active.is_(True)).first()
    if not h:
        return render(request, "public/404.html", status_code=404)
    start = today().replace(day=1)
    months = []
    for i in range(3):
        y, m = start.year + (start.month - 1 + i) // 12, (start.month - 1 + i) % 12 + 1
        first = date(y, m, 1)
        nxt = date(y + (m // 12), m % 12 + 1, 1)
        months.append((first, nxt))
    grid = av.month_grid(db, start, (months[-1][1] - start).days, include_pending=False).get(h.id, {})
    taken = sorted(d.isoformat() for d in grid)
    prices = {n.night.isoformat(): n.price for n in nightly_prices(db, h, today(), months[-1][1])}
    addons = db.query(Addon).filter(Addon.active.is_(True)).order_by(Addon.sort).all()
    others = [x for x in _houses(db) if x.id != h.id and x.kind == h.kind][:3]
    lang = get_lang(request)
    origin = site_url(request)
    crumbs = seo.breadcrumb_ld(origin, [
        ("หน้าแรก" if lang == "th" else "Home", lang_path(lang, "/")),
        ("บ้านพัก" if lang == "th" else "Houses", lang_path(lang, "/houses")),
        (h.name(lang), lang_path(lang, f"/houses/{h.slug}"))])
    return render(request, "public/house.html", h=h, months=months, taken=taken, prices=prices,
                  addons=addons, others=others, promos=_site_promos(db),
                  house_ld=seo.house_ld(h, origin, lang), crumbs_ld=crumbs)


@router.get("/availability")
def availability_page(request: Request, m: str | None = None, db: Session = Depends(get_db)):
    t = today()
    try:
        y, mo = (int(x) for x in (m or "").split("-"))
        first = date(y, mo, 1)
    except ValueError:
        first = t.replace(day=1)
    nxt = date(first.year + first.month // 12, first.month % 12 + 1, 1)
    prev = date(first.year - (1 if first.month == 1 else 0), 12 if first.month == 1 else first.month - 1, 1)
    days = [first + timedelta(days=i) for i in range((nxt - first).days)]
    grid = av.month_grid(db, first, len(days), include_pending=False)
    return render(request, "public/availability.html", houses=_houses(db), days=days, grid=grid,
                  first=first, nxt=nxt, prev=prev if prev >= t.replace(day=1) else None)


@router.get("/book")
def book_page(request: Request, house: str = "", ci: str | None = None, co: str | None = None,
              adults: int = 2, children: int = 0, db: Session = Depends(get_db)):
    houses = _houses(db)
    sel = next((h for h in houses if h.slug == house or str(h.id) == house), None)
    d1, d2, chosen = _dates(ci, co)
    addons = db.query(Addon).filter(Addon.active.is_(True)).order_by(Addon.sort).all()
    return render(request, "public/book.html", houses=houses, sel=sel, ci=d1, co=d2, chosen=chosen,
                  adults=adults, children=children, addons=addons, error=None, form={})


@router.post("/book")
async def book_submit(request: Request, db: Session = Depends(get_db)):
    lang = get_lang(request)
    form = await request.form()
    houses = _houses(db)
    h = db.get(House, int(form.get("house_id") or 0))
    d1, d2 = parse_date(form.get("check_in")), parse_date(form.get("check_out"))
    addon_qty = {}
    for k, v in form.items():
        if k.startswith("addon_") and str(v).isdigit() and int(v) > 0:
            addon_qty[int(k[6:])] = min(int(v), 10)
    error = None
    if form.get("website"):  # honeypot field: bots fill it, people never see it
        return RedirectResponse("/", 303)
    if not h or not d1 or not d2:
        error = "กรุณาเลือกบ้านและวันเข้าพัก" if lang == "th" else "Choose a house and your dates"
    elif not (form.get("name") or "").strip():
        error = "กรุณากรอกชื่อผู้จอง" if lang == "th" else "Please enter your name"
    elif form.get("agree") != "1":
        error = "กรุณายอมรับเงื่อนไขการเข้าพัก" if lang == "th" else "Please accept the house rules"
    if not error:
        try:
            mode = cfg.get(db, "booking_mode")
            b = svc.create(db, house=h, check_in=d1, check_out=d2, adults=int(form.get("adults") or 1),
                           children=int(form.get("children") or 0), pets=int(form.get("pets") or 0),
                           name=form.get("name", ""), phone=form.get("phone", ""), email=form.get("email", ""),
                           line_id=form.get("line_id", ""), addon_qty=addon_qty,
                           special_request=form.get("special_request", ""), channel="website",
                           status="awaiting_payment" if mode == "instant" else "pending",
                           base_url=PUBLIC_BASE_URL, lang=lang)
            mine = request.session.get("mine", [])
            request.session["mine"] = (mine + [b.code])[-10:]
            return RedirectResponse(lang_path(lang, f"/booking/{b.code}") + "?new=1", 303)
        except av.ConflictError as e:
            c = e.conflicts[0] if e.conflicts else None
            nxt = av.next_free_date(db, h.id, d1)
            error = (f"{h.name(lang)} ไม่ว่างในช่วงวันที่เลือก" if lang == "th" else f"{h.name(lang)} is taken on those dates")
            if nxt:
                error += (f" ว่างอีกครั้งตั้งแต่ {nxt.day}/{nxt.month}/{nxt.year + 543}" if lang == "th"
                          else f"; free again from {nxt.isoformat()}")
            db.rollback()
        except svc.BookingError as e:
            error = str(e)
            db.rollback()
    addons = db.query(Addon).filter(Addon.active.is_(True)).order_by(Addon.sort).all()
    return render(request, "public/book.html", status_code=422, houses=houses, sel=h,
                  ci=d1 or today(), co=d2 or today() + timedelta(days=1), chosen=bool(d1 and d2),
                  adults=int(form.get("adults") or 2), children=int(form.get("children") or 0),
                  addons=addons, error=error, form=dict(form))


def _get_booking(request: Request, db: Session, code: str) -> Booking | None:
    b = db.query(Booking).filter(Booking.code == code.upper()).first()
    if not b or b.code not in request.session.get("mine", []):
        return None
    return b


@router.get("/booking")
def lookup_page(request: Request):
    return render(request, "public/lookup.html", error=None)


@router.post("/booking")
def lookup_submit(request: Request, code: str = Form(""), phone: str = Form(""), db: Session = Depends(get_db)):
    lang = get_lang(request)
    b = db.query(Booking).filter(Booking.code == code.strip().upper()).first()
    if b and b.customer.phone == clean_phone(phone):
        mine = request.session.get("mine", [])
        request.session["mine"] = (mine + [b.code])[-10:]
        return RedirectResponse(lang_path(lang, f"/booking/{b.code}"), 303)
    return render(request, "public/lookup.html", status_code=404,
                  error="ไม่พบการจองนี้ ตรวจเลขจองและเบอร์โทรอีกครั้ง" if lang == "th"
                  else "We couldn't find that booking. Check the code and phone number.")


@router.get("/booking/{code}")
def booking_page(request: Request, code: str, new: int = 0, db: Session = Depends(get_db)):
    b = _get_booking(request, db, code)
    if not b:
        return RedirectResponse(lang_path(get_lang(request), "/booking"), 303)
    conf = cfg.get_all(db)
    due = max(0.0, float(b.deposit_due) - float(b.amount_paid))
    qr = promptpay.svg(conf["promptpay_id"], due) if conf["promptpay_id"] and due > 0 else None
    loyalty = None
    lp = db.query(Promotion).filter(Promotion.kind == "loyalty", Promotion.active.is_(True)).first()
    if lp:
        loyalty = {"done": completed_stays(db, b.customer.phone, b.channel, lp), "nth": lp.nth, "promo": lp}
    online = opn.public_config(db) if opn.enabled(db) else None
    charge = _last_charge(db, b) if online else None
    return render(request, "public/booking.html", b=b, bd=svc.breakdown(b), qr=qr, due=due,
                  new=new, loyalty=loyalty, error=request.session.pop("slip_error", None),
                  online=online, charge=charge, METHOD_TH=opn.METHOD_TH)


def _last_charge(db: Session, b: Booking) -> OnlineCharge | None:
    return db.query(OnlineCharge).filter(OnlineCharge.booking_id == b.id).order_by(OnlineCharge.id.desc()).first()


# ---------------------------------------------------------------- online payment (Opn)
@router.post("/booking/{code}/pay/opn")
async def pay_online(request: Request, code: str, db: Session = Depends(get_db)):
    lang = get_lang(request)
    b = _get_booking(request, db, code)
    if not b:
        return RedirectResponse(lang_path(lang, "/booking"), 303)
    back = lang_path(lang, f"/booking/{b.code}") + "#pay"
    if not opn.enabled(db):
        return RedirectResponse(back, 303)
    f = await request.form()
    token, source = str(f.get("omiseToken") or ""), str(f.get("omiseSource") or "")
    method = str(f.get("method") or "")
    try:
        if token and not token.startswith("tokn_") or source and not source.startswith("src_"):
            raise opn.OpnError("ข้อมูลการชำระไม่ถูกต้อง")
        row = opn.create_charge(db, b, method=method, token=token, source=source,
                                return_uri=site_url(request) + lang_path(lang, f"/booking/{b.code}/pay/return"))
        db.commit()
    except opn.OpnError as e:
        db.rollback()
        request.session["slip_error"] = (("ชำระออนไลน์ไม่สำเร็จ: " if lang == "th" else "Online payment failed: ") + str(e))
        return RedirectResponse(back, 303)
    if row.authorize_uri and row.status == "pending" and row.authorize_uri.startswith("https://"):
        return RedirectResponse(row.authorize_uri, 303)     # bank app / 3-D Secure page
    return RedirectResponse(back, 303)


@router.get("/booking/{code}/pay/return")
def pay_return(request: Request, code: str, db: Session = Depends(get_db)):
    """The bank / card page sends the guest back here."""
    b = db.query(Booking).filter(Booking.code == code.upper()).first()
    if b:
        row = _last_charge(db, b)
        if row:
            try:
                opn.refresh(db, row)
                db.commit()
            except opn.OpnError:
                db.rollback()
    return RedirectResponse(lang_path(get_lang(request), f"/booking/{code.upper()}") + "#pay", 303)


@router.get("/booking/{code}/pay/status")
def pay_status(request: Request, code: str, db: Session = Depends(get_db)):
    """Polled by the booking page while a QR is on screen."""
    b = _get_booking(request, db, code)
    if not b:
        return JSONResponse({"error": "not found"}, 404)
    row = _last_charge(db, b)
    if row and row.status == "pending":
        try:
            opn.refresh(db, row)
            db.commit()
        except opn.OpnError:
            db.rollback()
    return {"charge": row.status if row else None, "booking": b.status,
            "paid": float(b.amount_paid), "due": float(b.deposit_due)}


@router.get("/booking/{code}/pay/qr")
def pay_qr(request: Request, code: str, db: Session = Depends(get_db)):
    """Opn's PromptPay QR picture, fetched with our key and passed through."""
    import urllib.request
    b = _get_booking(request, db, code)
    row = _last_charge(db, b) if b else None
    if not row or not row.qr_url.startswith("https://") or row.status != "pending":
        return PlainTextResponse("not found", 404)
    try:
        import base64
        req = urllib.request.Request(row.qr_url, headers={
            "Authorization": "Basic " + base64.b64encode((cfg.get(db, "opn_secret_key") + ":").encode()).decode()})
        with urllib.request.urlopen(req, timeout=20) as r:
            data, ctype = r.read(), r.headers.get("Content-Type", "image/svg+xml")
    except Exception:  # noqa: BLE001
        return PlainTextResponse("unavailable", 502)
    if not ctype.startswith("image/"):
        ctype = "image/svg+xml"
    return Response(data, media_type=ctype, headers={"Cache-Control": "private, max-age=600"})


@router.post("/line/webhook")
async def line_webhook(request: Request, db: Session = Depends(get_db)):
    """LINE Messaging API webhook. Invite the bot into the admin group and the group ID is
    saved by itself; anyone who messages the bot gets their own user ID back (for
    personal commission alerts). Requests are checked with the channel secret."""
    import base64
    import hashlib
    import hmac
    import json as _json
    secret = cfg.get(db, "line_channel_secret")
    body = await request.body()
    sig = request.headers.get("x-line-signature", "")
    good = base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode() if secret else ""
    if not secret or not hmac.compare_digest(good, sig):
        return JSONResponse({"ok": False}, 401)
    try:
        events = _json.loads(body or b"{}").get("events", [])
    except ValueError:
        return JSONResponse({"ok": False}, 400)
    for ev in events:
        src = ev.get("source") or {}
        if src.get("type") == "group" and src.get("groupId"):
            gid = src["groupId"]
            if not cfg.get(db, "line_group_id"):
                cfg.set_many(db, {"line_group_id": gid})
                notify.bell(db, "เชื่อมกลุ่ม LINE แอดมินแล้ว", f"Group ID {gid} บันทึกในตั้งค่าให้อัตโนมัติ", "/admin/settings",
                            roles=("owner", "manager"))
                db.commit()
                notify.line_group(db, "✅ เชื่อมกลุ่มนี้กับระบบจอง โกลเด้น บีช รีสอร์ท แล้ว การแจ้งเตือนจะเข้ากลุ่มนี้")
        elif src.get("type") == "user" and src.get("userId") and ev.get("type") in ("follow", "message"):
            uid = src["userId"]
            if once_key(db, f"line-user:{uid}"):
                notify.bell(db, "มีคนทักบอท LINE", f"User ID: {uid} (นำไปใส่ในหน้า ผู้ใช้งาน เพื่อรับแจ้งค่าคอมส่วนตัว)",
                            "/admin/users", roles=("owner",))
                db.commit()
    return {"ok": True}


def once_key(db: Session, key: str) -> bool:
    ok = automation.once(db, key)
    db.commit()
    return ok


@router.post("/payments/opn/webhook")
async def opn_webhook(request: Request, db: Session = Depends(get_db)):
    try:
        payload = await request.json()
    except ValueError:
        return JSONResponse({"ok": False}, 400)
    try:
        result = opn.handle_webhook(db, payload if isinstance(payload, dict) else {})
    except opn.OpnError as e:
        db.rollback()
        return JSONResponse({"ok": False, "error": str(e)}, 502)   # Opn retries later
    return {"ok": True, "result": result}


@router.post("/booking/{code}/slip")
async def booking_slip(request: Request, code: str, amount: str = Form("0"), slip: UploadFile = File(...),
                       db: Session = Depends(get_db)):
    b = _get_booking(request, db, code)
    if not b:
        return RedirectResponse(lang_path(get_lang(request), "/booking"), 303)
    try:
        path, fp = await save_image_fp(slip, "slips", 1600)
        amt = float((amount or "0").replace(",", "")) or float(b.deposit_due) - float(b.amount_paid)
        svc.upload_slip(db, b, path, amt, fp)
        db.commit()
    except (UploadError, svc.BookingError, ValueError) as e:
        request.session["slip_error"] = str(e)
    return RedirectResponse(lang_path(get_lang(request), f"/booking/{b.code}") + "#pay", 303)


@router.get("/buyout")
def buyout_page(request: Request, db: Session = Depends(get_db)):
    return render(request, "public/buyout.html", houses=_houses(db),
                  pkg=db.query(BuyoutPackage).filter(BuyoutPackage.active.is_(True)).first(), sent=False)


@router.get("/promotions")
def promotions_page(request: Request, db: Session = Depends(get_db)):
    return render(request, "public/promotions.html", promos=_site_promos(db),
                  pkg=db.query(BuyoutPackage).filter(BuyoutPackage.active.is_(True)).first())


@router.get("/contact")
def contact_page(request: Request, sent: int = 0):
    return render(request, "public/contact.html", sent=sent)


@router.post("/inquiry")
async def inquiry(request: Request, db: Session = Depends(get_db)):
    f = await request.form()
    back = f.get("back") or "/contact"
    if not back.startswith("/") or back.startswith("//"):
        back = "/contact"
    back = lang_path(get_lang(request), back)
    if f.get("website"):
        return RedirectResponse(back, 303)
    if not (f.get("name") and (f.get("phone") or f.get("email"))):
        flash(request, "กรุณากรอกชื่อ และเบอร์โทรหรืออีเมล" if get_lang(request) == "th"
              else "Please leave your name and a phone number or e-mail", "error")
        return RedirectResponse(back + "#form", 303)
    iq = Inquiry(kind=f.get("kind") or "contact", name=f.get("name", "")[:120], phone=clean_phone(f.get("phone", "")),
                 email=f.get("email", "")[:160], date_from=parse_date(f.get("date_from")),
                 nights=int(f.get("nights") or 1), guests=int(f.get("guests") or 0), message=f.get("message", "")[:3000])
    db.add(iq)
    kind_th = {"buyout": "ขอใบเสนอราคาเหมารีสอร์ท", "event": "สอบถามจัดงาน"}.get(iq.kind, "ข้อความสอบถาม")
    notify.bell(db, f"{kind_th} จาก{iq.name}", iq.message[:120], "/admin/inquiries", "inquiry")
    notify.line_group(db, f"📩 {kind_th}\n👤 {iq.name} {iq.phone}\n{iq.message[:300]}")
    db.flush()
    automation.inquiry_reply(db, iq, site_url(request))
    db.commit()
    flash(request, "ได้รับข้อความแล้ว เจ้าหน้าที่จะติดต่อกลับโดยเร็ว" if get_lang(request) == "th"
          else "Thanks, we got your message and will get back to you soon.")
    return RedirectResponse(back + "#form", 303)


@router.get("/ical/{slug}.ics")
def ical_export(slug: str, token: str = "", db: Session = Depends(get_db)):
    h = db.query(House).filter(House.slug == slug).first()
    if not h or not token or token != h.ical_token:
        return PlainTextResponse("not found", 404)
    return Response(ical.export(db, h), media_type="text/calendar")


@router.get("/faq")
def faq_page(request: Request, db: Session = Depends(get_db)):
    lang = get_lang(request)
    items = seo.faq(db, lang)
    return render(request, "public/faq.html", items=items, faq_ld=seo.faq_ld(items, lang))


AI_SEARCH_BOTS = ["Googlebot", "Bingbot", "OAI-SearchBot", "ChatGPT-User", "GPTBot", "Claude-SearchBot", "Claude-User",
                  "ClaudeBot", "PerplexityBot", "Perplexity-User", "Google-Extended", "Applebot", "Applebot-Extended",
                  "DuckAssistBot", "Amzn-SearchBot", "meta-webindexer", "MistralAI-Index", "YouBot"]


@router.get("/robots.txt")
def robots(request: Request):
    origin = site_url(request)
    private = "Disallow: /admin\nDisallow: /booking/\nDisallow: /api/\nDisallow: /cron\n"
    body = "# Search engines and AI assistants are welcome to read and cite this site.\n"
    for bot in AI_SEARCH_BOTS:
        body += f"\nUser-agent: {bot}\nAllow: /\n{private}"
    body += f"\nUser-agent: *\nAllow: /\n{private}\nSitemap: {origin}/sitemap.xml\n"
    return PlainTextResponse(body)


@router.get("/llms.txt")
def llms(request: Request, db: Session = Depends(get_db)):
    return PlainTextResponse(seo.llms_txt(db, site_url(request)), media_type="text/markdown; charset=utf-8")


@router.get("/site.webmanifest")
def manifest(db: Session = Depends(get_db)):
    c = cfg.get_all(db)
    return JSONResponse({"name": c["resort_name_en"], "short_name": "Golden Beach", "lang": "th", "start_url": "/",
                         "display": "browser", "background_color": "#FAFAF7", "theme_color": "#2E1D12",
                         "icons": [{"src": "/static/img/apple-touch-icon.png", "sizes": "180x180", "type": "image/png"},
                                   {"src": "/static/img/favicon.svg", "sizes": "any", "type": "image/svg+xml"}]})


@router.get("/sitemap.xml")
def sitemap(request: Request, db: Session = Depends(get_db)):
    origin = site_url(request)
    pages = [("/", "1.0", "/static/img/row-houses.jpg"), ("/houses", "0.9", None), ("/availability", "0.8", None),
             ("/buyout", "0.8", "/static/img/lawn-gazebo.jpg"), ("/promotions", "0.7", None), ("/faq", "0.7", None),
             ("/contact", "0.6", None)]
    pages += [(f"/houses/{h.slug}", "0.8", h.cover) for h in _houses(db)]
    lastmod = today().isoformat()
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:xhtml="http://www.w3.org/1999/xhtml" '
           'xmlns:image="http://www.google.com/schemas/sitemap-image/1.1">']
    for path, prio, img in pages:
        for lang in ("th", "en"):
            loc = origin + lang_path(lang, path)
            out.append(f"<url><loc>{loc}</loc><lastmod>{lastmod}</lastmod><priority>{prio}</priority>"
                       f'<xhtml:link rel="alternate" hreflang="th" href="{origin}{path}"/>'
                       f'<xhtml:link rel="alternate" hreflang="en" href="{origin}{lang_path("en", path)}"/>'
                       f'<xhtml:link rel="alternate" hreflang="x-default" href="{origin}{path}"/>'
                       + (f"<image:image><image:loc>{origin if img.startswith('/') else ''}{img}</image:loc></image:image>" if img else "")
                       + "</url>")
    out.append("</urlset>")
    return Response("".join(out), media_type="application/xml")
