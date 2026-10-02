"""三個網站的抓取邏輯（Playwright 同步版）。

網頁元素沒有固定 id，因此「點選臺北市」採多種策略依序嘗試，
並把成功的策略寫進 CSV 的「補充說明」，方便日後網站改版時排查。
"""
import hashlib
import json
import os
import re

import requests
from playwright.sync_api import Page

from . import api, common, config

SITE_TEMPTOP = "1_縣市溫度極值"
SITE_W29 = "2_高溫資訊"
SITE_HEALTH = "3_熱傷害"


# ================================================================== 共用網頁操作
def open_page(page: Page, url: str) -> None:
    page.goto(url, wait_until="domcontentloaded", timeout=config.PAGE_TIMEOUT_MS)
    settle(page)


def settle(page: Page, extra_ms: int = 1500) -> None:
    try:
        page.wait_for_load_state("networkidle", timeout=20_000)
    except Exception:  # noqa: BLE001  有些網站會一直有背景連線
        pass
    page.wait_for_timeout(extra_ms)


def select_area(page: Page, names: list[str]) -> str:
    """依序嘗試多種方式點選地區，回傳成功的策略名稱；全部失敗則丟出例外。"""
    for name in names:
        # 1. 下拉選單 <select>
        for sel in page.locator("select").all():
            try:
                opts = [o.strip() for o in sel.locator("option").all_inner_texts()]
            except Exception:  # noqa: BLE001
                continue
            if name in opts:
                sel.select_option(label=name)
                settle(page)
                return f"下拉選單:{name}"
        # 2. 畫面上看得到、文字完全相同的元素（優先可點的連結與按鈕）
        loc = page.get_by_text(name, exact=True)
        candidates = []
        for i in range(min(loc.count(), 30)):
            el = loc.nth(i)
            try:
                if not el.is_visible():
                    continue
                tag = el.evaluate("e => e.tagName.toLowerCase()")
            except Exception:  # noqa: BLE001
                continue
            if tag in ("td", "th"):
                continue  # 表格內的文字不是選單，點了也不會切換地區
            priority = 0 if tag in ("a", "button", "li", "option", "label") else 1
            candidates.append((priority, i))
        for _, i in sorted(candidates):
            try:
                loc.nth(i).click(timeout=5_000)
                settle(page)
                return f"文字點擊:{name}"
            except Exception:  # noqa: BLE001
                continue
        # 3. title / aria-label 屬性（常見於 SVG 地圖）
        for css in (f'[title="{name}"]', f'[aria-label="{name}"]', f'[data-name="{name}"]'):
            loc = page.locator(css)
            for i in range(min(loc.count(), 10)):
                try:
                    loc.nth(i).click(timeout=5_000, force=True)
                    settle(page)
                    return f"屬性點擊:{css}"
                except Exception:  # noqa: BLE001
                    continue
        # 4. 隱藏在收合選單裡的項目：直接用 JavaScript 觸發點擊
        ok = page.evaluate(
            """(name) => {
                const els = [...document.querySelectorAll('a,li,button,span,div,label,path,g,text')];
                const el = els.find(e => (e.textContent || '').trim() === name
                                      || e.getAttribute('title') === name
                                      || e.getAttribute('data-name') === name);
                if (!el) return false;
                el.dispatchEvent(new MouseEvent('click', {bubbles: true}));
                return true;
            }""",
            name,
        )
        if ok:
            settle(page)
            return f"JS點擊:{name}"
    raise RuntimeError(f"找不到可點選的地區：{names}")


def table_rows(page: Page) -> list[list[str]]:
    """讀出頁面上所有表格列；每格包含文字、圖片 alt/title 與 class，用來判斷燈號顏色。"""
    return page.evaluate(
        """() => [...document.querySelectorAll('table tr')].map(tr =>
            [...tr.querySelectorAll('td,th')].map(td => {
                const parts = [td.innerText.trim()];
                td.querySelectorAll('img,span,i,div').forEach(e => {
                    ['alt','title','class'].forEach(a => { const v = e.getAttribute(a); if (v) parts.push(v); });
                    const bg = getComputedStyle(e).backgroundColor;
                    if (bg && bg !== 'rgba(0, 0, 0, 0)') parts.push('bg:' + bg);
                });
                const cls = td.getAttribute('class'); if (cls) parts.push(cls);
                return parts.join(' | ');
            }))"""
    )


LIGHT_WORDS = [
    ("紅色", ("紅", "red", "rgb(255, 0, 0)", "rgb(230, 0, 18)")),
    ("橙色", ("橙", "橘", "orange", "rgb(255, 165, 0)", "rgb(255, 128, 0)")),
    ("黃色", ("黃", "yellow", "rgb(255, 255, 0)")),
]


