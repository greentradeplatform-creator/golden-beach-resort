from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean, Date, DateTime, ForeignKey, Integer, LargeBinary, Numeric, String, Text,
    UniqueConstraint, Index,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base
from .timeutil import now


Money = Numeric(12, 2)

HOUSE_KINDS = ("big_front", "big", "small")
CHANNELS = ("website", "facebook", "line", "airbnb", "phone", "other")
ROLES = ("owner", "manager", "sales", "housekeeping")

# Statuses that hold the house (rows in night_locks)
HOLDING = {"awaiting_payment", "slip_uploaded", "confirmed", "checked_in"}
BOOKING_STATUSES = (
    "pending", "awaiting_payment", "slip_uploaded", "confirmed", "checked_in",
    "completed", "cancelled", "rejected", "expired", "no_show",
)


class House(Base):
    __tablename__ = "houses"
    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(60), unique=True)
    name_th: Mapped[str] = mapped_column(String(80))
    name_en: Mapped[str] = mapped_column(String(80))
    kind: Mapped[str] = mapped_column(String(20))           # big_front / big / small
    side: Mapped[str] = mapped_column(String(1), default="L")  # L / R of the path
    position: Mapped[int] = mapped_column(Integer, default=1)  # 1 = nearest the sea
    std_guests: Mapped[int] = mapped_column(Integer, default=2)
    max_guests: Mapped[int] = mapped_column(Integer, default=2)
    bedrooms: Mapped[int] = mapped_column(Integer, default=1)
    bathrooms: Mapped[int] = mapped_column(Integer, default=1)
    beds_th: Mapped[str] = mapped_column(String(120), default="")
    beds_en: Mapped[str] = mapped_column(String(120), default="")
    price_weekday: Mapped[float] = mapped_column(Money, default=0)
    price_weekend: Mapped[float] = mapped_column(Money, default=0)
    extra_guest_fee: Mapped[float] = mapped_column(Money, default=0)
    description_th: Mapped[str] = mapped_column(Text, default="")
    description_en: Mapped[str] = mapped_column(Text, default="")
    amenities: Mapped[str] = mapped_column(Text, default="")  # comma separated keys
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    housekeeping: Mapped[str] = mapped_column(String(20), default="ready")  # ready/dirty/cleaning
    ical_import_url: Mapped[str] = mapped_column(Text, default="")
    ical_token: Mapped[str] = mapped_column(String(40), default="")

    photos: Mapped[list["Photo"]] = relationship(
        back_populates="house", order_by="Photo.sort", cascade="all, delete-orphan")

    def name(self, lang: str) -> str:
        return self.name_en if lang == "en" else self.name_th

    @property
    def is_big(self) -> bool:
        return self.kind in ("big_front", "big")

    @property
    def cover(self) -> str:
        return self.photos[0].path if self.photos else "/static/img/row-houses-m.jpg"


class Photo(Base):
    __tablename__ = "photos"
    id: Mapped[int] = mapped_column(primary_key=True)
    house_id: Mapped[int | None] = mapped_column(ForeignKey("houses.id", ondelete="CASCADE"), nullable=True)
    path: Mapped[str] = mapped_column(String(255))
    tag: Mapped[str] = mapped_column(String(30), default="exterior")
    caption_th: Mapped[str] = mapped_column(String(160), default="")
    caption_en: Mapped[str] = mapped_column(String(160), default="")
    sort: Mapped[int] = mapped_column(Integer, default=0)
    house: Mapped[House | None] = relationship(back_populates="photos")


class RatePeriod(Base):
    """Price override for a date range. Inclusive dates (nights)."""
    __tablename__ = "rate_periods"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    house_kind: Mapped[str] = mapped_column(String(20), default="")   # "" = all kinds
    house_id: Mapped[int | None] = mapped_column(ForeignKey("houses.id", ondelete="CASCADE"), nullable=True)
    price: Mapped[float | None] = mapped_column(Money, nullable=True)        # fixed nightly price
    adjust_amount: Mapped[float | None] = mapped_column(Money, nullable=True)  # +/- on base
    is_holiday: Mapped[bool] = mapped_column(Boolean, default=False)


