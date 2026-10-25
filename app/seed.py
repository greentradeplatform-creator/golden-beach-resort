"""Create tables and load the resort's real data.

    python -m app.seed            # houses, promotions, settings, owner account
    python -m app.seed --demo     # + demo staff and sample bookings
    python -m app.seed --reset    # drop everything first (dev only)
"""
from __future__ import annotations

import secrets
import sys
from datetime import date, timedelta

from .config import ADMIN_PASSWORD, ADMIN_USERNAME
from .db import Base, SessionLocal, engine
from .models import (Addon, BuyoutPackage, CommissionRule, House, Photo, Promotion, RatePeriod, User)
from .services.security import hash_password
from .timeutil import today

BIG_AMEN = "aircon,wifi,tv,fridge,water_heater,living_room,sofa,kitchenette,microwave,porch_bench,linen"
SMALL_AMEN = "aircon,wifi,tv,fridge,water_heater,porch_bench,linen"

# side, position from the sea, slug, th, en, kind, guests, bedrooms, beds_th, beds_en, price, photos, description
HOUSES = [
    ("L", 1, "phutthaksa", "ดอกพุทธรักษา", "Phuttharaksa (Canna)", "big_front", 4, 2,
     "2 ห้อง เตียงเดี่ยว 5 ฟุต", "2 rooms, one 5 ft bed each", 3500,
     ["phutthaksa", "room-queen-teal", "porch-dining", "living-sofa"],
     ("หลังแรกฝั่งซ้าย อยู่ใกล้ทะเลที่สุด ระเบียงหน้าบ้านมีม้านั่งไม้กับเก้าอี้เหล็กดัดสีขาว "
      "ห้องนอนสองห้องเป็นเตียง 5 ฟุตทั้งคู่ เหมาะกับสองครอบครัวหรือเพื่อนสองคู่",
      "First house on the left and the closest to the water. The porch has a carved bench and a white iron chair. "
      "Both bedrooms have a 5 ft bed, good for two couples or two small families.")),
    ("L", 2, "phlapphlueng", "ดอกพลับพลึง", "Phlapphlueng (Crinum Lily)", "big", 4, 2,
     "ห้องเตียงเดี่ยว 5 ฟุต 1 ห้อง และห้องเตียงคู่ 3.5 ฟุต 1 ห้อง", "One room with a 5 ft bed, one with two 3.5 ft beds", 3000,
     ["row-houses", "room-twin-tv", "room-queen-grey", "living-sofa"],
     ("บ้านหลังที่สองฝั่งซ้าย มีห้องนั่งเล่นพร้อมโซฟาหนัง ทีวี มุมครัวเล็ก ตู้เย็นและไมโครเวฟ "
      "ห้องหนึ่งเป็นเตียงใหญ่ อีกห้องเป็นเตียงแยกสองเตียง",
      "Second on the left. A living room with a leather sofa, TV and a small kitchenette with fridge and microwave. "
      "One bedroom has a big bed, the other has two singles.")),
    ("L", 3, "daoruang", "ดอกดาวเรือง", "Daoruang (Marigold)", "small", 2, 1,
     "เตียงคู่ 3.5 ฟุต 2 เตียง", "Two 3.5 ft beds", 2000,
     ["house-side", "room-twin-tv"],
     ("บ้านหลังเล็กสำหรับสองคน เตียงแยกสองเตียง เหมาะกับเพื่อนที่มาด้วยกัน มีระเบียงไม้หน้าบ้านไว้นั่งรับลม",
      "A small house for two with twin beds, good for friends travelling together. Wooden porch out front.")),
    ("L", 4, "phutson", "ดอกพุดซ้อน", "Phutson (Gardenia)", "small", 2, 1,
     "เตียงคู่ 3.5 ฟุต 2 เตียง", "Two 3.5 ft beds", 2000,
     ["house-side", "room-twin-crystal"],
     ("บ้านหลังเล็กหลังสุดท้ายฝั่งซ้าย ใกล้ลานจอดรถ ขนของเข้าออกสะดวก เตียงแยกสองเตียง",
      "Last small house on the left, nearest the car park so unloading is easy. Twin beds.")),
    ("R", 1, "fueangfa", "ดอกเฟื่องฟ้า", "Fueangfa (Bougainvillea)", "big_front", 4, 2,
     "ห้องเตียงเดี่ยว 5 ฟุต 1 ห้อง และห้องเตียงคู่ 3.5 ฟุต 1 ห้อง", "One room with a 5 ft bed, one with two 3.5 ft beds", 3500,
     ["hero-house", "room-twin-crystal", "room-queen-teal-2", "porch-dining"],
     ("หลังแรกฝั่งขวา หลังคาดินเผาสีส้ม มองเห็นร่มสีชมพูของ Tutu Beach จากหน้าบ้าน "
      "ห้องหนึ่งเตียงใหญ่ อีกห้องเตียงแยก เหมาะกับครอบครัวที่มีลูก",
      "First on the right with the orange clay roof; you can see the pink umbrellas of Tutu Beach from the porch. "
      "One big bed and one twin room, a good fit for a family with kids.")),
    ("R", 2, "leelawadee", "ดอกลีลาวดี", "Leelawadee (Frangipani)", "big", 4, 2,
     "ห้องเตียงเดี่ยว 5 ฟุต 1 ห้อง และห้องเตียงคู่ 3.5 ฟุต 1 ห้อง", "One room with a 5 ft bed, one with two 3.5 ft beds", 3000,
     ["house-side", "room-twin-crystal", "room-queen-teal", "living-sofa"],
     ("บ้านหลังใหญ่ที่สร้างใหม่ล่าสุด ห้องโถงกว้าง มีห้องนั่งเล่น ห้องหนึ่งเตียงใหญ่ อีกห้องเตียงแยก",
      "The newest of the big houses, with a roomy hall and living room. One big bed and one twin room.")),
    ("R", 3, "mali", "ดอกมะลิ", "Mali (Jasmine)", "big", 4, 2,
     "2 ห้อง เตียงเดี่ยว 5 ฟุต", "2 rooms, one 5 ft bed each", 3000,
     ["row-houses", "room-queen-teal-2", "porch-dining"],
     ("สองห้องนอนเตียงใหญ่ทั้งคู่ มีโต๊ะทานข้าวสี่ที่นั่งบนระเบียงหน้าบ้าน นั่งกินมื้อเช้ารับลมได้",
      "Two bedrooms with big beds and a four-seat dining table on the porch for breakfast in the breeze.")),
    ("R", 4, "chaba", "ดอกชบา", "Chaba (Hibiscus)", "small", 2, 1,
     "เตียงเดี่ยว 5 ฟุต", "One 5 ft bed", 2000,
     ["house-side", "room-queen-grey"],
     ("บ้านหลังเล็กเตียงใหญ่ 5 ฟุต สำหรับคู่รัก มีทีวี ตู้เย็น และระเบียงไม้หน้าบ้าน",
      "A small house with one 5 ft bed for couples. TV, fridge and a wooden porch.")),
    ("R", 5, "bua", "ดอกบัว", "Bua (Lotus)", "big", 5, 2,
     "เตียงแยก 3 เตียง และเตียงเดี่ยว 5 ฟุต 1 เตียง", "Three single beds plus one 5 ft bed", 3000,
     ["row-houses", "room-twin-crystal", "living-sofa"],
     ("หลังเดียวที่พักได้ 5 ท่าน มีเตียงแยกสามเตียงกับเตียงใหญ่อีกหนึ่ง เหมาะกับครอบครัวใหญ่หรือกลุ่มเพื่อน",
      "The only house that sleeps five: three single beds plus one big bed. Made for big families or groups of friends.")),
]

