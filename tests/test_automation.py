"""Automations, login lock, quick quote, duplicate slips and the Opn payment gateway."""
import io
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import AutomationLog, Booking, House, OnlineCharge, Payout, User, WalletEntry
from app.seed import run as seed
from app.services import automation, notify, opn
from app.services import bookings as svc
from app.services import settings as cfg
from app.timeutil import now, today


@pytest.fixture()
def db():
    Base.metadata.drop_all(engine)
    seed(demo=True)
    s = SessionLocal()
    yield s
    s.close()


@pytest.fixture()
def mails(monkeypatch):
    sent = []
    monkeypatch.setattr(notify, "email", lambda db, to, subject, body: sent.append((to, subject, body)) if to else None)
    return sent


@pytest.fixture()
def lines(monkeypatch):
    sent = []
    monkeypatch.setattr(notify, "line_group", lambda db, text: sent.append(text))
    return sent


def client(user=None, password="golden1234"):
    c = TestClient(app, follow_redirects=False)
    if user:
        r = c.post("/admin/login", data={"username": user, "password": password})
        assert r.status_code == 303, r.text
    return c


def slip_png(seed_text="slip") -> bytes:
    im = Image.new("RGB", (300, 600), "white")
    d = ImageDraw.Draw(im)
    d.rectangle((20, 20, 280, 120), fill="#2C6E80")
    d.text((40, 200), seed_text * 3, fill="black")
    d.rectangle((40, 400, 40 + len(seed_text) * 20, 460), fill="black")
    b = io.BytesIO()
    im.save(b, "PNG")
    return b.getvalue()


def make(db, slug="mali", days=30, nights=2, status="pending", email="guest@example.com", phone="0812223333"):
    h = db.query(House).filter_by(slug=slug).one()
    ci = today() + timedelta(days=days)
    owner = db.query(User).filter_by(username="owner").one()
    b = svc.create(db, house=h, check_in=ci, check_out=ci + timedelta(days=nights), adults=2, children=0,
                   name="คุณทดสอบ", phone=phone, email=email, channel="website",
                   created_by=None if status == "pending" else owner, status=status)
    db.commit()
    return b


# ------------------------------------------------------------------ booking flow automations
def test_auto_reject_offers_other_houses(db, mails):
    a = make(db, "bua", 50, phone="0811111111")
    b = make(db, "bua", 50, phone="0822222222", email="second@example.com")
    svc.approve(db, a, None, "https://gbr.test")
    db.commit()
    assert b.status == "rejected"
    to, subject, body = next(m for m in mails if m[0] == "second@example.com")
    assert "ไม่ว่างแล้ว" in subject and "บ้านที่ยังว่างในวันเดียวกัน" in body and "https://gbr.test/book?house=" in body


def test_payment_reminder_once(db, mails):
    b = make(db, "daoruang", 20, status="awaiting_payment")
    assert automation.payment_reminders(db, "") == 0          # plenty of time left
    b.hold_until = now() + timedelta(minutes=50)
    db.commit()
    assert automation.payment_reminders(db, "") == 1
    assert automation.payment_reminders(db, "") == 0          # never twice
    assert any("เตือนชำระเงิน" in m[1] for m in mails)


def test_expired_hold_tells_the_guest(db, mails):
    b = make(db, "daoruang", 20, status="awaiting_payment")
    b.hold_until = now() - timedelta(minutes=1)
    db.commit()
    assert svc.expire_holds(db) == 1
    assert any("หมดเวลาชำระเงิน" in m[1] and "จองใหม่ได้ที่" in m[2] for m in mails), mails


def test_stale_nudge_levels(db, lines):
    cfg.set_many(db, {"nudge_minutes": "30"})
    b = make(db, "bua", 60)
    assert automation.stale_nudges(db) == 0 or b.code not in "".join(lines)
    db.query(Booking).filter_by(id=b.id).update({"updated_at": now() - timedelta(minutes=40), "created_at": now() - timedelta(minutes=40)})
    db.commit()
    lines.clear()
    automation.stale_nudges(db)
    assert any(b.code in t and "🟠" in t for t in lines)
    lines.clear()
    automation.stale_nudges(db)
    assert not any(b.code in t for t in lines)                 # same level: quiet
    db.query(Booking).filter_by(id=b.id).update({"updated_at": now() - timedelta(minutes=130)})
    db.commit()
    automation.stale_nudges(db)
    assert any(b.code in t and "🔴" in t for t in lines)


