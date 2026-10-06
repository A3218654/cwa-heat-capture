"""共用工具：時間、浮水印、狀態檔、CSV 紀錄、上傳 Google Drive。"""
import csv
import glob
import json
import os
import shutil
import subprocess
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont

from . import config

TZ = ZoneInfo("Asia/Taipei")


# ------------------------------------------------------------------ 時間
def now() -> datetime:
    fake = os.environ.get("CAPTURE_NOW")  # 測試用：指定假時間，例如 2026-10-03T23:50
    if fake:
        return datetime.fromisoformat(fake).replace(tzinfo=TZ)
    return datetime.now(TZ)


def in_window(t: datetime) -> bool:
    start = dtime(*config.WINDOW_START)
    end = dtime(*config.WINDOW_END)
    return start <= t.time().replace(second=0, microsecond=0) <= end


def stamp(t: datetime) -> str:
    return t.strftime("%Y-%m-%d %H:%M:%S")


# ------------------------------------------------------------------ 路徑
def day_dir(day: str) -> str:
    """day = 'YYYY-MM-DD'，回傳本機輸出資料夾（與雲端結構相同）。"""
    path = os.path.join(config.OUT_DIR, day[:7], day)
    os.makedirs(path, exist_ok=True)
    return path


def site_dir(day: str, site: str) -> str:
    path = os.path.join(day_dir(day), site)
    os.makedirs(path, exist_ok=True)
    return path


# ------------------------------------------------------------------ 浮水印
def _find_cjk_font() -> str | None:
    patterns = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK*.ttc",
        "/usr/share/fonts/**/NotoSansCJK*.ttc",
        "/usr/share/fonts/**/*CJK*.tt[cf]",
    ]
    for p in patterns:
        hits = sorted(glob.glob(p, recursive=True))
        if hits:
            return hits[0]
    return None


def _wrap(draw, text: str, font, max_w: int) -> list[str]:
    """超過畫面寬度時，優先在「 ｜ 」分隔處換行，必要時逐字換行。"""
    out, cur = [], ""
    for part in text.split(" ｜ "):
        cand = f"{cur} ｜ {part}" if cur else part
        if draw.textlength(cand, font=font) <= max_w:
            cur = cand
            continue
        if cur:
            out.append(cur)
        cur = ""
        for ch in part:
            if draw.textlength(cur + ch, font=font) > max_w and cur:
                out.append(cur)
                cur = ""
            cur += ch
    if cur:
        out.append(cur)
    return out


def watermark(png_path: str, lines: list[str]) -> str:
    """在截圖最上方加一條時間資訊橫幅（不遮住原畫面），過長的文字自動換行。"""
    img = Image.open(png_path).convert("RGB")
    font_path = _find_cjk_font()
    size = 22
    font = ImageFont.truetype(font_path, size) if font_path else ImageFont.load_default()
    measure = ImageDraw.Draw(img)
    wrapped = []  # (文字, 是否為第一行)
    for i, text in enumerate(lines):
        text = " / ".join(x.strip() for x in str(text).splitlines() if x.strip())  # 網頁儲存格可能有多行
        for seg in _wrap(measure, text, font, img.width - 32):
            wrapped.append((seg, i == 0))
    line_h = size + 10
    band_h = line_h * len(wrapped) + 16
    out = Image.new("RGB", (img.width, img.height + band_h), (20, 24, 33))
    out.paste(img, (0, band_h))
    draw = ImageDraw.Draw(out)
    y = 8
    for text, first in wrapped:
        draw.text((16, y), text, font=font, fill=(255, 214, 0) if first else (235, 235, 235))
        y += line_h
    # 存成 JPG（約為 PNG 的三分之一大小），刪除原本的 PNG，回傳新路徑
    jpg = os.path.splitext(png_path)[0] + ".jpg"
    out.save(jpg, "JPEG", quality=82, optimize=True, progressive=True)
    if jpg != png_path and os.path.exists(png_path):
        os.remove(png_path)
    return jpg


# ------------------------------------------------------------------ 狀態檔（記住上次的發佈時間）
STATE_FILE = "state.json"