GALLERY = [
    ("row-houses", "exterior", "ทางเดินระหว่างบ้าน ปลายทางคือทะเล", "The path between the houses runs to the sea"),
    ("hero-house", "exterior", "บ้านหลังหน้าสุด ติดร่มชมพูของ Tutu Beach", "Front house beside Tutu Beach"),
    ("sunset-gazebo", "beach", "ศาลาริมทะเลตอนพระอาทิตย์ตก", "Sea-front pavilion at sunset"),
    ("lawn-gazebo", "dining", "สนามหญ้าและลานจัดเลี้ยง", "Lawn and party area"),
    ("dining-tent", "dining", "ลานทานอาหารใต้เต็นท์ริมหาด", "Dining area under the tent"),
    ("sign", "exterior", "ป้ายหน้ารีสอร์ท", "The resort sign"),
    ("room-queen-teal", "bedroom", "ห้องนอนเตียงใหญ่", "Bedroom with a big bed"),
    ("room-twin-crystal", "bedroom", "ห้องเตียงแยก", "Twin room"),
    ("living-sofa", "living", "ห้องนั่งเล่นและมุมครัว", "Living room and kitchenette"),
    ("porch-dining", "porch", "โต๊ะทานข้าวบนระเบียง", "Porch dining table"),
    ("phutthaksa", "exterior", "บ้านดอกพุทธรักษา", "Phuttharaksa house"),
    ("room-twin-tv", "bedroom", "ห้องเตียงแยกพร้อมทีวี", "Twin room with TV"),
]

