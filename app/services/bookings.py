"""Booking lifecycle. Every status change goes through here so locks,
commission, notifications and the audit log stay consistent."""
from __future__ import annotations

import json
from datetime import date, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models import (AuditLog, Booking, BookingAddon, Customer, HOLDING, House, Payment, User)
from ..timeutil import now, today
from . import availability as av
from . import automation, commission, notify
from . import settings as cfg
from .pricing import Quote, quote
from .security import clean_phone


class BookingError(Exception):
    pass


STATUS_TH = {
    "pending": "รออนุมัติ", "awaiting_payment": "รอชำระเงิน", "slip_uploaded": "รอตรวจสลิป",
    "confirmed": "ยืนยันแล้ว", "checked_in": "กำลังเข้าพัก", "completed": "เช็คเอาท์แล้ว",
    "cancelled": "ยกเลิก", "rejected": "ปฏิเสธ", "expired": "หมดเวลาชำระ", "no_show": "ไม่มาเข้าพัก",
}
STATUS_EN = {
    "pending": "Waiting for approval", "awaiting_payment": "Awaiting payment",
    "slip_uploaded": "Checking your slip", "confirmed": "Confirmed", "checked_in": "Checked in",
    "completed": "Completed", "cancelled": "Cancelled", "rejected": "Not available",
    "expired": "Payment time ran out", "no_show": "No-show",
}


def audit(db: Session, action: str, detail: str = "", user: User | None = None, booking: Booking | None = None):
    db.add(AuditLog(user_id=user.id if user else None, booking_id=booking.id if booking else None,
                    action=action, detail=detail))


def new_code(db: Session) -> str:
    t = today()
    prefix = f"GB{t.strftime('%y%m')}"
    last = db.query(Booking.code).filter(Booking.code.like(f"{prefix}-%")).order_by(Booking.code.desc()).first()
    n = int(last[0].split("-")[1]) + 1 if last else 1
    return f"{prefix}-{n:04d}"


def upsert_customer(db: Session, name: str, phone: str, email: str = "", line_id: str = "") -> Customer:
    phone = clean_phone(phone)
    c = db.query(Customer).filter(Customer.phone == phone).first()
    if c:
        c.name = name or c.name
        c.email = email or c.email
        c.line_id = line_id or c.line_id
    else:
        c = Customer(name=name, phone=phone, email=email, line_id=line_id)
        db.add(c)
        db.flush()
    return c


def _apply_quote(b: Booking, q: Quote, db: Session):
    b.nights = q.n
    b.room_total = q.room_total
    b.extra_guest_total = q.extra_guest_total
    b.addons_total = q.addons_total
    b.discount_total = q.discount_total
    b.total = q.total
    b.deposit_due = q.deposit_due
    b.breakdown_json = json.dumps(q.to_json(), ensure_ascii=False)
    b.addons.clear()
    for l in q.addons:
        b.addons.append(BookingAddon(addon_id=l.ref, name=l.name, qty=l.qty, unit_price=l.unit_price, amount=l.amount))


def breakdown(b: Booking) -> dict:
    try:
        return json.loads(b.breakdown_json or "{}")
    except ValueError:
        return {}


def _base() -> str:
    from ..config import PUBLIC_BASE_URL
    return PUBLIC_BASE_URL.rstrip("/")


def hold_deadline(db: Session):
    return now() + timedelta(hours=int(cfg.get(db, "hold_hours") or 24))


