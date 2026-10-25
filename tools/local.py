"""Local control script behind the .bat files.

    python tools/local.py start [--lan] [--no-browser]
    python tools/local.py stop
    python tools/local.py restart
    python tools/local.py status
    python tools/local.py reset-demo
    python tools/local.py backup
"""
from __future__ import annotations

import json
import os
import secrets
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.request
import webbrowser
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCAL = ROOT / ".local"
PID_FILE = LOCAL / "server.json"
LOG_DIR = ROOT / "logs"
LOG_FILE = LOG_DIR / "server.log"
DB_FILE = ROOT / "resort.db"
PORT = int(os.environ.get("PORT", "8000"))
WIN = os.name == "nt"

OK, WARN, ERR = "[OK]", "[!]", "[X]"
REQ_FILE = ROOT / "requirements-local.txt"
STAMP = ROOT / ".venv" / "installed.txt"


class LocalError(Exception):
    def __init__(self, msg: str, hint: str = ""):
        super().__init__(msg)
        self.hint = hint


def say(msg: str = "") -> None:
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:  # very old consoles
        print(msg.encode("ascii", "replace").decode(), flush=True)


# ------------------------------------------------------------------ helpers
def secret_key() -> str:
    LOCAL.mkdir(exist_ok=True)
    f = LOCAL / "secret.txt"
    if not f.exists():
        f.write_text(secrets.token_urlsafe(48), encoding="utf-8")
    return f.read_text(encoding="utf-8").strip()


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def read_state() -> dict | None:
    try:
        return json.loads(PID_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if WIN:
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def healthy(port: int, timeout: float = 1.5) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=timeout) as r:
            return r.status == 200
    except OSError:
        return False


def pids_on_port(port: int) -> set[int]:
    """Processes listening on the port (used to clean up a server started some other way)."""
    found: set[int] = set()
    try:
        if WIN:
            out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True).stdout
            for line in out.splitlines():
                parts = line.split()
                # state names are translated on some Windows languages, so match the
                # listening socket by its empty remote address instead of the word LISTENING
                if (len(parts) >= 5 and parts[0].upper() == "TCP" and parts[1].endswith(f":{port}")
                        and parts[2] in ("0.0.0.0:0", "[::]:0") and parts[-1].isdigit()):
                    found.add(int(parts[-1]))
        else:
            out = subprocess.run(["lsof", "-t", f"-iTCP:{port}", "-sTCP:LISTEN"], capture_output=True, text=True).stdout
            found |= {int(x) for x in out.split() if x.isdigit()}
    except (OSError, ValueError):
        pass
    found.discard(0)
    return found


def cmdline(pid: int) -> str:
    try:
        if WIN:
            ps = f"(Get-CimInstance Win32_Process -Filter 'ProcessId={pid}').CommandLine"
            return subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                                  capture_output=True, text=True, timeout=15).stdout.strip()
        return Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
    except (OSError, subprocess.SubprocessError):
        return ""


def is_ours(pid: int) -> bool:
    """Only ever kill servers belonging to this project, never another program on the same port."""
    c = cmdline(pid).replace("\\", "/")
    return "tools/serve.py" in c or "app.main:app" in c


def kill_tree(pid: int) -> None:
    if WIN:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
    else:
        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except OSError:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass


def checkpoint_db() -> None:
    """Fold SQLite's write-ahead log back into resort.db so the file is complete on its own."""
    if DB_FILE.exists():
        try:
            con = sqlite3.connect(DB_FILE, timeout=5)
            con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            con.close()
        except sqlite3.Error as e:
            say(f"{WARN} รวมไฟล์ฐานข้อมูลไม่สำเร็จ: {e}")


