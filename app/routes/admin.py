"""Back office. Role rules:
owner        everything
manager      everything except users and paying out commission
sales        dashboard, calendar, bookings, customers, inquiries, own wallet
housekeeping housekeeping board and read-only calendar
"""
from __future__ import annotations

import csv
import io
import secrets
from collections import defaultdict
from datetime import date, timedelta

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse, Response
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from ..config import PUBLIC_BASE_URL
from ..db import get_db
from ..models import (Addon, AuditLog, Block, Booking, BuyoutPackage, CHANNELS, CommissionRule, Customer,
                      HOLDING, House, Inquiry, Notification, Payment, Payout, Photo, Promotion, RatePeriod,
                      ROLES, User, WalletEntry)
from ..services import availability as av
from ..services import bookings as svc
from ..services import automation, commission, ical, loginguard
from ..services import settings as cfg
from ..services.pricing import quote
from ..services.security import clean_phone, hash_password, verify_password
from ..services.storage import UploadError, save_image
from ..timeutil import nights_between, now, parse_date, thai_date, today
from ..web import flash, render

router = APIRouter()
STAFF = ("owner", "manager", "sales")
MGMT = ("owner", "manager")


class LoginRequired(Exception):
    pass


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    uid = request.session.get("uid")
    u = db.get(User, uid) if uid else None
    if not u or not u.active:
        raise LoginRequired()
    request.state.user = u
    request.state.unread = db.query(Notification).filter(
        Notification.user_id == u.id, Notification.read_at.is_(None)).count()
    request.state.pending = db.query(Booking).filter(Booking.status.in_(("pending", "slip_uploaded"))).count()
    return u


def need(*roles):
    def dep(request: Request, user: User = Depends(current_user)) -> User:
        if user.role not in roles:
            raise LoginRequired()
        return user
    return dep


def go(url: str):
    return RedirectResponse(url, 303)


def page(request: Request, name: str, **ctx):
    ctx.setdefault("unread", getattr(request.state, "unread", 0))
    ctx.setdefault("pending_count", getattr(request.state, "pending", 0))
    return render(request, name, lang="th", **ctx)


# ------------------------------------------------------------------ auth
@router.get("/login")
def login_page(request: Request):
    return render(request, "admin/login.html", lang="th", error=None)


@router.post("/login")
def login(request: Request, username: str = Form(""), password: str = Form(""), db: Session = Depends(get_db)):
    ip = (request.headers.get("x-forwarded-for") or (request.client.host if request.client else "")).split(",")[0].strip()
    name = username.strip().lower()
    wait = loginguard.locked_minutes(db, name, ip)
    if wait:
        return render(request, "admin/login.html", lang="th", status_code=429,
                      error=f"ใส่รหัสผิดหลายครั้ง ระบบล็อกไว้ชั่วคราว ลองใหม่ในอีก {wait} นาที")
    u = db.query(User).filter(User.username == name).first()
    if not u or not u.active or not verify_password(password, u.password_hash):
        left = loginguard.failed(db, name, ip)
        msg = "ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง"
        if left == 0:
            msg = "ใส่รหัสผิดหลายครั้ง ระบบล็อกไว้ 15 นาที"
        elif left <= 2:
            msg += f" (ลองได้อีก {left} ครั้งก่อนถูกล็อก 15 นาที)"
        return render(request, "admin/login.html", lang="th", status_code=401, error=msg)
    loginguard.succeeded(db, name, ip)
    request.session.clear()
    request.session["uid"] = u.id
    svc.audit(db, "login", ip, u)
    db.commit()
    return go("/admin/housekeeping" if u.role == "housekeeping" else "/admin")


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return go("/admin/login")


# ------------------------------------------------------------------ dashboard
@router.get("")
@router.get("/")
def dashboard(request: Request, user: User = Depends(need(*STAFF)), db: Session = Depends(get_db)):
    svc.expire_holds(db)
    t = today()
    arrivals = db.query(Booking).filter(Booking.check_in == t, Booking.status.in_(("confirmed", "checked_in", "awaiting_payment", "slip_uploaded"))).all()
    departures = db.query(Booking).filter(Booking.check_out == t, Booking.status.in_(("checked_in", "confirmed", "completed"))).all()
    inhouse = db.query(Booking).filter(Booking.status == "checked_in").count()
    todo = db.query(Booking).filter(Booking.status.in_(("pending", "slip_uploaded"))).order_by(Booking.created_at).all()
    holds = db.query(Booking).filter(Booking.status == "awaiting_payment").order_by(Booking.hold_until).all()
    m0 = t.replace(day=1)
    paid_q = db.query(Booking).filter(Booking.paid_at >= m0, Booking.status.in_(("confirmed", "checked_in", "completed")))
    month_rev = sum(float(b.total) for b in paid_q)
    houses = db.query(House).filter(House.is_active.is_(True)).order_by(House.side, House.position).all()
    tonight_taken = {r.house_id for r in db.query(Booking.house_id).filter(
        Booking.status.in_(HOLDING), Booking.check_in <= t, Booking.check_out > t)}
    blocked = {b.house_id for b in db.query(Block).filter(Block.start_date <= t, Block.end_date > t)}
    occ_nights = sum(len(set(nights_between(max(b.check_in, m0), min(b.check_out, t + timedelta(days=1)))))
                     for b in db.query(Booking).filter(Booking.status.in_(("confirmed", "checked_in", "completed")),
                                                       Booking.check_out > m0, Booking.check_in <= t))
    occ = occ_nights / max(1, len(houses) * t.day) * 100
    inquiries = db.query(Inquiry).filter(Inquiry.status == "new").count()
    return page(request, "admin/dashboard.html", checklist=automation.checklist(db, user), arrivals=arrivals, departures=departures, inhouse=inhouse,
                todo=todo, holds=holds, month_rev=month_rev, houses=houses, tonight_taken=tonight_taken,
                blocked=blocked, occ=occ, inquiries=inquiries,
                my_balance=commission.balance(db, user.id) if user.earns_commission else None)


@router.post("/automation/run")
def automation_run(request: Request, what: str = Form("all"), user: User = Depends(need(*MGMT)),
                   db: Session = Depends(get_db)):
    if what == "summary":
        text = automation.daily_summary(db, force=True)
        db.commit()
        flash(request, "ส่งสรุปงานวันนี้เข้ากลุ่ม LINE และกระดิ่งแล้ว")
        request.session["summary_text"] = text
        return go("/admin")
    svc.expire_holds(db)
    out = automation.run_all(db, PUBLIC_BASE_URL.rstrip("/"))
    done = {k: v for k, v in out.items() if v}
    flash(request, "รันระบบอัตโนมัติแล้ว" + (": " + ", ".join(f"{k} {v}" for k, v in done.items()) if done else " ไม่มีงานค้าง"))
    return go(request.headers.get("referer") or "/admin")


