"""Click-through of every page and admin action, as each role."""
import io
import re
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import Block, Booking, House, Inquiry, Payout, Promotion, RatePeriod, User, WalletEntry
from app.seed import run as seed
from app.timeutil import today


def png() -> bytes:
    b = io.BytesIO()
    Image.new("RGB", (40, 30), "#9A5A2E").save(b, "PNG")
    return b.getvalue()


@pytest.fixture()
def db():
    Base.metadata.drop_all(engine)
    seed(demo=True)
    s = SessionLocal()
    yield s
    s.close()


def client(user=None):
    c = TestClient(app, follow_redirects=False)
    if user:
        r = c.post("/admin/login", data={"username": user, "password": "golden1234"})
        assert r.status_code == 303, r.text
    return c


def flash_of(c, r):
    """Follow a redirect and return the flash text shown on the next page."""
    page = c.get(r.headers["location"])
    m = re.search(r'data-flash>(.*?)</div>', page.text, re.S)
    return (m.group(1).strip() if m else ""), page


# ------------------------------------------------------------------ public
PUBLIC = ["/", "/houses", "/houses/phutthaksa", "/availability", "/book", "/book?house=bua",
          "/booking", "/buyout", "/promotions", "/contact", "/faq", "/robots.txt", "/sitemap.xml",
          "/llms.txt", "/en", "/en/houses", "/en/houses/mali", "/en/faq", "/en/buyout"]


@pytest.mark.parametrize("url", PUBLIC)
def test_public_pages(db, url):
    r = client().get(url)
    assert r.status_code == 200, url


def test_404_and_languages(db):
    c = client()
    assert c.get("/houses/nope").status_code == 404
    assert c.get("/nope").status_code == 404
    en = c.get("/en/houses").text
    assert 'lang="en"' in en and "All nine houses" in en
    th = c.get("/houses").text
    assert 'lang="th"' in th and 'hreflang="en"' in th and "rel=\"canonical\"" in th


def test_public_booking_contact_and_lookup(db):
    c = client()
    ci = today() + timedelta(days=40)
    h = db.query(House).filter_by(slug="mali").one()
    r = c.post("/book", data={"house_id": h.id, "check_in": ci, "check_out": ci + timedelta(days=2), "adults": 2,
                              "children": 0, "pets": 1, "name": "คุณเว็บ", "phone": "0861112222", "agree": "1",
                              "addon_1": "1"})
    assert r.status_code == 303
    code = r.headers["location"].split("/")[2].split("?")[0]
    page = c.get(r.headers["location"])
    assert code in page.text and "ส่งคำขอจองแล้ว" in page.text
    b = db.query(Booking).filter_by(code=code).one()
    assert b.addons_total == 100 and b.pets == 1
    # same dates again: allowed (request), but a held booking blocks it
    assert c.post("/book", data={"house_id": h.id, "check_in": ci, "check_out": ci + timedelta(days=1), "adults": 9,
                                 "name": "x", "phone": "0861112223", "agree": "1"}).status_code == 422
    # missing consent
    r = c.post("/book", data={"house_id": h.id, "check_in": ci, "check_out": ci + timedelta(days=1), "adults": 2,
                              "name": "x", "phone": "0861112223"})
    assert r.status_code == 422 and "เงื่อนไข" in r.text
    # lookup from a fresh browser
    other = client()
    assert other.get(f"/booking/{code}").status_code == 303
    assert other.post("/booking", data={"code": code, "phone": "0000000000"}).status_code == 404
    assert other.post("/booking", data={"code": code.lower(), "phone": "086-111-2222"}).status_code == 303
    assert other.get(f"/booking/{code}").status_code == 200
    # contact + buyout inquiry
    for kind in ("contact", "buyout"):
        r = c.post("/inquiry", data={"kind": kind, "name": "ลูกค้า", "phone": "0812345678", "message": "สนใจ", "back": "/contact"})
        assert r.status_code == 303
    assert db.query(Inquiry).count() == 2
    # honeypot
    c.post("/inquiry", data={"name": "bot", "phone": "1", "website": "spam"})
    assert db.query(Inquiry).count() == 2


def test_api_and_ical(db):
    c = client()
    ci = today() + timedelta(days=3)
    d = c.get(f"/api/availability?ci={ci}&co={ci + timedelta(days=1)}&guests=5").json()
    assert len(d) == 9 and sum(1 for v in d.values() if v["fits"]) == 1   # only Bua sleeps five
    q = c.get(f"/api/quote?house=1&ci={ci}&co={ci + timedelta(days=2)}&adults=2&addons=1:1,2:1").json()
    assert q["addons_total"] == 300
    h = db.query(House).first()
    assert c.get(f"/ical/{h.slug}.ics").status_code == 404
    assert "BEGIN:VCALENDAR" in c.get(f"/ical/{h.slug}.ics?token={h.ical_token}").text