class Customer(Base):
    __tablename__ = "customers"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    phone: Mapped[str] = mapped_column(String(20), index=True)
    email: Mapped[str] = mapped_column(String(160), default="")
    line_id: Mapped[str] = mapped_column(String(80), default="")
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    bookings: Mapped[list["Booking"]] = relationship(back_populates="customer")


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(60), unique=True)
    password_hash: Mapped[str] = mapped_column(String(200))
    display_name: Mapped[str] = mapped_column(String(80))
    role: Mapped[str] = mapped_column(String(20), default="sales")
    phone: Mapped[str] = mapped_column(String(20), default="")
    line_user_id: Mapped[str] = mapped_column(String(80), default="")
    earns_commission: Mapped[bool] = mapped_column(Boolean, default=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

    def can(self, *roles: str) -> bool:
        return self.role in roles


class Booking(Base):
    __tablename__ = "bookings"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20), unique=True)
    house_id: Mapped[int] = mapped_column(ForeignKey("houses.id"))
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"))
    check_in: Mapped[date] = mapped_column(Date)
    check_out: Mapped[date] = mapped_column(Date)
    adults: Mapped[int] = mapped_column(Integer, default=2)
    children: Mapped[int] = mapped_column(Integer, default=0)
    pets: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    channel: Mapped[str] = mapped_column(String(20), default="website")
    agent_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    group_ref: Mapped[str] = mapped_column(String(20), default="")  # whole-resort buyout grouping

    nights: Mapped[int] = mapped_column(Integer, default=1)
    room_total: Mapped[float] = mapped_column(Money, default=0)
    extra_guest_total: Mapped[float] = mapped_column(Money, default=0)
    addons_total: Mapped[float] = mapped_column(Money, default=0)
    discount_total: Mapped[float] = mapped_column(Money, default=0)
    total: Mapped[float] = mapped_column(Money, default=0)
    deposit_due: Mapped[float] = mapped_column(Money, default=0)
    amount_paid: Mapped[float] = mapped_column(Money, default=0)
    platform_fee: Mapped[float] = mapped_column(Money, default=0)
    commission_amount: Mapped[float] = mapped_column(Money, default=0)
    resort_revenue: Mapped[float] = mapped_column(Money, default=0)
    breakdown_json: Mapped[str] = mapped_column(Text, default="{}")

    special_request: Mapped[str] = mapped_column(Text, default="")
    admin_note: Mapped[str] = mapped_column(Text, default="")
    cancel_reason: Mapped[str] = mapped_column(Text, default="")
    hold_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    house: Mapped[House] = relationship()
    customer: Mapped[Customer] = relationship(back_populates="bookings")
    agent: Mapped[User | None] = relationship(foreign_keys=[agent_id])
    created_by: Mapped[User | None] = relationship(foreign_keys=[created_by_id])
    addons: Mapped[list["BookingAddon"]] = relationship(cascade="all, delete-orphan")
    payments: Mapped[list["Payment"]] = relationship(
        cascade="all, delete-orphan", order_by="Payment.id")

    __table_args__ = (Index("ix_booking_dates", "house_id", "check_in", "check_out"),)

    @property
    def balance(self) -> float:
        return float(self.total or 0) - float(self.amount_paid or 0)


class Addon(Base):
    __tablename__ = "addons"
    id: Mapped[int] = mapped_column(primary_key=True)
    name_th: Mapped[str] = mapped_column(String(120))
    name_en: Mapped[str] = mapped_column(String(120), default="")
    price: Mapped[float] = mapped_column(Money, default=0)
    unit: Mapped[str] = mapped_column(String(10), default="stay")  # stay / night / person
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort: Mapped[int] = mapped_column(Integer, default=0)

    def name(self, lang: str) -> str:
        return (self.name_en or self.name_th) if lang == "en" else self.name_th


class BookingAddon(Base):
    __tablename__ = "booking_addons"
    id: Mapped[int] = mapped_column(primary_key=True)
    booking_id: Mapped[int] = mapped_column(ForeignKey("bookings.id", ondelete="CASCADE"))
    addon_id: Mapped[int | None] = mapped_column(ForeignKey("addons.id", ondelete="SET NULL"), nullable=True)
    name: Mapped[str] = mapped_column(String(120))
    qty: Mapped[int] = mapped_column(Integer, default=1)
    unit_price: Mapped[float] = mapped_column(Money, default=0)
    amount: Mapped[float] = mapped_column(Money, default=0)


