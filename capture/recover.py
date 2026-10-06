"""復原 10/4 與 10/6 上午被覆蓋的紀錄。

資料來源（都是當時程式實際存到 Drive 的佐證檔）：
- 高溫資訊：2_高溫資訊/HHMM_高溫資訊_Warning_63.js.txt（每次截圖時存的行政區燈號原始檔）
- 熱傷害：3_熱傷害/HHMM_熱傷害_臺北市資料.json（每次截圖時存的燈號原始資料）
- 體感溫度：_原始資料/…/體感溫度_日期.csv（未遺失）
- 臺北測站 10/4：當時執行紀錄的內容（manual_rows.json）

只會「新增」列，不會刪改現有紀錄。復原的列在補充說明註記「由佐證檔復原」。
環境變數：RECOVER_DAYS=2026-10-04,2026-10-06；RECOVER_APPLY=1 才會寫回 Drive（否則只產生報告）。
"""
import json
import os
import re
import sys
import tempfile

from . import common, config, sites, workbook
from .sites import TOWN_CODES, W29_LEVEL

NOTE = "由佐證檔復原（原紀錄於 10/6 被覆蓋）"
HERE = os.path.dirname(__file__)


def _row(day, hhmm, **kw):
    t = f"{day} {hhmm[:2]}:{hhmm[2:]}:00"
    h, m = int(hhmm[:2]), int(hhmm[2:])
    in_win = (config.WINDOW_START[0], config.WINDOW_START[1]) <= (h, m) <= (config.WINDOW_END[0], config.WINDOW_END[1])
    r = {"記錄時間": t, "關鍵時段內": "是" if in_win else "否"}
    r.update(kw)
    return r


def _list(rel_dir, pattern):
    res = common._rclone("lsf", f"{config.RCLONE_REMOTE}{rel_dir}", "--include", pattern)
    return sorted(x for x in res.stdout.split() if x)


def w29_rows(day, tmp):
    rows = []
    d = f"{day[:7]}/{day}/2_高溫資訊"
    for name in _list(d, "*_Warning_63.js.txt"):
        hhmm = name[:4]
        local = os.path.join(tmp, name)
        if common._fetch(f"{d}/{name}", local) != "ok":
            continue
        raw = open(local, encoding="utf-8").read()
        lights = {}
        for code, items in re.findall(r"'(\d{7})'\s*:\s*\[([^\]]*)\]", raw):
            if code in TOWN_CODES:
                lv = [W29_LEVEL[x] for x in re.findall(r"W29(?:-\d)?", items) if x in W29_LEVEL]
                order = ["黃色", "橙色", "紅色"]
                lights[TOWN_CODES[code]] = max(lv, key=order.index) if lv else "無"
        order = ["無", "黃色", "橙色", "紅色"]
        county = max(lights.values(), key=order.index) if lights else "無"
        shot = f"{hhmm}_高溫資訊_全縣市.png"
        rows.append(_row(day, hhmm, 網站="2_高溫資訊", 層級="縣市", 地區=config.COUNTY, 燈號_網頁=county,
                         截圖檔名=shot, 狀態="已截圖", 補充說明=f"{NOTE}；臺北市燈號取各區最高；資料檔 {name}"))
        for dname in config.DISTRICTS:
            rows.append(_row(day, hhmm, 網站="2_高溫資訊", 層級="行政區", 地區=f"{config.COUNTY}{dname}",
                             燈號_網頁=lights.get(dname, "無"), 截圖檔名=shot, 狀態="已記錄",
                             補充說明=f"{NOTE}；燈號取自 {name}"))
    return rows


