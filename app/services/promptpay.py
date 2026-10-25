"""Thai QR (PromptPay) payload with amount, following the EMVCo merchant spec."""
import io
import re

import segno


def _f(tag: str, value: str) -> str:
    return f"{tag}{len(value):02d}{value}"


def _crc16(data: str) -> str:
    crc = 0xFFFF
    for ch in data.encode("ascii"):
        crc ^= ch << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else (crc << 1)
            crc &= 0xFFFF
    return f"{crc:04X}"


def payload(promptpay_id: str, amount: float | None = None) -> str:
    pid = re.sub(r"\D", "", promptpay_id or "")
    if len(pid) == 10 and pid.startswith("0"):
        target = _f("01", "0066" + pid[1:])          # mobile number
    elif len(pid) == 13:
        target = _f("02", pid)                       # national / tax id
    elif len(pid) == 15:
        target = _f("03", pid)                       # e-wallet
    else:
        raise ValueError("PromptPay ID must be a mobile number, tax ID or e-wallet ID")
    merchant = _f("00", "A000000677010111") + target
    data = (_f("00", "01") + _f("01", "12" if amount else "11") + _f("29", merchant)
            + _f("53", "764") + (_f("54", f"{amount:.2f}") if amount else "") + _f("58", "TH"))
    data += "6304"
    return data + _crc16(data)


def svg(promptpay_id: str, amount: float | None = None) -> str | None:
    try:
        qr = segno.make(payload(promptpay_id, amount), error="m")
    except ValueError:
        return None
    buf = io.BytesIO()
    qr.save(buf, kind="svg", scale=6, border=2, dark="#1E1A16", xmldecl=False, svgns=True)
    return buf.getvalue().decode()
