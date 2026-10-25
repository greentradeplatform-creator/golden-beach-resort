"""Revenue split between the resort and the admin who closed the sale."""
from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models import Booking, CommissionRule, Payout, User, WalletEntry


def rule_for(db: Session, channel: str) -> CommissionRule | None:
    return db.query(CommissionRule).filter(CommissionRule.channel == channel).first()


def compute(db: Session, b: Booking) -> tuple[float, float, float]:
    """Return (platform_fee, commission, resort_revenue) for a booking."""
    total = float(b.total or 0)
    r = rule_for(db, b.channel)
    if not r:
        return 0.0, 0.0, total
    fee = round(total * float(r.platform_fee_percent or 0) / 100, 2)
    net = total - fee
    commission = 0.0
    agent = b.agent
    if r.mode != "none" and not r.to_pool and agent and agent.earns_commission and agent.active:
        if r.mode == "percent":
            commission = round(net * float(r.percent or 0) / 100, 2)
        elif r.mode == "fixed":
            commission = float(r.fixed_big if b.house.is_big else r.fixed_small) * max(1, b.nights)
    return fee, commission, round(net - commission, 2)


def post_commission(db: Session, b: Booking) -> WalletEntry | None:
    """Called once when payment is confirmed. Idempotent per booking."""
    fee, commission, resort = compute(db, b)
    b.platform_fee, b.commission_amount, b.resort_revenue = fee, commission, resort
    already = db.query(func.coalesce(func.sum(WalletEntry.amount), 0)).filter(
        WalletEntry.booking_id == b.id).scalar()
    if commission > 0 and b.agent_id and float(already or 0) == 0:
        e = WalletEntry(user_id=b.agent_id, booking_id=b.id, amount=commission, kind="commission",
                        note=f"{b.code} {b.house.name_th} ({b.channel})")
        db.add(e)
        return e
    return None


def reverse_commission(db: Session, b: Booking, reason: str = "") -> WalletEntry | None:
    net = db.query(func.coalesce(func.sum(WalletEntry.amount), 0)).filter(
        WalletEntry.booking_id == b.id).scalar()
    net = float(net or 0)
    if net > 0 and b.agent_id:
        e = WalletEntry(user_id=b.agent_id, booking_id=b.id, amount=-net, kind="reversal",
                        note=f"ยกเลิก {b.code} {reason}".strip())
        db.add(e)
        b.commission_amount = 0
        return e
    return None


def balance(db: Session, user_id: int, unpaid_only: bool = True) -> float:
    q = db.query(func.coalesce(func.sum(WalletEntry.amount), 0)).filter(WalletEntry.user_id == user_id)
    if unpaid_only:
        q = q.filter(WalletEntry.payout_id.is_(None))
    return float(q.scalar() or 0)


def create_payout(db: Session, user: User, created_by: User, note: str = "") -> Payout | None:
    entries = db.query(WalletEntry).filter(
        WalletEntry.user_id == user.id, WalletEntry.payout_id.is_(None)).all()
    amount = round(sum(float(e.amount) for e in entries), 2)
    if not entries or amount <= 0:
        return None
    p = Payout(user_id=user.id, amount=amount, created_by_id=created_by.id if created_by else None, note=note)
    db.add(p)
    db.flush()
    for e in entries:
        e.payout_id = p.id
    return p
