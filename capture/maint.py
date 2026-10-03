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
    paths = [p for p in os.environ.get("PURGE_PATHS", "").split("|") if p.strip()]
    if not common.rclone_available():
        print("rclone 未設定")
        return 1
    r = config.RCLONE_REMOTE
    report = []

    def log(*a):  # 同時印出並寫進報告
        report.append(" ".join(str(x) for x in a))
        print(*a)

    for p in paths:
        res = common._rclone("purge", f"{r}{p}") if not p.endswith((".csv", ".png", ".txt", ".json")) \
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
        rel = f"{day[:7]}/{day}/每日紀錄_{day}.csv"
        res = common._rclone("copyto", path, f"{r}{rel}")
        log("CSV 上傳", "OK" if res.returncode == 0 else res.stderr[-300:])
    ls = common._rclone("lsf", "-R", f"{r}{day[:7]}/{day}")
    log("---- 雲端目前內容 ----\n" + ls.stdout)
    os.makedirs(config.OUT_DIR, exist_ok=True)
    with open(os.path.join(config.OUT_DIR, "_purge_report.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