def test_prearrival_checkout_review(db, mails, monkeypatch):
    cfg.set_many(db, {"google_review_url": "https://g.page/r/test"})
    b = make(db, "chaba", 1, nights=1, status="confirmed")
    assert automation.prearrival(db, "https://gbr.test") >= 1
    assert any("พรุ่งนี้พบกัน" in m[1] and "วิธีเดินทาง" in m[2] and "maps" in m[2] for m in mails)
    assert automation.prearrival(db, "") == 0
    # the stay is over but nobody pressed check-out
    b.status = "checked_in"
    b.check_in, b.check_out = today() - timedelta(days=2), today() - timedelta(days=1)
    db.commit()
    assert automation.auto_checkout(db) >= 1
    db.commit()
    db.refresh(b)
    assert b.status == "completed" and b.house.housekeeping == "dirty"
    assert automation.review_requests(db) >= 1
    assert any("ขอบคุณ" in m[1] and "https://g.page/r/test" in m[2] for m in mails)
    assert automation.review_requests(db) == 0


def test_daily_summary_once_a_day(db, lines):
    text = automation.daily_summary(db, force=True)
    assert "สรุปงานวันนี้" in text and "ว่างคืนนี้" in text and "งานค้าง" in text
    cfg.set_many(db, {"daily_summary_time": "00:00"})
    assert automation.daily_summary(db)
    assert automation.daily_summary(db) is None
    assert db.query(AutomationLog).filter(AutomationLog.key.like("daily:%")).count() == 1


def test_monthly_payout_drafts(db, monkeypatch):
    monkeypatch.setattr(automation, "today", lambda: today().replace(day=1))
    fah = db.query(User).filter_by(username="fah").one()
    assert automation.monthly_payouts(db) >= 1
    db.commit()
    p = db.query(Payout).filter_by(user_id=fah.id).one()
    assert p.status == "unpaid" and float(p.amount) > 0
    assert automation.monthly_payouts(db) == 0


def test_run_all_switches(db):
    for k in ("auto_payment_reminder", "auto_nudge", "auto_prearrival", "auto_checkout",
              "auto_review_request", "auto_daily_summary", "auto_payout"):
        cfg.set_many(db, {k: "0"})
    assert automation.run_all(db, "") == {}
    cfg.set_many(db, {"auto_checkout": "1"})
    assert "auto_checkout" in automation.run_all(db, "")


def test_cron_runs_automations(db, monkeypatch):
    monkeypatch.setenv("CRON_TOKEN", "t0k")
    r = TestClient(app).get("/cron?token=t0k")
    assert r.status_code == 200 and "auto" in r.json()


# ------------------------------------------------------------------ admin helpers
def test_login_lockout(db):
    c = TestClient(app, follow_redirects=False)
    for i in range(4):
        r = c.post("/admin/login", data={"username": "fah", "password": "wrong"})
        assert r.status_code == 401
    assert "ลองได้อีก 1 ครั้ง" in r.text
    assert c.post("/admin/login", data={"username": "fah", "password": "wrong"}).status_code == 401
    r = c.post("/admin/login", data={"username": "fah", "password": "golden1234"})
    assert r.status_code == 429 and "ล็อก" in r.text            # even the right password waits
    assert client("boy").get("/admin").status_code == 200        # other accounts unaffected
    from app.models import LoginGuard
    db.query(LoginGuard).update({"locked_until": now() - timedelta(seconds=1)})
    db.commit()
    assert c.post("/admin/login", data={"username": "fah", "password": "golden1234"}).status_code == 303


def test_quick_quote_and_checklist(db):
    owner = client("owner")
    taken = db.query(Booking).filter(Booking.status == "confirmed", Booking.check_in > today()).first()
    r = owner.get(f"/admin/quick?ci={taken.check_in}&co={taken.check_in + timedelta(days=1)}&guests=2")
    assert r.status_code == 200 and "ข้อความตอบลูกค้า" in r.text and "ว่างค่ะ" in r.text
    assert "ไม่ว่าง" in r.text and taken.code in r.text
    full = owner.get("/admin/quick?ci=2031-01-01&co=2031-01-02&guests=60")
    assert "เต็มแล้ว" in full.text or "ไม่พอ" in full.text
    dash = owner.get("/admin")
    assert "เช็กลิสต์วันนี้" in dash.text and "รหัสผ่านเริ่มต้น" in dash.text
    assert "ตอบคำขอจอง" in dash.text                             # demo has a pending request
    assert client("fah").get("/admin/quick").status_code == 200
    r = owner.post("/admin/automation/run", data={"what": "summary"})
    assert r.status_code == 303
    assert owner.post("/admin/automation/run").status_code == 303