# ------------------------------------------------------------------ admin, owner
OWNER_PAGES = ["/admin", "/admin/calendar", "/admin/calendar?days=14", "/admin/bookings", "/admin/bookings?status=active",
               "/admin/bookings?q=GB&channel=facebook", "/admin/bookings/new", "/admin/bookings/1", "/admin/housekeeping",
               "/admin/houses", "/admin/houses/1", "/admin/promotions", "/admin/commission", "/admin/wallet",
               "/admin/wallet?uid=2", "/admin/reports", "/admin/reports.csv", "/admin/customers", "/admin/customers?q=สม",
               "/admin/inquiries", "/admin/notifications", "/admin/audit", "/admin/users", "/admin/settings",
               "/admin/api/notifications", "/admin/api/check?house=1&ci=2030-01-01&co=2030-01-03"]


def test_owner_pages(db):
    c = client("owner")
    for u in OWNER_PAGES:
        assert c.get(u).status_code == 200, u


def test_login_required_and_roles(db):
    anon = client()
    assert anon.get("/admin").headers["location"] == "/admin/login"
    assert anon.post("/admin/login", data={"username": "owner", "password": "bad"}).status_code == 401
    sales = client("fah")
    for u in ("/admin/settings", "/admin/users", "/admin/reports", "/admin/houses", "/admin/commission"):
        assert sales.get(u).status_code == 303, u
    assert sales.get("/admin/bookings").status_code == 200
    assert sales.post("/admin/commission", data={}).status_code == 303
    maid = client("maid")
    assert maid.get("/admin/housekeeping").status_code == 200
    assert maid.get("/admin/bookings").headers["location"] == "/admin/housekeeping"
    mgr = client("manager")
    assert mgr.get("/admin/users").status_code == 303
    assert mgr.get("/admin/settings").status_code == 200
    # slips are private
    assert anon.get("/uploads/slips/x.jpg").status_code == 404


def test_booking_lifecycle_from_admin(db):
    c = client("fah")
    ci = today() + timedelta(days=50)
    h = db.query(House).filter_by(slug="leelawadee").one()
    # channel is required
    r = c.post("/admin/bookings/new", data={"house_id": h.id, "check_in": ci, "check_out": ci + timedelta(days=2),
                                            "name": "ก", "phone": "0811111111"})
    assert r.status_code == 200 and "ช่องทาง" in r.text
    r = c.post("/admin/bookings/new", data={"channel": "facebook", "house_id": h.id, "check_in": ci,
                                            "check_out": ci + timedelta(days=2), "name": "คุณสมชาย", "phone": "0811111111",
                                            "adults": 2, "status": "awaiting_payment", "override_total": "5500"})
    assert r.status_code == 303
    bid = int(r.headers["location"].rsplit("/", 1)[1])
    b = db.get(Booking, bid)
    assert b.status == "awaiting_payment" and float(b.total) == 5500 and b.agent.username == "fah"
    # conflicting manual booking is refused with the reason
    r = c.post("/admin/bookings/new", data={"channel": "phone", "house_id": h.id, "check_in": ci + timedelta(days=1),
                                            "check_out": ci + timedelta(days=3), "name": "คนอื่น", "phone": "0822222222"})
    assert r.status_code == 200 and "ห้องไม่ว่าง" in r.text and b.code in r.text
    chk = c.get(f"/admin/api/check?house={h.id}&ci={ci}&co={ci + timedelta(days=1)}").json()
    assert not chk["free"] and chk["free_from"] == str(ci + timedelta(days=2))

    act = lambda **d: c.post(f"/admin/bookings/{bid}/action", data=d)  # noqa: E731
    msg, _ = flash_of(c, act(action="pay", amount="2000", method="transfer"))
    db.refresh(b)
    assert b.status == "awaiting_payment" and float(b.amount_paid) == 2000
    act(action="pay", amount="3500", method="cash")
    db.refresh(b)
    assert b.status == "confirmed" and float(b.commission_amount) == 550
    act(action="note", admin_note="ลูกค้าขอเตียงเสริม")
    act(action="move", house_id=db.query(House).filter_by(slug="mali").one().id, check_in=ci + timedelta(days=10),
        check_out=ci + timedelta(days=12))
    db.refresh(b)
    assert b.house.slug == "mali" and b.check_in == ci + timedelta(days=10)
    msg, _ = flash_of(c, act(action="checkin"))
    db.refresh(b)
    assert b.status == "checked_in"
    act(action="checkout")
    db.refresh(b)
    assert b.status == "completed" and b.house.housekeeping == "dirty"
    msg, _ = flash_of(c, act(action="cancel", reason="x"))
    assert "ปิดไปแล้ว" in msg
    # sales cannot reassign agents
    msg, _ = flash_of(c, act(action="agent", agent_id="", channel="phone"))
    assert "ผู้จัดการ" in msg


