import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
ROOT_DIR = BASE_DIR.parent


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


DATABASE_URL = _env("DATABASE_URL", f"sqlite:///{ROOT_DIR / 'resort.db'}")
# Render / Supabase give postgres:// ; SQLAlchemy wants postgresql+psycopg://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

SECRET_KEY = _env("SECRET_KEY", "dev-only-change-me")
UPLOAD_DIR = Path(_env("UPLOAD_DIR", str(ROOT_DIR / "uploads")))
PUBLIC_BASE_URL = _env("PUBLIC_BASE_URL", "http://localhost:8000")
TIMEZONE = "Asia/Bangkok"

# First owner account created by the seed script
ADMIN_USERNAME = _env("ADMIN_USERNAME", "owner")
ADMIN_PASSWORD = _env("ADMIN_PASSWORD", "golden1234")

# Where uploads live: "db" (default on PostgreSQL, survives free-host restarts) or "local" disk
STORAGE = _env("STORAGE", "local" if DATABASE_URL.startswith("sqlite") else "db")
CRON_TOKEN_ENV = "CRON_TOKEN"          # read at request time so it can change without restart
COOKIE_SECURE = _env("COOKIE_SECURE", "0") == "1"
DISABLE_BACKGROUND = _env("DISABLE_BACKGROUND", "0") == "1"

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
