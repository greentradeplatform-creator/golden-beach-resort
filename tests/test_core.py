import os
import tempfile
from datetime import date, timedelta

_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{_tmp}/test.db"
os.environ["UPLOAD_DIR"] = f"{_tmp}/uploads"

import pytest  # noqa: E402
from sqlalchemy.exc import IntegrityError  # noqa: E402

from app.db import Base, SessionLocal, engine  # noqa: E402
from app.models import Booking, House, NightLock, Promotion, User, WalletEntry  # noqa: E402
from app.seed import run as seed  # noqa: E402
from app.services import availability as av  # noqa: E402
from app.services import bookings as svc  # noqa: E402
from app.services import commission, ical, promptpay  # noqa: E402
from app.services.pricing import quote  # noqa: E402
from app.services.security import hash_password  # noqa: E402
from app.timeutil import today  # noqa: E402


@pytest.fixture()
def db():
    Base.metadata.drop_all(engine)
    seed()
    s = SessionLocal()
    s.add(User(username="fah", password_hash=hash_password("x" * 8), display_name="แอดมินฟ้า", role="sales"))
    s.commit()
    yield s
    s.close()


def house(db, slug):
    return db.query(House).filter(House.slug == slug).one()


def next_weekday(wd):  # 0 = Monday
    d = today() + timedelta(days=7)
    return d + timedelta(days=(wd - d.weekday()) % 7)


def test_weekday_discount_and_gifts(db):
    mon = next_weekday(0)
    q = quote(db, house(db, "leelawadee"), mon, mon + timedelta(days=2), 2)
    assert q.room_total == 6000
    assert q.discount_total == 1000          # Mon + Tue nights, 500 each
    assert q.total == 5000
    assert any("Tahiti" in g for g in q.gifts)


def test_weekend_nights_not_discounted(db):
    fri = next_weekday(4)
    q = quote(db, house(db, "chaba"), fri, fri + timedelta(days=2), 2)
    assert q.discount_total == 0 and q.total == 4000
    assert any("หมูกระทะ" in g for g in q.gifts)


def test_capacity_error(db):
    q = quote(db, house(db, "chaba"), today() + timedelta(days=3), today() + timedelta(days=4), 3)
    assert not q.ok


def test_full_website_flow_and_commission(db):
    fah = db.query(User).filter_by(username="fah").one()
    h = house(db, "leelawadee")
    ci = next_weekday(4)  # Friday, no weekday discount
    b = svc.create(db, house=h, check_in=ci, check_out=ci + timedelta(days=2), adults=2, children=0,
                   name="คุณสมชาย", phone="081-111-2222", channel="facebook", agent=fah, created_by=fah, status="pending")
    assert b.status == "pending" and b.total == 6000
    assert db.query(NightLock).filter_by(booking_id=b.id).count() == 0   # requests don't hold the house
    svc.approve(db, b, fah)
    db.commit()
    assert b.status == "awaiting_payment"
    assert db.query(NightLock).filter_by(booking_id=b.id).count() == 2
    svc.upload_slip(db, b, "/uploads/slips/x.jpg", 6000)
    db.commit()
    assert b.status == "slip_uploaded"
    svc.record_payment(db, b, 6000, "promptpay", fah, payment=b.payments[0])
    db.commit()
    assert b.status == "confirmed"
    assert float(b.commission_amount) == 600 and float(b.resort_revenue) == 5400
    assert commission.balance(db, fah.id) == 600
    # cancelling a paid booking reverses the commission and frees the nights
    svc.cancel(db, b, fah, "ลูกค้าป่วย")
    db.commit()
    assert commission.balance(db, fah.id) == 0
    assert db.query(NightLock).filter_by(booking_id=b.id).count() == 0


def test_double_booking_blocked(db):
    h = house(db, "mali")
    ci = today() + timedelta(days=10)
    a = svc.create(db, house=h, check_in=ci, check_out=ci + timedelta(days=3), adults=2, children=0,
                   name="A", phone="0800000001", status="awaiting_payment", created_by=db.query(User).first())
    with pytest.raises(av.ConflictError) as e:
        svc.create(db, house=h, check_in=ci + timedelta(days=1), check_out=ci + timedelta(days=2), adults=2,
                   children=0, name="B", phone="0800000002")
    assert e.value.conflicts[0].code == a.code
    db.rollback()
    # two pending requests may overlap, but only the first approval wins;
    # the other one is turned down automatically (with other houses offered)
    p1 = svc.create(db, house=h, check_in=ci + timedelta(days=5), check_out=ci + timedelta(days=6), adults=2, children=0, name="C", phone="0800000003")
    p2 = svc.create(db, house=h, check_in=ci + timedelta(days=5), check_out=ci + timedelta(days=7), adults=2, children=0, name="D", phone="0800000004")
    p3 = svc.create(db, house=h, check_in=ci + timedelta(days=6), check_out=ci + timedelta(days=7), adults=2, children=0, name="E", phone="0800000006")
    svc.approve(db, p1, None)
    db.commit()
    assert p2.status == "rejected" and p1.code in p2.cancel_reason
    assert p3.status == "pending"                      # different night, untouched
    with pytest.raises(svc.BookingError):
        svc.approve(db, p2, None)
    db.rollback()
    # with the automation switched off the old behaviour stays: approval is refused
    from app.services import settings as cfg
    cfg.set_many(db, {"auto_reject_overlaps": "0"})
    p4 = svc.create(db, house=h, check_in=ci + timedelta(days=6), check_out=ci + timedelta(days=8), adults=2, children=0, name="F", phone="0800000007")
    svc.approve(db, p3, None)
    db.commit()
    assert p4.status == "pending"
    with pytest.raises(av.ConflictError):
        svc.approve(db, p4, None)
    db.rollback()
    assert av.next_free_date(db, h.id, ci) == ci + timedelta(days=3)


