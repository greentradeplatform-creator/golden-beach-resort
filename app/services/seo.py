"""Structured data (schema.org JSON-LD), FAQ content and plain-text facts.

Search engines read JSON-LD for rich results; AI search engines (ChatGPT search,
Perplexity, Google AI Overviews, Claude) quote pages that state facts plainly.
Everything here is generated from the database so prices never go stale.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from ..models import BuyoutPackage, House, Promotion
from . import settings as cfg

AMENITY_EN = {
    "aircon": "Air conditioning", "wifi": "Free Wi-Fi", "tv": "TV", "fridge": "Refrigerator",
    "water_heater": "Hot shower", "living_room": "Living room", "sofa": "Sofa", "kitchenette": "Kitchenette",
    "microwave": "Microwave", "porch_bench": "Wooden porch", "linen": "Bedding and towels",
}


def _lp(lang, path):
    return path if lang != "en" else "/en" + (path if path != "/" else "")


def houses(db: Session) -> list[House]:
    return db.query(House).filter(House.is_active.is_(True)).order_by(House.side, House.position).all()


def price_range(hs: list[House]) -> tuple[float, float]:
    ps = [float(min(h.price_weekday, h.price_weekend)) for h in hs] or [0]
    qs = [float(max(h.price_weekday, h.price_weekend)) for h in hs] or [0]
    return min(ps), max(qs)


def resort_ld(db: Session, origin: str, lang: str) -> dict:
    c = cfg.get_all(db)
    hs = houses(db)
    lo, hi = price_range(hs)
    th = lang != "en"
    amen = ["Beachfront", "Free parking", "Free Wi-Fi", "Breakfast included", "Pets allowed", "BBQ area",
            "Karaoke", "Sea-view pavilion", "Group buyout"]
    data = {
        "@context": "https://schema.org",
        "@type": "Resort",
        "@id": origin + "/#resort",
        "name": c["resort_name_en"],
        "alternateName": [c["resort_name_th"], "Golden Beach Resort Na Jomtien", "โกลเด้นบีช รีสอร์ท นาจอมเทียน"],
        "description": ("รีสอร์ทบ้านไม้สักทรงไทย 9 หลัง ติดชายหาดนาจอมเทียน พัทยา ข้าง Tutu Beach พักได้ 2–5 ท่านต่อหลัง "
                        "รวมอาหารเช้า สัตว์เลี้ยงเข้าพักฟรี รับเหมาทั้งรีสอร์ท") if th else
                       ("Nine Thai teak houses on Na Jomtien beach, Pattaya, next to Tutu Beach. 2–5 guests per house, "
                        "breakfast included, pets stay free, whole-resort buyouts for groups."),
        "url": origin + _lp(lang, "/"),
        "telephone": "+66" + c["phone_main"].replace("-", "")[1:],
        "image": [origin + p for p in ("/static/img/row-houses.jpg", "/static/img/hero-house.jpg",
                                       "/static/img/phutthaksa.jpg", "/static/img/room-queen-teal.jpg",
                                       "/static/img/sunset-gazebo.jpg")],
        "logo": origin + "/static/img/favicon.svg",
        "priceRange": f"฿{lo:,.0f}–฿{hi:,.0f}",
        "currenciesAccepted": "THB",
        "paymentAccepted": "Bank transfer, PromptPay, Cash",
        "address": {"@type": "PostalAddress", "streetAddress": "888 Moo 2, Na Jomtien Soi 10",
                    "addressLocality": "Na Chom Thian", "addressRegion": "Chon Buri",
                    "postalCode": c["address_en"].split()[-1] if c["address_en"].split()[-1].isdigit() else "20250",
                    "addressCountry": "TH"},
        "geo": {"@type": "GeoCoordinates", "latitude": float(c["lat"]), "longitude": float(c["lng"])},
        "hasMap": f"https://www.google.com/maps/search/?api=1&query={c['lat']},{c['lng']}",
        "checkinTime": c["checkin_time"], "checkoutTime": c["checkout_time"],
        "petsAllowed": c["pets_allowed"] == "1",
        "numberOfRooms": len(hs),
        "amenityFeature": [{"@type": "LocationFeatureSpecification", "name": a, "value": True} for a in amen],
        "containsPlace": [{"@id": origin + f"/houses/{h.slug}#house"} for h in hs],
        "sameAs": [u for u in (c["facebook_url"],) if u and "search" not in u],
        "isAccessibleForFree": False,
        "knowsLanguage": ["th", "en"],
    }
    if c["email"]:
        data["email"] = c["email"]
    return data


def website_ld(db: Session, origin: str, lang: str) -> dict:
    c = cfg.get_all(db)
    return {"@context": "https://schema.org", "@type": "WebSite", "@id": origin + "/#website",
            "url": origin + "/", "name": c["resort_name_en"], "alternateName": c["resort_name_th"],
            "inLanguage": ["th", "en"], "publisher": {"@id": origin + "/#resort"}}


def house_ld(h: House, origin: str, lang: str) -> dict:
    th = lang != "en"
    return {
        "@context": "https://schema.org",
        "@type": ["Accommodation", "House"],
        "@id": origin + f"/houses/{h.slug}#house",
        "name": f"{h.name_en} – Golden Beach Resort" if not th else f"บ้าน{h.name_th} โกลเด้น บีช รีสอร์ท",
        "description": h.description_en if not th else h.description_th,
        "url": origin + _lp(lang, f"/houses/{h.slug}"),
        "image": [origin + p.path if p.path.startswith("/") else p.path for p in h.photos[:4]],
        "numberOfRooms": h.bedrooms,
        "numberOfBedrooms": h.bedrooms,
        "numberOfBathroomsTotal": h.bathrooms,
        "occupancy": {"@type": "QuantitativeValue", "minValue": 1, "maxValue": h.max_guests},
        "bed": h.beds_en,
        "petsAllowed": True,
        "amenityFeature": [{"@type": "LocationFeatureSpecification", "name": AMENITY_EN.get(a, a), "value": True}
                           for a in h.amenities.split(",") if a],
        "containedInPlace": {"@id": origin + "/#resort"},
        "offers": {"@type": "Offer", "price": float(min(h.price_weekday, h.price_weekend)), "priceCurrency": "THB",
                   "availability": "https://schema.org/InStock",
                   "url": origin + _lp(lang, f"/book?house={h.slug}"),
                   "priceSpecification": {"@type": "UnitPriceSpecification",
                                          "price": float(min(h.price_weekday, h.price_weekend)),
                                          "priceCurrency": "THB", "unitText": "night"}},
    }


def breadcrumb_ld(origin: str, items: list[tuple[str, str]]) -> dict:
    return {"@context": "https://schema.org", "@type": "BreadcrumbList",
            "itemListElement": [{"@type": "ListItem", "position": i + 1, "name": n, "item": origin + u}
                                for i, (n, u) in enumerate(items)]}


def faq(db: Session, lang: str) -> list[tuple[str, str]]:
    """Questions guests actually ask, answered with live data."""
    c = cfg.get_all(db)
    hs = houses(db)
    lo, hi = price_range(hs)
    big = [h for h in hs if h.kind != "small"]
    small = [h for h in hs if h.kind == "small"]
    front = [h for h in hs if h.kind == "big_front"]
    pkg = db.query(BuyoutPackage).filter(BuyoutPackage.active.is_(True)).first()
    promos = [p for p in db.query(Promotion).filter(Promotion.active.is_(True), Promotion.show_on_site.is_(True)).order_by(Promotion.sort)]
    names = lambda xs: ", ".join(h.name(lang) for h in xs)  # noqa: E731
    ph = f"{c['phone_main']}" + (f" / {c['phone_alt']} ({c['phone_alt_label']})" if c["phone_alt"] else "")
    if lang == "en":
        out = [
            ("Where is Golden Beach Resort?",
             f"On Na Jomtien beach, Pattaya (Sattahip district, Chon Buri), right next to Tutu Beach café. From Na Jomtien Road take Soi 10, then the second right. GPS {c['lat']}, {c['lng']}."),
            ("How much does a night cost?",
             f"Prices are per house, not per person: from {lo:,.0f} to {hi:,.0f} baht a night. Small houses for 2 ({names(small)}) and big houses for 4–5 ({names(big)}). The sea-front houses are {names(front)}."),
            ("Is breakfast included?", "Yes. Every house includes breakfast (rice porridge) for the number of guests staying." if c["breakfast_included"] == "1" else "Breakfast is not included at the moment."),
            ("Can I bring my dog or cat?", "Yes, pets stay free. Please tell us when you book." if c["pets_allowed"] == "1" else "Sorry, pets are not allowed."),
            ("What time is check-in and check-out?", f"Check-in from {c['checkin_time']}, check-out by {c['checkout_time']}."),
            ("What do 'single bed' and 'twin bed' mean here?", "Our 'single bed' room has one 5 ft bed for two people. A 'twin' room has two separate 3.5 ft beds."),
            ("Is the resort on the beach?", "Yes. The houses line a path that ends at the beach; the two front houses are the closest to the water and you can see Tutu Beach's pink umbrellas from them."),
            ("Can we barbecue?", "Yes, bring your own food (none is sold on site). A grill is 100 baht and a mookata pan 200 baht."),
            ("Is there parking?", "Yes, the resort has its own car park at the entrance, with room for vans."),
            ("How do I book and pay?", f"Book on this website or call {ph}. We confirm the house is free, then you pay by bank transfer or PromptPay QR and upload the slip on your booking page."),
        ]
        if pkg:
            out.append(("Can we rent the whole resort for a group?",
                        f"Yes. All {len(hs)} houses for up to {pkg.guests} guests cost {float(pkg.price):,.0f} baht a night, including: {', '.join(l for l in (pkg.includes_en or pkg.includes_th).splitlines() if l.strip())}."))
        if promos:
            out.append(("Are there any promotions?", " ".join(f"{p.name('en')}." for p in promos)))
        out.append(("What is nearby?", "Tutu Beach (next door), Tahiti Beach Pattaya, Club Loong Chat for kitesurfing, windsurfing and SUP, and CAVE Beach Club. Central Pattaya is about 20 minutes by car and Bangkok about 2 hours."))
        return out
    out = [
        ("โกลเด้น บีช รีสอร์ท อยู่ที่ไหน",
         f"อยู่ริมหาดนาจอมเทียน พัทยา (อ.สัตหีบ จ.ชลบุรี) ติดคาเฟ่ Tutu Beach เข้าทางถนนนาจอมเทียน ซอย 10 แล้วเลี้ยวขวาเข้าซอยที่ 2 พิกัด {c['lat']}, {c['lng']}"),
        ("ราคาบ้านพักคืนละเท่าไหร่",
         f"คิดราคาเป็นหลัง ไม่ใช่รายคน คืนละ {lo:,.0f}–{hi:,.0f} บาท บ้านหลังเล็กพัก 2 ท่าน ({names(small)}) บ้านหลังใหญ่พัก 4–5 ท่าน ({names(big)}) หลังหน้าสุดติดทะเลคือ {names(front)}"),
        ("รวมอาหารเช้าไหม", "รวมอาหารเช้าทุกหลัง (โจ๊กหมู) ตามจำนวนผู้เข้าพัก" if c["breakfast_included"] == "1" else "ตอนนี้ไม่รวมอาหารเช้า"),
        ("พาสัตว์เลี้ยงไปได้ไหม", "ได้ สัตว์เลี้ยงเข้าพักฟรี กรุณาแจ้งตอนจอง" if c["pets_allowed"] == "1" else "ขออภัย ไม่รับสัตว์เลี้ยง"),
        ("เช็คอินเช็คเอาท์กี่โมง", f"เช็คอินตั้งแต่ {c['checkin_time']} น. เช็คเอาท์ก่อน {c['checkout_time']} น."),
        ("เตียงเดี่ยวกับเตียงคู่ต่างกันอย่างไร", "ห้อง \"เตียงเดี่ยว\" ของรีสอร์ทคือเตียงใหญ่ 5 ฟุต 1 เตียง นอนได้ 2 คน ส่วนห้อง \"เตียงคู่\" คือเตียง 3.5 ฟุต 2 เตียงแยกกัน"),
        ("รีสอร์ทติดทะเลไหม", "ติดทะเล บ้านเรียงสองฝั่งทางเดินที่ไปสุดที่ชายหาด สองหลังหน้าสุดใกล้น้ำที่สุด มองเห็นร่มสีชมพูของ Tutu Beach จากหน้าบ้าน"),
        ("ปิ้งย่างได้ไหม", "ได้ นำวัตถุดิบมาเอง (รีสอร์ทไม่มีจำหน่าย) เช่าเตาปิ้งย่าง 100 บาท หรือกระทะหมูกระทะ 200 บาท"),
        ("มีที่จอดรถไหม", "มีลานจอดรถของรีสอร์ทอยู่หน้าทางเข้า จอดรถตู้ได้"),
        ("จองและชำระเงินอย่างไร", f"จองผ่านเว็บไซต์นี้ หรือโทร {ph} แอดมินยืนยันว่าบ้านว่าง แล้วชำระด้วยการโอนหรือสแกน QR พร้อมเพย์ และแนบสลิปในหน้าการจองของคุณ"),
    ]
    if pkg:
        out.append(("เหมาทั้งรีสอร์ทได้ไหม ราคาเท่าไหร่",
                    f"ได้ ทั้ง {len(hs)} หลัง พักได้ {pkg.guests} ท่าน ราคา {float(pkg.price):,.0f} บาทต่อคืน รวม {' '.join(l for l in pkg.includes_th.splitlines() if l.strip())}"))
    if promos:
        out.append(("ตอนนี้มีโปรโมชั่นอะไรบ้าง", " ".join(p.name("th") for p in promos)))
    out.append(("ใกล้รีสอร์ทมีที่เที่ยวอะไร", "Tutu Beach อยู่ติดรีสอร์ท, Tahiti Beach Pattaya, คลับลุงชัช (ไคท์เซิร์ฟ วินด์เซิร์ฟ ซัพบอร์ด) และ CAVE Beach Club ขับรถเข้าเมืองพัทยาประมาณ 20 นาที จากกรุงเทพฯ ประมาณ 2 ชั่วโมง"))
    return out


def faq_ld(items: list[tuple[str, str]], lang: str) -> dict:
    return {"@context": "https://schema.org", "@type": "FAQPage", "inLanguage": lang,
            "mainEntity": [{"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}}
                           for q, a in items]}


def llms_txt(db: Session, origin: str) -> str:
    """https://llmstxt.org — a plain summary AI assistants can read in one request."""
    c = cfg.get_all(db)
    hs = houses(db)
    lo, hi = price_range(hs)
    lines = [
        f"# {c['resort_name_en']} ({c['resort_name_th']})",
        "",
        f"> Beachfront resort of {len(hs)} traditional Thai teak houses on Na Jomtien beach, Pattaya, Chon Buri, Thailand, "
        f"next to Tutu Beach café. Priced per house: {lo:,.0f}–{hi:,.0f} THB per night, breakfast included, pets stay free. "
        "Bookings online or by phone; whole-resort buyouts for groups.",
        "",
        "## Key facts",
        f"- Address: {c['address_en']} (entrance via Na Jomtien Soi 10, second right)",
        f"- GPS: {c['lat']}, {c['lng']}",
        f"- Phone: {c['phone_main']}" + (f", {c['phone_alt']} ({c['phone_alt_label']})" if c["phone_alt"] else ""),
        f"- Facebook: {c['facebook_name']}",
        f"- Check-in {c['checkin_time']}, check-out {c['checkout_time']}",
        f"- Breakfast included: {'yes' if c['breakfast_included'] == '1' else 'no'}; pets allowed: {'yes, free' if c['pets_allowed'] == '1' else 'no'}",
        "- Payment: bank transfer or PromptPay QR; booking confirmed after the slip is verified",
        "- Nearby: Tutu Beach (next door), Tahiti Beach Pattaya, Club Loong Chat (kitesurf, windsurf, SUP), CAVE Beach Club",
        "",
        "## Houses",
    ]
    for h in hs:
        lines.append(f"- [{h.name_en} / {h.name_th}]({origin}/en/houses/{h.slug}): {h.kind.replace('_', ' ')}, sleeps {h.max_guests}, "
                     f"{h.bedrooms} bedroom(s), {h.beds_en}; {float(h.price_weekday):,.0f} THB/night")
    pkg = db.query(BuyoutPackage).filter(BuyoutPackage.active.is_(True)).first()
    if pkg:
        lines += ["", "## Whole-resort buyout",
                  f"- All {len(hs)} houses, up to {pkg.guests} guests: {float(pkg.price):,.0f} THB per night ({origin}/en/buyout)"]
    promos = db.query(Promotion).filter(Promotion.active.is_(True), Promotion.show_on_site.is_(True)).order_by(Promotion.sort).all()
    if promos:
        lines += ["", "## Current offers"] + [f"- {p.name('en')}: {p.detail('en')}" for p in promos]
    lines += ["", "## Pages",
              f"- [Home]({origin}/en): overview and interactive site plan",
              f"- [All houses]({origin}/en/houses)",
              f"- [Live availability]({origin}/en/availability)",
              f"- [Book a house]({origin}/en/book)",
              f"- [FAQ]({origin}/en/faq)",
              f"- [Contact and directions]({origin}/en/contact)",
              f"- Thai version: {origin}/",
              ""]
    return "\n".join(lines)
