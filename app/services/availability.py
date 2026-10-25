"""Availability, conflicts and the night-lock double-booking guard."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import and_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import Block, Booking, HOLDING, House, NightLock
from ..timeutil import nights_between


class ConflictError(Exception):
    def __init__(self, conflicts: list["Conflict"]):
        super().__init__("dates not available")
        self.conflicts = conflicts


@dataclass
class Conflict:
    kind: str          # booking / block / pending
    start: date
    end: date          # exclusive
    status: str = ""
    channel: str = ""
    code: str = ""
    booking_id: int | None = None
    reason: str = ""
    block_id: int | None = None

    @property
    def hard(self) -> bool:
        return self.kind != "pending"


def conflicts(db: Session, house_id: int, check_in: date, check_out: date,
              exclude_booking_id: int | None = None, include_pending: bool = True) -> list[Conflict]:
    out: list[Conflict] = []
    statuses = set(HOLDING) | ({"pending"} if include_pending else set())
    q = db.query(Booking).filter(
        Booking.house_id == house_id,
        Booking.status.in_(statuses),
        Booking.check_in < check_out,
        Booking.check_out > check_in,
    )
    if exclude_booking_id:
        q = q.filter(Booking.id != exclude_booking_id)
    for b in q.order_by(Booking.check_in):
        out.append(Conflict("pending" if b.status == "pending" else "booking",
                            b.check_in, b.check_out, b.status, b.channel, b.code, b.id))
    for bl in db.query(Block).filter(
            Block.house_id == house_id, Block.start_date < check_out, Block.end_date > check_in):
        out.append(Conflict("block", bl.start_date, bl.end_date, "blocked", bl.source, reason=bl.reason, block_id=bl.id))
    return out


def is_free(db: Session, house_id: int, check_in: date, check_out: date,
            exclude_booking_id: int | None = None) -> bool:
    return not any(c.hard for c in conflicts(db, house_id, check_in, check_out, exclude_booking_id, False))


def next_free_date(db: Session, house_id: int, start: date, horizon: int = 180) -> date | None:
    """First night on/after `start` that nobody holds."""
    taken = {r.night for r in db.query(NightLock.night).filter(
        NightLock.house_id == house_id, NightLock.night >= start,
        NightLock.night < start + timedelta(days=horizon))}
    for i in range(horizon):
        d = start + timedelta(days=i)
        if d not in taken:
            return d
    return None


def alternatives(db: Session, check_in: date, check_out: date, guests: int = 1,
                 exclude_house_id: int | None = None) -> list[House]:
    houses = db.query(House).filter(House.is_active.is_(True)).order_by(House.side, House.position).all()
    return [h for h in houses
            if h.id != exclude_house_id and h.max_guests >= guests
            and is_free(db, h.id, check_in, check_out)]


def _insert_locks(db: Session, house_id: int, nights: list[date], booking_id=None, block_id=None):
    try:
        with db.begin_nested():
            for d in nights:
                db.add(NightLock(house_id=house_id, night=d, booking_id=booking_id, block_id=block_id))
            db.flush()
    except IntegrityError:
        raise ConflictError([])


def lock_booking(db: Session, b: Booking) -> None:
    hard = [c for c in conflicts(db, b.house_id, b.check_in, b.check_out, b.id, False) if c.hard]
    if hard:
        raise ConflictError(hard)
    existing = {r.night for r in db.query(NightLock.night).filter(NightLock.booking_id == b.id)}
    need = [d for d in nights_between(b.check_in, b.check_out) if d not in existing]
    if need:
        try:
            _insert_locks(db, b.house_id, need, booking_id=b.id)
        except ConflictError:
            raise ConflictError(conflicts(db, b.house_id, b.check_in, b.check_out, b.id, False))


def release_booking(db: Session, booking_id: int) -> None:
    db.query(NightLock).filter(NightLock.booking_id == booking_id).delete()


def lock_block(db: Session, bl: Block) -> None:
    hard = [c for c in conflicts(db, bl.house_id, bl.start_date, bl.end_date, None, False)
            if c.hard and c.block_id != bl.id]
    if hard:
        raise ConflictError(hard)
    _insert_locks(db, bl.house_id, nights_between(bl.start_date, bl.end_date), block_id=bl.id)


def month_grid(db: Session, start: date, days: int, include_pending: bool = True):
    """Return {house_id: {date: cell}} where cell = dict(status, code, booking_id, first)."""
    end = start + timedelta(days=days)
    grid: dict[int, dict[date, dict]] = {}
    statuses = set(HOLDING) | {"pending"} if include_pending else set(HOLDING)
    q = db.query(Booking).filter(
        Booking.status.in_(statuses), Booking.check_in < end, Booking.check_out > start)
    # paint pending first so holding bookings overwrite them
    for b in sorted(q.all(), key=lambda x: 0 if x.status == "pending" else 1):
        st = {"awaiting_payment": "hold", "slip_uploaded": "hold", "confirmed": "paid",
              "checked_in": "inhouse", "pending": "pending"}[b.status]
        for d in nights_between(max(b.check_in, start), min(b.check_out, end)):
            cell = grid.setdefault(b.house_id, {}).get(d)
            if cell and cell["status"] != "pending" and st == "pending":
                continue
            grid[b.house_id][d] = {"status": st, "code": b.code, "booking_id": b.id,
                                   "first": d == b.check_in, "name": b.customer.name if b.customer else "",
                                   "channel": b.channel}
    for bl in db.query(Block).filter(Block.start_date < end, Block.end_date > start):
        for d in nights_between(max(bl.start_date, start), min(bl.end_date, end)):
            grid.setdefault(bl.house_id, {})[d] = {"status": "blocked", "code": "", "booking_id": None,
                                                    "first": d == bl.start_date, "name": bl.reason,
                                                    "channel": bl.source, "block_id": bl.id}
    return grid


def free_houses_for(db: Session, check_in: date, check_out: date) -> set[int]:
    return {h.id for h in db.query(House).filter(House.is_active.is_(True))
            if is_free(db, h.id, check_in, check_out)}
