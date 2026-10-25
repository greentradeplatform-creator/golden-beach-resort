"""Key/value resort settings with defaults. Editable in admin > Settings."""
from sqlalchemy.orm import Session

from ..models import Setting

DEFAULTS: dict[str, str] = {
    "resort_name_th": "โกลเด้น บีช รีสอร์ท",
    "resort_name_en": "Golden Beach Resort",
    "address_th": "888 หมู่ 2 ต.นาจอมเทียน อ.สัตหีบ จ.ชลบุรี 20250",
    "address_en": "888 Moo 2, Na Chom Thian, Sattahip, Chonburi 20250",
    "directions_th": "จากถนนนาจอมเทียน เข้าซอย 10 ตรงไปแล้วเลี้ยวขวาเข้าซอยที่ 2 ทางเข้ารีสอร์ทอยู่ซ้ายมือ ถัดจากลานจอดรถของรีสอร์ท",
    "directions_en": "From Na Jomtien Road turn into Soi 10, then take the second right. The resort entrance is on the left, just past the resort car park.",
    "lat": "12.846639",
    "lng": "100.903056",
    "phone_main": "092-966-0739",
    "phone_main_label": "รีสอร์ท",
    "phone_alt": "086-896-8360",
    "phone_alt_label": "คุณฝน",
    "email": "",
    "facebook_url": "https://www.facebook.com/search/top?q=Golden%20Beach%20Resort%20%E0%B8%9E%E0%B8%B1%E0%B8%97%E0%B8%A2%E0%B8%B2",
    "facebook_name": "Golden Beach Resort พัทยา",
    "line_oa_id": "",
    "bank_name": "ธนาคารกสิกรไทย",
    "bank_account_no": "204-3-08847-8",
    "bank_account_name": "บริษัท โกลเด้น บีช รีสอร์ท จำกัด",
    "promptpay_id": "",            # phone (10 digits) or tax id (13 digits)
    "checkin_time": "14:00",
    "checkout_time": "12:00",
    "deposit_percent": "100",
    "hold_hours": "24",
    "booking_mode": "approval",    # approval (mode B) / instant (mode A)
    "breakfast_included": "1",
    "pets_allowed": "1",
    "house_rules_th": "ปิ้งย่างได้ นำวัตถุดิบมาเอง (รีสอร์ทไม่มีจำหน่าย)\nสัตว์เลี้ยงเข้าพักได้ฟรี กรุณาแจ้งล่วงหน้า\nงดส่งเสียงดังหลัง 22:00 ยกเว้นแพ็กเกจเหมาที่มีคาราโอเกะ\nห้ามสูบบุหรี่ภายในบ้านพัก",
    "house_rules_en": "BBQ is welcome; bring your own ingredients (none sold on site)\nPets stay free, please tell us in advance\nQuiet after 22:00 unless you booked the whole resort with karaoke\nNo smoking inside the houses",
    "cancel_policy_th": "ยกเลิกก่อนเข้าพัก 7 วันขึ้นไป เลื่อนวันเข้าพักได้ 1 ครั้ง\nยกเลิกภายใน 7 วันก่อนเข้าพัก ขอสงวนสิทธิ์ไม่คืนเงิน",
    "cancel_policy_en": "Cancel 7 or more days before arrival to move your dates once.\nCancellations within 7 days of arrival are non-refundable.",
    "line_channel_token": "",
    "line_group_id": "",
    "line_channel_secret": "",     # lets /line/webhook pick up the group ID by itself
    "smtp_host": "", "smtp_port": "587", "smtp_user": "", "smtp_password": "", "smtp_from": "",
    "notify_email": "",
    "brevo_api_key": "",           # e-mail over HTTPS; use this on Render free (SMTP ports are blocked)
    "google_site_verification": "",
    "bing_site_verification": "",
    "ga4_id": "",
    # ---- automation (admin > Settings > ระบบอัตโนมัติ); "1" = on
    "auto_reject_overlaps": "1",     # approving one request turns down overlapping ones + offers other houses
    "auto_payment_reminder": "1",    # remind the guest before the payment hold runs out
    "auto_prearrival": "1",          # e-mail directions / check-in time the day before arrival
    "auto_checkout": "1",            # close stays after check-out time and mark the house dirty
    "auto_review_request": "1",      # thank-you e-mail with the review link after check-out
    "auto_inquiry_reply": "1",       # instant e-mail reply to contact / buyout forms
    "auto_daily_summary": "1",       # morning summary to the LINE group and the bell
    "daily_summary_time": "07:30",
    "auto_nudge": "1",               # chase requests / slips nobody has handled
    "nudge_minutes": "30",
    "auto_payout": "1",              # draft commission payouts on the 1st of each month
    "google_review_url": "",
    # ---- online payment gateway (Opn Payments, formerly Omise)
    "online_payment": "0",
    "opn_public_key": "",
    "opn_secret_key": "",
    "opn_methods": "promptpay,mobile_banking_kbank,mobile_banking_scb,mobile_banking_bay,mobile_banking_bbl,mobile_banking_ktb",
    "opn_cards": "1",
}

SECRET_KEYS = ("smtp_password", "line_channel_token", "opn_secret_key", "brevo_api_key", "line_channel_secret")


def on(db: Session, key: str) -> bool:
    return get(db, key) == "1"


def get_all(db: Session) -> dict[str, str]:
    data = dict(DEFAULTS)
    for row in db.query(Setting).all():
        data[row.key] = row.value
    return data


def get(db: Session, key: str) -> str:
    row = db.get(Setting, key)
    return row.value if row else DEFAULTS.get(key, "")


def set_many(db: Session, values: dict[str, str]) -> None:
    for k, v in values.items():
        if k not in DEFAULTS:
            continue
        if k in SECRET_KEYS and v == "":
            continue                     # blank password box = keep the saved secret
        if k in SECRET_KEYS and v.strip() == "-":
            v = ""                       # type "-" to remove it
        row = db.get(Setting, k)
        if row:
            row.value = v
        else:
            db.add(Setting(key=k, value=v))
    db.commit()