def light_of(text: str) -> str:
    low = text.lower()
    for name, words in LIGHT_WORDS:
        if any(w in low for w in words):
            return name
    return "無"


def find_row(rows: list[list[str]], name: str, exact: bool = False) -> list[str] | None:
    for r in rows:
        if not r:
            continue
        first = r[0].split(" | ")[0].strip()
        if (first == name) if exact else (name in first):
            return r
    return None


def labeled_time(page: Page, label: str) -> str:
    """讀取「發佈時間：…」這類欄位的整行文字（有效時間通常是一段區間）。"""
    text = page.inner_text("body")
    m = re.search(label + r"[：:][ \t]*([^\n]{1,60})", text)
    if not m:
        return ""
    value = m.group(1).strip()
    # 同一行若接著別的欄位（例如「有效時間：」），只保留本欄位
    value = re.split(r"\s*(?:發佈時間|發布時間|有效時間|更新時間)[：:]", value)[0]
    return value.strip()


def shoot(page: Page, path: str, t, url: str, extra: str) -> str:
    page.screenshot(path=path, full_page=True)
    common.watermark(path, [
        f"截圖時間：{common.stamp(t)}（臺北時間 UTC+8）",
        f"來源：{url}",
        extra,
    ])
    return os.path.basename(path)


def short_hash(obj) -> str:
    return hashlib.sha1(json.dumps(obj, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:12]


def base_row(t, site: str, level: str, area: str, **kw) -> dict:
    row = {
        "記錄時間": common.stamp(t), "網站": site, "層級": level, "地區": area,
        "關鍵時段內": "是" if common.in_window(t) else "否",
    }
    row.update(kw)
    return row


# ================================================================== 網站一：縣市溫度極值
def run_temptop(page: Page, t, which: str) -> tuple[str, list[dict]]:
    """which = '今日' 或 '昨日'。回傳 (資料日期, CSV 列)。"""
    from datetime import timedelta

    day = (t if which == "今日" else t - timedelta(days=1)).strftime("%Y-%m-%d")
    open_page(page, config.URL_TEMPTOP)
    notes = [select_area(page, [which]), select_area(page, ["高溫"])]
    try:
        page.wait_for_function(
            "() => [...document.querySelectorAll('table tr')].some(tr => tr.innerText.includes('臺北市'))",
            timeout=20_000,
        )
    except Exception:  # noqa: BLE001
        notes.append("表格未出現臺北市")
    row = find_row(table_rows(page), config.COUNTY, exact=True)
    cells = [c.split(" | ")[0] for c in row] if row else []
    temp, obs_time, station = (cells + ["", "", "", ""])[1:4] if cells else ("", "", "")

    fname = f"{t:%H%M}_縣市溫度極值_{which}_高溫.png"
    shot = shoot(page, os.path.join(common.site_dir(day, SITE_TEMPTOP), fname), t, config.URL_TEMPTOP,
                 f"資料日期：{day}（{which}）｜臺北市最高溫 {temp}°C ｜ 觀測時間 {obs_time} ｜ 測站 {station}")

    api_note = ""
    if which == "今日" and config.CWA_API_KEY:
        try:
            hi = api.taipei_daily_high()
            if hi:
                api_note = f"API核對：{hi['value']}°C {hi['station']}({hi['station_id']}) {hi['time'][11:16]}"
        except Exception as e:  # noqa: BLE001
            api_note = f"API核對失敗：{e}"
    rec = base_row(t, SITE_TEMPTOP, "縣市", config.COUNTY,
                   **{"網頁發佈時間": obs_time, "數值": temp,
                      "補充說明": "；".join([f"資料日期{day}({which})", f"測站{station}", api_note, *notes]),
                      "截圖檔名": shot, "狀態": "已截圖" if cells else "已截圖-未讀到數值"})
    rec["關鍵時段內"] = ""
    return day, [rec]


# ================================================================== 網站二：高溫資訊
def run_w29(page: Page, t, state: dict, force: bool = False) -> list[dict]:
    day = t.strftime("%Y-%m-%d")
    open_page(page, config.URL_W29)
    issue = labeled_time(page, "發佈時間")
    valid = labeled_time(page, "有效時間")
    all_rows = table_rows(page)
    county_row = find_row(all_rows, config.COUNTY, exact=True)
    county_light = light_of(" ".join(county_row[1:])) if county_row else "無"

    version = issue or short_hash(all_rows)
    st = state.setdefault("w29", {})
    changed = version != st.get("last_version")
    base = common.baseline_due(state, "w29", t)
    common_kw = {"網頁發佈時間": issue, "網頁有效時間": valid}

    if not (changed or base or force):
        return [base_row(t, SITE_W29, "縣市", config.COUNTY, 燈號_網頁=county_light,
                         狀態="檢查-無變化", 補充說明=f"發佈時間與上次相同（{issue}）", **common_kw)]

    reason = "手動強制" if force else ("發佈時間變更" if changed else f"基準截圖{base}時")
    out = os.path.join(common.site_dir(day, SITE_W29))
    rows: list[dict] = []

    # 臺北市整體
    strat = select_area(page, [config.COUNTY])
    county_rows = table_rows(page)
    shot = shoot(page, os.path.join(out, f"{t:%H%M}_高溫資訊_臺北市.png"), t, config.URL_W29,
                 f"網頁發佈時間：{issue} ｜ 有效時間：{valid} ｜ 地區：臺北市 ｜ 燈號：{county_light} ｜ 原因：{reason}")
    rows.append(base_row(t, SITE_W29, "縣市", config.COUNTY, 燈號_網頁=county_light, 截圖檔名=shot,
                         狀態="已截圖", 補充說明=f"{reason}；{strat}", **common_kw))

    # 12 個行政區
    for d in config.DISTRICTS:
        r = find_row(county_rows, d) or find_row(all_rows, f"{config.COUNTY}{d}")
        light = light_of(" ".join(r[1:])) if r else "無"
        shot, note, status = "", "", "已記錄"
        if config.DISTRICT_SCREENSHOTS:
            try:
                note = select_area(page, [f"{config.COUNTY}{d}", d])
                r2 = find_row(table_rows(page), d)
                if r2 and light == "無":
                    light = light_of(" ".join(r2[1:]))
                shot = shoot(page, os.path.join(out, f"{t:%H%M}_高溫資訊_臺北市{d}.png"), t, config.URL_W29,
                             f"網頁發佈時間：{issue} ｜ 地區：臺北市{d} ｜ 燈號：{light} ｜ 原因：{reason}")
                status = "已截圖"
            except Exception as e:  # noqa: BLE001
                note, status = f"行政區截圖失敗：{e}", "已記錄-截圖失敗"
        rows.append(base_row(t, SITE_W29, "行政區", f"{config.COUNTY}{d}", 燈號_網頁=light, 截圖檔名=shot,
                             狀態=status, 補充說明=f"{reason}；{note}".strip("；"), **common_kw))

    # 官方整張圖備份
    try:
        img = requests.get(config.URL_W29_IMAGE, timeout=30)
        if img.ok:
            p = os.path.join(out, f"{t:%H%M}_高溫資訊_官方全臺圖.png")
            with open(p, "wb") as f:
                f.write(img.content)
            common.watermark(p, [f"下載時間：{common.stamp(t)}（臺北時間 UTC+8）", f"來源：{config.URL_W29_IMAGE}"])
    except Exception:  # noqa: BLE001
        pass

    # API 核對：縣市目前警特報
    if config.CWA_API_KEY:
        try:
            rows[0]["燈號_API"] = api.taipei_current_hazards()
        except Exception as e:  # noqa: BLE001
            rows[0]["燈號_API"] = f"失敗：{e}"

    st["last_version"] = version
    if base:
        common.mark_baseline(state, "w29", t, base)
    return rows


# ================================================================== 網站三：健康氣象（熱傷害）
def _window_entries(entries: list[dict], day: str) -> list[dict]:
    """取當天、時段起點落在 9–14 時的逐三小時預報（通常是 09 時與 12 時兩段）。"""
    sel = []
    for e in entries:
        if not e["time"].startswith(day):
            continue
        try:
            h = int(e["time"][11:13])
        except ValueError:
            continue
        if config.WINDOW_START[0] <= h <= config.WINDOW_END[0]:
            sel.append(e)
    return sel


def _fmt(entries: list[dict]) -> str:
    return " / ".join(f"{e['time'][11:16]} {e['warning'] or '無'}({e['index']})" for e in entries)


def run_health(page: Page, t, state: dict, force: bool = False) -> list[dict]:
    day = t.strftime("%Y-%m-%d")
    st = state.setdefault("health", {})

    # 1) 先用 API 取得燈號（也用來判斷是否有更新）
    per_district: dict[str, list[dict]] = {}
    api_err = ""
    if config.CWA_API_KEY:
        try:
            data = api.taipei_heat_injury()
            for d in config.DISTRICTS:
                per_district[d] = _window_entries(data.get(d, []), day)
        except Exception as e:  # noqa: BLE001
            api_err = f"API失敗：{e}"

    # 2) 開網頁
    page_err = ""
    try:
        open_page(page, config.URL_HEALTH)
        page_sig = short_hash(page.inner_text("body")[:20000])
    except Exception as e:  # noqa: BLE001
        page_err, page_sig = f"網頁開啟失敗：{e}", ""

    version = short_hash(per_district) if per_district else page_sig
    changed = bool(version) and version != st.get("last_version")
    base = common.baseline_due(state, "health", t)
    all_entries = [e for v in per_district.values() for e in v]
    county_light = api.worst_warning(all_entries) if per_district else ""

    if not (changed or base or force) and not page_err:
        return [base_row(t, SITE_HEALTH, "縣市", config.COUNTY, 燈號_API=county_light,
                         狀態="檢查-無變化", 補充說明="熱傷害預報與上次相同")]

    reason = "手動強制" if force else ("資料更新" if changed else f"基準截圖{base}時")
    out = common.site_dir(day, SITE_HEALTH)
    rows: list[dict] = []

    shot, note = "", page_err
    if not page_err:
        try:
            note = select_area(page, [config.COUNTY])
        except Exception as e:  # noqa: BLE001
            note = f"未能點選臺北市（截取預設畫面）：{e}"
        shot = shoot(page, os.path.join(out, f"{t:%H%M}_熱傷害_臺北市.png"), t, config.URL_HEALTH,
                     f"地區：臺北市 ｜ 9–14時最高燈號(API)：{county_light or '未取得'} ｜ 原因：{reason}")
    rows.append(base_row(t, SITE_HEALTH, "縣市", config.COUNTY, 燈號_API=county_light, 截圖檔名=shot,
                         狀態="已截圖" if shot else "截圖失敗",
                         補充說明="；".join(x for x in (reason, note, api_err) if x)))

    for d in config.DISTRICTS:
        entries = per_district.get(d, [])
        light = api.worst_warning(entries) if entries else ""
        dshot, dnote, status = "", "", "已記錄"
        if config.DISTRICT_SCREENSHOTS and not page_err:
            try:
                dnote = select_area(page, [f"{config.COUNTY}{d}", d])
                dshot = shoot(page, os.path.join(out, f"{t:%H%M}_熱傷害_臺北市{d}.png"), t, config.URL_HEALTH,
                              f"地區：臺北市{d} ｜ {_fmt(entries) or '未取得API資料'} ｜ 原因：{reason}")
                status = "已截圖"
            except Exception as e:  # noqa: BLE001
                dnote, status = f"行政區截圖失敗：{e}", "已記錄-截圖失敗"
        rows.append(base_row(t, SITE_HEALTH, "行政區", f"{config.COUNTY}{d}", 燈號_API=light,
                             數值=_fmt(entries), 截圖檔名=dshot, 狀態=status,
                             補充說明="；".join(x for x in (reason, dnote) if x)))

    if version:
        st["last_version"] = version
    if base:
        common.mark_baseline(state, "health", t, base)
    return rows


# ================================================================== 探勘模式
def probe(page: Page, t) -> str:
    """把三個網頁的 HTML、截圖、資料連線與「臺北市」候選元素存下來，供調整選取方式。"""
    folder = os.path.join(config.OUT_DIR, "_probe", f"{t:%Y%m%d_%H%M}")
    os.makedirs(folder, exist_ok=True)
    for key, url in (("temptop", config.URL_TEMPTOP), ("w29", config.URL_W29), ("health", config.URL_HEALTH)):
        responses = []

        def on_response(r, acc=responses):
            acc.append({"url": r.url, "status": r.status, "type": r.request.resource_type})

        page.on("response", on_response)
        info = {"url": url}
        try:
            open_page(page, url)
            info["title"] = page.title()
            info["final_url"] = page.url
            info["candidates"] = page.evaluate(
                """() => [...document.querySelectorAll('*')]
                    .filter(e => e.children.length < 3 && (e.textContent||'').includes('臺北市')
                              && (e.textContent||'').trim().length < 20)
                    .slice(0, 80)
                    .map(e => ({tag: e.tagName, id: e.id, cls: e.className && e.className.baseVal !== undefined
                                 ? e.className.baseVal : e.className,
                                text: e.textContent.trim(), visible: !!e.offsetParent,
                                html: e.outerHTML.slice(0, 300)}))"""
            )
            info["selects"] = page.evaluate(
                "() => [...document.querySelectorAll('select')].map(s => ({id: s.id, name: s.name, "
                "options: [...s.options].map(o => o.text).slice(0, 40)}))"
            )
            with open(os.path.join(folder, f"{key}.html"), "w", encoding="utf-8") as f:
                f.write(page.content())
            page.screenshot(path=os.path.join(folder, f"{key}.png"), full_page=True)
        except Exception as e:  # noqa: BLE001
            info["error"] = str(e)
        info["network"] = [r for r in responses if r["type"] in ("xhr", "fetch", "script", "document")]
        with open(os.path.join(folder, f"{key}.json"), "w", encoding="utf-8") as f:
            json.dump(info, f, ensure_ascii=False, indent=2)
        page.remove_listener("response", on_response)
    return folder