def test_unique_constraint_is_the_last_line(db):
    h = house(db, "bua")
    d = today() + timedelta(days=20)
    db.add(NightLock(house_id=h.id, night=d))
    db.commit()
    db.add(NightLock(house_id=h.id, night=d))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_hold_expires(db):
    h = house(db, "daoruang")
    ci = today() + timedelta(days=4)
    b = svc.create(db, house=h, check_in=ci, check_out=ci + timedelta(days=1), adults=2, children=0,
                   name="E", phone="0800000005", status="awaiting_payment", created_by=db.query(User).first())
    b.hold_until = b.hold_until - timedelta(days=2)
    db.commit()
    assert svc.expire_holds(db) == 1
    assert db.get(Booking, b.id).status == "expired"
    assert av.is_free(db, h.id, ci, ci + timedelta(days=1))


def test_loyalty_sixth_stay(db):
    lp = db.query(Promotion).filter_by(kind="loyalty").one()
    lp.start_date, lp.end_date = None, None
    db.commit()
    h = house(db, "chaba")
    owner = db.query(User).first()
    for i in range(5):
        ci = today() - timedelta(days=60 - i * 7)
        b = svc.create(db, house=h, check_in=ci, check_out=ci + timedelta(days=1), adults=2, children=0,
                       name="ลูกค้าประจำ", phone="0891234567", channel="line", created_by=owner, status="confirmed")
        svc.check_out(db, b, owner)
        db.commit()
    fri = next_weekday(4)
    q = quote(db, h, fri, fri + timedelta(days=1), 2, phone="0891234567", channel="line")
    assert q.total == 1000                     # 50% of 2,000
    q2 = quote(db, h, fri, fri + timedelta(days=1), 2, phone="0891234567", channel="facebook")
    assert q2.total == 2000                    # must book through the same channel


def test_ical_roundtrip(db):
    h = house(db, "fueangfa")
    ci = today() + timedelta(days=30)
    feed = ("BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:abc@airbnb\r\n"
            f"DTSTART;VALUE=DATE:{ci:%Y%m%d}\r\nDTEND;VALUE=DATE:{ci + timedelta(days=2):%Y%m%d}\r\n"
            "SUMMARY:Reserved\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")
    r = ical.sync(db, h, feed)
    assert r["added"] == 1
    assert not av.is_free(db, h.id, ci, ci + timedelta(days=1))
    r = ical.sync(db, h, "BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n")
    assert r["removed"] == 1 and av.is_free(db, h.id, ci, ci + timedelta(days=1))


def test_promptpay_payload():
    p = promptpay.payload("0812345678", 1500)
    assert p.startswith("000201010212") and "0066812345678" in p and "54071500.00" in p
    assert len(p.split("6304")[-1]) == 4


def test_timezone_fallback_without_tz_database(monkeypatch):
    """Windows has no time-zone database unless tzdata is installed; the app must still start."""
    import importlib
    import zoneinfo
    import app.timeutil as tu

    def boom(*a, **k):
        raise zoneinfo.ZoneInfoNotFoundError("no tz data")
    monkeypatch.setattr(zoneinfo, "ZoneInfo", boom)
    importlib.reload(tu)
    try:
        assert tu.BKK.utcoffset(None) == timedelta(hours=7)
        assert abs((tu.now() - __import__("datetime").datetime.now(__import__("datetime").timezone.utc).replace(tzinfo=None)).total_seconds() - 7 * 3600) < 5
    finally:
        monkeypatch.undo()
        importlib.reload(tu)


def test_local_control_script(tmp_path):
    """tools/local.py: self-check passes and the setup stamp detects changed requirements."""
    import subprocess
    import sys
    root = __import__("pathlib").Path(__file__).resolve().parent.parent
    r = subprocess.run([sys.executable, str(root / "tools" / "local.py"), "check"], capture_output=True, text=True,
                       encoding="utf-8", env={**os.environ, "PYTHONHOME": "", "PORT": "8199"})
    assert r.returncode == 0, r.stdout + r.stderr
    assert "ตรวจระบบผ่านทั้งหมด" in r.stdout


def test_python_picker(tmp_path, monkeypatch):
    """tools/pick_python.py prefers 3.12, skips broken/unsupported installs and demotes noisy ones."""
    import importlib.util
    import shutil
    import stat
    root = __import__("pathlib").Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location("pick_python", root / "tools" / "pick_python.py")
    pp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pp)

    out = (" -V:3.13 *        C:\\Users\\wee ac\\AppData\\Local\\Programs\\Python\\Python313\\python.exe\n"
           " -V:3.12          C:\\Python312\\python.exe\n"
           " -3.11-64         D:\\Tools\\Py 311\\python.exe\n")
    assert pp.parse_py_list(out) == ["C:\\Users\\wee ac\\AppData\\Local\\Programs\\Python\\Python313\\python.exe",
                                     "C:\\Python312\\python.exe", "D:\\Tools\\Py 311\\python.exe"]
    assert pp.probe("C:\\Users\\x\\AppData\\Local\\Microsoft\\WindowsApps\\python.exe")["ok"] is False

    real = shutil.which("python3")
    noisy = tmp_path / "noisy-python"
    noisy.write_text(f'#!/bin/sh\necho "Could not find platform independent libraries <prefix>" >&2\nexec {real} "$@"\n')
    noisy.chmod(noisy.stat().st_mode | stat.S_IEXEC)
    clean, bad = pp.probe(real), pp.probe(str(noisy))
    assert clean["ok"] and bad["ok"] and bad["score"] < clean["score"] - 40
    assert "คำเตือน" in bad["note"]
