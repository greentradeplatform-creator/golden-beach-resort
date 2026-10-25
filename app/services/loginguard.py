"""Slow down password guessing on /admin/login.

5 wrong passwords for a username, or 20 from one IP address, within 15 minutes
lock that username / address for 15 minutes. A correct sign-in clears the count.
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy.orm import Session

from ..models import LoginGuard
from ..timeutil import now

WINDOW = timedelta(minutes=15)
LOCK = timedelta(minutes=15)
LIMITS = {"user": 5, "ip": 20}


def _keys(username: str, ip: str) -> list[tuple[str, str]]:
    return [("user", f"user:{username.strip().lower()[:100]}"), ("ip", f"ip:{ip[:100]}")]


def locked_minutes(db: Session, username: str, ip: str) -> int:
    t = now()
    for _, key in _keys(username, ip):
        g = db.query(LoginGuard).filter(LoginGuard.key == key).first()
        if g and g.locked_until and g.locked_until > t:
            return max(1, int((g.locked_until - t).total_seconds() // 60) + 1)
    return 0


def failed(db: Session, username: str, ip: str) -> int:
    """Record a wrong password. Returns attempts left for this username (0 = now locked)."""
    t = now()
    left = LIMITS["user"]
    for kind, key in _keys(username, ip):
        g = db.query(LoginGuard).filter(LoginGuard.key == key).first()
        if not g:
            g = LoginGuard(key=key, fails=0, first_at=t)
            db.add(g)
        if t - (g.first_at or t) > WINDOW:
            g.fails, g.first_at, g.locked_until = 0, t, None
        g.fails += 1
        if g.fails >= LIMITS[kind]:
            g.locked_until = t + LOCK
            g.fails, g.first_at = 0, t
            if kind == "user":
                left = 0
        elif kind == "user":
            left = LIMITS[kind] - g.fails
    db.commit()
    return left


def succeeded(db: Session, username: str, ip: str) -> None:
    for _, key in _keys(username, ip):
        db.query(LoginGuard).filter(LoginGuard.key == key).delete()