# ------------------------------------------------------------------ quick quote / chat reply
@router.get("/quick")
def quick_quote(request: Request, ci: str = "", co: str = "", guests: int = 2, user: User = Depends(need(*STAFF)),
                db: Session = Depends(get_db)):
    """Answer "ว่างไหม ราคาเท่าไหร่" in seconds: free houses, prices and a reply to paste in chat."""
    d1 = parse_date(ci) or today() + timedelta(days=1)
    d2 = parse_date(co) or d1 + timedelta(days=1)
    if d2 <= d1:
        d2 = d1 + timedelta(days=1)
    guests = max(1, min(guests, 60))
    base = PUBLIC_BASE_URL.rstrip("/")
    rows = []
    for h in db.query(House).filter(House.is_active.is_(True)).order_by(House.side, House.position):
        free = av.is_free(db, h.id, d1, d2)
        q = quote(db, h, d1, d2, guests)
        conflict = None if free else next(iter(av.conflicts(db, h.id, d1, d2, include_pending=False)), None)
        rows.append({"h": h, "free": free, "q": q, "fits": h.max_guests >= guests,
                     "conflict": conflict, "next": None if free else av.next_free_date(db, h.id, d1),
                     "link": automation._book_link(base, h, d1, d2, guests)})
    good = [r for r in rows if r["free"] and r["fits"] and r["q"].ok]
    n = (d2 - d1).days
    lines = [f"สวัสดีค่ะ วันที่ {thai_date(d1, year=False)} – {thai_date(d2)} ({n} คืน) {guests} ท่าน"]
    if good:
        lines.append("ว่างค่ะ มีบ้านให้เลือกดังนี้")
        for r in good:
            q = r["q"]
            extra = (f" (ลด {q.discount_total:,.0f})" if q.discount_total else "")
            lines.append(f"🏠 {r['h'].name_th} พักได้ {r['h'].max_guests} ท่าน {q.total:,.0f} บาท{extra}")
            for g in q.gifts[:2]:
                lines.append(f"   🎁 {g}")
        conf = cfg.get_all(db)
        lines.append(f"เช็คอิน {conf['checkin_time']} น. เช็คเอาท์ {conf['checkout_time']} น."
                     + (" รวมอาหารเช้า" if conf["breakfast_included"] == "1" else ""))
        lines.append(f"จองออนไลน์ได้ที่ {base}/book?ci={d1.isoformat()}&co={d2.isoformat()}&adults={guests}")
    else:
        lines.append("ขออภัยค่ะ วันดังกล่าวบ้านเต็มแล้ว")
        nxt = [r for r in rows if r["fits"] and r["next"]]
        if nxt:
            first = min(nxt, key=lambda r: r["next"])
            lines.append(f"วันที่ใกล้ที่สุดที่ว่างคือ {thai_date(first['next'])} ({first['h'].name_th})")
    return page(request, "admin/quick.html", rows=rows, ci=d1, co=d2, guests=guests, reply="\n".join(lines), nights=n)


