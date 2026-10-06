"""新增的兩個紀錄項目。

4. 臺北測站（466920）11、12、13 時整點觀測：
   測站頁面列出過去 24 小時每 10 分鐘的觀測（tbody#obstime）。每到整點後第一次執行，
   就把該整點那一列標黃、截圖並記錄；若當時那一列還沒上線，下一次執行再補。
   當天 23:55 的執行會補抓任何漏掉的整點（表格保留 24 小時）。

5. 臺北市 12 區逐時體感溫度（07–18 時）：
   鄉鎮預報頁「過去 24 小時」表格逐時列出溫度與體感溫度，資料來自
   /Data/js/GT/ChartData_GT24hr_T_63.js（12 區共用一個檔）。每天 19 時後抓一次：
   數值從資料檔讀取，每區開一次頁面，把表格捲到 07:00 起截圖。
"""
import csv
import json
import os
import re

from playwright.sync_api import Page

from . import common, config
from .sites import TOWN_CODES, base_row, open_page, save_text, settle

SITE_STATION = "4_臺北測站逐時"
SITE_TOWN = "5_鄉鎮體感溫度"


def _days_state(state: dict, key: str, day: str):
    st = state.setdefault(key, {})
    for d in sorted(st)[:-7]:  # 只留最近 7 天
        st.pop(d, None)
    return st


# ================================================================== 4. 臺北測站
def _station_rows(page: Page) -> list[dict]:
    return page.evaluate(
        """() => [...document.querySelectorAll('#obstime tr')].map((tr, i) => {
            const th = tr.querySelector('th');
            const txt = th ? th.innerText.replace(/\\s+/g, ' ').trim() : '';
            const m = txt.match(/(\\d{2}\\/\\d{2})\\s+(\\d{2}:\\d{2})/);
            const tds = [...tr.querySelectorAll('td')];
            const first = el => el ? ((el.querySelector('span') || el).innerText || '').trim() : '';
            const img = tds[1] ? tds[1].querySelector('img') : null;
            return {i, date: m ? m[1] : '', time: m ? m[2] : '',
                    temp: first(tds[0]), weather: img ? img.getAttribute('title') : '',
                    rh: tds[6] ? tds[6].innerText.trim() : ''};
        })"""
    )


def run_station(page: Page, t, state: dict, final: bool = False, force: bool = False) -> list[dict]:
    """final=True 表示當天最後一次機會（23:55），抓不到的整點要記成缺漏。"""
    day = t.strftime("%Y-%m-%d")
    done = _days_state(state, "station", day).setdefault(day, [])
    due = [h for h in config.STATION_HOURS if h <= t.hour and (force or h not in done)]
    if not due:
        return []

    open_page(page, config.URL_STATION)
    page.wait_for_selector("#obstime tr", timeout=30_000)
    rows = _station_rows(page)
    mmdd = t.strftime("%m/%d")
    latest = f"{rows[0]['date']} {rows[0]['time']}" if rows else ""
    out = common.site_dir(day, SITE_STATION)
    records = []

    for h in due:
        hhmm = f"{h:02d}:00"
        hit = next((r for r in rows if r["date"] == mmdd and r["time"] == hhmm), None)
        if not hit:
            if final:
                records.append(base_row(t, SITE_STATION, "測站", config.STATION_NAME,
                                        網頁發佈時間=f"{mmdd} {hhmm}", 狀態="缺漏",
                                        補充說明=f"測站表格找不到 {hhmm} 這一列（表格最新 {latest}）"))
                done.append(h)
            continue

        # 標黃該列，截取從頁首到該列的範圍
        box = page.evaluate(
            """(i) => { const tr = document.querySelectorAll('#obstime tr')[i];
                        tr.style.outline = '3px solid #e60000';
                        tr.querySelectorAll('th,td').forEach(c => c.style.background = '#fff176');
                        const r = tr.getBoundingClientRect();
                        return {bottom: r.bottom + window.scrollY,
                                width: document.documentElement.scrollWidth}; }""",
            hit["i"],
        )
        path = os.path.join(out, f"{t:%H%M}_臺北測站_{h:02d}時.png")
        page.screenshot(path=path, full_page=True,
                        clip={"x": 0, "y": 0, "width": box["width"], "height": box["bottom"] + 12})
        page.evaluate(
            """(i) => { const tr = document.querySelectorAll('#obstime tr')[i];
                        tr.style.outline = ''; tr.querySelectorAll('th,td').forEach(c => c.style.background = ''); }""",
            hit["i"],
        )
        path = common.watermark(path, [
            f"截圖時間：{common.stamp(t)}（臺北時間 UTC+8）",
            f"來源：{config.URL_STATION}",
            f"臺北測站 {mmdd} {hhmm} 觀測（黃色列）｜溫度 {hit['temp']}°C ｜ {hit['weather']} ｜ 相對溼度 {hit['rh']}%",
        ])
        records.append(base_row(t, SITE_STATION, "測站", config.STATION_NAME,
                                網頁發佈時間=f"{mmdd} {hhmm}", 數值=f"{hit['temp']}°C",
                                截圖檔名=os.path.basename(path), 狀態="已截圖",
                                補充說明=f"{hhmm} 整點觀測；{hit['weather']}；相對溼度 {hit['rh']}%"))
        if h not in done:
            done.append(h)
        if force:
            records[-1]["補充說明"] += "；補抓（原紀錄於 10/6 被覆蓋，數值取自測站頁面過去 24 小時表格）"
    return records


