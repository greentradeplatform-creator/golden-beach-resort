"""Find every Python on this computer, test each one and pick the best for this project.

    python tools/pick_python.py                  # show the table, print the chosen path
    python tools/pick_python.py --write FILE     # also save the chosen python.exe path to FILE
    python tools/pick_python.py --choose         # let the user choose by number

Standard library only, so any Python 3.8+ can run it to find a better one.

How the choice is made (highest score wins):
  * must be 3.10-3.14, 64-bit preferred, a real install (not a venv, not the Microsoft
    Store placeholder, not a free-threaded "t" build which many libraries don't support yet)
  * must have venv, ensurepip and sqlite3 (needed to install and run the site)
  * 3.12 first because the free host (Render / Dockerfile) runs 3.12, so local tests match
    production; then 3.13, 3.11, 3.14, 3.10
  * an install that prints warnings when it starts (e.g. "Could not find platform
    independent libraries") is pushed down: it works, but something in it is damaged
"""
from __future__ import annotations

import glob
import json
import os
import re
import subprocess
import sys

WIN = os.name == "nt"
PREFERRED = {(3, 12): 100, (3, 13): 90, (3, 11): 80, (3, 14): 70, (3, 10): 60}
REASON = {(3, 12): "ตรงกับเวอร์ชันบนเซิร์ฟเวอร์จริง (Render / Docker ใช้ 3.12)",
          (3, 13): "รองรับครบ", (3, 11): "รองรับครบ", (3, 14): "เวอร์ชันใหม่ล่าสุด",
          (3, 10): "เวอร์ชันต่ำสุดที่รองรับ"}

PROBE = r"""
import json, struct, sys, sysconfig
mods = {}
for m in ("venv", "ensurepip", "sqlite3", "ssl", "zoneinfo"):
    try:
        __import__(m); mods[m] = True
    except Exception:
        mods[m] = False
print(json.dumps({
    "version": list(sys.version_info[:3]),
    "bits": struct.calcsize("P") * 8,
    "free_threaded": bool(sysconfig.get_config_var("Py_GIL_DISABLED")),
    "in_venv": sys.prefix != getattr(sys, "base_prefix", sys.prefix),
    "conda": "conda" in sys.version.lower() or "Anaconda" in sys.version,
    "exe": sys.executable,
    "mods": mods,
}))
"""


def parse_py_list(out: str) -> list[str]:
    """Paths from `py -0p`, old (" -3.12-64  C:\\...") and new (" -V:3.12 *  C:\\...") formats."""
    paths = []
    for line in out.splitlines():
        m = re.search(r"([A-Za-z]:\\.*?\.exe)\s*$", line.strip())
        if m:
            paths.append(m.group(1))
    return paths


def candidates() -> list[str]:
    found: list[str] = []

    seen: set[str] = set()

    def add(p):
        if p and os.path.isfile(p):
            p = os.path.normcase(os.path.abspath(p))
            real = os.path.normcase(os.path.realpath(p))   # the same install reached by two links
            if real not in seen:
                seen.add(real)
                found.append(p)

    if WIN:
        # 1) the py launcher's list of registered installs
        for args in (["py", "-0p"], ["py", "--list-paths"]):
            try:
                out = subprocess.run(args, capture_output=True, text=True, timeout=20).stdout
            except (OSError, subprocess.SubprocessError):
                continue
            for path in parse_py_list(out):
                add(path)
        # 2) the registry (python.org installers register here)
        try:
            import winreg
            for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                for base in (r"Software\Python\PythonCore", r"Software\WOW6432Node\Python\PythonCore"):
                    try:
                        k = winreg.OpenKey(hive, base)
                    except OSError:
                        continue
                    for i in range(winreg.QueryInfoKey(k)[0]):
                        tag = winreg.EnumKey(k, i)
                        try:
                            ik = winreg.OpenKey(k, tag + r"\InstallPath")
                            try:
                                add(winreg.QueryValueEx(ik, "ExecutablePath")[0])
                            except OSError:
                                add(os.path.join(winreg.QueryValueEx(ik, "")[0], "python.exe"))
                        except OSError:
                            pass
        except ImportError:
            pass
        # 3) the usual folders
        local = os.environ.get("LOCALAPPDATA", "")
        for pattern in (rf"{local}\Programs\Python\Python3*\python.exe", rf"{local}\Python\pythoncore-3*\python.exe",
                        r"C:\Python3*\python.exe", r"C:\Program Files\Python3*\python.exe",
                        r"C:\Program Files (x86)\Python3*\python.exe"):
            for p in glob.glob(pattern):
                add(p)
        # 4) whatever "python" means on PATH
        try:
            out = subprocess.run(["where", "python"], capture_output=True, text=True, timeout=10).stdout
            for p in out.splitlines():
                add(p.strip())
        except (OSError, subprocess.SubprocessError):
            pass
    else:
        for name in ("python3.12", "python3.13", "python3.11", "python3.14", "python3.10", "python3", "python"):
            for d in os.environ.get("PATH", "").split(os.pathsep):
                add(os.path.join(d, name))
    return found


