from datetime import date, datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
    BKK = ZoneInfo("Asia/Bangkok")
except Exception:  # noqa: BLE001
    # Windows has no built-in time-zone database (it comes from the "tzdata" package).
    # Thailand is UTC+7 all year with no daylight saving, so a fixed offset is exact.
    BKK = timezone(timedelta(hours=7), "Asia/Bangkok")

TH_MONTHS = ["", "มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน",
             "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม"]
TH_MONTHS_SHORT = ["", "ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.",
                   "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."]
EN_MONTHS_SHORT = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
                   "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
TH_DOW = ["จ", "อ", "พ", "พฤ", "ศ", "ส", "อา"]
EN_DOW = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]


def now() -> datetime:
    """Naive Bangkok wall-clock time (stored as-is in the DB)."""
    return datetime.now(BKK).replace(tzinfo=None)


def today() -> date:
    return now().date()


def nights_between(a: date, b: date) -> list[date]:
    return [a + timedelta(days=i) for i in range((b - a).days)]


def thai_date(d: date | datetime | None, lang: str = "th", short: bool = True, year: bool = True) -> str:
    if not d:
        return "-"
    if lang == "en":
        s = f"{d.day} {EN_MONTHS_SHORT[d.month]}"
        return f"{s} {d.year}" if year else s
    m = TH_MONTHS_SHORT[d.month] if short else TH_MONTHS[d.month]
    s = f"{d.day} {m}"
    return f"{s} {d.year + 543}" if year else s


def month_label(year: int, month: int, lang: str = "th") -> str:
    if lang == "en":
        return f"{['','January','February','March','April','May','June','July','August','September','October','November','December'][month]} {year}"
    return f"{TH_MONTHS[month]} {year + 543}"


def parse_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None