def create(db: Session, *, house: House, check_in: date, check_out: date, adults: int, children: int,
           name: str, phone: str, email: str = "", line_id: str = "", pets: int = 0,
           addon_qty: dict[int, int] | None = None, special_request: str = "", channel: str = "website",
           agent: User | None = None, created_by: User | None = None, status: str = "pending",
           override_total: float | None = None, group_ref: str = "", admin_note: str = "",
           base_url: str = "", lang: str = "th") -> Booking:
    if not clean_phone(phone) or len(clean_phone(phone)) < 9:
        raise BookingError("กรุณากรอกเบอร์โทรให้ถูกต้อง" if lang == "th" else "Please enter a valid phone number")
    if check_in < today() and created_by is None:
        raise BookingError("วันเช็คอินต้องไม่ใช่วันที่ผ่านมาแล้ว" if lang == "th" else "Check-in can't be in the past")
    q = quote(db, house, check_in, check_out, adults, children, addon_qty, clean_phone(phone), channel, lang=lang)
    if not q.ok:
        raise BookingError(q.errors[0])
    # A website request is refused straight away when the house is already held.
    hard = [c for c in av.conflicts(db, house.id, check_in, check_out, include_pending=False) if c.hard]
    if hard:
        raise av.ConflictError(hard)

    cust = upsert_customer(db, name.strip(), phone, email.strip(), line_id.strip())
    b = Booking(code=new_code(db), house_id=house.id, customer_id=cust.id, check_in=check_in,
                check_out=check_out, adults=adults, children=children, pets=pets, channel=channel,
                agent_id=agent.id if agent else None, created_by_id=created_by.id if created_by else None,
                special_request=special_request.strip(), status="pending", group_ref=group_ref,
                admin_note=admin_note)
    db.add(b)
    db.flush()
    _apply_quote(b, q, db)
    if override_total is not None:
        b.discount_total = max(0.0, float(b.total) - override_total + float(b.discount_total))
        b.total = override_total
        pct = float(cfg.get(db, "deposit_percent") or 100)
        b.deposit_due = round(override_total * pct / 100)
    db.refresh(b, ["house", "customer", "agent"])
    audit(db, "create", f"สร้างการจอง ช่องทาง {channel}", created_by, b)

    if status in ("awaiting_payment", "confirmed"):
        approve(db, b, created_by, base_url, quiet=True)
        if status == "confirmed":
            record_payment(db, b, float(b.total), "transfer", created_by, verified=True, base_url=base_url)
    elif created_by is None:
        notify.on_new_request(db, b)
    db.commit()
    return b


def approve(db: Session, b: Booking, user: User | None, base_url: str = "", quiet: bool = False):
    if b.status not in ("pending",):
        raise BookingError(f"อนุมัติไม่ได้ สถานะปัจจุบันคือ {STATUS_TH[b.status]}")
    av.lock_booking(db, b)
    b.status = "awaiting_payment"
    b.approved_at = now()
    b.hold_until = hold_deadline(db)
    if user and not b.agent_id and b.channel != "website":
        b.agent_id = user.id
    audit(db, "approve", "ยืนยันห้องว่าง รอชำระเงิน", user, b)
    automation.reject_overlapping(db, b, base_url or _base())
    if not quiet:
        notify.on_approved(db, b, base_url)


def reject(db: Session, b: Booking, user: User, reason: str):
    if b.status != "pending":
        raise BookingError("ปฏิเสธได้เฉพาะคำขอที่รออนุมัติ")
    b.status = "rejected"
    b.cancel_reason = reason
    audit(db, "reject", reason, user, b)


def upload_slip(db: Session, b: Booking, path: str, amount: float, fingerprint: str = "") -> Payment:
    if b.status not in ("awaiting_payment", "slip_uploaded", "confirmed"):
        raise BookingError("ยังแนบสลิปไม่ได้ในสถานะนี้")
    p = Payment(booking_id=b.id, amount=amount, method="promptpay", slip_path=path, status="pending")
    db.add(p)
    db.flush()
    if b.status == "awaiting_payment":
        b.status = "slip_uploaded"
    audit(db, "slip", f"ลูกค้าแนบสลิป {amount:,.0f} บาท", None, b)
    automation.check_slip(db, b, p, fingerprint)
    notify.on_slip(db, b)
    return p


