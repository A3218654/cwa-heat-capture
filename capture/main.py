"""進入點。

用法：
  python -m capture.main --mode auto            # 依臺北時間自動判斷要做什麼（排程使用）
  python -m capture.main --mode heat            # 高溫資訊 + 熱傷害
  python -m capture.main --mode temptop-today   # 縣市溫度極值（今日）
  python -m capture.main --mode temptop-yesterday
  python -m capture.main --mode probe           # 探勘模式：存下頁面結構供除錯
加上 --force 會忽略「無變化不截圖」直接截圖。
"""
import argparse
import os
import sys
import traceback
from datetime import timedelta

from playwright.sync_api import sync_playwright

from . import common, config, extra, sites, workbook


def decide(mode: str, t) -> str:
    # 「今日最高溫」排程若被延遲到隔天才執行，改截「昨日」，資料才會對到正確日期
    if mode == "temptop-today" and t.hour < 12:
        return "temptop-yesterday"
    if mode != "auto":
        return mode
    if t.hour == 23 and t.minute >= 30:
        return "temptop-today"
    if t.hour in (0, 1):
        return "temptop-yesterday"
    return "heat"


def with_retry(fn, label: str, errors: list[str]):
    for attempt in range(config.RETRIES + 1):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            print(f"[{label}] 第 {attempt + 1} 次失敗：{e}")
            traceback.print_exc()
            if attempt == config.RETRIES:
                errors.append(f"{label}：{e}")
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="auto",
                    choices=["auto", "heat", "temptop-today", "temptop-yesterday", "probe",
                             "station", "town", "temptop-2daysago"])
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    t = common.now()
    mode = decide(args.mode, t)
    print(f"臺北時間 {common.stamp(t)}，執行模式：{mode}")

    today = t.strftime("%Y-%m-%d")
    yesterday = (t - timedelta(days=1)).strftime("%Y-%m-%d")
    data_day = {"temptop-yesterday": yesterday,
                "temptop-2daysago": (t - timedelta(days=2)).strftime("%Y-%m-%d")}.get(mode, today)
    if mode != "probe" and config.END_DAY and data_day > config.END_DAY:
        print(f"記錄期間已於 {config.END_DAY} 結束，本次不執行。")
        return 0
    day_before = (t - timedelta(days=2)).strftime("%Y-%m-%d")
    touched = [today, yesterday] + ([day_before] if mode == "temptop-2daysago" else [])
    common.pull_existing(touched)
    state = common.load_state()
    errors: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport=config.VIEWPORT, locale="zh-TW",
                                  timezone_id="Asia/Taipei", device_scale_factor=1)
        page = ctx.new_page()

        if mode == "probe":
            folder = sites.probe(page, t)
            print(f"探勘結果：{folder}")

        elif mode in ("temptop-today", "temptop-yesterday", "temptop-2daysago"):
            which = {"temptop-today": "今日", "temptop-yesterday": "昨日", "temptop-2daysago": "前日"}[mode]
            res = with_retry(lambda: sites.run_temptop(page, t, which), "縣市溫度極值", errors)
            if res:
                day, rows = res
                common.append_rows(day, rows)
            if mode == "temptop-today":  # 當天最後一次機會：補抓漏掉的測站整點與體感溫度
                rows = with_retry(lambda: extra.run_station(page, t, state, final=True), "臺北測站", errors) or []
                common.append_rows(today, rows)
                rows = with_retry(lambda: extra.run_town(page, t, state), "鄉鎮體感溫度", errors) or []
                common.append_rows(today, rows)

        elif mode == "station":
            rows = with_retry(lambda: extra.run_station(page, t, state), "臺北測站", errors) or []
            common.append_rows(today, rows)

        elif mode == "town":
            rows = with_retry(lambda: extra.run_town(page, t, state, force=args.force), "鄉鎮體感溫度", errors) or []
            common.append_rows(today, rows)

        else:  # heat
            rows = with_retry(lambda: sites.run_w29(page, t, state, args.force), "高溫資訊", errors) or []
            common.append_rows(today, rows)
            rows = with_retry(lambda: sites.run_health(page, t, state, args.force), "熱傷害", errors) or []
            common.append_rows(today, rows)
            rows = with_retry(lambda: extra.run_station(page, t, state), "臺北測站", errors) or []
            common.append_rows(today, rows)
            rows = with_retry(lambda: extra.run_town(page, t, state), "鄉鎮體感溫度", errors) or []
            common.append_rows(today, rows)

        # 失敗也寫一筆紀錄，證明這個時間點有執行
        for err in errors:
            common.append_rows(today, [{"記錄時間": common.stamp(t), "網站": err.split("：")[0],
                                        "關鍵時段內": "是" if common.in_window(t) else "否",
                                        "狀態": "失敗", "補充說明": err[:500]}])
        browser.close()

    common.save_state(state)
    if mode != "probe":
        synced = common.sync_raw(touched)   # 先與雲端合併 CSV（只增不減）
        workbook.refresh(synced)
    uploaded = common.push_all()
    if common.UNSYNCED:
        errors.append(f"無法與雲端紀錄合併，已改存補件檔，下次執行會自動合併：{sorted(common.UNSYNCED)}")
    if not common.rclone_available() and mode != "probe" and not os.environ.get("ALLOW_NO_UPLOAD"):
        errors.append("尚未設定 Google Drive（GDRIVE_TOKEN / GDRIVE_FOLDER_ID），本次結果沒有存檔")

    if errors or not uploaded:
        print("有錯誤，請查看上方訊息：", errors)
        return 1
    print("完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
