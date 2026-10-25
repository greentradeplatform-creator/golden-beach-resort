"""Timed and event-driven helpers that take routine work off the admins.

Every timed job is safe to run every few minutes: what has been done is written to
AutomationLog under a unique key, so a reminder or a summary is never sent twice.
Each job can be switched off in admin > Settings > ระบบอัตโนมัติ.
"""
from __future__ import annotations

import hashlib
import io
import logging
from datetime import date, datetime, time, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import (AutomationLog, Block, Booking, HOLDING, House, Inquiry, Payment, SlipFingerprint, User)
from ..timeutil import now, thai_date, today
from . import availability as av
from . import commission, notify
from . import settings as cfg

log = logging.getLogger("automation")


# ---------------------------------------------------------------- helpers
def once(db: Session, key: str) -> bool:
    """True the first time a key is seen (and records it), False afterwards."""
    if db.query(AutomationLog.id).filter(AutomationLog.key == key).first():
        return False
    try:
        with db.begin_nested():
            db.add(AutomationLog(key=key[:120]))
    except IntegrityError:
        return False
    return True


def _clock(value: str, default: str) -> time:
    try:
        h, m = (value or default).split(":")[:2]
        return time(int(h), int(m))
    except (ValueError, TypeError):
        h, m = default.split(":")
        return time(int(h), int(m))


def _money(x) -> str:
    return f"{float(x or 0):,.0f}"


def _book_link(base_url: str, h: House, ci: date, co: date, guests: int) -> str:
    return f"{base_url}/book?house={h.slug}&ci={ci.isoformat()}&co={co.isoformat()}&adults={guests}"


def offer_lines(db: Session, ci: date, co: date, guests: int, base_url: str, exclude: int | None = None,
                limit: int = 4) -> list[str]:
    """Free houses for the same dates with their price, for e-mails and chat replies."""
    from .pricing import quote
    lines = []
    for h in av.alternatives(db, ci, co, guests, exclude)[:limit]:
        q = quote(db, h, ci, co, guests)
        if q.ok:
            lines.append(f"• {h.name_th} ({h.max_guests} ท่าน) {_money(q.total)} บาท\n  จองได้ที่ {_book_link(base_url, h, ci, co, guests)}")
    return lines


# ---------------------------------------------------------------- event driven
def reject_overlapping(db: Session, winner: Booking, base_url: str = "") -> list[Booking]:
    """Once a house is held for `winner`, other requests for the same nights can never be
    approved. Turn them down at once and offer the guest the houses still free."""
    if not cfg.on(db, "auto_reject_overlaps"):
        return []
    losers = db.query(Booking).filter(
        Booking.house_id == winner.house_id, Booking.id != winner.id, Booking.status == "pending",
        Booking.check_in < winner.check_out, Booking.check_out > winner.check_in).all()
    for b in losers:
        b.status = "rejected"
        b.cancel_reason = f"บ้านถูกจองแล้ว (เลขจอง {winner.code}) ระบบปฏิเสธอัตโนมัติ"
        db.add(_audit(b, "auto_reject", b.cancel_reason))
        offers = offer_lines(db, b.check_in, b.check_out, b.adults + b.children, base_url, b.house_id)
        nxt = av.next_free_date(db, b.house_id, b.check_in)
        body = (f"เรียนคุณ{b.customer.name}\n\nขออภัย {b.house.name_th} วันที่ {thai_date(b.check_in)} – "
                f"{thai_date(b.check_out)} มีผู้จองและชำระเงินก่อนแล้ว\n\n")
        if offers:
            body += "บ้านที่ยังว่างในวันเดียวกัน:\n" + "\n".join(offers) + "\n\n"
        if nxt:
            body += f"{b.house.name_th} ว่างอีกครั้งตั้งแต่ {thai_date(nxt)}\n\n"
        body += f"สอบถามโทร {cfg.get(db, 'phone_main')}\n\n{cfg.get(db, 'resort_name_th')}"
        notify.email(db, b.customer.email, f"{b.house.name_th} ไม่ว่างแล้ว ({b.code}) มีบ้านหลังอื่นให้เลือก", body)
        notify.bell(db, f"ปฏิเสธอัตโนมัติ {b.code}", f"ชนกับ {winner.code} {'เสนอบ้านอื่นแล้ว' if offers else 'ไม่มีบ้านอื่นว่าง'}"
                    f" โทรหา {b.customer.name} {b.customer.phone}", f"/admin/bookings/{b.id}", "expired")
    return losers


