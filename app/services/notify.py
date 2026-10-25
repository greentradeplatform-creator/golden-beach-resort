"""Admin bell, LINE Messaging API and e-mail.

LINE Notify was shut down on 31 Mar 2025, so group and personal alerts go
through the resort's LINE Official Account (Messaging API push).
Without a token configured, messages are written to the log instead.
"""
from __future__ import annotations

import json
import logging
import smtplib
import threading
import urllib.request
from email.message import EmailMessage

from sqlalchemy.orm import Session

from ..models import Booking, Notification, User
from ..timeutil import thai_date
from . import settings as cfg

log = logging.getLogger("notify")

CHANNEL_TH = {"website": "เว็บไซต์", "facebook": "Facebook", "line": "LINE OA",
              "airbnb": "Airbnb", "phone": "โทรศัพท์", "other": "อื่นๆ"}


def _bg(fn, *args):
    threading.Thread(target=fn, args=args, daemon=True).start()


def _line_push(token: str, to: str, text: str):
    req = urllib.request.Request(
        "https://api.line.me/v2/bot/message/push",
        data=json.dumps({"to": to, "messages": [{"type": "text", "text": text[:4900]}]}).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=10).read()
    except Exception as e:  # noqa: BLE001 - never break a booking because LINE failed
        log.warning("LINE push failed: %s", e)


def _send_mail(conf: dict, to: str, subject: str, body: str):
    try:
        msg = EmailMessage()
        msg["From"] = conf["smtp_from"] or conf["smtp_user"]
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(body)
        with smtplib.SMTP(conf["smtp_host"], int(conf["smtp_port"] or 587), timeout=15) as s:
            s.starttls()
            if conf["smtp_user"]:
                s.login(conf["smtp_user"], conf["smtp_password"])
            s.send_message(msg)
    except Exception as e:  # noqa: BLE001
        log.warning("email failed: %s", e)