def ensure_db() -> None:
    if DB_FILE.exists():
        return
    say("สร้างฐานข้อมูลตัวอย่างครั้งแรก (บ้าน 9 หลัง โปรโมชั่น และการจองตัวอย่าง)...")
    LOG_DIR.mkdir(exist_ok=True)
    r = subprocess.run([sys.executable, "-m", "app.seed", "--demo"], cwd=ROOT, env=base_env(),
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        with open(LOG_DIR / "error.log", "a", encoding="utf-8") as f:
            f.write(f"\n===== seed {datetime.now():%Y-%m-%d %H:%M:%S} =====\n{r.stdout}\n{r.stderr}\n")
        for p in ROOT.glob("resort.db*"):   # never leave a half-created database behind
            p.unlink(missing_ok=True)
        last = (r.stderr.strip().splitlines() or ["ไม่ทราบสาเหตุ"])[-1]
        raise LocalError(f"สร้างฐานข้อมูลไม่สำเร็จ: {last}",
                         "รัน setup.bat อีกครั้ง ถ้ายังไม่หายส่งไฟล์ logs\\error.log มาให้ผู้พัฒนาดู")


def base_env() -> dict:
    env = dict(os.environ)
    # A PYTHONHOME/PYTHONPATH left behind by other software breaks Python's own
    # library lookup ("Could not find platform independent libraries"). Never pass them on.
    env.pop("PYTHONHOME", None)
    env.pop("PYTHONPATH", None)
    env.setdefault("DATABASE_URL", f"sqlite:///{DB_FILE.as_posix()}")
    env.setdefault("SECRET_KEY", secret_key())
    env.setdefault("PUBLIC_BASE_URL", f"http://localhost:{PORT}")
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


# ------------------------------------------------------------------ commands
def start(lan: bool = False, browser: bool = True) -> int:
    st = read_state()
    if st and pid_alive(st.get("pid", 0)) and healthy(st.get("port", PORT)):
        say(f"{OK} เว็บเปิดอยู่แล้ว: http://localhost:{st['port']}")
        if browser:
            webbrowser.open(f"http://localhost:{st['port']}")
        return 0
    if port_in_use(PORT):
        owners = pids_on_port(PORT)
        say(f"{ERR} พอร์ต {PORT} ถูกโปรแกรมอื่นใช้อยู่ (PID {', '.join(map(str, owners)) or 'ไม่ทราบ'})")
        say("    ปิดโปรแกรมนั้น หรือรัน stop.bat ก่อน หรือเปลี่ยนพอร์ต: set PORT=8080 แล้วรัน start.bat ใหม่")
        return 1
    ensure_db()
    LOG_DIR.mkdir(exist_ok=True)
    token = secrets.token_hex(16)
    env = base_env()
    env.update(HOST="0.0.0.0" if lan else "127.0.0.1", PORT=str(PORT), LOCAL_SHUTDOWN_TOKEN=token)
    log = open(LOG_FILE, "a", encoding="utf-8")
    log.write(f"\n===== start {datetime.now():%Y-%m-%d %H:%M:%S} =====\n")
    log.flush()
    kwargs: dict = dict(cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    if WIN:
        kwargs["creationflags"] = (subprocess.CREATE_NEW_PROCESS_GROUP | 0x00000008  # DETACHED_PROCESS
                                   | 0x08000000)                                    # CREATE_NO_WINDOW
    else:
        kwargs["start_new_session"] = True
    # pythonw-free: the detached child has no console window on Windows
    proc = subprocess.Popen([sys.executable, str(ROOT / "tools" / "serve.py")], **kwargs)
    LOCAL.mkdir(exist_ok=True)
    PID_FILE.write_text(json.dumps({"pid": proc.pid, "port": PORT, "token": token, "lan": lan,
                                    "started": datetime.now().isoformat(timespec="seconds")}), encoding="utf-8")
    say("กำลังเปิดเว็บ", )
    for _ in range(60):
        if proc.poll() is not None:
            say(f"\n{ERR} เปิดเว็บไม่สำเร็จ ดูรายละเอียดใน logs\\server.log (10 บรรทัดสุดท้าย):")
            say("".join(LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines(True)[-10:]))
            PID_FILE.unlink(missing_ok=True)
            return 1
        if healthy(PORT, 1):
            break
        time.sleep(0.5)
    else:
        say(f"{WARN} เว็บยังไม่ตอบกลับภายใน 30 วินาที ลองเปิด http://localhost:{PORT} เองอีกครั้ง")
    url = f"http://localhost:{PORT}"
    say("")
    say(f"{OK} เว็บไซต์ทำงานแล้ว")
    say(f"    หน้าเว็บลูกค้า   {url}")
    say(f"    ภาษาอังกฤษ      {url}/en")
    say(f"    หลังบ้าน         {url}/admin")
    say("    บัญชีทดลอง     owner / golden1234  (เจ้าของ)")
    say("                   fah / golden1234    (แอดมินฝ่ายขาย)")
    say("                   maid / golden1234   (แม่บ้าน)")
    if lan:
        ip = lan_ip()
        say("")
        say(f"    ทดสอบบนมือถือ (ต่อ Wi-Fi เดียวกัน): http://{ip}:{PORT}")
        say("    ถ้า Windows ถามเรื่อง Firewall ให้กด Allow access สำหรับ Private network")
    say("")
    say("    ปิดเว็บด้วย stop.bat   ดูสถานะด้วย status.bat")
    if browser:
        webbrowser.open(url)
    return 0


def stop(quiet: bool = False) -> int:
    st = read_state()
    stopped = False
    if st:
        pid, port, token = st.get("pid", 0), st.get("port", PORT), st.get("token", "")
        if pid_alive(pid):
            # 1) ask the server to finish and exit by itself
            try:
                req = urllib.request.Request(f"http://127.0.0.1:{port}/__local/shutdown?t={token}", method="POST")
                urllib.request.urlopen(req, timeout=5).read()
            except OSError:
                pass
            for _ in range(30):
                if not pid_alive(pid):
                    stopped = True
                    break
                time.sleep(0.5)
            # 2) still running: end the whole process tree
            if not stopped:
                kill_tree(pid)
                time.sleep(1)
                stopped = not pid_alive(pid)
        PID_FILE.unlink(missing_ok=True)
    # 3) anything else still holding the port (e.g. started with uvicorn by hand)
    holders = pids_on_port(PORT)
    leftovers = {p for p in holders if is_ours(p)}
    for p in leftovers:
        kill_tree(p)
    if leftovers:
        time.sleep(1)
    checkpoint_db()
    others = holders - leftovers
    if others:
        say(f"{WARN} พอร์ต {PORT} ถูกโปรแกรมอื่นที่ไม่ใช่เว็บนี้ใช้อยู่ (PID {', '.join(map(str, others))}) ระบบไม่ได้ปิดโปรแกรมนั้น")
    elif port_in_use(PORT):
        say(f"{ERR} ยังมีโปรแกรมใช้พอร์ต {PORT} อยู่ ลองรัน stop.bat อีกครั้ง หรือรีสตาร์ทเครื่อง")
        return 1
    if not quiet:
        say(f"{OK} ปิดเว็บเรียบร้อย" if (stopped or leftovers or st) else f"{OK} เว็บไม่ได้เปิดอยู่")
    return 0


def status() -> int:
    st = read_state()
    port = (st or {}).get("port", PORT)
    up = healthy(port)
    if st and pid_alive(st.get("pid", 0)) and up:
        say(f"{OK} เว็บกำลังทำงาน  http://localhost:{port}  (เริ่มเมื่อ {st.get('started')}, PID {st['pid']})")
        if st.get("lan"):
            say(f"    เปิดให้มือถือในวง Wi-Fi: http://{lan_ip()}:{port}")
    elif up:
        say(f"{WARN} มีเว็บตอบที่พอร์ต {port} แต่ไม่ได้เปิดจาก start.bat")
    else:
        say(f"{OK} เว็บไม่ได้เปิดอยู่")
    say(f"    Python ที่ใช้: {sys.version.split()[0]} ({sys.base_prefix})")
    if DB_FILE.exists():
        say(f"    ฐานข้อมูล: {DB_FILE.name} ({DB_FILE.stat().st_size / 1024:,.0f} KB)")
    if LOG_FILE.exists():
        lines = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()[-8:]
        say("    log ล่าสุด:")
        for line in lines:
            say("      " + line[:140])
    return 0


def reset_demo() -> int:
    stop(quiet=True)
    for f in ROOT.glob("resort.db*"):
        f.unlink()
    up = ROOT / "uploads"
    if up.exists():
        shutil.rmtree(up)
    ensure_db()
    say(f"{OK} ล้างข้อมูลและสร้างข้อมูลตัวอย่างใหม่แล้ว")
    return 0


def backup() -> int:
    if not DB_FILE.exists():
        say(f"{ERR} ยังไม่มีฐานข้อมูลให้สำรอง")
        return 1
    out_dir = ROOT / "backups"
    out_dir.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    tmp = LOCAL / f"resort-{stamp}.db"
    LOCAL.mkdir(exist_ok=True)
    # SQLite's backup API copies a consistent snapshot even while the site is running
    src = sqlite3.connect(DB_FILE)
    dst = sqlite3.connect(tmp)
    src.backup(dst)
    dst.close()
    src.close()
    target = out_dir / f"backup-{stamp}.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(tmp, "resort.db")
        up = ROOT / "uploads"
        if up.exists():
            for f in up.rglob("*"):
                if f.is_file():
                    z.write(f, f.relative_to(ROOT).as_posix())
    tmp.unlink()
    say(f"{OK} สำรองข้อมูลแล้ว: backups\\{target.name} ({target.stat().st_size / 1024:,.0f} KB)")
    say("    กู้คืน: ปิดเว็บ แตกไฟล์ zip แล้ววาง resort.db และโฟลเดอร์ uploads ทับในโฟลเดอร์โปรแกรม")
    return 0


def req_hash() -> str:
    import hashlib
    return hashlib.sha256(REQ_FILE.read_bytes()).hexdigest()[:16] if REQ_FILE.exists() else "none"


def needs_setup() -> int:
    """Exit 0 when .venv matches requirements-local.txt, 1 when setup.bat must run (again)."""
    try:
        return 0 if STAMP.read_text(encoding="utf-8").split()[0] == req_hash() else 1
    except (OSError, IndexError):
        return 1


def mark_installed() -> int:
    STAMP.write_text(f"{req_hash()} {sys.version.split()[0]} {datetime.now():%Y-%m-%d %H:%M}\n", encoding="utf-8")
    return 0


def check() -> int:
    """Self-test run at the end of setup.bat so problems show up before the first start."""
    bad = 0

    def line(ok: bool, text: str, fix: str = ""):
        nonlocal bad
        say(f"{OK if ok else ERR} {text}")
        if not ok:
            bad += 1
            if fix:
                say(f"    วิธีแก้: {fix}")

    line(sys.version_info >= (3, 10), f"Python {sys.version.split()[0]}", "ติดตั้ง Python 3.10 ขึ้นไป")
    for var in ("PYTHONHOME", "PYTHONPATH"):
        val = os.environ.get(var) or os.environ.get(f"GB_ORIG_{var}")
        if val:
            say(f"{WARN} เครื่องนี้ตั้งค่า {var}={val} ไว้ (มักเป็นต้นเหตุของข้อความ "
                f"\"Could not find platform independent libraries\") โปรแกรมนี้จะไม่ใช้ค่านี้ "
                f"ถ้าต้องการลบถาวร: Settings > System > About > Advanced system settings > Environment Variables")
    base = Path(sys.base_prefix)
    if not (base / "Lib" / "os.py").exists() and not (base / "lib").exists():
        say(f"{WARN} ไม่พบไฟล์มาตรฐานของ Python ที่ {base}")
    if WIN:
        say(f"    (ถ้าเห็นข้อความ \"Could not find platform independent libraries\" เป็นคำเตือนจากตัวติดตั้ง Python "
            f"{sys.version.split()[0]} ในเครื่อง ไม่กระทบโปรแกรมนี้ ถ้าอยากให้หาย: Settings > Apps > Python > Modify > Repair "
            f"หรือติดตั้ง Python 3.13 เวอร์ชันล่าสุดทับ)")
    for mod in ("fastapi", "uvicorn", "sqlalchemy", "jinja2", "multipart", "itsdangerous", "PIL", "segno", "tzdata"):
        try:
            __import__(mod)
            line(True, f"ไลบรารี {mod}")
        except Exception as e:  # noqa: BLE001
            line(False, f"ไลบรารี {mod}: {e}", "รัน setup.bat ใหม่")
    try:
        from app.timeutil import now
        line(True, f"เวลาไทยของระบบ {now():%Y-%m-%d %H:%M}")
    except Exception as e:  # noqa: BLE001
        line(False, f"โซนเวลา: {e}", "รัน setup.bat ใหม่")
    try:
        os.environ.update({k: v for k, v in base_env().items() if k in ("DATABASE_URL", "SECRET_KEY")})
        import app.main  # noqa: F401  (imports every route, template helper and model)
        line(True, "โหลดโปรแกรมครบทุกส่วน")
    except Exception as e:  # noqa: BLE001
        line(False, f"โหลดโปรแกรมไม่สำเร็จ: {type(e).__name__}: {e}", "ส่งข้อความนี้ให้ผู้พัฒนา")
    try:
        t = ROOT / ".local" / "write-test.db"
        t.parent.mkdir(exist_ok=True)
        con = sqlite3.connect(t)
        con.execute("create table if not exists x(a)")
        con.close()
        t.unlink()
        line(True, "เขียนฐานข้อมูลในโฟลเดอร์นี้ได้")
    except Exception as e:  # noqa: BLE001
        line(False, f"เขียนไฟล์ในโฟลเดอร์นี้ไม่ได้: {e}", "ย้ายโฟลเดอร์โปรแกรมไปไว้ที่ที่มีสิทธิ์เขียน เช่น D:\\ หรือ Documents")
    for f in ("app/static/fonts/chonburi-thai-400-normal.woff2", "app/static/img/hero-house.jpg", "app/templates/public/home.html"):
        line((ROOT / f).exists(), f"ไฟล์ {f}", "แตกไฟล์ zip ใหม่ให้ครบ")
    st = read_state()
    if port_in_use(PORT) and not (st and healthy(PORT)):
        say(f"{WARN} พอร์ต {PORT} มีโปรแกรมอื่นใช้อยู่ ตอนเปิดเว็บให้ตั้ง set PORT=8080 ก่อน")
    say("")
    say(f"{OK} ตรวจระบบผ่านทั้งหมด" if not bad else f"{ERR} พบปัญหา {bad} ข้อ ดูวิธีแก้ด้านบน")
    return 1 if bad else 0


def main(argv: list[str]) -> int:
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    cmd = argv[0] if argv else "status"
    if cmd == "start":
        return start(lan="--lan" in argv, browser="--no-browser" not in argv)
    if cmd == "stop":
        return stop()
    if cmd == "restart":
        was_lan = bool((read_state() or {}).get("lan"))
        stop(quiet=True)
        return start(lan=was_lan or "--lan" in argv, browser="--no-browser" not in argv)
    if cmd == "status":
        return status()
    if cmd == "reset-demo":
        return reset_demo()
    if cmd == "backup":
        return backup()
    if cmd == "check":
        return check()
    if cmd == "needs-setup":
        return needs_setup()
    if cmd == "mark-installed":
        return mark_installed()
    say(__doc__)
    return 2


if __name__ == "__main__":
    for _v in ("PYTHONHOME", "PYTHONPATH"):
        os.environ.pop(_v, None)
    try:
        sys.exit(main(sys.argv[1:]))
    except LocalError as e:
        say(f"{ERR} {e}")
        if e.hint:
            say(f"    {e.hint}")
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as e:  # noqa: BLE001  - show a readable message, keep the details in a file
        import traceback
        LOG_DIR.mkdir(exist_ok=True)
        with open(LOG_DIR / "error.log", "a", encoding="utf-8") as f:
            f.write(f"\n===== {datetime.now():%Y-%m-%d %H:%M:%S} {' '.join(sys.argv[1:])} =====\n")
            traceback.print_exc(file=f)
        say(f"{ERR} เกิดข้อผิดพลาด: {type(e).__name__}: {e}")
        say("    รายละเอียดบันทึกไว้ที่ logs\\error.log")
        sys.exit(1)