# ================================================================== 5. 鄉鎮體感溫度
def _parse_24hr(raw: str) -> tuple[list[tuple[str, int]], dict]:
    """回傳 ([(MM/DD, 時), ...], {鄉鎮代碼: {'T': [...], 'AT': [...]}})。"""
    labels = [(d, int(h)) for h, d in re.findall(r"'(\d{2}) (\d{2}/\d{2})<br>", raw.split("TempArray")[0])]
    data = {}
    for code, body in re.findall(r"'(\d{7})'\s*:\s*\{\s*'C'\s*:\s*(\{[^{}]*\})", raw):
        try:
            data[code] = json.loads(body.replace("'", '"'))
        except ValueError:
            continue
    return labels, data


def _scroll_table_to(page: Page, label: str) -> float | None:
    """把「過去 24 小時」表格捲到 label（例如 07:00）那一欄，回傳表格底部的頁面座標。"""
    return page.evaluate(
        """(label) => {
            const cells = [...document.querySelectorAll('th,td')]
                .filter(e => e.offsetParent && e.innerText.trim() === label);
            if (!cells.length) return null;
            const c = cells[0];
            c.closest('table').style.zoom = '0.9';  // 縮小一點，讓 07–18 時 12 欄完整入鏡
            let p = c.parentElement;
            while (p && p !== document.body && !(p.scrollWidth > p.clientWidth + 5)) p = p.parentElement;
            const firstCol = c.closest('tr').querySelector('th,td');
            if (p && p !== document.body) {
                p.scrollLeft = 0;
                const off = c.getBoundingClientRect().left - p.getBoundingClientRect().left;
                p.scrollLeft = off - (firstCol ? firstCol.offsetWidth : 0);
            }
            const r = c.closest('table').getBoundingClientRect();
            return r.bottom + window.scrollY;
        }""",
        label,
    )