def _send_brevo(conf: dict, to: str, subject: str, body: str):
    """E-mail over HTTPS (Brevo). Needed on hosts that block SMTP ports, e.g. Render free."""
    import html
    sender = conf["smtp_from"] or conf["email"]
    name, addr = (sender.split("<")[0].strip(' "'), sender.split("<")[1].rstrip("> ")) if "<" in sender else \
        (conf["resort_name_th"], sender)
    payload = {"sender": {"email": addr, "name": name or conf["resort_name_th"]}, "to": [{"email": to}],
               "subject": subject, "textContent": body,
               "htmlContent": "<div style='font-family:sans-serif;line-height:1.6'>" + html.escape(body).replace("\n", "<br>") + "</div>"}
    req = urllib.request.Request("https://api.brevo.com/v3/smtp/email", data=json.dumps(payload).encode(),
                                 headers={"api-key": conf["brevo_api_key"], "Content-Type": "application/json",
                                          "Accept": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=15).read()
    except Exception as e:  # noqa: BLE001
        log.warning("Brevo email failed: %s", e)


def email_ready(conf: dict) -> bool:
    return bool((conf.get("brevo_api_key") and (conf.get("smtp_from") or conf.get("email"))) or conf.get("smtp_host"))


def line_group(db: Session, text: str):
    conf = cfg.get_all(db)
    if conf["line_channel_token"] and conf["line_group_id"]:
        _bg(_line_push, conf["line_channel_token"], conf["line_group_id"], text)
    else:
        log.info("[LINE group] %s", text)


def line_user(db: Session, user: User | None, text: str):
    conf = cfg.get_all(db)
    if user and conf["line_channel_token"] and user.line_user_id:
        _bg(_line_push, conf["line_channel_token"], user.line_user_id, text)
    else:
        log.info("[LINE %s] %s", user.display_name if user else "-", text)


def email(db: Session, to: str, subject: str, body: str):
    conf = cfg.get_all(db)
    if to and conf["brevo_api_key"] and (conf["smtp_from"] or conf["email"]):
        _bg(_send_brevo, conf, to, subject, body)
    elif to and conf["smtp_host"]:
        _bg(_send_mail, conf, to, subject, body)
    else:
        log.info("[email %s] %s", to or "-", subject)


def bell(db: Session, title: str, body: str = "", link: str = "", kind: str = "info",
         user: User | None = None, roles=("owner", "manager", "sales")):
    users = [user] if user else db.query(User).filter(User.active.is_(True), User.role.in_(roles)).all()
    for u in users:
        db.add(Notification(user_id=u.id, title=title, body=body, link=link, kind=kind))


def booking_summary(b: Booking) -> str:
    who = b.agent.display_name if b.agent else "ระบบเว็บไซต์"
    return (f"🏠 {b.house.name_th}\n"
            f"📅 {thai_date(b.check_in)} – {thai_date(b.check_out)} ({b.nights} คืน)\n"
            f"👤 {b.customer.name} โทร {b.customer.phone}\n"
            f"🌐 {CHANNEL_TH.get(b.channel, b.channel)} | ผู้ดูแล {who}\n"
            f"💰 {float(b.total):,.0f} บาท | เลขจอง {b.code}")


def on_new_request(db: Session, b: Booking):
    bell(db, f"คำขอจองใหม่ {b.house.name_th}", f"{b.customer.name} {thai_date(b.check_in)}",
         f"/admin/bookings/{b.id}", "new")
    line_group(db, "🔔 มีคำขอจองใหม่\n" + booking_summary(b) + "\nสถานะ: รออนุมัติ")
    notify_to = cfg.get(db, "notify_email")
    if notify_to:
        email(db, notify_to, f"คำขอจองใหม่ {b.code}", booking_summary(b))


def on_slip(db: Session, b: Booking):
    bell(db, f"ลูกค้าแนบสลิป {b.code}", f"{b.house.name_th} {b.customer.name}",
         f"/admin/bookings/{b.id}", "slip")
    line_group(db, "🧾 ลูกค้าแนบสลิปแล้ว รอตรวจสอบ\n" + booking_summary(b))


def on_approved(db: Session, b: Booking, base_url: str):
    link = f"{base_url}/booking/{b.code}"
    email(db, b.customer.email, f"ยืนยันห้องว่าง {b.code} กรุณาชำระเงิน",
          f"เรียนคุณ{b.customer.name}\n\n{b.house.name_th} ว่างสำหรับวันที่ "
          f"{thai_date(b.check_in)} – {thai_date(b.check_out)}\nยอดชำระ {float(b.deposit_due):,.0f} บาท\n"
          f"ชำระและแนบสลิปได้ที่ {link}\n\nโกลเด้น บีช รีสอร์ท")


def on_confirmed(db: Session, b: Booking, commission_entry, wallet_total: float, base_url: str):
    line_group(db, "✅ ชำระเงินแล้ว จองสำเร็จ\n" + booking_summary(b))
    bell(db, f"ยืนยันการจอง {b.code}", f"{b.house.name_th} ชำระแล้ว", f"/admin/bookings/{b.id}", "paid")
    email(db, b.customer.email, f"ยืนยันการจอง {b.code}",
          f"เรียนคุณ{b.customer.name}\n\nการจองของคุณสำเร็จแล้ว\n{booking_summary(b)}\n"
          f"รายละเอียด: {base_url}/booking/{b.code}\n\nโกลเด้น บีช รีสอร์ท")
    if commission_entry and b.agent:
        text = (f"💰 ยินดีด้วย! ค่าคอมเข้ากระเป๋าแล้ว\n📌 {b.house.name_th} "
                f"({thai_date(b.check_in, year=False)}–{thai_date(b.check_out, year=False)})\n"
                f"🌐 {CHANNEL_TH.get(b.channel, b.channel)}\n💵 ยอดจอง {float(b.total):,.0f} บาท\n"
                f"🏆 ค่าคอมของคุณ +{float(commission_entry.amount):,.0f} บาท\n"
                f"💼 ยอดสะสมรอบนี้ {wallet_total:,.0f} บาท")
        line_user(db, b.agent, text)
        bell(db, f"ค่าคอม +{float(commission_entry.amount):,.0f} บาท", f"{b.code} {b.house.name_th}",
             "/admin/wallet", "money", user=b.agent)