def health_rows(day, tmp):
    rows = []
    d = f"{day[:7]}/{day}/3_熱傷害"
    for name in _list(d, "*_熱傷害_臺北市資料.json"):
        hhmm = name[:4]
        local = os.path.join(tmp, name)
        if common._fetch(f"{d}/{name}", local) != "ok":
            continue
        subset = json.load(open(local, encoding="utf-8"))
        today = {x["townName"]: sites.HEALTH_STATUS.get(x.get("warnStatus"), "")
                 for x in subset.get("getWarnTown", [])}
        slots = {}
        for x in subset.get("getUserRiskForecast", []):
            slots[x["town"]] = [{"time": (e.get("dt") or "")[:16], "index": e.get("wbgt", ""),
                                 "warning": "" if not e.get("warnStatus")
                                 else sites.HEALTH_STATUS.get(e["warnStatus"], str(e["warnStatus"]))}
                                for e in x.get("dailyHealth", [])]
        rank = list(sites.HEALTH_STATUS.values())
        county = max((today.get(dn, "無") or "無" for dn in config.DISTRICTS), key=rank.index) if today else ""
        rows.append(_row(day, hhmm, 網站="3_熱傷害", 層級="縣市", 地區=config.COUNTY, 燈號_網頁=county,
                         截圖檔名=f"{hhmm}_熱傷害_臺北市.png", 狀態="已截圖",
                         補充說明=f"{NOTE}；縣市燈號取各區最高；資料檔 {name}"))
        for dn in config.DISTRICTS:
            win = sites._window_entries(slots.get(dn, []), day)
            rows.append(_row(day, hhmm, 網站="3_熱傷害", 層級="行政區", 地區=f"{config.COUNTY}{dn}",
                             燈號_網頁=today.get(dn, ""), 數值=sites._fmt(win),
                             截圖檔名=f"{hhmm}_熱傷害_臺北市{dn}.png", 狀態="已截圖", 補充說明=NOTE))
    return rows


def town_rows(day):
    """10/4 的體感溫度執行列（體感溫度 CSV 未遺失，依它還原每日紀錄中的對應列）。"""
    path = common.town_csv_path(day)
    rel = common.raw_rel(day, f"體感溫度_{day}.csv")
    if common._fetch(rel, path) != "ok":
        return []
    manual = json.load(open(os.path.join(HERE, "manual_rows.json"), encoding="utf-8")).get("town_time", {})
    hhmm = manual.get(day)
    if not hhmm:
        return []
    rows = []
    mmdd = f"{day[5:7]}/{day[8:10]}"
    import csv
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if r.get("項目") != "體感溫度":
                continue
            vals = [(h, r.get(f"{h:02d}時", "")) for h in config.TOWN_HOURS]
            txt = " ".join(f"{h:02d}時{v}" for h, v in vals if v != "")
            miss = [f"{h:02d}時" for h, v in vals if v == ""]
            note = f"最高體感 {r.get('最高')}°C" + (f"；缺 {', '.join(miss)}" if miss else "")
            rows.append({"記錄時間": f"{day} {hhmm[:2]}:{hhmm[2:4]}:{hhmm[4:]}", "網站": "5_鄉鎮體感溫度",
                         "層級": "行政區", "地區": r["行政區"], "網頁發佈時間": f"{mmdd} 07–18時",
                         "數值": f"體感溫度 {txt}", "截圖檔名": f"{hhmm[:4]}_體感溫度_{r['行政區']}.png",
                         "狀態": "已截圖", "補充說明": f"{note}；{NOTE}"})
    return rows


def manual_rows(day):
    data = json.load(open(os.path.join(HERE, "manual_rows.json"), encoding="utf-8"))
    return [dict(r, 補充說明=f"{r.get('補充說明', '')}；{NOTE}") for r in data.get("rows", {}).get(day, [])]


def main() -> int:
    days = [d.strip() for d in os.environ.get("RECOVER_DAYS", "").split(",") if d.strip()]
    apply = os.environ.get("RECOVER_APPLY") == "1"
    report = []
    common.pull_existing(days)
    for day in days:
        tmp = tempfile.mkdtemp()
        current = common._read_rows(common.csv_path(day))
        rebuilt = w29_rows(day, tmp) + health_rows(day, tmp) + town_rows(day) + manual_rows(day)
        existing_keys = {(r.get("記錄時間", "")[:16], r.get("網站"), r.get("地區")) for r in current}
        new = [r for r in rebuilt if (r["記錄時間"][:16], r["網站"], r["地區"]) not in existing_keys]
        report.append(f"===== {day}：現有 {len(current)} 列，可復原 {len(new)} 列")
        for r in new:
            report.append(" | ".join(str(r.get(k, "")) for k in
                                     ("記錄時間", "網站", "地區", "關鍵時段內", "燈號_網頁", "數值", "截圖檔名")))
        if apply and new:
            common.append_rows(day, new)
    os.makedirs(config.OUT_DIR, exist_ok=True)
    if apply:
        ok = common.sync_raw(days)
        workbook.refresh(ok)
        common.push_all()
        report.append(f"已寫回：{ok}；未同步：{sorted(common.UNSYNCED)}")
    with open(os.path.join(config.OUT_DIR, "_purge_report.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(report))
    print("\n".join(report[:50]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