def _audit(b: Booking, action: str, detail: str):
    from ..models import AuditLog
    return AuditLog(booking_id=b.id, action=action, detail=detail)


def inquiry_reply(db: Session, iq: Inquiry, base_url: str = "") -> str:
    """Instant answer to a contact / buyout form. Returns the text (also e-mailed)."""
    name = cfg.get(db, "resort_name_th")
    lines = [f"เรียนคุณ{iq.name}", "", f"{name} ได้รับข้อความของคุณแล้ว เจ้าหน้าที่จะติดต่อกลับโดยเร็ว"]
    if iq.date_from and iq.kind in ("buyout", "event"):
        d2 = iq.date_from + timedelta(days=max(1, iq.nights or 1))
        houses = db.query(House).filter(House.is_active.is_(True)).all()
        free = [h for h in houses if av.is_free(db, h.id, iq.date_from, d2)]
        lines.append("")
        if len(free) == len(houses):
            lines.append(f"ข่าวดี: วันที่ {thai_date(iq.date_from)} – {thai_date(d2)} ยังว่างทั้งรีสอร์ท ({len(houses)} หลัง)")
            from ..models import BuyoutPackage
            pk = db.query(BuyoutPackage).filter(BuyoutPackage.active.is_(True)).order_by(BuyoutPackage.id).first()
            if pk:
                lines.append(f"แพ็กเกจเหมา {pk.name_th}: {_money(pk.price)} บาท/คืน")
        elif free:
            lines.append(f"วันที่ {thai_date(iq.date_from)} – {thai_date(d2)} ยังว่าง {len(free)} จาก {len(houses)} หลัง "
                         "เจ้าหน้าที่จะเสนอทางเลือกให้")
        else:
            lines.append(f"วันที่ {thai_date(iq.date_from)} – {thai_date(d2)} มีผู้จองแล้ว เจ้าหน้าที่จะเสนอวันอื่นให้")
    lines += ["", f"โทร {cfg.get(db, 'phone_main')}", name]
    text = "\n".join(lines)
    if iq.email and cfg.on(db, "auto_inquiry_reply"):
        notify.email(db, iq.email, f"{name} ได้รับข้อความของคุณแล้ว", text)
    return text


# ---------------------------------------------------------------- duplicate slips
def fingerprint(data: bytes) -> str:
    """Fingerprint of the picture itself (not the file), so the same slip saved again
    or sent through LINE still matches."""
    try:
        from PIL import Image, ImageOps
        im = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("L").resize((48, 96))
        px = im.tobytes()                       # one byte per pixel in mode "L"
        avg = sum(px) / len(px)
        bits = "".join("1" if p > avg else "0" for p in px)
        return hashlib.sha256(bits.encode()).hexdigest()
    except Exception:  # noqa: BLE001
        return hashlib.sha256(data).hexdigest()


def check_slip(db: Session, b: Booking, payment: Payment, fp: str) -> list[Booking]:
    """Record the slip's fingerprint; warn the team if it was already used."""
    if not fp:
        return []
    dups = (db.query(Booking).join(SlipFingerprint, SlipFingerprint.booking_id == Booking.id)
            .filter(SlipFingerprint.fingerprint == fp, SlipFingerprint.payment_id != payment.id).all())
    db.add(SlipFingerprint(payment_id=payment.id, booking_id=b.id, fingerprint=fp))
    if dups:
        codes = ", ".join(sorted({d.code for d in dups}))
        payment.note = (payment.note + "\n" if payment.note else "") + f"⚠️ สลิปนี้เคยถูกส่งมาแล้วใน {codes}"
        notify.bell(db, f"⚠️ สลิปซ้ำ {b.code}", f"เหมือนสลิปของ {codes} ตรวจยอดเงินเข้าก่อนยืนยัน",
                    f"/admin/bookings/{b.id}", "expired")
        notify.line_group(db, f"⚠️ ระวังสลิปซ้ำ\nเลขจอง {b.code} ส่งสลิปเดียวกับ {codes}\nกรุณาเช็คเงินเข้าบัญชีก่อนยืนยัน")
    return dups