def record_payment(db: Session, b: Booking, amount: float, method: str, user: User | None,
                   verified: bool = True, payment: Payment | None = None, base_url: str = ""):
    if payment is None:
        payment = Payment(booking_id=b.id, amount=amount, method=method)
        db.add(payment)
    payment.amount = amount
    payment.status = "verified" if verified else "pending"
    payment.verified_by_id = user.id if user else None
    payment.verified_at = now()
    db.flush()
    paid = float(db.query(func.coalesce(func.sum(Payment.amount), 0)).filter(
        Payment.booking_id == b.id, Payment.status == "verified").scalar() or 0)
    db.expire(b, ["payments"])
    b.amount_paid = paid
    audit(db, "payment", f"รับชำระ {amount:,.0f} บาท ({method})", user, b)
    if b.status in ("awaiting_payment", "slip_uploaded") and paid >= float(b.deposit_due) - 0.5:
        confirm(db, b, user, base_url)


def reject_slip(db: Session, b: Booking, payment: Payment, user: User, note: str):
    payment.status = "rejected"
    payment.note = note
    if b.status == "slip_uploaded":
        b.status = "awaiting_payment"
        b.hold_until = max(b.hold_until or now(), now() + timedelta(hours=6))
    audit(db, "slip_rejected", note, user, b)


def confirm(db: Session, b: Booking, user: User | None, base_url: str = ""):
    av.lock_booking(db, b)
    b.status = "confirmed"
    b.paid_at = now()
    b.hold_until = None
    db.flush()
    db.refresh(b, ["agent", "house"])
    entry = commission.post_commission(db, b)
    db.flush()
    total = commission.balance(db, b.agent_id) if b.agent_id else 0
    audit(db, "confirm", f"ชำระครบ ยืนยันการจอง รายได้รีสอร์ท {float(b.resort_revenue):,.0f} "
                         f"ค่าคอม {float(b.commission_amount):,.0f}", user, b)
    notify.on_confirmed(db, b, entry, total, base_url)


def check_in(db: Session, b: Booking, user: User):
    if b.status != "confirmed":
        raise BookingError("เช็คอินได้เฉพาะการจองที่ยืนยันแล้ว")
    b.status = "checked_in"
    audit(db, "check_in", "", user, b)


def check_out(db: Session, b: Booking, user: User):
    if b.status not in ("checked_in", "confirmed"):
        raise BookingError("เช็คเอาท์ได้เฉพาะการจองที่เข้าพักอยู่")
    b.status = "completed"
    b.house.housekeeping = "dirty"
    audit(db, "check_out", "บ้านรอทำความสะอาด", user, b)


def cancel(db: Session, b: Booking, user: User | None, reason: str, status: str = "cancelled"):
    if b.status in ("completed", "cancelled", "rejected", "expired", "no_show"):
        raise BookingError("รายการนี้ปิดไปแล้ว")
    was_paid = b.status in ("confirmed", "checked_in")
    av.release_booking(db, b.id)
    b.status = status
    b.cancel_reason = reason
    if was_paid:
        commission.reverse_commission(db, b, reason)
    audit(db, status, reason, user, b)


def change_dates(db: Session, b: Booking, check_in: date, check_out: date, house: House, user: User):
    """Move a booking (dates and/or house) keeping the agreed total unless the admin re-quotes."""
    held = b.status in HOLDING
    if held:
        av.release_booking(db, b.id)
    old = f"{b.house.name_th} {b.check_in}→{b.check_out}"
    b.house_id, b.check_in, b.check_out = house.id, check_in, check_out
    b.nights = (check_out - check_in).days
    db.flush()
    db.refresh(b, ["house"])
    if held:
        av.lock_booking(db, b)
    audit(db, "move", f"{old} เป็น {house.name_th} {check_in}→{check_out}", user, b)


def expire_holds(db: Session) -> int:
    n = 0
    for b in db.query(Booking).filter(Booking.status == "awaiting_payment",
                                      Booking.hold_until.is_not(None), Booking.hold_until < now()).all():
        av.release_booking(db, b.id)
        b.status = "expired"
        audit(db, "expired", "หมดเวลาชำระเงิน ปล่อยห้องอัตโนมัติ", None, b)
        notify.bell(db, f"หมดเวลาชำระ {b.code}", f"{b.house.name_th} ถูกปล่อยว่างแล้ว",
                    f"/admin/bookings/{b.id}", "expired")
        db.flush()
        automation.expiry_notice(db, b, _base())
        n += 1
    if n:
        db.commit()
    return n
