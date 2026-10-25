"""Nightly pricing + promotion engine.

Everything the resort changes often (prices, holiday rates, promotions) lives in
the database, so this module only encodes the rules for combining them.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import date

from sqlalchemy.orm import Session

from ..models import Addon, Booking, Customer, House, Promotion, RatePeriod
from ..timeutil import nights_between, today
from . import settings as cfg

WEEKEND_NIGHTS = (4, 5)  # Friday and Saturday nights


@dataclass
class NightPrice:
    night: date
    price: float
    weekend: bool
    label: str = ""


@dataclass
class Line:
    name: str
    amount: float
    ref: int | None = None
    qty: int = 1
    unit_price: float = 0


@dataclass
class Quote:
    house_id: int
    check_in: date
    check_out: date
    guests: int
    nights: list[NightPrice] = field(default_factory=list)
    room_total: float = 0
    extra_guest_total: float = 0
    addons: list[Line] = field(default_factory=list)
    addons_total: float = 0
    discounts: list[Line] = field(default_factory=list)
    discount_total: float = 0
    gifts: list[str] = field(default_factory=list)
    total: float = 0
    deposit_due: float = 0
    errors: list[str] = field(default_factory=list)

    @property
    def n(self) -> int:
        return len(self.nights)

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_json(self) -> dict:
        d = asdict(self)
        for k in ("check_in", "check_out"):
            d[k] = d[k].isoformat()
        for nn in d["nights"]:
            nn["night"] = nn["night"].isoformat()
        return d


def _periods(db: Session, start: date, end: date) -> list[RatePeriod]:
    return db.query(RatePeriod).filter(
        RatePeriod.start_date <= end, RatePeriod.end_date >= start).all()


def nightly_prices(db: Session, house: House, check_in: date, check_out: date,
                   periods: list[RatePeriod] | None = None) -> list[NightPrice]:
    nights = nights_between(check_in, check_out)
    if not nights:
        return []
    if periods is None:
        periods = _periods(db, nights[0], nights[-1])
    out = []
    for d in nights:
        weekend = d.weekday() in WEEKEND_NIGHTS
        price = float(house.price_weekend if weekend else house.price_weekday)
        label = ""
        # most specific period wins: this house > this kind > all houses
        best, rank = None, -1
        for p in periods:
            if not (p.start_date <= d <= p.end_date):
                continue
            if p.house_id and p.house_id != house.id:
                continue
            if p.house_kind and p.house_kind != house.kind:
                continue
            r = 2 if p.house_id else (1 if p.house_kind else 0)
            if r > rank:
                best, rank = p, r
            if p.is_holiday and not label:
                label = p.name
        if best is not None:
            if best.price is not None:
                price = float(best.price)
            elif best.adjust_amount is not None:
                price = max(0.0, price + float(best.adjust_amount))
            label = label or best.name
        out.append(NightPrice(d, price, weekend, label))
    return out


def _in_window(p: Promotion, d: date) -> bool:
    if p.start_date and d < p.start_date:
        return False
    if p.end_date and d > p.end_date:
        return False
    return True


def completed_stays(db: Session, phone: str, channel: str | None, p: Promotion) -> int:
    q = db.query(Booking).join(Customer).filter(
        Customer.phone == phone, Booking.status == "completed")
    if channel and p.same_channel:
        q = q.filter(Booking.channel == channel)
    if p.start_date:
        q = q.filter(Booking.check_in >= p.start_date)
    if p.end_date:
        q = q.filter(Booking.check_in <= p.end_date)
    return q.count()


def applicable_promotions(db: Session) -> list[Promotion]:
    return db.query(Promotion).filter(Promotion.active.is_(True)).order_by(Promotion.sort).all()


def quote(db: Session, house: House, check_in: date, check_out: date, adults: int = 2,
          children: int = 0, addon_qty: dict[int, int] | None = None, phone: str = "",
          channel: str = "website", booked_on: date | None = None,
          lang: str = "th") -> Quote:
    booked_on = booked_on or today()
    guests = max(0, adults) + max(0, children)
    q = Quote(house.id, check_in, check_out, guests)

    if check_out <= check_in:
        q.errors.append("วันเช็คเอาท์ต้องอยู่หลังวันเช็คอิน" if lang == "th"
                        else "Check-out must be after check-in")
        return q
    if guests > house.max_guests:
        q.errors.append(
            f"{house.name(lang)} พักได้สูงสุด {house.max_guests} ท่าน" if lang == "th"
            else f"{house.name(lang)} sleeps up to {house.max_guests}")

    q.nights = nightly_prices(db, house, check_in, check_out)
    q.room_total = sum(n.price for n in q.nights)
    extra = max(0, guests - house.std_guests)
    q.extra_guest_total = extra * float(house.extra_guest_fee or 0) * q.n

    # add-ons
    for addon_id, qty in (addon_qty or {}).items():
        if qty <= 0:
            continue
        a = db.get(Addon, addon_id)
        if not a or not a.active:
            continue
        mult = q.n if a.unit == "night" else (guests if a.unit == "person" else 1)
        amt = float(a.price) * qty * mult
        q.addons.append(Line(a.name(lang), amt, a.id, qty, float(a.price)))
    q.addons_total = sum(l.amount for l in q.addons)

    # promotions
    stack: list[Line] = []
    solo: list[Line] = []
    for p in applicable_promotions(db):
        kinds = [k for k in p.house_kinds.split(",") if k]
        if kinds and house.kind not in kinds:
            continue
        if q.n < (p.min_nights or 1):
            continue
        if p.window_on == "booking" and not _in_window(p, booked_on):
            continue
        stay_ok = p.window_on == "booking" or _in_window(p, check_in)
        amount = 0.0

        if p.kind == "weekday_discount":
            days = {int(x) for x in p.weekdays.split(",") if x.strip().isdigit()}
            for n in q.nights:
                if n.night.weekday() in days and (p.window_on == "booking" or _in_window(p, n.night)):
                    amount += min(float(p.amount), n.price)
        elif p.kind == "nth_night_discount" and stay_ok:
            if p.nth and q.n >= p.nth:
                amount = min(float(p.amount), q.nights[p.nth - 1].price)
        elif p.kind == "percent_discount" and stay_ok:
            amount = q.room_total * float(p.percent) / 100
        elif p.kind == "loyalty" and stay_ok and phone and p.nth:
            done = completed_stays(db, phone, channel, p)
            if (done + 1) % p.nth == 0:
                amount = q.room_total * float(p.percent) / 100
        elif p.kind == "gift" and stay_ok:
            if p.gift(lang):
                q.gifts.append(p.gift(lang))
            continue

        if amount > 0:
            line = Line(p.name(lang), round(amount, 2), p.id)
            (stack if p.stackable else solo).append(line)

    stack_sum = sum(l.amount for l in stack)
    best_solo = max(solo, key=lambda l: l.amount, default=None)
    if best_solo and best_solo.amount > stack_sum:
        q.discounts = [best_solo]
    else:
        q.discounts = stack
    q.discount_total = min(sum(l.amount for l in q.discounts), q.room_total)

    q.total = round(q.room_total + q.extra_guest_total + q.addons_total - q.discount_total, 2)
    pct = float(cfg.get(db, "deposit_percent") or 100)
    q.deposit_due = round(q.total * pct / 100)
    return q


def from_price(db: Session, house: House) -> float:
    return float(min(house.price_weekday, house.price_weekend))