# ---------------------------------------------------------------- timed jobs
def payment_reminders(db: Session, base_url: str) -> int:
    n = 0
    t = now()
    for b in db.query(Booking).filter(Booking.status == "awaiting_payment", Booking.hold_until.is_not(None),
                                      Booking.hold_until > t):
        left = b.hold_until - t
        total = b.hold_until - (b.approved_at or b.created_at)
        if left > max(timedelta(hours=1), min(timedelta(hours=6), total / 3)):
            continue
        if not once(db, f"pay-remind:{b.id}"):
            continue
        due = float(b.deposit_due) - float(b.amount_paid)
        notify.email(db, b.customer.email, f"เตือนชำระเงิน {b.code} ก่อน {b.hold_until:%H:%M} น.",
                     f"เรียนคุณ{b.customer.name}\n\nรีสอร์ทกัน{b.house.name_th}ไว้ให้ถึง {thai_date(b.hold_until)} "
                     f"เวลา {b.hold_until:%H:%M} น.\nยอดที่ต้องชำระ {due:,.0f} บาท\n"
                     f"ชำระและแนบสลิปได้ที่ {base_url}/booking/{b.code}\n\nหากเลยเวลา ระบบจะปล่อยห้องให้ผู้อื่นอัตโนมัติ")
        msg = (f"⏰ ใกล้หมดเวลาชำระ {b.code}\n{b.house.name_th} {thai_date(b.check_in)}\n"
               f"👤 {b.customer.name} โทร {b.customer.phone}\nหมดเวลา {b.hold_until:%d/%m %H:%M} น.")
        if b.agent:
            notify.line_user(db, b.agent, msg)
        notify.bell(db, f"ใกล้หมดเวลาชำระ {b.code}", f"{b.customer.name} โทร {b.customer.phone}",
                    f"/admin/bookings/{b.id}", "new", user=b.agent)
        n += 1
    return n


def stale_nudges(db: Session) -> int:
    mins = max(5, int(cfg.get(db, "nudge_minutes") or 30))
    t = now()
    n = 0
    rows = db.query(Booking).filter(Booking.status.in_(("pending", "slip_uploaded"))).all()
    for b in rows:
        since = b.updated_at or b.created_at
        if b.status == "slip_uploaded" and b.payments:
            since = max(p.created_at for p in b.payments)
        waited = t - since
        if waited < timedelta(minutes=mins):
            continue
        level = 2 if waited >= timedelta(minutes=mins * 4) else 1
        if not once(db, f"nudge:{b.id}:{b.status}:{level}"):
            continue
        what = "คำขอจองยังไม่มีใครตอบ" if b.status == "pending" else "สลิปยังไม่มีใครตรวจ"
        notify.line_group(db, f"{'🔴' if level == 2 else '🟠'} {what} ({int(waited.total_seconds() // 60)} นาที)\n"
                              + notify.booking_summary(b))
        notify.bell(db, f"{what} {b.code}", f"รอมา {int(waited.total_seconds() // 60)} นาที", f"/admin/bookings/{b.id}", "new")
        n += 1
    return n