def run_town(page: Page, t, state: dict, force: bool = False) -> list[dict]:
    day = t.strftime("%Y-%m-%d")
    st = _days_state(state, "town", day)
    if (st.get(day) or t.hour < config.TOWN_READY_HOUR) and not force:
        return []

    mmdd = t.strftime("%m/%d")
    out = common.site_dir(day, SITE_TOWN)
    codes = list(TOWN_CODES)  # 6300100 … 6301200，順序同 config.DISTRICTS

    # 先開第一區取得資料檔（12 區共用）
    open_page(page, config.URL_TOWN.format(tid=codes[0]))
    raw = page.evaluate("(u) => fetch(u + '?_=' + Date.now()).then(r => r.ok ? r.text() : '')",
                        config.URL_TOWN_24HR_DATA)
    labels, data = _parse_24hr(raw)
    if not labels or not data:
        raise RuntimeError("讀不到過去 24 小時資料檔 ChartData_GT24hr_T_63.js")
    save_text(os.path.join(out, f"{t:%H%M}_ChartData_GT24hr_T_63.js.txt"), raw)
    idx = {h: labels.index((mmdd, h)) for h in config.TOWN_HOURS if (mmdd, h) in labels}

    table = []   # 給彙整 CSV
    records = []
    for i, code in enumerate(codes):
        name = TOWN_CODES[code]
        series = data.get(code, {})
        at = {h: series.get("AT", [None] * 24)[j] for h, j in idx.items()}
        tt = {h: series.get("T", [None] * 24)[j] for h, j in idx.items()}
        missing = [h for h in config.TOWN_HOURS if at.get(h) is None]
        at_text = " ".join(f"{h:02d}時{at[h]}" for h in config.TOWN_HOURS if at.get(h) is not None)
        hi = max((v for v in at.values() if v is not None), default=None)

        shot, note = "", ""
        try:
            if i > 0:
                open_page(page, config.URL_TOWN.format(tid=code))
            first = f"{config.TOWN_HOURS[0]:02d}:00"
            bottom = None
            for attempt in range(3):  # 表格有時載入較慢：等待後重試，必要時重新開頁
                if attempt == 2:
                    open_page(page, config.URL_TOWN.format(tid=code))
                page.locator("#Tab_24hrTable").first.click(timeout=10_000)
                try:
                    page.wait_for_function(
                        """(label) => [...document.querySelectorAll('th,td')]
                            .some(e => e.offsetParent && e.innerText.trim() === label)""",
                        arg=first, timeout=15_000)
                except Exception:  # noqa: BLE001
                    pass
                settle(page, 1000)
                bottom = _scroll_table_to(page, first)
                if bottom is not None:
                    break
            if bottom is None:
                raise RuntimeError(f"表格中找不到 {first} 欄")
            path = os.path.join(out, f"{t:%H%M}_體感溫度_臺北市{name}.png")
            width = page.evaluate("() => document.documentElement.scrollWidth")
            page.screenshot(path=path, full_page=True,
                            clip={"x": 0, "y": 0, "width": width, "height": bottom + 16})
            path = common.watermark(path, [
                f"截圖時間：{common.stamp(t)}（臺北時間 UTC+8）",
                f"來源：{config.URL_TOWN.format(tid=code)}（過去24小時）",
                f"臺北市{name} {mmdd} 體感溫度 ｜ {at_text} ｜ 最高 {hi}°C",
            ])
            shot = os.path.basename(path)
        except Exception as e:  # noqa: BLE001
            note = f"截圖失敗：{e}"

        table.append({"行政區": f"臺北市{name}", "項目": "體感溫度",
                      **{f"{h:02d}時": at.get(h, "") for h in config.TOWN_HOURS}, "最高": hi})
        table.append({"行政區": f"臺北市{name}", "項目": "溫度",
                      **{f"{h:02d}時": tt.get(h, "") for h in config.TOWN_HOURS},
                      "最高": max((v for v in tt.values() if v is not None), default="")})
        if missing:
            note = "；".join(x for x in (note, f"缺 {', '.join(f'{h:02d}時' for h in missing)}") if x)
        records.append(base_row(t, SITE_TOWN, "行政區", f"臺北市{name}",
                                網頁發佈時間=f"{mmdd} 07–18時", 數值=f"體感溫度 {at_text}",
                                截圖檔名=shot, 狀態="已截圖" if shot else "已記錄-截圖失敗",
                                補充說明="；".join(x for x in (f"最高體感 {hi}°C", note) if x)))

    # 彙整表：一區兩列（體感溫度、溫度），欄位為 07–18 時
    path = common.town_csv_path(day)
    fields = ["行政區", "項目", *[f"{h:02d}時" for h in config.TOWN_HOURS], "最高"]
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(table)

    for r in records:
        r["關鍵時段內"] = ""
    st[day] = True
    return records