class Payment(Base):
    __tablename__ = "payments"
    id: Mapped[int] = mapped_column(primary_key=True)
    booking_id: Mapped[int] = mapped_column(ForeignKey("bookings.id", ondelete="CASCADE"))
    amount: Mapped[float] = mapped_column(Money, default=0)
    method: Mapped[str] = mapped_column(String(20), default="transfer")
    slip_path: Mapped[str] = mapped_column(String(255), default="")
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending/verified/rejected
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    verified_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Block(Base):
    """House closed for maintenance, owner use or an Airbnb (iCal) reservation."""
    __tablename__ = "blocks"
    id: Mapped[int] = mapped_column(primary_key=True)
    house_id: Mapped[int] = mapped_column(ForeignKey("houses.id", ondelete="CASCADE"))
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)  # exclusive, like check_out
    reason: Mapped[str] = mapped_column(String(160), default="")
    source: Mapped[str] = mapped_column(String(20), default="manual")  # manual / ical
    external_uid: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    house: Mapped[House] = relationship()


class NightLock(Base):
    """One row per occupied night. The unique constraint is the double-booking guard."""
    __tablename__ = "night_locks"
    id: Mapped[int] = mapped_column(primary_key=True)
    house_id: Mapped[int] = mapped_column(ForeignKey("houses.id", ondelete="CASCADE"))
    night: Mapped[date] = mapped_column(Date)
    booking_id: Mapped[int | None] = mapped_column(ForeignKey("bookings.id", ondelete="CASCADE"), nullable=True)
    block_id: Mapped[int | None] = mapped_column(ForeignKey("blocks.id", ondelete="CASCADE"), nullable=True)
    __table_args__ = (UniqueConstraint("house_id", "night", name="uq_house_night"),)


class Promotion(Base):
    __tablename__ = "promotions"
    id: Mapped[int] = mapped_column(primary_key=True)
    name_th: Mapped[str] = mapped_column(String(160))
    name_en: Mapped[str] = mapped_column(String(160), default="")
    detail_th: Mapped[str] = mapped_column(Text, default="")
    detail_en: Mapped[str] = mapped_column(Text, default="")
    # weekday_discount | nth_night_discount | percent_discount | gift | loyalty
    kind: Mapped[str] = mapped_column(String(30))
    amount: Mapped[float] = mapped_column(Money, default=0)
    percent: Mapped[float] = mapped_column(Money, default=0)
    nth: Mapped[int] = mapped_column(Integer, default=0)
    weekdays: Mapped[str] = mapped_column(String(20), default="6,0,1,2,3")  # python weekday of the night
    house_kinds: Mapped[str] = mapped_column(String(40), default="big_front,big,small")
    window_on: Mapped[str] = mapped_column(String(10), default="stay")  # stay / booking
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    min_nights: Mapped[int] = mapped_column(Integer, default=1)
    stackable: Mapped[bool] = mapped_column(Boolean, default=True)
    same_channel: Mapped[bool] = mapped_column(Boolean, default=False)
    gift_th: Mapped[str] = mapped_column(Text, default="")
    gift_en: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    show_on_site: Mapped[bool] = mapped_column(Boolean, default=True)
    sort: Mapped[int] = mapped_column(Integer, default=0)

    def name(self, lang: str) -> str:
        return (self.name_en or self.name_th) if lang == "en" else self.name_th

    def detail(self, lang: str) -> str:
        return (self.detail_en or self.detail_th) if lang == "en" else self.detail_th

    def gift(self, lang: str) -> str:
        return (self.gift_en or self.gift_th) if lang == "en" else self.gift_th


class BuyoutPackage(Base):
    __tablename__ = "buyout_packages"
    id: Mapped[int] = mapped_column(primary_key=True)
    name_th: Mapped[str] = mapped_column(String(160))
    name_en: Mapped[str] = mapped_column(String(160), default="")
    guests: Mapped[int] = mapped_column(Integer, default=40)
    price: Mapped[float] = mapped_column(Money, default=0)
    includes_th: Mapped[str] = mapped_column(Text, default="")  # one item per line
    includes_en: Mapped[str] = mapped_column(Text, default="")
    note_th: Mapped[str] = mapped_column(Text, default="")
    note_en: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Inquiry(Base):
    __tablename__ = "inquiries"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(20), default="contact")  # contact / buyout / event
    name: Mapped[str] = mapped_column(String(120))
    phone: Mapped[str] = mapped_column(String(20), default="")
    email: Mapped[str] = mapped_column(String(160), default="")
    date_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    nights: Mapped[int] = mapped_column(Integer, default=1)
    guests: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="new")
    handled_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class CommissionRule(Base):
    __tablename__ = "commission_rules"
    id: Mapped[int] = mapped_column(primary_key=True)
    channel: Mapped[str] = mapped_column(String(20), unique=True)
    mode: Mapped[str] = mapped_column(String(10), default="percent")  # percent / fixed / none
    percent: Mapped[float] = mapped_column(Money, default=0)
    fixed_big: Mapped[float] = mapped_column(Money, default=0)
    fixed_small: Mapped[float] = mapped_column(Money, default=0)
    platform_fee_percent: Mapped[float] = mapped_column(Money, default=0)
    to_pool: Mapped[bool] = mapped_column(Boolean, default=False)