def prearrival(db: Session, base_url: str) -> int:
    n = 0
    tomorrow = today() + timedelta(days=1)
    conf = cfg.get_all(db)
    for b in db.query(Booking).filter(Booking.status == "confirmed", Booking.check_in.in_((today(), tomorrow))):
        if not b.customer.email or not once(db, f"prearrival:{b.id}"):
            continue
        maps = f"https://www.google.com/maps/search/?api=1&query={conf['lat']},{conf['lng']}"
        bal = b.balance
        body = (f"เรียนคุณ{b.customer.name}\n\nรีสอร์ทพร้อมต้อนรับคุณ{' พรุ่งนี้' if b.check_in == tomorrow else 'วันนี้'}\n\n"
                f"🏠 {b.house.name_th}\n📅 เช็คอิน {thai_date(b.check_in)} ตั้งแต่ {conf['checkin_time']} น.\n"
                f"📅 เช็คเอาท์ {thai_date(b.check_out)} ก่อน {conf['checkout_time']} น.\n"
                + (f"💰 ยอดคงเหลือชำระที่รีสอร์ท {bal:,.0f} บาท\n" if bal > 0.5 else "")
                + f"\n📍 วิธีเดินทาง: {conf['directions_th']}\nแผนที่: {maps}\n\n"
                f"กฎของบ้าน:\n{conf['house_rules_th']}\n\nโทร {conf['phone_main']}\n"
                f"รายละเอียดการจอง: {base_url}/booking/{b.code}\n\n{conf['resort_name_th']}")
        notify.email(db, b.customer.email, f"พรุ่งนี้พบกันที่{conf['resort_name_th']} ({b.code})", body)
        n += 1
    return n


def auto_checkout(db: Session) -> int:
    t = now()
    cut = datetime.combine(today(), _clock(cfg.get(db, "checkout_time"), "12:00")) + timedelta(hours=1)
    n = 0
    last = today() if t >= cut else today() - timedelta(days=1)
    for b in db.query(Booking).filter(Booking.status == "checked_in", Booking.check_out <= last):
        b.status = "completed"
        b.house.housekeeping = "dirty"
        db.add(_audit(b, "check_out", "เช็คเอาท์อัตโนมัติ บ้านรอทำความสะอาด"))
        notify.bell(db, f"{b.house.name_th} รอทำความสะอาด", f"เช็คเอาท์อัตโนมัติ {b.code}", "/admin/housekeeping",
                    "info", roles=("owner", "manager", "housekeeping"))
        n += 1
    return n


def review_requests(db: Session) -> int:
    n = 0
    url = cfg.get(db, "google_review_url")
    name = cfg.get(db, "resort_name_th")
    t = now()
    cut = _clock(cfg.get(db, "checkout_time"), "12:00")
    for b in db.query(Booking).filter(Booking.status == "completed", Booking.check_out >= today() - timedelta(days=2),
                                      Booking.check_out <= today()):
        if b.check_out == today() and t.time() < cut:
            continue
        if not b.customer.email or not once(db, f"review:{b.id}"):
            continue
        body = (f"เรียนคุณ{b.customer.name}\n\nขอบคุณที่เลือกพักกับ{name} หวังว่าจะได้ต้อนรับอีกครั้ง\n"
                + (f"\nถ้ามีเวลา ช่วยรีวิวให้เราสักนิดนะคะ: {url}\n" if url else "")
                + f"\nจองครั้งหน้าโทร {cfg.get(db, 'phone_main')} หรือที่เว็บไซต์ได้เลย\n\n{name}")
        notify.email(db, b.customer.email, f"ขอบคุณที่มาพักกับ{name}", body)
        n += 1
    return n


