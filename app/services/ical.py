"""Two-way calendar sync with Airbnb / Agoda via iCal (.ics)."""
from __future__ import annotations

import re
import urllib.request
from datetime import date, datetime, timezone

from sqlalchemy.orm import Session

from ..models import Block, Booking, HOLDING, House
from . import availability as av
from . import notify


def export(db: Session, house: House) -> str:
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Golden Beach Resort//Booking//TH",
             "CALSCALE:GREGORIAN", f"X-WR-CALNAME:{house.name_en}"]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    for b in db.query(Booking).filter(Booking.house_id == house.id, Booking.status.in_(HOLDING)):
        lines += ["BEGIN:VEVENT", f"UID:{b.code}@goldenbeach", f"DTSTAMP:{stamp}",
                  f"DTSTART;VALUE=DATE:{b.check_in:%Y%m%d}", f"DTEND;VALUE=DATE:{b.check_out:%Y%m%d}",
                  "SUMMARY:Reserved", "END:VEVENT"]
    for bl in db.query(Block).filter(Block.house_id == house.id, Block.source != "ical"):
        lines += ["BEGIN:VEVENT", f"UID:block-{bl.id}@goldenbeach", f"DTSTAMP:{stamp}",
                  f"DTSTART;VALUE=DATE:{bl.start_date:%Y%m%d}", f"DTEND;VALUE=DATE:{bl.end_date:%Y%m%d}",
                  "SUMMARY:Not available", "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def parse(text: str) -> list[tuple[str, date, date, str]]:
    text = re.sub(r"\r?\n[ \t]", "", text)  # unfold
    events = []
    for block in re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", text, re.S):
        def val(key):
            m = re.search(rf"^{key}[^:\n]*:(.+)$", block, re.M)
            return m.group(1).strip() if m else ""
        try:
            s = datetime.strptime(val("DTSTART")[:8], "%Y%m%d").date()
            e = datetime.strptime(val("DTEND")[:8], "%Y%m%d").date()
        except ValueError:
            continue
        events.append((val("UID") or f"{s}-{e}", s, e, val("SUMMARY")))
    return events


def sync(db: Session, house: House, text: str | None = None) -> dict:
    if text is None:
        if not house.ical_import_url:
            return {"added": 0, "removed": 0, "conflicts": 0}
        with urllib.request.urlopen(house.ical_import_url, timeout=20) as r:
            text = r.read().decode("utf-8", "ignore")
    events = parse(text)
    seen = set()
    added = conflicts = 0
    existing = {bl.external_uid: bl for bl in db.query(Block).filter(
        Block.house_id == house.id, Block.source == "ical")}
    for uid, s, e, summary in events:
        seen.add(uid)
        bl = existing.get(uid)
        if bl and (bl.start_date, bl.end_date) == (s, e):
            continue
        if bl:  # dates changed on Airbnb: drop the old block (its night locks cascade)
            db.delete(bl)
            db.flush()
        nb = Block(house_id=house.id, start_date=s, end_date=e, reason=f"Airbnb: {summary or 'Reserved'}",
                   source="ical", external_uid=uid)
        db.add(nb)
        db.flush()
        try:
            av.lock_block(db, nb)
            added += 1
        except av.ConflictError:
            conflicts += 1
            db.delete(nb)
            notify.bell(db, f"ปฏิทิน Airbnb ชนกับการจอง: {house.name_th}",
                        f"{s} ถึง {e} มีการจองในระบบอยู่แล้ว กรุณาตรวจสอบ", "/admin/calendar", "warn")
    removed = 0
    for uid, bl in existing.items():
        if uid not in seen:
            db.delete(bl)
            removed += 1
    db.commit()
    return {"added": added, "removed": removed, "conflicts": conflicts}
