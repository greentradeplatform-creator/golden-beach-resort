"""Online payments through Opn Payments (formerly Omise), Thailand.

Turn on in admin > Settings > รับชำระออนไลน์ after opening a merchant account at
https://www.opn.ooo and copying the public and secret keys (test keys work too).

* PromptPay: we create the charge on the server and show Opn's QR; the guest pays from
  any bank app and the booking confirms itself — no slip to check.
* Mobile banking / cards: Opn's own payment form (Omise.js) collects the card or bank
  choice, we create the charge and send the guest to their bank / 3-D Secure page.
* Money is only trusted after we ask Opn ourselves (GET /charges/{id}) and it says
  "successful". The webhook and the booking page both trigger that check, so the
  booking confirms even if the webhook is not set up.
"""
from __future__ import annotations

import base64
import json
import logging
import urllib.error
import urllib.parse
import urllib.request

from sqlalchemy.orm import Session

from ..models import Booking, OnlineCharge
from . import settings as cfg

log = logging.getLogger("opn")
API = "https://api.omise.co"
MIN_THB, MAX_THB = 20, 150_000

METHOD_TH = {
    "promptpay": "พร้อมเพย์ (QR)", "card": "บัตรเครดิต/เดบิต", "truemoney": "TrueMoney",
    "mobile_banking_kbank": "K PLUS", "mobile_banking_scb": "SCB Easy", "mobile_banking_bay": "KMA กรุงศรี",
    "mobile_banking_bbl": "Bualuang mBanking", "mobile_banking_ktb": "Krungthai NEXT",
}


class OpnError(Exception):
    pass


def enabled(db: Session) -> bool:
    c = cfg.get_all(db)
    return c["online_payment"] == "1" and c["opn_public_key"].startswith("pkey_") and c["opn_secret_key"].startswith("skey_")


def public_config(db: Session) -> dict:
    c = cfg.get_all(db)
    methods = [m.strip() for m in c["opn_methods"].split(",") if m.strip()]
    return {"public_key": c["opn_public_key"], "cards": c["opn_cards"] == "1",
            "methods": methods, "promptpay": "promptpay" in methods,
            "other": [m for m in methods if m != "promptpay"]}


def _api(db: Session, method: str, path: str, data: dict | None = None) -> dict:
    """Call the Opn REST API with the secret key (HTTP basic auth, key as user name)."""
    key = cfg.get(db, "opn_secret_key")
    body = urllib.parse.urlencode(data, doseq=True).encode() if data is not None else None
    req = urllib.request.Request(API + path, data=body, method=method, headers={
        "Authorization": "Basic " + base64.b64encode(f"{key}:".encode()).decode(),
        "Content-Type": "application/x-www-form-urlencoded", "User-Agent": "GoldenBeachResort/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read().decode())
            raise OpnError(err.get("message") or err.get("code") or str(e)) from None
        except ValueError:
            raise OpnError(str(e)) from None
    except (urllib.error.URLError, TimeoutError) as e:
        raise OpnError(f"ติดต่อระบบชำระเงินไม่ได้: {e}") from None


def amount_due(b: Booking) -> float:
    return round(max(0.0, float(b.deposit_due) - float(b.amount_paid)), 2)


def create_charge(db: Session, b: Booking, *, method: str, token: str = "", source: str = "",
                  return_uri: str = "") -> OnlineCharge:
    """method 'promptpay' makes the QR here; otherwise pass a card token (tokn_) or a
    source (src_) that Omise.js created in the guest's browser."""
    due = amount_due(b)
    if b.status not in ("awaiting_payment", "slip_uploaded"):
        raise OpnError("การจองนี้ยังไม่อยู่ในขั้นชำระเงิน")
    if due < MIN_THB:
        raise OpnError("ยอดชำระน้อยกว่าขั้นต่ำของระบบชำระออนไลน์")
    if due > MAX_THB:
        raise OpnError("ยอดเกินวงเงินชำระออนไลน์ กรุณาโอนเข้าบัญชี")
    data = {"amount": int(round(due * 100)), "currency": "THB",
            "description": f"{b.code} {b.house.name_en}", "metadata[booking]": b.code}
    if token:
        data["card"] = token
        method = "card"
    elif source:
        data["source"] = source
    elif method == "promptpay":
        data["source[type]"] = "promptpay"
    else:
        raise OpnError("ไม่รู้จักช่องทางชำระเงินนี้")
    if return_uri:
        data["return_uri"] = return_uri
    ch = _api(db, "POST", "/charges", data)
    src = ch.get("source") or {}
    if source and src.get("type"):
        method = src["type"]
    qr = (((src.get("scannable_code") or {}).get("image") or {}).get("download_uri")) or ""
    row = OnlineCharge(booking_id=b.id, charge_id=ch["id"], method=method, amount=due,
                       status=ch.get("status", "pending"), qr_url=qr, authorize_uri=ch.get("authorize_uri") or "",
                       failure=ch.get("failure_message") or "")
    db.add(row)
    db.flush()
    if row.status == "successful":
        settle(db, row, ch)
    return row


def refresh(db: Session, row: OnlineCharge) -> OnlineCharge:
    """Ask Opn for the latest state of a charge and apply it."""
    if row.status in ("successful", "failed", "expired", "reversed"):
        return row
    ch = _api(db, "GET", f"/charges/{row.charge_id}")
    if ch.get("object") != "charge" or ch.get("id") != row.charge_id:
        raise OpnError("ข้อมูลจากระบบชำระเงินไม่ตรงกัน")
    row.status = ch.get("status", row.status)
    row.failure = ch.get("failure_message") or row.failure
    if row.status == "successful":
        settle(db, row, ch)
    return row


def settle(db: Session, row: OnlineCharge, ch: dict) -> None:
    """Record the money once and let the booking confirm itself."""
    from . import bookings as svc
    if row.payment_id:
        return
    if not ch.get("paid", ch.get("status") == "successful"):
        return
    if (ch.get("currency") or "THB").upper() != "THB":
        return
    amount = int(ch.get("amount") or 0) / 100
    b = db.get(Booking, row.booking_id)
    p_method = "card" if row.method == "card" else ("promptpay" if row.method == "promptpay" else "online")
    svc.record_payment(db, b, amount, p_method, None)
    p = b.payments[-1] if b.payments else None
    if p:
        p.note = f"ชำระออนไลน์ผ่าน Opn ({METHOD_TH.get(row.method, row.method)}) charge {row.charge_id}"
        row.payment_id = p.id
    svc.audit(db, "online_paid", f"Opn {row.charge_id} {amount:,.0f} บาท ยืนยันอัตโนมัติ", None, b)


def handle_webhook(db: Session, payload: dict) -> str:
    """Opn posts events here. Never trust the body: re-fetch the charge with our key."""
    data = payload.get("data") or {}
    charge_id = data.get("id", "") if data.get("object") == "charge" else ""
    if not charge_id.startswith("chrg_"):
        return "ignored"
    row = db.query(OnlineCharge).filter(OnlineCharge.charge_id == charge_id).first()
    if not row:
        return "unknown charge"
    refresh(db, row)
    db.commit()
    return row.status
