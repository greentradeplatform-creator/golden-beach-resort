from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import DATABASE_URL

_is_sqlite = DATABASE_URL.startswith("sqlite")
engine = create_engine(
    DATABASE_URL,
    # prepare_threshold=None keeps psycopg compatible with Supabase's pgbouncer pooler
    connect_args={"check_same_thread": False} if _is_sqlite else {"prepare_threshold": None},
    pool_pre_ping=True,
    pool_recycle=300,
    # Supabase free session pooler allows a small number of clients; stay well under it
    **({} if _is_sqlite else {"pool_size": 5, "max_overflow": 3}),
)

if _is_sqlite:
    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.close()

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