def test_website_request_reject_slip_and_cancel(db):
    owner = client("owner")
    pending = db.query(Booking).filter_by(status="pending").first()
    msg, _ = flash_of(owner, owner.post(f"/admin/bookings/{pending.id}/action", data={"action": "reject", "reason": "เต็ม"}))
    db.refresh(pending)
    assert pending.status == "rejected"
    # customer slip -> admin rejects slip -> customer back to awaiting payment
    pub = client()
    held = db.query(Booking).filter_by(status="awaiting_payment").first()
    pub.post("/booking", data={"code": held.code, "phone": held.customer.phone})
    r = pub.post(f"/booking/{held.code}/slip", data={"amount": "100"}, files={"slip": ("s.png", png(), "image/png")})
    assert r.status_code == 303
    db.refresh(held)
    assert held.status == "slip_uploaded"
    slip = held.payments[-1]
    assert owner.get(slip.slip_path).status_code == 200            # staff can open it
    assert client().get(slip.slip_path).status_code == 404         # the public cannot
    owner.post(f"/admin/bookings/{held.id}/action", data={"action": "reject_slip", "payment_id": slip.id, "reason": "ยอดไม่ตรง"})
    db.refresh(held)
    assert held.status == "awaiting_payment"
    # a non-image upload is refused politely
    pub.post(f"/booking/{held.code}/slip", data={"amount": "1"}, files={"slip": ("a.txt", b"hello", "text/plain")})
    assert "รองรับเฉพาะไฟล์รูปภาพ" in pub.get(f"/booking/{held.code}").text
    # cancelling a paid booking reverses commission
    paid = db.query(Booking).filter(Booking.status == "confirmed", Booking.agent_id.is_not(None)).first()
    owner.post(f"/admin/bookings/{paid.id}/action", data={"action": "no_show", "reason": "ไม่มา"})
    db.refresh(paid)
    assert paid.status == "no_show"
    net = sum(float(e.amount) for e in db.query(WalletEntry).filter_by(booking_id=paid.id))
    assert net == 0


def test_buyout_booking(db):
    c = client("manager")
    ci = today() + timedelta(days=80)
    r = c.post("/admin/bookings/new", data={"buyout": "1", "channel": "phone", "check_in": ci,
                                            "check_out": ci + timedelta(days=1), "name": "บริษัท ก", "phone": "0899999999",
                                            "status": "awaiting_payment"})
    assert r.status_code == 303
    group = db.query(Booking).filter(Booking.check_in == ci).all()
    assert len(group) == 9 and len({b.group_ref for b in group}) == 1
    assert round(sum(float(b.total) for b in group)) == 23000


def test_calendar_blocks(db):
    c = client("owner")
    h = db.query(House).filter_by(slug="bua").one()
    s = today() + timedelta(days=90)
    c.post("/admin/blocks", data={"house_id": h.id, "start_date": s, "end_date": s + timedelta(days=2), "reason": "ซ่อมแอร์"})
    bl = db.query(Block).filter_by(house_id=h.id).one()
    assert client().get(f"/api/availability?ci={s}&co={s + timedelta(days=1)}").json()[str(h.id)]["free"] is False
    c.post(f"/admin/blocks/{bl.id}/delete")
    assert db.query(Block).count() == 0