def load_state() -> dict:
    path = os.path.join(config.STATE_DIR, STATE_FILE)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_state(state: dict) -> None:
    os.makedirs(config.STATE_DIR, exist_ok=True)
    with open(os.path.join(config.STATE_DIR, STATE_FILE), "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def baseline_due(state: dict, key: str, t: datetime) -> str | None:
    """若現在已過某個基準整點、且今天還沒截過，回傳該整點字串（例如 '09'）。"""
    if not (min(config.BASELINE_HOURS) <= t.hour <= max(config.BASELINE_HOURS)):
        return None
    today = t.strftime("%Y-%m-%d")
    done = state.setdefault(key, {}).setdefault("baseline", {}).get(today, [])
    due = [h for h in config.BASELINE_HOURS if t.hour >= h and f"{h:02d}" not in done]
    return f"{max(due):02d}" if due else None


def mark_baseline(state: dict, key: str, t: datetime, hour: str) -> None:
    today = t.strftime("%Y-%m-%d")
    b = state.setdefault(key, {}).setdefault("baseline", {})
    # 只保留最近 7 天，避免狀態檔越來越大
    for d in sorted(b)[:-7]:
        b.pop(d, None)
    done = b.setdefault(today, [])
    for h in config.BASELINE_HOURS:
        if int(h) <= int(hour) and f"{h:02d}" not in done:
            done.append(f"{h:02d}")


# ------------------------------------------------------------------ CSV 紀錄
CSV_FIELDS = [
    "記錄時間", "網站", "層級", "地區", "關鍵時段內",
    "網頁發佈時間", "網頁有效時間", "燈號_網頁", "燈號_API",
    "數值", "補充說明", "截圖檔名", "狀態",
]


RAW_DIR = "_原始資料"  # 程式累加用的 CSV 底稿，集中放在 Drive 最外層，不放進每日資料夾


def raw_rel(day: str, name: str) -> str:
    return f"{RAW_DIR}/{day[:7]}/{day}/{name}"


def csv_path(day: str) -> str:
    path = os.path.join(config.OUT_DIR, raw_rel(day, f"每日紀錄_{day}.csv"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


def town_csv_path(day: str) -> str:
    path = os.path.join(config.OUT_DIR, raw_rel(day, f"體感溫度_{day}.csv"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


def append_rows(day: str, rows: list[dict]) -> None:
    if not rows:
        return
    path = csv_path(day)
    new = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8-sig" if new else "utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        if new:
            w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in CSV_FIELDS})


# ------------------------------------------------------------------ rclone
def rclone_available() -> bool:
    return shutil.which("rclone") is not None and bool(os.environ.get("RCLONE_CONFIG_GDRIVE_TOKEN"))


def _rclone(*args: str) -> subprocess.CompletedProcess:
    cmd = ["rclone", *args, "--retries", "3", "--low-level-retries", "5"]
    return subprocess.run(cmd, capture_output=True, text=True)


# 下載失敗（雲端有檔案但沒下載成功）的相對路徑；這些檔案本次一律不覆蓋
PULL_FAILED: set[str] = set()
# 本次無法與雲端合併的日期（CSV 改存補件檔，試算表本次不更新）
UNSYNCED: set[str] = set()


def _fetch(rel: str, local: str) -> str:
    """下載單一檔案，回傳 ok／missing／failed。"""
    os.makedirs(os.path.dirname(local) or ".", exist_ok=True)
    for attempt in range(3):
        res = _rclone("copyto", f"{config.RCLONE_REMOTE}{rel}", local)
        if res.returncode == 0:
            return "ok" if os.path.exists(local) else "missing"
        err = res.stderr.lower()
        if "not found" in err or "doesn't exist" in err:
            return "missing"
        import time
        time.sleep(5 * (attempt + 1))
    print(f"[rclone] 下載失敗：{rel}\n{res.stderr[-800:]}")
    return "failed"


def pull_existing(days: list[str]) -> None:
    """執行前先把雲端上的狀態檔、當日 CSV 與總表拉下來。下載失敗的檔案會記下來，本次不會覆蓋它。"""
    if not rclone_available():
        print("[rclone] 未設定，略過下載（僅本機輸出）")
        return
    r = config.RCLONE_REMOTE
    _rclone("copy", f"{r}_state", config.STATE_DIR)
    for day in days:
        for name in (f"每日紀錄_{day}.csv", f"體感溫度_{day}.csv"):
            rel = raw_rel(day, name)
            if _fetch(rel, os.path.join(config.OUT_DIR, rel)) == "failed":
                PULL_FAILED.add(rel)
    os.makedirs(config.OUT_DIR, exist_ok=True)
    for name in MASTER_FILES():
        if _fetch(name, os.path.join(config.OUT_DIR, name)) == "failed":
            PULL_FAILED.add(name)


def MASTER_FILES() -> tuple:
    return (config.MASTER_TOWN_XLSX, config.MASTER_TEMPTOP_XLSX, config.MASTER_W29_XLSX,
            config.MASTER_HEALTH_XLSX, config.MASTER_STATION_XLSX, config.MASTER_RECORD_XLSX)


def _read_rows(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _write_rows(path: str, rows: list[dict]) -> None:
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in CSV_FIELDS})


def union_rows(*groups: list[dict]) -> list[dict]:
    """合併多份紀錄，完全相同的列只留一份，依記錄時間排序（只增不減）。"""
    seen, out = set(), []
    for rows in groups:
        for r in rows:
            key = tuple(r.get(k, "") for k in CSV_FIELDS)
            if key not in seen:
                seen.add(key)
                out.append(r)
    out.sort(key=lambda r: r.get("記錄時間", ""))
    return out


def sync_raw(days: list[str]) -> list[str]:
    """上傳前重新下載雲端 CSV 與本機合併後再上傳，確保不會蓋掉雲端已有的紀錄。
    下載失敗時改存成補件檔（下次合併），回傳可安全更新試算表的日期。"""
    if not rclone_available():
        return list(days)
    import tempfile
    from datetime import datetime as _dt
    r = config.RCLONE_REMOTE
    ok_days = []
    for day in days:
        local = csv_path(day)
        rel = raw_rel(day, f"每日紀錄_{day}.csv")
        tmp = tempfile.mkdtemp()
        remote_copy = os.path.join(tmp, "remote.csv")
        state = _fetch(rel, remote_copy)
        if state == "failed":
            if os.path.exists(local):
                pend = raw_rel(day, f"每日紀錄_{day}_補件_{_dt.now(TZ):%H%M%S}.csv")
                _rclone("copyto", local, f"{r}{pend}")
                print(f"[同步] {day} 無法下載雲端紀錄，本次資料改存補件檔 {pend}")
            UNSYNCED.add(day)
            continue
        # 一併收進之前留下的補件檔
        pend_dir = os.path.join(tmp, "pending")
        listing = _rclone("lsf", f"{r}{common_dir(day)}", "--include", f"每日紀錄_{day}_補件_*.csv")
        pend_names = [x for x in listing.stdout.split() if x]
        pend_rows = []
        for name in pend_names:
            if _fetch(f"{common_dir(day)}/{name}", os.path.join(pend_dir, name)) == "ok":
                pend_rows.append(_read_rows(os.path.join(pend_dir, name)))
        merged = union_rows(_read_rows(remote_copy), *pend_rows, _read_rows(local))
        if not merged:
            ok_days.append(day)
            continue
        _write_rows(local, merged)
        res = _rclone("copyto", local, f"{r}{rel}")
        if res.returncode != 0:
            print(f"[同步] {day} 上傳失敗：{res.stderr[-500:]}")
            UNSYNCED.add(day)
            continue
        for name in pend_names:
            _rclone("deletefile", f"{r}{common_dir(day)}/{name}")
        ok_days.append(day)
    return ok_days


def common_dir(day: str) -> str:
    return f"{RAW_DIR}/{day[:7]}/{day}"


def push_all() -> bool:
    """上傳截圖與試算表。CSV 由 sync_raw 負責；下載失敗的總表與未同步日期的試算表本次不上傳。"""
    if not rclone_available():
        print("[rclone] 未設定，略過上傳")
        return True
    r = config.RCLONE_REMOTE
    excludes = ["--exclude", f"/{RAW_DIR}/**"]
    for rel in sorted(PULL_FAILED):
        excludes += ["--exclude", f"/{rel}"]
    for day in sorted(UNSYNCED):
        excludes += ["--exclude", f"/{day[:7]}/{day}/紀錄_{day}.xlsx"]
    ok = True
    for args in (("copy", config.OUT_DIR, r, *excludes), ("copy", config.STATE_DIR, f"{r}_state")):
        res = _rclone(*args)
        if res.returncode != 0:
            ok = False
            print(f"[rclone] 上傳失敗：{' '.join(args[:3])}\n{res.stderr[-2000:]}")
    # 體感溫度 CSV（每次整份重寫）與其他原始檔：只上傳本機有、且不是下載失敗的
    raw_root = os.path.join(config.OUT_DIR, RAW_DIR)
    for root, _, files in os.walk(raw_root):
        for name in files:
            if not name.startswith("體感溫度_"):
                continue
            local = os.path.join(root, name)
            rel = os.path.relpath(local, config.OUT_DIR).replace(os.sep, "/")
            if rel in PULL_FAILED:
                continue
            res = _rclone("copyto", local, f"{r}{rel}")
            ok = ok and res.returncode == 0
    if ok:
        print("[rclone] 已上傳到 Google Drive")
    if PULL_FAILED or UNSYNCED:
        print(f"[rclone] 本次為保護既有資料而略過：{sorted(PULL_FAILED)} {sorted(UNSYNCED)}")
    return ok