PROMOTIONS = [
    dict(name_th="วันธรรมดา ลดทันที 500 บาท ทุกหลัง", name_en="Weeknights: 500 baht off every house",
         detail_th="เข้าพักคืนวันอาทิตย์ถึงพฤหัสบดี ลดคืนละ 500 บาท ใช้ได้กับบ้านทุกหลัง",
         detail_en="Stay Sunday to Thursday night and get 500 baht off per night, any house.",
         kind="weekday_discount", amount=500, weekdays="6,0,1,2,3", stackable=True, sort=1),
    dict(name_th="บ้านหลังใหญ่ รับคูปอง Tahiti Beach 500 บาท", name_en="Big houses: 500 baht Tahiti Beach voucher",
         detail_th="เข้าพักบ้านหลังใหญ่ รับคูปองส่วนลดค่าอาหารร้าน Tahiti Beach Pattaya 500 บาท พร้อมอาหารเช้า 4 ชุด",
         detail_en="Stay in a big house and get a 500 baht food voucher for Tahiti Beach Pattaya plus breakfast for 4.",
         kind="gift", house_kinds="big_front,big",
         gift_th="คูปองส่วนลดร้าน Tahiti Beach Pattaya 500 บาท และอาหารเช้า 4 ชุด",
         gift_en="500 baht Tahiti Beach Pattaya voucher and breakfast for 4", sort=2),
    dict(name_th="บ้านหลังเล็ก ฟรีหมูกระทะ 1 ชุด", name_en="Small houses: free mookata set",
         detail_th="เข้าพักบ้านหลังเล็ก รับหมูกระทะฟรี 1 ชุด พร้อมอาหารเช้า 2 ชุด",
         detail_en="Stay in a small house and get a free mookata (Thai BBQ) set plus breakfast for 2.",
         kind="gift", house_kinds="small",
         gift_th="หมูกระทะ 1 ชุด และอาหารเช้า 2 ชุด", gift_en="1 mookata set and breakfast for 2", sort=3),
    dict(name_th="สะสมครบ 5 ครั้ง ครั้งที่ 6 ลด 50%", name_en="Stay 5 times, 6th stay half price",
         detail_th="1 คนต่อ 1 สิทธิ์ ใช้แทนกันไม่ได้ ต้องจองผ่านช่องทางเดิมทุกครั้ง ระบบนับจากเบอร์โทรที่ใช้จอง",
         detail_en="One per person, not transferable. Book through the same channel each time; stays are counted by phone number.",
         kind="loyalty", nth=6, percent=50, same_channel=True, stackable=False,
         start_date=date(2026, 6, 1), end_date=date(2026, 11, 30), sort=4),
    dict(name_th="คืนที่ 2 ลดทันที 500 บาท", name_en="500 baht off your second night",
         detail_th="พัก 2 คืนขึ้นไป คืนที่สองลด 500 บาท", detail_en="Stay 2+ nights, second night 500 baht off.",
         kind="nth_night_discount", nth=2, amount=500, min_nights=2, active=False, sort=5),
]