def test_secrets_are_not_shown_and_blank_keeps_them(db):
    owner = client("owner")
    cfg.set_many(db, {"opn_secret_key": "skey_test_secret123", "line_channel_token": "LINEtok"})
    page = owner.get("/admin/settings").text
    assert "skey_test_secret123" not in page and "LINEtok" not in page and "/payments/opn/webhook" in page
    owner.post("/admin/settings", data={"opn_secret_key": "", "line_channel_token": "-", "auto_nudge": "1"})
    db.expire_all()
    assert cfg.get(db, "opn_secret_key") == "skey_test_secret123"
    assert cfg.get(db, "line_channel_token") == ""
    assert cfg.get(db, "auto_nudge") == "1" and cfg.get(db, "auto_payout") == "0"


def test_inquiry_auto_reply_checks_whole_resort(db, mails):
    d = today() + timedelta(days=200)
    c = TestClient(app, follow_redirects=False)
    c.post("/inquiry", data={"kind": "buyout", "name": "บริษัท ก", "email": "hr@example.com", "date_from": d.isoformat(),
                             "nights": "1", "guests": "40", "message": "งานเลี้ยงบริษัท"})
    to, subject, body = next(m for m in mails if m[0] == "hr@example.com")
    assert "ได้รับข้อความ" in subject and "ว่างทั้งรีสอร์ท" in body and "23,000" in body


def test_duplicate_slip_is_flagged(db, lines):
    pub = TestClient(app, follow_redirects=False)
    holds = db.query(Booking).filter_by(status="awaiting_payment").all()[:2]
    pic = slip_png("same")
    for b in holds:
        pub.post("/booking", data={"code": b.code, "phone": b.customer.phone})
        pub.post(f"/booking/{b.code}/slip", data={"amount": "100"}, files={"slip": ("s.png", pic, "image/png")})
    db.expire_all()
    second = db.get(Booking, holds[1].id)
    assert "เคยถูกส่งมาแล้ว" in second.payments[-1].note and holds[0].code in second.payments[-1].note
    assert any("สลิปซ้ำ" in t for t in lines)
    first = db.get(Booking, holds[0].id)
    assert "เคยถูกส่ง" not in (first.payments[-1].note or "")
    # a different slip is not flagged
    other = make(db, "bua", 80, status="awaiting_payment", phone="0899991111")
    pub.post("/booking", data={"code": other.code, "phone": "0899991111"})
    pub.post(f"/booking/{other.code}/slip", data={"amount": "100"}, files={"slip": ("s.png", slip_png("another one"), "image/png")})
    db.expire_all()
    assert "เคยถูกส่ง" not in (db.get(Booking, other.id).payments[-1].note or "")
    page = client("owner").get(f"/admin/bookings/{second.id}").text
    assert "ตรวจยอดเงินเข้าบัญชีจริง" in page


# ------------------------------------------------------------------ Opn payment gateway (HTTP mocked)
class FakeOpn:
    """Stands in for api.omise.co. Charges start pending; .pay() marks one paid."""

    def __init__(self):
        self.charges = {}
        self.calls = []

    def __call__(self, db, method, path, data=None):
        self.calls.append((method, path, dict(data or {})))
        if method == "POST" and path == "/charges":
            cid = f"chrg_test_{len(self.charges) + 1}"
            ch = {"object": "charge", "id": cid, "amount": data["amount"], "currency": "THB", "status": "pending",
                  "paid": False, "authorize_uri": None, "source": None}
            if data.get("source[type]") == "promptpay":
                ch["source"] = {"type": "promptpay", "scannable_code": {"image": {"download_uri": f"https://api.omise.co/charges/{cid}/documents/qr"}}}
            elif data.get("source", "").startswith("src_"):
                ch["source"] = {"type": "mobile_banking_kbank"}
                ch["authorize_uri"] = f"https://pay.omise.co/payments/{cid}/authorize"
            elif data.get("card"):
                ch["authorize_uri"] = f"https://pay.omise.co/3ds/{cid}"
            self.charges[cid] = ch
            return dict(ch)
        if method == "GET" and path.startswith("/charges/"):
            return dict(self.charges[path.split("/")[2]])
        raise AssertionError(path)

    def pay(self, cid):
        self.charges[cid].update(status="successful", paid=True)


@pytest.fixture()
def gateway(db, monkeypatch):
    fake = FakeOpn()
    monkeypatch.setattr(opn, "_api", fake)
    cfg.set_many(db, {"online_payment": "1", "opn_public_key": "pkey_test_1", "opn_secret_key": "skey_test_1"})
    return fake