def daily_summary(db: Session, force: bool = False) -> str | None:
    t = today()
    if not force and now().time() < _clock(cfg.get(db, "daily_summary_time"), "07:30"):
        return None
    if not force and not once(db, f"daily:{t.isoformat()}"):
        return None
    houses = db.query(House).filter(House.is_active.is_(True)).order_by(House.side, House.position).all()
    arrivals = db.query(Booking).filter(Booking.check_in == t, Booking.status.in_(HOLDING)).all()
    departures = db.query(Booking).filter(Booking.check_out == t, Booking.status.in_(("checked_in", "confirmed"))).all()
    taken = {r.house_id for r in db.query(Booking.house_id).filter(
        Booking.status.in_(HOLDING), Booking.check_in <= t, Booking.check_out > t)}
    taken |= {r.house_id for r in db.query(Block.house_id).filter(Block.start_date <= t, Block.end_date > t)}
    free = [h for h in houses if h.id not in taken]
    pending = db.query(Booking).filter(Booking.status == "pending").count()
    slips = db.query(Booking).filter(Booking.status == "slip_uploaded").count()
    holds = db.query(Booking).filter(Booking.status == "awaiting_payment").count()
    dirty = [h for h in houses if h.housekeeping != "ready"]
    inquiries = db.query(Inquiry).filter(Inquiry.status == "new").count()
    lines = [f"☀️ สรุปงานวันนี้ {thai_date(t)}"]
    lines.append(f"🛬 เข้าพัก {len(arrivals)} หลัง" + (": " + ", ".join(
        f"{b.house.name_th} ({b.customer.name}{', ค้าง ' + _money(b.balance) if b.balance > 0.5 else ''})" for b in arrivals)
        if arrivals else ""))
    lines.append(f"🛫 เช็คเอาท์ {len(departures)} หลัง" + (": " + ", ".join(b.house.name_th for b in departures) if departures else ""))
    lines.append(f"🧹 รอทำความสะอาด {len(dirty)} หลัง" + (": " + ", ".join(h.name_th for h in dirty) if dirty else ""))
    lines.append(f"🏠 ว่างคืนนี้ {len(free)}/{len(houses)} หลัง" + (": " + ", ".join(h.name_th for h in free) if free else " (เต็ม)"))
    todo = []
    if pending:
        todo.append(f"คำขอรออนุมัติ {pending}")
    if slips:
        todo.append(f"สลิปรอตรวจ {slips}")
    if holds:
        todo.append(f"รอลูกค้าชำระ {holds}")
    if inquiries:
        todo.append(f"ข้อความใหม่ {inquiries}")
    lines.append("📌 งานค้าง: " + (", ".join(todo) if todo else "ไม่มี 👍"))
    text = "\n".join(lines)
    notify.line_group(db, text)
    notify.bell(db, f"สรุปงานวันนี้ {thai_date(t)}", " | ".join(lines[1:]), "/admin", "info",
                roles=("owner", "manager", "sales", "housekeeping"))
    return text


def monthly_payouts(db: Session) -> int:
    t = today()
    if t.day != 1:
        return 0
    n = 0
    owner = db.query(User).filter(User.role == "owner", User.active.is_(True)).first()
    for u in db.query(User).filter(User.earns_commission.is_(True), User.active.is_(True)):
        if commission.balance(db, u.id) <= 0 or not once(db, f"payout:{t:%Y-%m}:{u.id}"):
            continue
        p = commission.create_payout(db, u, owner, f"สรุปอัตโนมัติ รอบ {thai_date(t - timedelta(days=1), short=False)}")
        if p:
            notify.bell(db, f"สรุปค่าคอมรอจ่าย {u.display_name} {_money(p.amount)} บาท", "กด 'จ่ายแล้ว' หลังโอน",
                        f"/admin/wallet?uid={u.id}", "money", roles=("owner",))
            notify.line_user(db, u, f"📋 สรุปค่าคอมรอบเดือนนี้ {_money(p.amount)} บาท รอรีสอร์ทโอน")
            n += 1
    return n


def expiry_notice(db: Session, b: Booking, base_url: str):
    """Tell the guest their hold ran out, with a link to book again if the house is still free."""
    free = av.is_free(db, b.house_id, b.check_in, b.check_out)
    notify.email(db, b.customer.email, f"หมดเวลาชำระเงิน {b.code}",
                 f"เรียนคุณ{b.customer.name}\n\nเลยเวลาชำระเงินแล้ว ระบบจึงยกเลิกการกัน{b.house.name_th}\n"
                 + (f"ตอนนี้บ้านยังว่างอยู่ จองใหม่ได้ที่ {_book_link(base_url, b.house, b.check_in, b.check_out, b.adults)}\n"
                    if free else "")
                 + f"\nสอบถามโทร {cfg.get(db, 'phone_main')}")