# ------------------------------------------------------------------ calendar
@router.get("/calendar")
def calendar(request: Request, start: str | None = None, days: int = 31,
             user: User = Depends(need(*ROLES)), db: Session = Depends(get_db)):
    s = parse_date(start) or today() - timedelta(days=2)
    days = max(7, min(days, 62))
    dates = [s + timedelta(days=i) for i in range(days)]
    houses = db.query(House).order_by(House.side, House.position).all()
    grid = av.month_grid(db, s, days)
    # month picture for Facebook: the month that most of the visible range falls in
    mid = s + timedelta(days=days // 2)
    m1 = mid.replace(day=1)
    m2 = date(m1.year + m1.month // 12, m1.month % 12 + 1, 1)
    mdays = [m1 + timedelta(days=i) for i in range((m2 - m1).days)]
    mgrid = av.month_grid(db, m1, len(mdays), include_pending=False)
    conf = cfg.get_all(db)
    label = f"{['','มกราคม','กุมภาพันธ์','มีนาคม','เมษายน','พฤษภาคม','มิถุนายน','กรกฎาคม','สิงหาคม','กันยายน','ตุลาคม','พฤศจิกายน','ธันวาคม'][m1.month]} {m1.year + 543}"
    export = {"title_month": label, "data": {
        "title": f"ตารางห้องพัก เดือน {label}", "sub": conf["resort_name_en"], "phone": f"จองโทร {conf['phone_main']}",
        "file": m1.strftime("%Y-%m"), "days": [{"d": d.day, "w": d.weekday() in (4, 5)} for d in mdays],
        "houses": [{"name": h.name_th, "cells": [1 if d in mgrid.get(h.id, {}) else 0 for d in mdays]}
                   for h in houses if h.is_active]}}
    return page(request, "admin/calendar.html", dates=dates, houses=houses, grid=grid, start=s, days=days,
                prev=s - timedelta(days=days), nxt=s + timedelta(days=days), export=export)


@router.post("/blocks")
def block_create(request: Request, house_id: int = Form(...), start_date: str = Form(...), end_date: str = Form(...),
                 reason: str = Form(""), user: User = Depends(need(*MGMT)), db: Session = Depends(get_db)):
    s, e = parse_date(start_date), parse_date(end_date)
    if not s or not e or e <= s:
        flash(request, "ช่วงวันที่ไม่ถูกต้อง", "error")
        return go("/admin/calendar")
    bl = Block(house_id=house_id, start_date=s, end_date=e, reason=reason or "ปิดห้อง")
    db.add(bl)
    db.flush()
    try:
        av.lock_block(db, bl)
        svc.audit(db, "block", f"ปิดห้อง {bl.house_id} {s}–{e} {reason}", user)
        db.commit()
        flash(request, "ปิดห้องแล้ว")
    except av.ConflictError as ex:
        db.rollback()
        c = ex.conflicts[0] if ex.conflicts else None
        flash(request, f"ปิดห้องไม่ได้ ชนกับการจอง {c.code if c else ''}", "error")
    return go(f"/admin/calendar?start={s - timedelta(days=2)}")


@router.post("/blocks/{bid}/delete")
def block_delete(request: Request, bid: int, user: User = Depends(need(*MGMT)), db: Session = Depends(get_db)):
    bl = db.get(Block, bid)
    if bl:
        svc.audit(db, "unblock", f"เปิดห้อง {bl.house_id} {bl.start_date}–{bl.end_date}", user)
        db.delete(bl)
        db.commit()
        flash(request, "เปิดห้องแล้ว")
    return go("/admin/calendar")


# ------------------------------------------------------------------ bookings
@router.get("/bookings")
def bookings(request: Request, status: str = "", q: str = "", channel: str = "", agent: str = "",
             frm: str = "", to: str = "", user: User = Depends(need(*STAFF)), db: Session = Depends(get_db)):
    query = db.query(Booking).join(Customer).join(House)
    if status == "active":
        query = query.filter(Booking.status.in_(("pending", "awaiting_payment", "slip_uploaded", "confirmed", "checked_in")))
    elif status:
        query = query.filter(Booking.status == status)
    if channel:
        query = query.filter(Booking.channel == channel)
    if agent.isdigit():
        query = query.filter(Booking.agent_id == int(agent))
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(or_(Booking.code.ilike(like), Customer.name.ilike(like),
                                 Customer.phone.like(f"%{clean_phone(q) or q}%"), House.name_th.ilike(like)))
    d1, d2 = parse_date(frm), parse_date(to)
    if d1:
        query = query.filter(Booking.check_out > d1)
    if d2:
        query = query.filter(Booking.check_in <= d2)
    rows = query.order_by(Booking.check_in.desc(), Booking.id.desc()).limit(300).all()
    agents = db.query(User).filter(User.role.in_(STAFF)).order_by(User.display_name).all()
    return page(request, "admin/bookings.html", rows=rows, f=dict(status=status, q=q, channel=channel, agent=agent, frm=frm, to=to),
                agents=agents, channels=CHANNELS)


@router.get("/api/check")
def api_check(house: int, ci: str, co: str, exclude: int = 0, guests: int = 1,
              user: User = Depends(need(*STAFF)), db: Session = Depends(get_db)):
    d1, d2 = parse_date(ci), parse_date(co)
    h = db.get(House, house)
    if not h or not d1 or not d2 or d2 <= d1:
        return JSONResponse({"error": "invalid"}, 400)
    cs = av.conflicts(db, house, d1, d2, exclude or None)
    hard = [c for c in cs if c.hard]
    out = {"free": not hard, "conflicts": [
        {"kind": c.kind, "start": c.start.isoformat(), "end": c.end.isoformat(), "status": svc.STATUS_TH.get(c.status, c.status),
         "channel": c.channel, "code": c.code, "booking_id": c.booking_id, "reason": c.reason} for c in cs]}
    if hard:
        last_end = max(c.end for c in hard)
        nxt = av.next_free_date(db, house, last_end)
        out["free_from"] = nxt.isoformat() if nxt else None
        out["alternatives"] = [{"id": x.id, "name": x.name_th} for x in av.alternatives(db, d1, d2, guests, house)]
    q = quote(db, h, d1, d2, max(1, guests), 0)
    out["quote"] = {"total": q.total, "nights": q.n, "discounts": [d.name for d in q.discounts], "gifts": q.gifts,
                    "errors": q.errors}
    return out


@router.get("/bookings/new")
def booking_new(request: Request, house: int = 0, ci: str = "", co: str = "",
                user: User = Depends(need(*STAFF)), db: Session = Depends(get_db)):
    d1 = parse_date(ci) or today()
    d2 = parse_date(co) or d1 + timedelta(days=1)
    return page(request, "admin/booking_new.html", houses=db.query(House).filter(House.is_active.is_(True)).order_by(House.side, House.position).all(),
                addons=db.query(Addon).filter(Addon.active.is_(True)).all(), channels=CHANNELS,
                agents=db.query(User).filter(User.role.in_(STAFF), User.active.is_(True)).all(),
                pkg=db.query(BuyoutPackage).filter(BuyoutPackage.active.is_(True)).first(),
                f={"house_id": house, "check_in": d1, "check_out": d2, "agent_id": user.id, "channel": "facebook"}, error=None)


@router.post("/bookings/new")
async def booking_create(request: Request, user: User = Depends(need(*STAFF)), db: Session = Depends(get_db)):
    f = dict(await request.form())
    d1, d2 = parse_date(f.get("check_in")), parse_date(f.get("check_out"))
    channel = f.get("channel") or ""
    error = None
    if channel not in CHANNELS:
        error = "กรุณาเลือกช่องทางการจอง"
    elif not d1 or not d2 or d2 <= d1:
        error = "ช่วงวันที่ไม่ถูกต้อง"
    elif not f.get("name", "").strip() or not f.get("phone", "").strip():
        error = "กรุณากรอกชื่อและเบอร์โทรลูกค้า"
    agent = db.get(User, int(f.get("agent_id") or 0)) if f.get("agent_id") else None
    if channel == "website":
        agent = None
    elif user.role == "sales":
        agent = user  # sales staff always book under their own name
    status = f.get("status") or "awaiting_payment"
    addon_qty = {int(k[6:]): int(v) for k, v in f.items() if k.startswith("addon_") and str(v).isdigit() and int(v) > 0}
    override = float(f["override_total"]) if f.get("override_total", "").replace(".", "", 1).isdigit() else None
    if not error:
        try:
            if f.get("buyout") == "1":
                houses = db.query(House).filter(House.is_active.is_(True)).all()
                pkg = db.query(BuyoutPackage).filter(BuyoutPackage.active.is_(True)).first()
                nights = (d2 - d1).days
                total = override if override is not None else float(pkg.price if pkg else 0) * nights
                # split the package price across houses by their list price so reports stay per-house
                weights = {h.id: float(h.price_weekday) for h in houses}
                wsum = sum(weights.values()) or 1
                ref = "BO" + secrets.token_hex(3).upper()
                created = []
                for h in houses:
                    part = round(total * weights[h.id] / wsum, 2)
                    created.append(svc.create(db, house=h, check_in=d1, check_out=d2, adults=h.max_guests, children=0,
                                              name=f["name"], phone=f["phone"], email=f.get("email", ""), channel=channel,
                                              agent=agent, created_by=user, status=status, override_total=part,
                                              group_ref=ref, admin_note=f"เหมารีสอร์ท {ref} " + f.get("admin_note", ""),
                                              special_request=f.get("special_request", ""), base_url=PUBLIC_BASE_URL))
                flash(request, f"สร้างการจองเหมารีสอร์ท {len(created)} หลังแล้ว (กลุ่ม {ref})")
                return go(f"/admin/bookings/{created[0].id}")
            h = db.get(House, int(f.get("house_id") or 0))
            if not h:
                raise svc.BookingError("กรุณาเลือกบ้าน")
            b = svc.create(db, house=h, check_in=d1, check_out=d2, adults=int(f.get("adults") or 2),
                           children=int(f.get("children") or 0), pets=int(f.get("pets") or 0),
                           name=f["name"], phone=f["phone"], email=f.get("email", ""), line_id=f.get("line_id", ""),
                           addon_qty=addon_qty, special_request=f.get("special_request", ""), channel=channel,
                           agent=agent, created_by=user, status=status, override_total=override,
                           admin_note=f.get("admin_note", ""), base_url=PUBLIC_BASE_URL)
            flash(request, f"สร้างการจอง {b.code} แล้ว")
            return go(f"/admin/bookings/{b.id}")
        except av.ConflictError as e:
            db.rollback()
            c = e.conflicts[0] if e.conflicts else None
            error = "ห้องไม่ว่าง" + (f" ชนกับ {c.code or c.reason} ({c.start}–{c.end})" if c else "")
        except svc.BookingError as e:
            db.rollback()
            error = str(e)
    f["check_in"], f["check_out"] = d1 or today(), d2 or today() + timedelta(days=1)
    f["house_id"] = int(f.get("house_id") or 0)
    f["agent_id"] = int(f.get("agent_id") or 0)
    return page(request, "admin/booking_new.html", houses=db.query(House).filter(House.is_active.is_(True)).order_by(House.side, House.position).all(),
                addons=db.query(Addon).filter(Addon.active.is_(True)).all(), channels=CHANNELS,
                agents=db.query(User).filter(User.role.in_(STAFF), User.active.is_(True)).all(),
                pkg=db.query(BuyoutPackage).filter(BuyoutPackage.active.is_(True)).first(), f=f, error=error)


@router.get("/bookings/{bid}")
def booking_detail(request: Request, bid: int, user: User = Depends(need(*STAFF)), db: Session = Depends(get_db)):
    b = db.get(Booking, bid)
    if not b:
        return go("/admin/bookings")
    logs = db.query(AuditLog).filter(AuditLog.booking_id == b.id).order_by(AuditLog.id.desc()).all()
    group = db.query(Booking).filter(Booking.group_ref == b.group_ref, Booking.id != b.id).all() if b.group_ref else []
    history = db.query(Booking).filter(Booking.customer_id == b.customer_id, Booking.id != b.id).order_by(Booking.check_in.desc()).limit(10).all()
    conflicts = [c for c in av.conflicts(db, b.house_id, b.check_in, b.check_out, b.id) if b.status == "pending"]
    from ..models import OnlineCharge
    charges = db.query(OnlineCharge).filter(OnlineCharge.booking_id == b.id).order_by(OnlineCharge.id.desc()).all()
    return page(request, "admin/booking.html", b=b, bd=svc.breakdown(b), logs=logs, group=group, history=history,
                chat=automation.chat_message(db, b, PUBLIC_BASE_URL.rstrip("/")), charges=charges,
                conflicts=conflicts, houses=db.query(House).order_by(House.side, House.position).all(),
                agents=db.query(User).filter(User.role.in_(STAFF)).all(), channels=CHANNELS,
                alts=av.alternatives(db, b.check_in, b.check_out, b.adults + b.children, b.house_id) if conflicts else [])


@router.post("/bookings/{bid}/action")
async def booking_action(request: Request, bid: int, user: User = Depends(need(*STAFF)), db: Session = Depends(get_db)):
    f = await request.form()
    act = f.get("action", "")
    b = db.get(Booking, bid)
    if not b:
        return go("/admin/bookings")
    try:
        if act == "approve":
            svc.approve(db, b, user, PUBLIC_BASE_URL)
            msg = "ยืนยันห้องว่างแล้ว ระบบกันห้องและส่งลิงก์ชำระเงินให้ลูกค้า"
        elif act == "reject":
            svc.reject(db, b, user, f.get("reason") or "ห้องไม่ว่าง")
            msg = "ปฏิเสธคำขอแล้ว"
        elif act == "pay":
            amt = float(str(f.get("amount") or 0).replace(",", ""))
            if amt <= 0:
                raise svc.BookingError("กรุณากรอกยอดเงิน")
            svc.record_payment(db, b, amt, f.get("method") or "transfer", user, base_url=PUBLIC_BASE_URL)
            msg = f"บันทึกรับเงิน {amt:,.0f} บาท"
        elif act == "verify_slip":
            p = db.get(Payment, int(f.get("payment_id")))
            amt = float(str(f.get("amount") or p.amount).replace(",", ""))
            svc.record_payment(db, b, amt, p.method, user, payment=p, base_url=PUBLIC_BASE_URL)
            msg = "ยืนยันสลิปแล้ว"
        elif act == "reject_slip":
            p = db.get(Payment, int(f.get("payment_id")))
            svc.reject_slip(db, b, p, user, f.get("reason") or "ยอดไม่ตรง")
            msg = "ปฏิเสธสลิปแล้ว"
        elif act == "checkin":
            svc.check_in(db, b, user)
            msg = "เช็คอินแล้ว"
        elif act == "checkout":
            svc.check_out(db, b, user)
            msg = "เช็คเอาท์แล้ว บ้านรอทำความสะอาด"
        elif act in ("cancel", "no_show"):
            svc.cancel(db, b, user, f.get("reason") or "", "no_show" if act == "no_show" else "cancelled")
            msg = "บันทึกแล้ว ห้องว่างแล้ว"
        elif act == "note":
            b.admin_note = f.get("admin_note", "")
            svc.audit(db, "note", "แก้หมายเหตุ", user, b)
            msg = "บันทึกหมายเหตุแล้ว"
        elif act == "agent":
            if user.role not in MGMT:
                raise svc.BookingError("เฉพาะผู้จัดการเปลี่ยนผู้ปิดการขายได้")
            old = b.agent.display_name if b.agent else "-"
            b.agent_id = int(f.get("agent_id")) if f.get("agent_id") else None
            b.channel = f.get("channel") or b.channel
            svc.audit(db, "agent", f"ผู้ปิดการขาย {old} เป็น {f.get('agent_id')}, ช่องทาง {b.channel}", user, b)
            msg = "แก้ไขผู้ปิดการขายแล้ว"
        elif act == "move":
            h = db.get(House, int(f.get("house_id")))
            d1, d2 = parse_date(f.get("check_in")), parse_date(f.get("check_out"))
            if not h or not d1 or not d2 or d2 <= d1:
                raise svc.BookingError("ข้อมูลไม่ถูกต้อง")
            svc.change_dates(db, b, d1, d2, h, user)
            msg = "ย้ายการจองแล้ว"
        else:
            raise svc.BookingError("ไม่รู้จักคำสั่งนี้")
        db.commit()
        flash(request, msg)
    except av.ConflictError as e:
        db.rollback()
        c = e.conflicts[0] if e.conflicts else None
        nxt = av.next_free_date(db, b.house_id, c.end) if c else None
        flash(request, f"ห้องไม่ว่าง! {b.house.name_th} ถูกจองแล้ว"
              + (f" ({c.code or c.reason} ช่องทาง {c.channel}) {c.start}–{c.end}" if c else "")
              + (f" ว่างอีกครั้ง {nxt}" if nxt else ""), "error")
    except (svc.BookingError, ValueError, TypeError) as e:
        db.rollback()
        flash(request, str(e), "error")
    return go(f"/admin/bookings/{bid}")


# ------------------------------------------------------------------ housekeeping
@router.get("/housekeeping")
def housekeeping(request: Request, user: User = Depends(need(*ROLES)), db: Session = Depends(get_db)):
    t = today()
    houses = db.query(House).order_by(House.side, House.position).all()
    arr = {b.house_id: b for b in db.query(Booking).filter(Booking.check_in == t, Booking.status.in_(HOLDING))}
    dep = {b.house_id: b for b in db.query(Booking).filter(Booking.check_out == t, Booking.status.in_(("checked_in", "completed", "confirmed")))}
    stay = {b.house_id: b for b in db.query(Booking).filter(Booking.status == "checked_in")}
    tomorrow = {b.house_id: b for b in db.query(Booking).filter(Booking.check_in == t + timedelta(days=1), Booking.status.in_(HOLDING))}
    return page(request, "admin/housekeeping.html", houses=houses, arr=arr, dep=dep, stay=stay, tomorrow=tomorrow)


@router.post("/housekeeping/{hid}")
def housekeeping_set(request: Request, hid: int, state: str = Form(...), user: User = Depends(need(*ROLES)),
                     db: Session = Depends(get_db)):
    h = db.get(House, hid)
    if h and state in ("ready", "dirty", "cleaning"):
        h.housekeeping = state
        svc.audit(db, "housekeeping", f"{h.name_th} {state}", user)
        db.commit()
    return go("/admin/housekeeping")


# ------------------------------------------------------------------ houses, photos, rates
@router.get("/houses")
def houses(request: Request, user: User = Depends(need(*MGMT)), db: Session = Depends(get_db)):
    return page(request, "admin/houses.html", houses=db.query(House).order_by(House.side, House.position).all(),
                rates=db.query(RatePeriod).order_by(RatePeriod.start_date.desc()).all(), base=PUBLIC_BASE_URL)


@router.get("/houses/{hid}")
def house_edit(request: Request, hid: int, user: User = Depends(need(*MGMT)), db: Session = Depends(get_db)):
    h = db.get(House, hid)
    if not h:
        return go("/admin/houses")
    return page(request, "admin/house_edit.html", h=h, base=PUBLIC_BASE_URL)


@router.post("/houses/{hid}")
async def house_save(request: Request, hid: int, user: User = Depends(need(*MGMT)), db: Session = Depends(get_db)):
    h = db.get(House, hid)
    f = await request.form()
    for k in ("name_th", "name_en", "beds_th", "beds_en", "description_th", "description_en", "kind", "side", "ical_import_url"):
        if k in f:
            setattr(h, k, str(f[k]).strip())
    for k in ("position", "std_guests", "max_guests", "bedrooms", "bathrooms"):
        if f.get(k, "").isdigit():
            setattr(h, k, int(f[k]))
    for k in ("price_weekday", "price_weekend", "extra_guest_fee"):
        try:
            setattr(h, k, float(str(f.get(k, "")).replace(",", "")))
        except ValueError:
            pass
    h.amenities = ",".join(f.getlist("amenities"))
    h.is_active = f.get("is_active") == "1"
    svc.audit(db, "house", f"แก้ข้อมูลบ้าน {h.name_th} ราคา {h.price_weekday}/{h.price_weekend}", user)
    db.commit()
    flash(request, "บันทึกข้อมูลบ้านแล้ว")
    return go(f"/admin/houses/{hid}")


@router.post("/houses/{hid}/photos")
async def house_photo_add(request: Request, hid: int, photo: UploadFile = File(...), user: User = Depends(need(*MGMT)),
                          db: Session = Depends(get_db)):
    h = db.get(House, hid)
    try:
        path = await save_image(photo, "photos")
        h.photos.append(Photo(path=path, sort=len(h.photos)))
        db.commit()
        flash(request, "เพิ่มรูปแล้ว")
    except UploadError as e:
        flash(request, str(e), "error")
    return go(f"/admin/houses/{hid}#photos")


@router.post("/photos/{pid}/{op}")
def photo_op(request: Request, pid: int, op: str, user: User = Depends(need(*MGMT)), db: Session = Depends(get_db)):
    p = db.get(Photo, pid)
    if not p:
        return go("/admin/houses")
    hid = p.house_id
    if op == "delete":
        db.delete(p)
    elif op == "cover" and hid:
        for other in db.get(House, hid).photos:
            other.sort = other.sort + 1
        p.sort = 0
    db.commit()
    return go(f"/admin/houses/{hid}#photos" if hid else "/admin/houses")


@router.post("/houses/{hid}/ical")
def house_ical(request: Request, hid: int, user: User = Depends(need(*MGMT)), db: Session = Depends(get_db)):
    h = db.get(House, hid)
    try:
        r = ical.sync(db, h)
        flash(request, f"ดึงปฏิทินแล้ว เพิ่ม {r['added']} ลบ {r['removed']} ชน {r['conflicts']}")
    except Exception as e:  # noqa: BLE001
        flash(request, f"ดึงปฏิทินไม่สำเร็จ: {e}", "error")
    return go(f"/admin/houses/{hid}")


@router.post("/rates")
def rate_add(request: Request, name: str = Form(...), start_date: str = Form(...), end_date: str = Form(...),
             house_kind: str = Form(""), price: str = Form(""), adjust_amount: str = Form(""), is_holiday: str = Form(""),
             user: User = Depends(need(*MGMT)), db: Session = Depends(get_db)):
    s, e = parse_date(start_date), parse_date(end_date)
    if not s or not e or e < s:
        flash(request, "ช่วงวันที่ไม่ถูกต้อง", "error")
        return go("/admin/houses#rates")
    db.add(RatePeriod(name=name, start_date=s, end_date=e, house_kind=house_kind,
                      price=float(price) if price.strip() else None,
                      adjust_amount=float(adjust_amount) if adjust_amount.strip() else None,
                      is_holiday=is_holiday == "1"))
    svc.audit(db, "rate", f"เพิ่มช่วงราคา {name} {s}–{e}", user)
    db.commit()
    flash(request, "เพิ่มช่วงราคาแล้ว")
    return go("/admin/houses#rates")


@router.post("/rates/{rid}/delete")
def rate_delete(request: Request, rid: int, user: User = Depends(need(*MGMT)), db: Session = Depends(get_db)):
    r = db.get(RatePeriod, rid)
    if r:
        db.delete(r)
        db.commit()
    return go("/admin/houses#rates")


# ------------------------------------------------------------------ promotions, add-ons, buyout
@router.get("/promotions")
def promotions(request: Request, user: User = Depends(need(*MGMT)), db: Session = Depends(get_db)):
    return page(request, "admin/promotions.html", promos=db.query(Promotion).order_by(Promotion.sort).all(),
                addons=db.query(Addon).order_by(Addon.sort).all(),
                pkg=db.query(BuyoutPackage).first())


@router.post("/promotions/{pid}")
async def promotion_save(request: Request, pid: int, user: User = Depends(need(*MGMT)), db: Session = Depends(get_db)):
    f = await request.form()
    p = db.get(Promotion, pid) if pid else None
    if f.get("delete") == "1" and p:
        db.delete(p)
        db.commit()
        flash(request, "ลบโปรโมชั่นแล้ว")
        return go("/admin/promotions")
    if not p:
        p = Promotion(kind=f.get("kind") or "gift", name_th="")
        db.add(p)
    for k in ("name_th", "name_en", "detail_th", "detail_en", "gift_th", "gift_en", "kind", "window_on"):
        if k in f:
            setattr(p, k, str(f[k]).strip())
    for k in ("amount", "percent"):
        try:
            setattr(p, k, float(f.get(k) or 0))
        except ValueError:
            pass
    for k in ("nth", "min_nights", "sort"):
        if str(f.get(k, "")).isdigit():
            setattr(p, k, int(f[k]))
    p.weekdays = ",".join(f.getlist("weekdays")) or p.weekdays
    p.house_kinds = ",".join(f.getlist("house_kinds"))
    p.start_date, p.end_date = parse_date(f.get("start_date")), parse_date(f.get("end_date"))
    p.active = f.get("active") == "1"
    p.stackable = f.get("stackable") == "1"
    p.same_channel = f.get("same_channel") == "1"
    p.show_on_site = f.get("show_on_site") == "1"
    svc.audit(db, "promotion", f"บันทึกโปร {p.name_th} ({'เปิด' if p.active else 'ปิด'})", user)
    db.commit()
    flash(request, "บันทึกโปรโมชั่นแล้ว")
    return go("/admin/promotions")


@router.post("/addons/{aid}")
async def addon_save(request: Request, aid: int, user: User = Depends(need(*MGMT)), db: Session = Depends(get_db)):
    f = await request.form()
    a = db.get(Addon, aid) if aid else None
    if f.get("delete") == "1" and a:
        a.active = False
        db.commit()
        return go("/admin/promotions#addons")
    if not a:
        a = Addon(name_th="")
        db.add(a)
    a.name_th, a.name_en = f.get("name_th", "").strip(), f.get("name_en", "").strip()
    a.price = float(f.get("price") or 0)
    a.unit = f.get("unit") or "stay"
    a.active = f.get("active") == "1"
    db.commit()
    flash(request, "บันทึกบริการเสริมแล้ว")
    return go("/admin/promotions#addons")


@router.post("/buyout")
async def buyout_save(request: Request, user: User = Depends(need(*MGMT)), db: Session = Depends(get_db)):
    f = await request.form()
    p = db.query(BuyoutPackage).first() or BuyoutPackage(name_th="")
    db.add(p)
    for k in ("name_th", "name_en", "includes_th", "includes_en", "note_th", "note_en"):
        setattr(p, k, str(f.get(k, "")).strip())
    p.guests = int(f.get("guests") or 40)
    p.price = float(f.get("price") or 0)
    p.active = f.get("active") == "1"
    svc.audit(db, "buyout", f"แพ็กเกจเหมา {p.price} / {p.guests} ท่าน", user)
    db.commit()
    flash(request, "บันทึกแพ็กเกจเหมาแล้ว")
    return go("/admin/promotions#buyout")


# ------------------------------------------------------------------ commission & wallet
@router.get("/commission")
def commission_rules(request: Request, user: User = Depends(need(*MGMT)), db: Session = Depends(get_db)):
    rules = {r.channel: r for r in db.query(CommissionRule)}
    return page(request, "admin/commission.html", rules=rules, channels=CHANNELS)


@router.post("/commission")
async def commission_save(request: Request, user: User = Depends(need("owner")), db: Session = Depends(get_db)):
    f = await request.form()
    for ch in CHANNELS:
        r = db.query(CommissionRule).filter(CommissionRule.channel == ch).first() or CommissionRule(channel=ch)
        db.add(r)
        r.mode = f.get(f"{ch}_mode") or "none"
        for k in ("percent", "fixed_big", "fixed_small", "platform_fee_percent"):
            try:
                setattr(r, k, float(f.get(f"{ch}_{k}") or 0))
            except ValueError:
                pass
        r.to_pool = f.get(f"{ch}_to_pool") == "1"
    svc.audit(db, "commission_rules", "แก้กฎค่าคอม", user)
    db.commit()
    flash(request, "บันทึกกฎค่าคอมมิชชั่นแล้ว มีผลกับการจองที่ชำระหลังจากนี้")
    return go("/admin/commission")


@router.get("/wallet")
def wallet(request: Request, uid: int = 0, user: User = Depends(need(*STAFF)), db: Session = Depends(get_db)):
    see_all = user.role in MGMT
    target = db.get(User, uid) if (uid and see_all) else user
    entries = db.query(WalletEntry).filter(WalletEntry.user_id == target.id).order_by(WalletEntry.id.desc()).limit(200).all()
    payouts = db.query(Payout).filter(Payout.user_id == target.id).order_by(Payout.id.desc()).all()
    team = []
    if see_all:
        for u in db.query(User).filter(User.earns_commission.is_(True)).order_by(User.display_name):
            m0 = today().replace(day=1)
            month = db.query(func.coalesce(func.sum(WalletEntry.amount), 0)).filter(
                WalletEntry.user_id == u.id, WalletEntry.created_at >= m0).scalar()
            team.append({"u": u, "unpaid": commission.balance(db, u.id), "month": float(month or 0),
                         "cases": db.query(Booking).filter(Booking.agent_id == u.id, Booking.paid_at >= m0).count()})
    return page(request, "admin/wallet.html", target=target, entries=entries, payouts=payouts, team=team,
                unpaid=commission.balance(db, target.id), lifetime=commission.balance(db, target.id, False),
                see_all=see_all)


@router.post("/payouts")
def payout_create(request: Request, user_id: int = Form(...), note: str = Form(""), user: User = Depends(need(*MGMT)),
                  db: Session = Depends(get_db)):
    target = db.get(User, user_id)
    p = commission.create_payout(db, target, user, note)
    if p:
        svc.audit(db, "payout", f"สรุปยอดจ่าย {target.display_name} {float(p.amount):,.0f} บาท", user)
        db.commit()
        flash(request, f"สรุปยอดจ่าย {float(p.amount):,.0f} บาท แล้ว รอกด 'จ่ายแล้ว'")
    else:
        flash(request, "ไม่มียอดค้างจ่าย", "error")
    return go(f"/admin/wallet?uid={user_id}")


@router.post("/payouts/{pid}/paid")
def payout_paid(request: Request, pid: int, user: User = Depends(need("owner")), db: Session = Depends(get_db)):
    p = db.get(Payout, pid)
    if p and p.status != "paid":
        p.status, p.paid_at, p.paid_by_id = "paid", now(), user.id
        svc.audit(db, "payout_paid", f"จ่ายค่าคอม {p.user.display_name} {float(p.amount):,.0f} บาท", user)
        from ..services import notify
        notify.line_user(db, p.user, f"✅ รีสอร์ทโอนค่าคอมให้แล้ว {float(p.amount):,.0f} บาท")
        notify.bell(db, f"ได้รับค่าคอม {float(p.amount):,.0f} บาท", "สถานะ: จ่ายแล้ว", "/admin/wallet", "money", user=p.user)
        db.commit()
        flash(request, "บันทึกว่าจ่ายแล้ว")
    return go(f"/admin/wallet?uid={p.user_id if p else 0}")


# ------------------------------------------------------------------ reports
def _report_rows(db: Session, d1: date, d2: date):
    return db.query(Booking).filter(Booking.status.in_(("confirmed", "checked_in", "completed")),
                                    Booking.check_in >= d1, Booking.check_in <= d2).order_by(Booking.check_in).all()


@router.get("/reports")
def reports(request: Request, frm: str = "", to: str = "", user: User = Depends(need(*MGMT)), db: Session = Depends(get_db)):
    t = today()
    d1 = parse_date(frm) or t.replace(day=1)
    d2 = parse_date(to) or (date(d1.year + d1.month // 12, d1.month % 12 + 1, 1) - timedelta(days=1))
    rows = _report_rows(db, d1, d2)
    by_ch = defaultdict(lambda: {"n": 0, "total": 0.0})
    by_house = defaultdict(lambda: {"n": 0, "nights": 0, "total": 0.0})
    by_agent = defaultdict(lambda: {"n": 0, "total": 0.0, "comm": 0.0})
    by_month = defaultdict(float)
    for b in rows:
        by_ch[b.channel]["n"] += 1
        by_ch[b.channel]["total"] += float(b.total)
        by_house[b.house.name_th]["n"] += 1
        by_house[b.house.name_th]["nights"] += b.nights
        by_house[b.house.name_th]["total"] += float(b.total)
        key = b.agent.display_name if b.agent else "เว็บไซต์ / กองกลาง"
        by_agent[key]["n"] += 1
        by_agent[key]["total"] += float(b.total)
        by_agent[key]["comm"] += float(b.commission_amount)
    # 12-month revenue trend by check-in month
    start12 = date(t.year - 1, t.month, 1)
    for b in _report_rows(db, start12, t + timedelta(days=365)):
        by_month[b.check_in.strftime("%Y-%m")] += float(b.total)
    total = sum(float(b.total) for b in rows)
    comm = sum(float(b.commission_amount) for b in rows)
    fee = sum(float(b.platform_fee) for b in rows)
    house_count = db.query(House).filter(House.is_active.is_(True)).count()
    nights_sold = sum(b.nights for b in rows)
    occ = nights_sold / max(1, house_count * ((d2 - d1).days + 1)) * 100
    addons = sum(float(b.addons_total) for b in rows)
    cancelled = db.query(Booking).filter(Booking.status.in_(("cancelled", "no_show", "expired")),
                                         Booking.check_in >= d1, Booking.check_in <= d2).count()
    return page(request, "admin/reports.html", d1=d1, d2=d2, rows=rows, by_ch=dict(by_ch), by_house=dict(by_house),
                by_agent=dict(by_agent), by_month=dict(sorted(by_month.items())), total=total, comm=comm, fee=fee,
                resort=total - comm - fee, occ=occ, nights_sold=nights_sold, addons=addons, cancelled=cancelled)


@router.get("/reports.csv")
def reports_csv(frm: str = "", to: str = "", user: User = Depends(need(*MGMT)), db: Session = Depends(get_db)):
    d1 = parse_date(frm) or today().replace(day=1)
    d2 = parse_date(to) or today()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["เลขจอง", "บ้าน", "เช็คอิน", "เช็คเอาท์", "คืน", "ลูกค้า", "โทร", "ช่องทาง", "ผู้ปิดการขาย",
                "ยอดรวม", "ส่วนลด", "บริการเสริม", "ค่าธรรมเนียมแพลตฟอร์ม", "ค่าคอม", "รายได้รีสอร์ท", "สถานะ"])
    for b in _report_rows(db, d1, d2):
        w.writerow([b.code, b.house.name_th, b.check_in, b.check_out, b.nights, b.customer.name, b.customer.phone,
                    b.channel, b.agent.display_name if b.agent else "", float(b.total), float(b.discount_total),
                    float(b.addons_total), float(b.platform_fee), float(b.commission_amount), float(b.resort_revenue),
                    svc.STATUS_TH[b.status]])
    return Response("﻿" + buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f"attachment; filename=golden-beach-{d1}-{d2}.csv"})


# ------------------------------------------------------------------ customers, inquiries, notifications, audit
@router.get("/customers")
def customers(request: Request, q: str = "", user: User = Depends(need(*STAFF)), db: Session = Depends(get_db)):
    query = db.query(Customer)
    if q:
        query = query.filter(or_(Customer.name.ilike(f"%{q}%"), Customer.phone.like(f"%{clean_phone(q) or q}%")))
    rows = query.order_by(Customer.id.desc()).limit(200).all()
    stats = {c.id: {"n": sum(1 for b in c.bookings if b.status == "completed"),
                    "spent": sum(float(b.total) for b in c.bookings if b.status in ("confirmed", "checked_in", "completed"))}
             for c in rows}
    return page(request, "admin/customers.html", rows=rows, stats=stats, q=q)


@router.get("/inquiries")
def inquiries(request: Request, user: User = Depends(need(*STAFF)), db: Session = Depends(get_db)):
    return page(request, "admin/inquiries.html", rows=db.query(Inquiry).order_by(Inquiry.status.desc(), Inquiry.id.desc()).limit(200).all())


@router.post("/inquiries/{iid}/done")
def inquiry_done(request: Request, iid: int, user: User = Depends(need(*STAFF)), db: Session = Depends(get_db)):
    i = db.get(Inquiry, iid)
    if i:
        i.status, i.handled_by_id = "done", user.id
        db.commit()
    return go("/admin/inquiries")


@router.get("/notifications")
def notifications(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.query(Notification).filter(Notification.user_id == user.id).order_by(Notification.id.desc()).limit(100).all()
    return page(request, "admin/notifications.html", rows=rows)


@router.get("/api/notifications")
def api_notifications(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.query(Notification).filter(Notification.user_id == user.id, Notification.read_at.is_(None)).order_by(Notification.id.desc()).limit(8).all()
    return {"count": len(rows), "items": [{"id": n.id, "title": n.title, "body": n.body, "link": n.link, "kind": n.kind} for n in rows]}


@router.post("/notifications/read")
def notifications_read(request: Request, nid: int = Form(0), user: User = Depends(current_user), db: Session = Depends(get_db)):
    q = db.query(Notification).filter(Notification.user_id == user.id, Notification.read_at.is_(None))
    if nid:
        q = q.filter(Notification.id == nid)
    for n in q:
        n.read_at = now()
    db.commit()
    return go(request.headers.get("referer") or "/admin/notifications")


@router.get("/audit")
def audit_log(request: Request, user: User = Depends(need(*MGMT)), db: Session = Depends(get_db)):
    return page(request, "admin/audit.html", rows=db.query(AuditLog).order_by(AuditLog.id.desc()).limit(300).all())


# ------------------------------------------------------------------ users & settings
@router.get("/users")
def users(request: Request, user: User = Depends(need("owner")), db: Session = Depends(get_db)):
    return page(request, "admin/users.html", rows=db.query(User).order_by(User.id).all(), roles=ROLES)


@router.post("/users/{uid}")
async def user_save(request: Request, uid: int, user: User = Depends(need("owner")), db: Session = Depends(get_db)):
    f = await request.form()
    u = db.get(User, uid) if uid else None
    if not u:
        uname = f.get("username", "").strip().lower()
        if not uname or db.query(User).filter(User.username == uname).first():
            flash(request, "ชื่อผู้ใช้ซ้ำหรือว่าง", "error")
            return go("/admin/users")
        if len(f.get("password", "")) < 8:
            flash(request, "รหัสผ่านต้องยาวอย่างน้อย 8 ตัวอักษร", "error")
            return go("/admin/users")
        u = User(username=uname, password_hash="")
        db.add(u)
    u.display_name = f.get("display_name", "").strip() or u.username
    u.role = f.get("role") if f.get("role") in ROLES else "sales"
    u.phone = f.get("phone", "")
    u.line_user_id = f.get("line_user_id", "").strip()
    u.earns_commission = f.get("earns_commission") == "1"
    u.active = f.get("active") == "1" or u.id == user.id
    if f.get("password"):
        if len(f["password"]) < 8:
            flash(request, "รหัสผ่านต้องยาวอย่างน้อย 8 ตัวอักษร", "error")
            return go("/admin/users")
        u.password_hash = hash_password(f["password"])
    svc.audit(db, "user", f"บันทึกผู้ใช้ {u.username} ({u.role})", user)
    db.commit()
    flash(request, "บันทึกผู้ใช้แล้ว")
    return go("/admin/users")


@router.get("/settings")
def settings_page(request: Request, user: User = Depends(need(*MGMT)), db: Session = Depends(get_db)):
    from ..models import AutomationLog
    recent = db.query(AutomationLog).order_by(AutomationLog.id.desc()).limit(15).all()
    return page(request, "admin/settings.html", s=cfg.get_all(db), base_url=PUBLIC_BASE_URL.rstrip("/"), recent=recent)


@router.post("/settings")
async def settings_save(request: Request, user: User = Depends(need(*MGMT)), db: Session = Depends(get_db)):
    f = await request.form()
    vals = {k: str(v) for k, v in f.items()}
    for chk in ("breakfast_included", "pets_allowed", "online_payment", "opn_cards",
                "auto_reject_overlaps", "auto_payment_reminder", "auto_prearrival", "auto_checkout",
                "auto_review_request", "auto_inquiry_reply", "auto_daily_summary", "auto_nudge", "auto_payout"):
        vals[chk] = "1" if f.get(chk) == "1" else "0"
    cfg.set_many(db, vals)
    svc.audit(db, "settings", "แก้ตั้งค่าระบบ", user)
    db.commit()
    flash(request, "บันทึกการตั้งค่าแล้ว")
    return go("/admin/settings")


@router.get("/backup.zip")
def backup_download(user: User = Depends(need("owner")), db: Session = Depends(get_db)):
    """Everything (bookings, customers, photos, slips, settings) in one file."""
    from ..backup import dump
    svc.audit(db, "backup", "ดาวน์โหลดไฟล์สำรองข้อมูล", user)
    db.commit()
    name = f"golden-beach-backup-{today().isoformat()}.zip"
    return Response(dump(), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})


@router.post("/me/password")
def change_password(request: Request, old: str = Form(""), new: str = Form(""), user: User = Depends(current_user),
                    db: Session = Depends(get_db)):
    if not verify_password(old, user.password_hash) or len(new) < 8:
        flash(request, "รหัสผ่านเดิมไม่ถูกต้อง หรือรหัสใหม่สั้นกว่า 8 ตัวอักษร", "error")
    else:
        user.password_hash = hash_password(new)
        db.commit()
        flash(request, "เปลี่ยนรหัสผ่านแล้ว")
    return go(request.headers.get("referer") or "/admin")