def _guest(b):
    c = TestClient(app, follow_redirects=False)
    c.post("/booking", data={"code": b.code, "phone": b.customer.phone})
    return c


def test_promptpay_auto_confirms_by_polling(db, gateway):
    b = db.query(Booking).filter_by(status="awaiting_payment").first()
    due = float(b.deposit_due) - float(b.amount_paid)
    g = _guest(b)
    page = g.get(f"/booking/{b.code}").text
    assert "ชำระออนไลน์" in page and "พร้อมเพย์ QR" in page and "cdn.omise.co/omise.js" in page
    r = g.post(f"/booking/{b.code}/pay/opn", data={"method": "promptpay"})
    assert r.status_code == 303 and r.headers["location"].endswith("#pay")
    cid, sent = next(iter(gateway.charges)), gateway.calls[0][2]
    assert sent["amount"] == int(round(due * 100)) and sent["currency"] == "THB" and sent["source[type]"] == "promptpay"
    page = g.get(f"/booking/{b.code}").text
    assert "/pay/qr" in page and "data-poll" in page
    assert g.get(f"/booking/{b.code}/pay/status").json()["booking"] == "awaiting_payment"
    gateway.pay(cid)
    st = g.get(f"/booking/{b.code}/pay/status").json()
    assert st["charge"] == "successful" and st["booking"] == "confirmed"
    db.expire_all()
    b = db.get(Booking, b.id)
    assert b.status == "confirmed" and abs(float(b.amount_paid) - float(b.deposit_due)) < 1
    assert "Opn" in b.payments[-1].note
    # polling again or a late webhook must not record the money twice
    g.get(f"/booking/{b.code}/pay/status")
    TestClient(app).post("/payments/opn/webhook", json={"key": "charge.complete", "data": {"object": "charge", "id": cid}})
    db.expire_all()
    assert len([p for p in db.get(Booking, b.id).payments if p.status == "verified"]) == 1


def test_webhook_never_trusts_the_body(db, gateway):
    b = db.query(Booking).filter_by(status="awaiting_payment").first()
    g = _guest(b)
    g.post(f"/booking/{b.code}/pay/opn", data={"method": "promptpay"})
    cid = next(iter(gateway.charges))
    forged = {"key": "charge.complete", "data": {"object": "charge", "id": cid, "status": "successful", "paid": True}}
    r = TestClient(app).post("/payments/opn/webhook", json=forged)
    assert r.json()["result"] == "pending"
    db.expire_all()
    assert db.get(Booking, b.id).status == "awaiting_payment"
    gateway.pay(cid)
    assert TestClient(app).post("/payments/opn/webhook", json=forged).json()["result"] == "successful"
    db.expire_all()
    assert db.get(Booking, b.id).status == "confirmed"
    assert TestClient(app).post("/payments/opn/webhook", json={"data": {"object": "charge", "id": "chrg_unknown"}}).json()["result"] == "unknown charge"
    assert TestClient(app).post("/payments/opn/webhook", content=b"not json").status_code == 400


def test_card_and_mobile_banking_redirect_then_return(db, gateway):
    b = db.query(Booking).filter_by(status="awaiting_payment").first()
    g = _guest(b)
    r = g.post(f"/booking/{b.code}/pay/opn", data={"omiseSource": "src_test_1"})
    assert r.status_code == 303 and r.headers["location"].startswith("https://pay.omise.co/payments/")
    assert gateway.calls[-1][2]["return_uri"].endswith(f"/booking/{b.code}/pay/return")
    row = db.query(OnlineCharge).filter_by(booking_id=b.id).one()
    assert row.method == "mobile_banking_kbank"
    gateway.pay(row.charge_id)
    r = TestClient(app, follow_redirects=False).get(f"/booking/{b.code}/pay/return")   # even without the session
    assert r.status_code == 303
    db.expire_all()
    assert db.get(Booking, b.id).status == "confirmed"
    # bad tokens are refused before reaching the gateway
    other = db.query(Booking).filter_by(status="awaiting_payment").first()
    g2 = _guest(other)
    n = len(gateway.calls)
    g2.post(f"/booking/{other.code}/pay/opn", data={"omiseToken": "javascript:alert(1)"})
    assert len(gateway.calls) == n
    r = g2.post(f"/booking/{other.code}/pay/opn", data={"omiseToken": "tokn_test_9"})
    assert r.headers["location"].startswith("https://pay.omise.co/3ds/")