JOBS = ("payment_reminders", "stale_nudges", "prearrival", "auto_checkout", "review_requests",
        "daily_summary", "monthly_payouts")


def run_all(db: Session, base_url: str = "") -> dict:
    out: dict = {}
    plan = [
        ("payment_reminders", "auto_payment_reminder", lambda: payment_reminders(db, base_url)),
        ("stale_nudges", "auto_nudge", lambda: stale_nudges(db)),
        ("prearrival", "auto_prearrival", lambda: prearrival(db, base_url)),
        ("auto_checkout", "auto_checkout", lambda: auto_checkout(db)),
        ("review_requests", "auto_review_request", lambda: review_requests(db)),
        ("daily_summary", "auto_daily_summary", lambda: bool(daily_summary(db))),
        ("monthly_payouts", "auto_payout", lambda: monthly_payouts(db)),
    ]
    for name, switch, fn in plan:
        if not cfg.on(db, switch):
            continue
        try:
            out[name] = fn()
            db.commit()
        except Exception as e:  # noqa: BLE001 - one broken job must not stop the others
            db.rollback()
            log.warning("automation %s failed: %s", name, e)
            out[name] = f"error: {e}"
    return out


# ---------------------------------------------------------------- chat messages
def chat_message(db: Session, b: Booking, base_url: str) -> str:
    """A ready reply for guests who booked through Facebook / LINE / phone and have no e-mail."""
    c = cfg.get_all(db)
    head = (f"คุณ{b.customer.name} 🙏\n🏠 {b.house.name_th}\n📅 {thai_date(b.check_in)} – {thai_date(b.check_out)} "
            f"({b.nights} คืน) {b.adults + b.children} ท่าน\n🔖 เลขจอง {b.code}")
    link = f"{base_url}/booking/{b.code}"
    due = float(b.deposit_due) - float(b.amount_paid)
    if b.status in ("awaiting_payment", "slip_uploaded"):
        lines = ["บ้านว่างค่ะ รีสอร์ทกันไว้ให้แล้ว", head, f"💰 ยอดชำระ {due:,.0f} บาท"]
        if c["promptpay_id"]:
            lines.append(f"สแกน QR พร้อมเพย์ได้ที่ {link}")
        lines.append(f"หรือโอน {c['bank_name']} {c['bank_account_no']}\n{c['bank_account_name']}")
        if b.hold_until:
            lines.append(f"⏰ กรุณาชำระภายใน {thai_date(b.hold_until)} {b.hold_until:%H:%M} น. แล้วส่งสลิปในแชทนี้หรือแนบที่ลิงก์ด้านบน")
        lines.append("(เปิดลิงก์ด้วยเลขจองและเบอร์โทรที่ให้ไว้)")
    elif b.status in ("confirmed", "checked_in"):
        lines = ["ได้รับชำระเงินแล้ว การจองสำเร็จค่ะ ✅", head,
                 f"เช็คอิน {c['checkin_time']} น. เช็คเอาท์ {c['checkout_time']} น."]
        if b.balance > 0.5:
            lines.append(f"ยอดคงเหลือชำระที่รีสอร์ท {b.balance:,.0f} บาท")
        lines.append(f"📍 {c['directions_th']}\nแผนที่ https://www.google.com/maps/search/?api=1&query={c['lat']},{c['lng']}")
    elif b.status == "pending":
        lines = ["ได้รับคำขอจองแล้วค่ะ กำลังตรวจสอบห้องว่างให้", head, f"💰 ราคารวม {float(b.total):,.0f} บาท"]
    else:
        return ""
    lines.append(f"สอบถามโทร {c['phone_main']}")
    return "\n".join(lines)