def test_settings_houses_promos_users(db):
    c = client("owner")
    h = db.query(House).filter_by(slug="chaba").one()
    form = {"name_th": "ดอกชบา", "name_en": "Chaba", "kind": "small", "side": "R", "position": "4", "std_guests": "2",
            "max_guests": "3", "bedrooms": "1", "bathrooms": "1", "price_weekday": "1800", "price_weekend": "2200",
            "extra_guest_fee": "300", "is_active": "1", "amenities": ["wifi", "tv"]}
    c.post(f"/admin/houses/{h.id}", data=form)
    db.refresh(h)
    assert float(h.price_weekend) == 2200 and h.max_guests == 3 and h.amenities == "wifi,tv"
    r = c.post(f"/admin/houses/{h.id}/photos", files={"photo": ("p.png", png(), "image/png")})
    db.refresh(h)
    new = h.photos[-1]
    assert client().get(new.path).status_code == 200
    c.post(f"/admin/photos/{new.id}/cover")
    db.refresh(h)
    assert h.photos[0].id == new.id
    c.post(f"/admin/photos/{new.id}/delete")
    # rate period changes the quote
    s = today() + timedelta(days=100)
    c.post("/admin/rates", data={"name": "ไฮซีซั่น", "start_date": s, "end_date": s, "house_kind": "small", "price": "2500", "is_holiday": "1"})
    q = client().get(f"/api/quote?house={h.id}&ci={s}&co={s + timedelta(days=1)}&adults=2").json()
    assert q["room_total"] == 2500 and q["nights"][0]["label"] == "ไฮซีซั่น"
    rp = db.query(RatePeriod).filter_by(name="ไฮซีซั่น").one()
    c.post(f"/admin/rates/{rp.id}/delete")
    # promotions
    c.post("/admin/promotions/0", data={"name_th": "ลด 10%", "kind": "percent_discount", "percent": "10", "active": "1",
                                        "stackable": "1", "show_on_site": "1", "house_kinds": ["small"], "window_on": "stay"})
    p = db.query(Promotion).filter_by(name_th="ลด 10%").one()
    assert "ลด 10%" in client().get("/promotions").text
    c.post(f"/admin/promotions/{p.id}", data={"delete": "1"})
    assert db.query(Promotion).filter_by(name_th="ลด 10%").count() == 0
    c.post("/admin/addons/0", data={"name_th": "เตียงเสริม", "price": "300", "unit": "night", "active": "1"})
    c.post("/admin/buyout", data={"name_th": "เหมา", "price": "18000", "guests": "41", "active": "1", "includes_th": "อาหารเช้า"})
    assert "18,000" in client().get("/buyout").text
    c.post("/admin/commission", data={"facebook_mode": "fixed", "facebook_fixed_big": "200", "facebook_fixed_small": "100"})
    # settings
    c.post("/admin/settings", data={"phone_main": "099-999-9999", "promptpay_id": "0812345678", "booking_mode": "instant"})
    assert "099-999-9999" in client().get("/contact").text
    # users
    c.post("/admin/users/0", data={"username": "nong", "display_name": "แอดมินน้อง", "role": "sales", "password": "abcdefgh",
                                   "earns_commission": "1", "active": "1"})
    nong = TestClient(app, follow_redirects=False)
    assert nong.post("/admin/login", data={"username": "nong", "password": "abcdefgh"}).status_code == 303
    msg, _ = flash_of(c, c.post("/admin/me/password", data={"old": "golden1234", "new": "short"}, headers={"referer": "/admin"}))
    assert "8" in msg


def test_instant_mode_shows_promptpay(db):
    c = client("owner")
    c.post("/admin/settings", data={"promptpay_id": "0812345678", "booking_mode": "instant"})
    pub = client()
    ci = today() + timedelta(days=60)
    h = db.query(House).filter_by(slug="bua").one()
    r = pub.post("/book", data={"house_id": h.id, "check_in": ci, "check_out": ci + timedelta(days=1), "adults": 2,
                                "name": "ทันที", "phone": "0870000000", "agree": "1"})
    page = pub.get(r.headers["location"])
    assert "<svg" in page.text and "สแกน" in page.text


def test_wallet_payouts_inquiries_notifications_housekeeping(db):
    owner = client("owner")
    fah = db.query(User).filter_by(username="fah").one()
    assert owner.post("/admin/payouts", data={"user_id": fah.id}).status_code == 303
    p = db.query(Payout).filter_by(user_id=fah.id).one()
    assert float(p.amount) > 0
    owner.post(f"/admin/payouts/{p.id}/paid")
    db.refresh(p)
    assert p.status == "paid"
    assert "จ่ายแล้ว" in client("fah").get("/admin/wallet").text
    iq = Inquiry(name="x", phone="1", message="m")
    db.add(iq)
    db.commit()
    owner.post(f"/admin/inquiries/{iq.id}/done")
    db.refresh(iq)
    assert iq.status == "done"
    owner.post("/admin/notifications/read", data={"nid": 0})
    assert owner.get("/admin/api/notifications").json()["count"] == 0
    maid = client("maid")
    h = db.query(House).first()
    maid.post(f"/admin/housekeeping/{h.id}", data={"state": "cleaning"})
    db.refresh(h)
    assert h.housekeeping == "cleaning"


def test_cron_endpoint(db):
    c = client()
    assert c.get("/cron").status_code in (401, 403)
    import os
    os.environ["CRON_TOKEN"] = "abc"
    assert c.get("/cron?token=abc").status_code == 200