def run(demo: bool = False, reset: bool = False):
    if reset:
        Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    db = SessionLocal()
    try:
        if db.query(House).count() == 0:
            for (side, pos, slug, th, en, kind, guests, bedrooms, beds_th, beds_en, price, photos, (dth, den)) in HOUSES:
                h = House(slug=slug, name_th=th, name_en=en, kind=kind, side=side, position=pos,
                          std_guests=guests, max_guests=guests, bedrooms=bedrooms, bathrooms=1,
                          beds_th=beds_th, beds_en=beds_en, price_weekday=price, price_weekend=price,
                          description_th=dth, description_en=den,
                          amenities=SMALL_AMEN if kind == "small" else BIG_AMEN,
                          ical_token=secrets.token_hex(12))
                for i, p in enumerate(photos):
                    h.photos.append(Photo(path=f"/static/img/{p}.jpg", sort=i,
                                          tag="exterior" if i == 0 else "bedroom"))
                db.add(h)
            for i, (p, tag, cth, cen) in enumerate(GALLERY):
                db.add(Photo(path=f"/static/img/{p}.jpg", tag=tag, caption_th=cth, caption_en=cen, sort=i))
            for p in PROMOTIONS:
                db.add(Promotion(**p))
            db.add_all([
                Addon(name_th="เช่าเตาปิ้งย่าง", name_en="BBQ grill rental", price=100, unit="stay", sort=1),
                Addon(name_th="เช่ากระทะปิ้งย่าง (หมูกระทะ)", name_en="Mookata pan rental", price=200, unit="stay", sort=2),
            ])
            db.add(BuyoutPackage(
                name_th="เหมาทั้งรีสอร์ท 9 หลัง", name_en="The whole resort, all 9 houses",
                guests=40, price=23000,
                includes_th="อาหารเช้าสำหรับทุกคน\nคาราโอเกะร้องได้ทั้งคืน\nลานทานอาหารริมหาดพร้อมเต็นท์และโต๊ะยาว",
                includes_en="Breakfast for everyone\nKaraoke all night\nBeach-side dining lawn with tent and long tables",
                note_th="ราคาต่อคืน พักได้ 40 ท่าน ปิ้งย่างได้ นำวัตถุดิบมาเอง",
                note_en="Per night for up to 40 guests. BBQ welcome, bring your own ingredients."))
            db.add_all([
                CommissionRule(channel="website", mode="none", to_pool=True),
                CommissionRule(channel="facebook", mode="percent", percent=10),
                CommissionRule(channel="line", mode="percent", percent=10),
                CommissionRule(channel="phone", mode="percent", percent=10),
                CommissionRule(channel="airbnb", mode="percent", percent=5, platform_fee_percent=15),
                CommissionRule(channel="other", mode="fixed", fixed_big=200, fixed_small=100),
            ])
            y = today().year
            db.add_all([
                RatePeriod(name="ปีใหม่", start_date=date(y, 12, 30), end_date=date(y + 1, 1, 1), is_holiday=True),
                RatePeriod(name="สงกรานต์", start_date=date(y + 1, 4, 12), end_date=date(y + 1, 4, 15), is_holiday=True),
            ])
        if not db.query(User).filter(User.username == ADMIN_USERNAME).first():
            db.add(User(username=ADMIN_USERNAME, password_hash=hash_password(ADMIN_PASSWORD),
                        display_name="เจ้าของรีสอร์ท", role="owner", earns_commission=False))
        db.commit()
        if demo and db.query(User).count() == 1:
            _demo(db)
    finally:
        db.close()


def _demo(db):
    from .services import bookings as svc
    pw = hash_password("golden1234")
    fah = User(username="fah", password_hash=pw, display_name="แอดมินฟ้า", role="sales")
    boy = User(username="boy", password_hash=pw, display_name="แอดมินบอย", role="sales")
    mgr = User(username="manager", password_hash=pw, display_name="คุณฝน", role="manager", earns_commission=False)
    hk = User(username="maid", password_hash=pw, display_name="แม่บ้าน", role="housekeeping", earns_commission=False)
    db.add_all([fah, boy, mgr, hk])
    db.commit()
    H = {h.slug: h for h in db.query(House)}
    t = today()
    rows = [
        ("leelawadee", 2, 2, "คุณสมชาย ใจดี", "0811112222", "facebook", fah, "confirmed"),
        ("phutthaksa", 0, 2, "คุณวิภา ทองดี", "0822223333", "line", boy, "confirmed"),
        ("chaba", 0, 1, "คุณเอก", "0833334444", "phone", fah, "confirmed"),
        ("bua", 5, 2, "คุณนิดา", "0844445555", "website", None, "pending"),
        ("mali", 7, 1, "คุณกิตติ", "0855556666", "facebook", boy, "awaiting_payment"),
        ("fueangfa", 9, 3, "คุณพร", "0866667777", "airbnb", fah, "confirmed"),
        ("daoruang", 1, 1, "คุณมิว", "0877778888", "website", None, "pending"),
        ("phlapphlueng", 14, 2, "คุณศักดิ์", "0888889999", "phone", boy, "confirmed"),
        ("phutson", 3, 2, "คุณจอย", "0899990000", "line", fah, "awaiting_payment"),
    ]
    for slug, off, n, name, phone, ch, agent, st in rows:
        ci = t + timedelta(days=off)
        b = svc.create(db, house=H[slug], check_in=ci, check_out=ci + timedelta(days=n), adults=2, children=0,
                       name=name, phone=phone, channel=ch, agent=agent,
                       created_by=agent or (None if ch == "website" else fah),
                       status="pending" if st == "pending" else st)
        if off == 0 and st == "confirmed":
            svc.check_in(db, b, mgr)
            db.commit()
    # one finished stay last week
    ci = t - timedelta(days=6)
    b = svc.create(db, house=H["mali"], check_in=ci, check_out=ci + timedelta(days=2), adults=4, children=0,
                   name="คุณสมชาย ใจดี", phone="0811112222", channel="facebook", agent=fah, created_by=fah,
                   status="confirmed")
    svc.check_out(db, b, mgr)
    db.commit()


if __name__ == "__main__":
    run(demo="--demo" in sys.argv, reset="--reset" in sys.argv)
    print("seed done")