def test_gateway_off_or_errors_fall_back_to_slip(db, gateway, monkeypatch):
    b = db.query(Booking).filter_by(status="awaiting_payment").first()
    g = _guest(b)
    cfg.set_many(db, {"online_payment": "0"})
    assert "ชำระออนไลน์" not in g.get(f"/booking/{b.code}").text
    cfg.set_many(db, {"online_payment": "1"})

    def down(*a, **k):
        raise opn.OpnError("timeout")
    monkeypatch.setattr(opn, "_api", down)
    g.post(f"/booking/{b.code}/pay/opn", data={"method": "promptpay"})
    page = g.get(f"/booking/{b.code}").text
    assert "ชำระออนไลน์ไม่สำเร็จ" in page and "แนบสลิป" in page


def test_chat_message_on_booking_page(db):
    owner = client("owner")
    cfg.set_many(db, {"promptpay_id": "0812345678"})
    held = db.query(Booking).filter_by(status="awaiting_payment").first()
    page = owner.get(f"/admin/bookings/{held.id}").text
    assert "ข้อความส่งลูกค้า" in page and "ยอดชำระ" in page and f"/booking/{held.code}" in page
    done = db.query(Booking).filter_by(status="confirmed").first()
    assert "การจองสำเร็จ" in owner.get(f"/admin/bookings/{done.id}").text


def test_brevo_email_over_https(db, monkeypatch):
    """Render free blocks SMTP ports, so e-mail can go through Brevo's HTTPS API."""
    import json as _json
    sent = []

    class Resp:
        def read(self):
            return b'{"messageId":"x"}'
    monkeypatch.setattr(notify.urllib.request, "urlopen", lambda req, timeout=0: sent.append(req) or Resp())
    monkeypatch.setattr(notify, "_bg", lambda fn, *a: fn(*a))
    cfg.set_many(db, {"brevo_api_key": "xkeysib-test", "smtp_from": "Golden Beach <booking@example.com>"})
    notify.email(db, "guest@example.com", "ทดสอบ", "บรรทัด 1\nบรรทัด 2 <b>")
    req = sent[-1]
    body = _json.loads(req.data)
    assert req.full_url == "https://api.brevo.com/v3/smtp/email" and req.headers["Api-key"] == "xkeysib-test"
    assert body["sender"] == {"email": "booking@example.com", "name": "Golden Beach"}
    assert body["to"] == [{"email": "guest@example.com"}] and "<br>" in body["htmlContent"] and "&lt;b&gt;" in body["htmlContent"]
    assert "ระบบส่งอีเมล" not in " ".join(i["text"] for i in automation.checklist(db, db.query(User).filter_by(username="owner").one()))


def test_line_webhook_saves_group_id(db, lines):
    import base64, hashlib, hmac, json as _json
    cfg.set_many(db, {"line_channel_secret": "s3cret", "line_group_id": ""})
    body = _json.dumps({"events": [{"type": "join", "source": {"type": "group", "groupId": "Cabc123"}}]}).encode()
    c = TestClient(app)
    assert c.post("/line/webhook", content=body, headers={"x-line-signature": "bad"}).status_code == 401
    sig = base64.b64encode(hmac.new(b"s3cret", body, hashlib.sha256).digest()).decode()
    assert c.post("/line/webhook", content=body, headers={"x-line-signature": sig}).status_code == 200
    db.expire_all()
    assert cfg.get(db, "line_group_id") == "Cabc123"
    body2 = _json.dumps({"events": [{"type": "follow", "source": {"type": "user", "userId": "U999"}}]}).encode()
    sig2 = base64.b64encode(hmac.new(b"s3cret", body2, hashlib.sha256).digest()).decode()
    assert c.post("/line/webhook", content=body2, headers={"x-line-signature": sig2}).status_code == 200
    from app.models import Notification
    assert db.query(Notification).filter(Notification.body.like("%U999%")).count() >= 1


def test_backup_roundtrip(db, tmp_path):
    import io as _io, zipfile
    owner = client("owner")
    assert client("fah").get("/admin/backup.zip").status_code in (303, 403)
    r = owner.get("/admin/backup.zip")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    z = zipfile.ZipFile(_io.BytesIO(r.content))
    assert "bookings.json" in z.namelist() and "manifest.json" in z.namelist()
    before = db.query(Booking).count()
    from app.backup import restore
    with pytest.raises(SystemExit):
        restore(r.content)                       # not empty, no --replace
    counts = restore(r.content, replace=True)
    db.expire_all()
    assert counts["bookings"] == before == db.query(Booking).count()
    # new rows still get fresh ids after a restore
    make(db, "bua", 120, phone="0877776666")
    assert db.query(Booking).count() == before + 1
