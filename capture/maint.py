"""維護工具：清除誤上傳到 Google Drive 的測試紀錄。

環境變數：
  PURGE_DAY    要處理的日期，例如 2026-10-03
  PURGE_STAMP  要刪掉的 CSV 列的「記錄時間」開頭，例如 "2026-10-03 23:50"
  PURGE_PATHS  要刪除的雲端路徑（相對於 Drive 資料夾，以 | 分隔）
"""
import csv
import os
import sys

from . import common, config


def main() -> int:
    day = os.environ["PURGE_DAY"]
    stamp = os.environ.get("PURGE_STAMP", "")
    if stamp == "MIGRATE_RAW":
        stamp = ""
    paths = [p for p in os.environ.get("PURGE_PATHS", "").split("|") if p.strip()]
    if not common.rclone_available():
        print("rclone 未設定")
        return 1
    r = config.RCLONE_REMOTE
    if os.environ.get("MIGRATE_RAW") == "1":  # 一次性：把每日資料夾裡的 CSV 搬到 _原始資料
        months = [m.strip("/") for m in common._rclone("lsf", "--dirs-only", r).stdout.split()
                  if len(m.strip("/")) == 7 and m[4] == "-"]
        for m in months:
            res = common._rclone("move", f"{r}{m}", f"{r}{common.RAW_DIR}/{m}",
                                 "--include", "*/每日紀錄_*.csv", "--include", "*/體感溫度_*.csv")
            print("搬移", m, "OK" if res.returncode == 0 else res.stderr[-300:])
    report = []

    def log(*a):  # 同時印出並寫進報告
        report.append(" ".join(str(x) for x in a))
        print(*a)

    if stamp == "FETCH_DOCS":  # 取回總表、當天試算表與幾張截圖範例（給說明文件用）
        stamp = ""
        d = os.path.join(config.OUT_DIR, "_docs")
        common._rclone("copy", r, d, "--max-depth", "1", "--include", "*.xlsx")
        common._rclone("copy", f"{r}{day[:7]}/{day}", os.path.join(d, day), "--include", "*.xlsx",
                       "--include", "*/*.jpg", "--include", "*/*.png", "--max-size", "3M")
        prev = os.environ.get("PREV_DAY", "")
        if prev:
            common._rclone("copy", f"{r}{prev[:7]}/{prev}/1_縣市溫度極值", os.path.join(d, prev, "1_縣市溫度極值"),
                           "--include", "2355_*")
        log("---- 每日資料夾 ----\n" + common._rclone("lsf", f"{r}{day[:7]}/{day}").stdout)
        log("---- 月份資料夾 ----\n" + common._rclone("lsf", f"{r}{day[:7]}").stdout)

    for p in paths:
        res = common._rclone("purge", f"{r}{p}") if not p.endswith((".csv", ".png", ".txt", ".json", ".xlsx")) \
            else common._rclone("deletefile", f"{r}{p}")
        log("刪除", p, "OK" if res.returncode == 0 else res.stderr[-300:])
    if stamp:
        common.pull_existing([day])
        path = common.csv_path(day)
        with open(path, encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        keep = [x for x in rows if not x.get("記錄時間", "").startswith(stamp)]
        log(f"CSV {len(rows)} 列 → 保留 {len(keep)} 列")
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=common.CSV_FIELDS, extrasaction="ignore")
            w.writeheader()
            w.writerows(keep)
        rel = common.raw_rel(day, f"每日紀錄_{day}.csv")
        res = common._rclone("copyto", path, f"{r}{rel}")
        log("CSV 上傳", "OK" if res.returncode == 0 else res.stderr[-300:])
    if not stamp:  # 只查詢：列出當天失敗與新項目的紀錄
        common.pull_existing([day])
        path = common.csv_path(day)
        if os.path.exists(path):
            with open(path, encoding="utf-8-sig") as f:
                for x in csv.DictReader(f):
                    if x.get("狀態") in ("失敗", "缺漏") or x.get("網站", "")[:1] in "45" or "縣市溫度極值" in x.get("網站", ""):
                        log(" | ".join(x.get(k, "") for k in ("記錄時間", "網站", "地區", "網頁發佈時間", "數值", "狀態", "補充說明")))
    top = common._rclone("lsf", "--max-depth", "1", r)
    log("---- Drive 最外層 ----\n" + top.stdout)
    ls = common._rclone("lsf", "-R", f"{r}{day[:7]}/{day}")
    log("---- 雲端目前內容 ----\n" + ls.stdout)
    os.makedirs(config.OUT_DIR, exist_ok=True)
    with open(os.path.join(config.OUT_DIR, "_purge_report.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
