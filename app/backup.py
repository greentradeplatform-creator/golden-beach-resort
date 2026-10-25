"""Whole-database backup as one zip of JSON files (works on SQLite and PostgreSQL).

Download:  admin > ตั้งค่าระบบ > ดาวน์โหลดไฟล์สำรอง   (owner only, /admin/backup.zip)
Restore:   set DATABASE_URL to the target database, then
           python -m app.backup restore golden-beach-backup-2026-10-24.zip --replace
           (--replace wipes what is there first, e.g. the starter data a new site creates)
"""
from __future__ import annotations

import base64
import io
import json
import sys
import zipfile
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import LargeBinary, insert, select, text

from .db import Base, engine

VERSION = 1


def _enc(v):
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, (bytes, bytearray, memoryview)):
        return base64.b64encode(bytes(v)).decode()
    return v


def dump() -> bytes:
    from . import models  # noqa: F401  (register every table)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z, engine.connect() as conn:
        counts = {}
        for t in Base.metadata.sorted_tables:
            rows = [{k: _enc(v) for k, v in r._mapping.items()} for r in conn.execute(select(t))]
            counts[t.name] = len(rows)
            z.writestr(f"{t.name}.json", json.dumps(rows, ensure_ascii=False))
        z.writestr("manifest.json", json.dumps({"version": VERSION, "created": datetime.now().isoformat(),
                                                "tables": counts}, ensure_ascii=False, indent=1))
    return buf.getvalue()


def _dec(col, v):
    if v is None:
        return None
    if isinstance(col.type, LargeBinary):
        return base64.b64decode(v)
    py = getattr(col.type, "python_type", None)
    try:
        if py is datetime:
            return datetime.fromisoformat(v)
        if py is date:
            return date.fromisoformat(v)
    except NotImplementedError:
        pass
    return v


def restore(data: bytes, replace: bool = False) -> dict:
    from . import models  # noqa: F401
    Base.metadata.create_all(engine)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        manifest = json.loads(z.read("manifest.json"))
        with engine.begin() as conn:
            if replace:
                for t in reversed(Base.metadata.sorted_tables):
                    conn.execute(t.delete())
            for t in Base.metadata.sorted_tables:
                if conn.execute(select(t).limit(1)).first() is not None:
                    raise SystemExit(f"Table {t.name} is not empty: add --replace to overwrite it")
            for t in Base.metadata.sorted_tables:
                if f"{t.name}.json" not in z.namelist():
                    continue
                rows = json.loads(z.read(f"{t.name}.json"))
                cols = {c.name: c for c in t.columns}
                for i in range(0, len(rows), 500):
                    chunk = [{k: _dec(cols[k], v) for k, v in r.items() if k in cols} for r in rows[i:i + 500]]
                    if chunk:
                        conn.execute(insert(t), chunk)
            if engine.dialect.name == "postgresql":     # keep new ids after the restored ones
                for t in Base.metadata.sorted_tables:
                    if "id" in t.columns and t.columns["id"].autoincrement:
                        conn.execute(text(f"SELECT setval(pg_get_serial_sequence('{t.name}', 'id'), "
                                          f"COALESCE((SELECT MAX(id) FROM {t.name}), 0) + 1, false)"))
    return manifest["tables"]


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "restore":
        with open(sys.argv[2], "rb") as f:
            print(restore(f.read(), replace="--replace" in sys.argv))
    else:
        print(__doc__)
