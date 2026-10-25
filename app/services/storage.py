"""File storage for payment slips and house photos.

STORAGE=db     images are stored in the database (default on PostgreSQL). Works on
               free hosts whose disk is wiped on every restart.
STORAGE=local  images are written to UPLOAD_DIR (default with SQLite / your own server).
Either way the app stores and serves the same "/uploads/<folder>/<name>.jpg" path.
"""
from __future__ import annotations

import io
import secrets

from fastapi import UploadFile
from PIL import Image, ImageOps, UnidentifiedImageError

from ..config import STORAGE, UPLOAD_DIR
from ..db import SessionLocal
from ..models import StoredFile

MAX_BYTES = 8 * 1024 * 1024
FOLDERS = ("slips", "photos")


class UploadError(Exception):
    pass


async def save_image(f: UploadFile, folder: str, max_side: int = 1800) -> str:
    return (await save_image_fp(f, folder, max_side))[0]


async def save_image_fp(f: UploadFile, folder: str, max_side: int = 1800) -> tuple[str, str]:
    """Save and also return the picture fingerprint (used to spot a slip sent twice)."""
    from .automation import fingerprint
    assert folder in FOLDERS
    data = await f.read()
    if not data:
        raise UploadError("ยังไม่ได้เลือกไฟล์")
    if len(data) > MAX_BYTES:
        raise UploadError("ไฟล์ใหญ่เกิน 8 MB")
    try:
        im = Image.open(io.BytesIO(data))
        im.load()
    except (UnidentifiedImageError, OSError):
        raise UploadError("รองรับเฉพาะไฟล์รูปภาพ (JPG, PNG, WEBP)")
    im = ImageOps.exif_transpose(im).convert("RGB")   # phone photos come rotated
    im.thumbnail((max_side, max_side))
    out = io.BytesIO()
    im.save(out, "JPEG", quality=82, optimize=True, progressive=True)
    name = f"{secrets.token_hex(8)}.jpg"
    if STORAGE == "db":
        db = SessionLocal()
        try:
            db.add(StoredFile(folder=folder, name=name, data=out.getvalue()))
            db.commit()
        finally:
            db.close()
    else:
        d = UPLOAD_DIR / folder
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_bytes(out.getvalue())
    return f"/uploads/{folder}/{name}", fingerprint(data)


def load(folder: str, name: str) -> bytes | None:
    if folder not in FOLDERS or "/" in name or ".." in name:
        return None
    if STORAGE == "db":
        db = SessionLocal()
        try:
            row = db.query(StoredFile).filter_by(folder=folder, name=name).first()
            return bytes(row.data) if row else None
        finally:
            db.close()
    p = UPLOAD_DIR / folder / name
    return p.read_bytes() if p.is_file() else None
