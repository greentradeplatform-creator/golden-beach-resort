import hashlib
import hmac
import re
import secrets


def hash_password(pw: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 240_000)
    return f"pbkdf2${salt}${dk.hex()}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        _, salt, h = stored.split("$")
    except ValueError:
        return False
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 240_000)
    return hmac.compare_digest(dk.hex(), h)


def clean_phone(s: str) -> str:
    d = re.sub(r"\D", "", s or "")
    if d.startswith("66") and len(d) == 11:
        d = "0" + d[2:]
    return d


def fmt_phone(d: str) -> str:
    d = clean_phone(d)
    if len(d) == 10:
        return f"{d[:3]}-{d[3:6]}-{d[6:]}"
    return d