class Payout(Base):
    __tablename__ = "payouts"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    amount: Mapped[float] = mapped_column(Money, default=0)
    status: Mapped[str] = mapped_column(String(10), default="unpaid")  # unpaid / paid
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    paid_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    user: Mapped[User] = relationship(foreign_keys=[user_id])


class WalletEntry(Base):
    """Append-only ledger. Never update amounts; post a reversal instead."""
    __tablename__ = "wallet_entries"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    booking_id: Mapped[int | None] = mapped_column(ForeignKey("bookings.id", ondelete="SET NULL"), nullable=True)
    amount: Mapped[float] = mapped_column(Money)
    kind: Mapped[str] = mapped_column(String(20))  # commission / reversal / adjustment
    note: Mapped[str] = mapped_column(Text, default="")
    payout_id: Mapped[int | None] = mapped_column(ForeignKey("payouts.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    booking: Mapped[Booking | None] = relationship()


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(30), default="info")
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text, default="")
    link: Mapped[str] = mapped_column(String(255), default="")
    read_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    booking_id: Mapped[int | None] = mapped_column(ForeignKey("bookings.id", ondelete="CASCADE"), nullable=True)
    action: Mapped[str] = mapped_column(String(60))
    detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    user: Mapped[User | None] = relationship()


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")


class StoredFile(Base):
    """Uploaded images kept inside the database, so free hosts with a wiped
    disk (Render free, Koyeb, HF Spaces) never lose slips or photos."""
    __tablename__ = "stored_files"
    id: Mapped[int] = mapped_column(primary_key=True)
    folder: Mapped[str] = mapped_column(String(20), index=True)
    name: Mapped[str] = mapped_column(String(60), unique=True)
    content_type: Mapped[str] = mapped_column(String(40), default="image/jpeg")
    data: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class AutomationLog(Base):
    """One row per automatic action already done (reminder sent, summary posted...),
    so the timed jobs can run every few minutes without repeating themselves."""
    __tablename__ = "automation_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(120), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class LoginGuard(Base):
    """Failed sign-ins per username and per IP address, for the temporary lock."""
    __tablename__ = "login_guards"
    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(120), unique=True)
    fails: Mapped[int] = mapped_column(Integer, default=0)
    first_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class SlipFingerprint(Base):
    """Picture fingerprint of every uploaded slip, to catch the same slip used twice."""
    __tablename__ = "slip_fingerprints"
    id: Mapped[int] = mapped_column(primary_key=True)
    payment_id: Mapped[int] = mapped_column(ForeignKey("payments.id", ondelete="CASCADE"), index=True)
    booking_id: Mapped[int] = mapped_column(ForeignKey("bookings.id", ondelete="CASCADE"))
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class OnlineCharge(Base):
    """A charge at the payment gateway (Opn Payments): PromptPay QR, mobile banking or card."""
    __tablename__ = "online_charges"
    id: Mapped[int] = mapped_column(primary_key=True)
    booking_id: Mapped[int] = mapped_column(ForeignKey("bookings.id", ondelete="CASCADE"), index=True)
    charge_id: Mapped[str] = mapped_column(String(60), unique=True)
    method: Mapped[str] = mapped_column(String(40), default="")
    amount: Mapped[float] = mapped_column(Money, default=0)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending/successful/failed/expired
    qr_url: Mapped[str] = mapped_column(Text, default="")
    authorize_uri: Mapped[str] = mapped_column(Text, default="")
    failure: Mapped[str] = mapped_column(Text, default="")
    payment_id: Mapped[int | None] = mapped_column(ForeignKey("payments.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)