def probe(exe: str) -> dict:
    info = {"path": exe, "ok": False, "score": -1, "note": ""}
    low = exe.lower()
    if "windowsapps" in low:
        info["note"] = "ตัวเชื่อม Microsoft Store (ไม่ใช่ Python จริง)"
        return info
    try:
        env = dict(os.environ)
        env.pop("PYTHONHOME", None)
        env.pop("PYTHONPATH", None)
        r = subprocess.run([exe, "-I", "-c", PROBE], capture_output=True, text=True, timeout=30, env=env)
    except (OSError, subprocess.SubprocessError) as e:
        info["note"] = f"เปิดไม่ได้ ({type(e).__name__})"
        return info
    if r.returncode != 0 or not r.stdout.strip():
        info["note"] = "เปิดไม่ได้หรือเสียหาย"
        return info
    try:
        d = json.loads(r.stdout.strip().splitlines()[-1])
    except ValueError:
        info["note"] = "อ่านข้อมูลไม่ได้"
        return info
    v = tuple(d["version"])
    info.update(version=".".join(map(str, v)), bits=d["bits"], warnings=r.stderr.strip())
    if d["in_venv"]:
        info["note"] = "เป็น virtual environment ของโปรเจกต์อื่น"
        return info
    if d["free_threaded"]:
        info["note"] = "รุ่น free-threaded (ไลบรารีหลายตัวยังไม่รองรับ)"
        return info
    if v[:2] not in PREFERRED:
        info["note"] = "ไม่รองรับ (ต้องเป็น 3.10 ถึง 3.14)"
        return info
    missing = [m for m in ("venv", "ensurepip", "sqlite3") if not d["mods"][m]]
    if missing:
        info["note"] = "ขาดโมดูล " + ", ".join(missing)
        return info
    score = PREFERRED[v[:2]] + v[2] / 100
    notes = [REASON[v[:2]]]
    if d["bits"] != 64:
        score -= 30
        notes.append("32-bit")
    if d["conda"]:
        score -= 10
        notes.append("Anaconda")
    if info["warnings"]:
        score -= 50
        notes.append("มีคำเตือนตอนเริ่มทำงาน: " + info["warnings"].splitlines()[0][:70])
    info.update(ok=True, score=score, note=", ".join(notes))
    return info


def table(results: list[dict], chosen: dict | None) -> None:
    print(f"พบ Python ในเครื่อง {len(results)} ตัว:")
    for i, r in enumerate(results, 1):
        mark = ">>" if r is chosen else ("  " if r["ok"] else " x")
        ver = r.get("version", "?")
        bits = f"{r['bits']}-bit" if r.get("bits") else ""
        print(f" {mark} {i:>2}. {ver:<8} {bits:<6} {r['path']}")
        print(f"          {r['note']}")


def main(argv: list[str]) -> int:
    results = [probe(p) for p in candidates()]
    results.sort(key=lambda r: r["score"], reverse=True)
    good = [r for r in results if r["ok"]]
    forced = os.environ.get("GB_PYTHON")
    chosen = None
    if forced:
        chosen = next((r for r in results if os.path.normcase(os.path.abspath(forced)) == r["path"]), None) or probe(
            os.path.normcase(os.path.abspath(forced)))
        if not chosen["ok"]:
            print(f"[X] GB_PYTHON={forced} ใช้ไม่ได้: {chosen['note']}")
            return 1
    elif good:
        chosen = good[0]

    if "--choose" in argv:
        table(results, chosen)
        if not good:
            return 1
        try:
            ans = input("\nพิมพ์หมายเลข Python ที่ต้องการ (Enter = ใช้ตัวที่มี >>): ").strip()
        except EOFError:
            ans = ""
        if ans:
            try:
                pick = results[int(ans) - 1]
            except (ValueError, IndexError):
                print("[X] หมายเลขไม่ถูกต้อง")
                return 1
            if not pick["ok"]:
                print(f"[X] ตัวนี้ใช้ไม่ได้: {pick['note']}")
                return 1
            chosen = pick
    else:
        table(results, chosen)

    if not chosen:
        print("[X] ไม่พบ Python ที่ใช้ได้ (ต้องเป็น 3.10 ถึง 3.14 แบบ 64-bit)")
        return 1
    print(f"\n[OK] เลือกใช้ Python {chosen['version']}: {chosen['path']}")
    if "--write" in argv:
        out = argv[argv.index("--write") + 1]
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            f.write(chosen["path"])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