# ---------------------------------------------------------------- admin checklist
def checklist(db: Session, user: User | None = None) -> list[dict]:
    """Things that need a human today, most urgent first. Shown on the dashboard."""
    from .security import verify_password
    t, n = today(), now()
    items: list[dict] = []

    def add(level, text, link, hint=""):
        items.append({"level": level, "text": text, "link": link, "hint": hint})

    for b in db.query(Booking).filter(Booking.status == "slip_uploaded").order_by(Booking.updated_at):
        dup = any("สลิปนี้เคยถูกส่ง" in (p.note or "") for p in b.payments if p.status == "pending")
        add("red" if dup else "amber", f"{'⚠️ สลิปซ้ำ ' if dup else ''}ตรวจสลิป {b.code} {b.customer.name}",
            f"/admin/bookings/{b.id}", f"{b.house.name_th} ยอด {_money(b.deposit_due)} บาท")
    for b in db.query(Booking).filter(Booking.status == "pending").order_by(Booking.created_at):
        add("amber", f"ตอบคำขอจอง {b.code} {b.customer.name}", f"/admin/bookings/{b.id}",
            f"{b.house.name_th} {thai_date(b.check_in, year=False)}–{thai_date(b.check_out, year=False)}")
    for b in db.query(Booking).filter(Booking.status == "confirmed", Booking.check_in <= t):
        late = b.check_in < t
        add("red" if late else "blue", f"{'ยังไม่ได้เช็คอิน (เลยวันแล้ว)' if late else 'รอเช็คอินวันนี้'} {b.house.name_th}",
            f"/admin/bookings/{b.id}", f"{b.customer.name} {b.customer.phone}"
            + (f" ค้างชำระ {_money(b.balance)} บาท" if b.balance > 0.5 else ""))
    co_cut = datetime.combine(t, _clock(cfg.get(db, "checkout_time"), "12:00"))
    for b in db.query(Booking).filter(Booking.status == "checked_in", Booking.check_out <= t):
        if b.check_out < t or n >= co_cut:
            add("amber", f"เลยเวลาเช็คเอาท์ {b.house.name_th}", f"/admin/bookings/{b.id}", b.customer.name)
    arriving = {b.house_id for b in db.query(Booking).filter(Booking.check_in == t, Booking.status.in_(HOLDING))}
    for h in db.query(House).filter(House.housekeeping != "ready"):
        add("red" if h.id in arriving else "grey", f"{h.name_th} ยังไม่พร้อม ({'ทำความสะอาดอยู่' if h.housekeeping == 'cleaning' else 'รอทำความสะอาด'})",
            "/admin/housekeeping", "มีแขกเข้าวันนี้" if h.id in arriving else "")
    soon = n + timedelta(hours=3)
    for b in db.query(Booking).filter(Booking.status == "awaiting_payment", Booking.hold_until <= soon):
        add("grey", f"ใกล้หมดเวลาชำระ {b.code}", f"/admin/bookings/{b.id}",
            f"{b.customer.name} {b.customer.phone} ถึง {b.hold_until:%H:%M} น.")
    k = db.query(Inquiry).filter(Inquiry.status == "new").count()
    if k:
        add("blue", f"ข้อความ/ขอใบเสนอราคาใหม่ {k} รายการ", "/admin/inquiries")

    if user and user.role in ("owner", "manager"):
        conf = cfg.get_all(db)
        if user.role == "owner" and verify_password("golden1234", user.password_hash):
            add("red", "ยังใช้รหัสผ่านเริ่มต้นอยู่ เปลี่ยนก่อนเปิดใช้งานจริง", "/admin/settings#password")
        if not conf["promptpay_id"]:
            add("grey", "ยังไม่ได้ใส่เลขพร้อมเพย์ (หน้าชำระเงินจะไม่มี QR)", "/admin/settings")
        if not (conf["line_channel_token"] and conf["line_group_id"]):
            add("grey", "ยังไม่ได้ตั้ง LINE แจ้งเตือนกลุ่มแอดมิน", "/admin/settings")
        if not notify.email_ready(conf):
            add("grey", "ยังไม่ได้ตั้งระบบส่งอีเมล (Brevo หรือ SMTP) ระบบส่งอีเมลหาลูกค้าไม่ได้", "/admin/settings")
    order = {"red": 0, "amber": 1, "blue": 2, "grey": 3}
    items.sort(key=lambda i: order[i["level"]])
    return items
