"""三個網站的抓取邏輯（Playwright 同步版）。

選取方式依 2026-10-02 在 GitHub 雲端機器上實測的網頁結構撰寫：
- 縣市溫度極值：#SDay（今日/昨日/前日）、#STemp（高溫/低溫）、tbody#CountyTemp_MOD
- 高溫資訊：#warningTime、#validEnd、#CID（地點切換，無燈號的縣市會被停用）、
  td[data-name="C63"]（臺北市燈號），各行政區燈號來自 /Data/js/warn/Warning_63.js
- 健康氣象：頁面上唯一的 <select>（選擇縣市）＋各行政區按鈕、「更新時間」
找不到預期元素時，會退回通用的文字比對方式，並寫進 CSV 的「補充說明」。
"""
import hashlib
import json
import os
import re
from datetime import timedelta

import requests
from playwright.sync_api import Page

from . import api, common, config

SITE_TEMPTOP = "1_縣市溫度極值"
SITE_W29 = "2_高溫資訊"
SITE_HEALTH = "3_熱傷害"

COUNTY_CODE = "63"
# 臺北市各行政區代碼（與 config.DISTRICTS 順序相同）：6300100 松山區 … 6301200 北投區
TOWN_CODES = {f"63{i:03d}00": d for i, d in enumerate(config.DISTRICTS, start=1)}
W29_LEVEL = {"W29-1": "黃色", "W29-2": "橙色", "W29-3": "紅色", "W29": "黃色"}


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


def choose(page: Page, css: str, label: str) -> str:
    """在指定的下拉選單選擇文字開頭為 label 的選項（選項可能帶有「(橙色燈號)」等後綴）。"""
    sel = page.locator(css)
    if sel.count() == 0:
        raise RuntimeError(f"找不到下拉選單 {css}")
    opts = sel.first.locator("option")
    for i in range(opts.count()):
        o = opts.nth(i)
        text = o.inner_text().strip()
        if text == label or text.startswith(label + " ") or text.startswith(label + "("):
            if o.get_attribute("disabled") is not None:
                raise RuntimeError(f"{css} 的「{text}」目前無法選取")
            sel.first.select_option(index=i)
            settle(page)
            return f"{css}:{text}"
    raise RuntimeError(f"{css} 沒有「{label}」選項")


def click_text(page: Page, name: str) -> str:
    """通用備援：點擊畫面上文字完全相同的按鈕或連結（略過表格內文字）。"""
    loc = page.get_by_role("button", name=name, exact=True)
    if loc.count() == 0:
        loc = page.get_by_text(name, exact=True)
    for i in range(min(loc.count(), 30)):
        el = loc.nth(i)
        try:
            if not el.is_visible():
                continue
            if el.evaluate("e => !!e.closest('table')"):
                continue
            el.click(timeout=5_000)
            settle(page, 1000)
            return f"點擊:{name}"
        except Exception:  # noqa: BLE001
            continue
    raise RuntimeError(f"找不到可點選的「{name}」")


def text_of(page: Page, css: str) -> str:
    loc = page.locator(css)
    return loc.first.inner_text().strip() if loc.count() else ""


def labeled_time(page: Page, label: str) -> str:
    """讀取「更新時間 …」「發佈時間：…」這類欄位的整行文字。"""
    text = page.inner_text("body")
    m = re.search(label + r"[：:]?[ \t]*([0-9][^\n]{0,40})", text)
    if not m:
        return ""
    value = re.split(r"\s*(?:發佈時間|發布時間|有效時間|更新時間)[：:]?", m.group(1))[0]
    return value.strip()


def shoot(page: Page, path: str, t, url: str, extra: str) -> str:
    page.screenshot(path=path, full_page=True)
    common.watermark(path, [
        f"截圖時間：{common.stamp(t)}（臺北時間 UTC+8）",
        f"來源：{url}",
        extra,
    ])
    return os.path.basename(path)


def save_text(path: str, text: str) -> str:
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
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
    day = (t if which == "今日" else t - timedelta(days=1)).strftime("%Y-%m-%d")
    open_page(page, config.URL_TEMPTOP)
    notes = [choose(page, "#SDay", which), choose(page, "#STemp", "高溫")]
    page.wait_for_function(
        """(which) => { const tr = document.querySelector('#CountyTemp_MOD tr');
                        return tr && (tr.dataset.subtitle || '').includes(which); }""",
        arg=which, timeout=20_000,
    )
    info = page.evaluate(
        """(county) => {
            const first = document.querySelector('#CountyTemp_MOD tr');
            const row = [...document.querySelectorAll('#CountyTemp_MOD tr')]
                .find(tr => (tr.querySelector('th')?.innerText || '').trim() === county);
            const cells = row ? [...row.querySelectorAll('td')].map(td => td.innerText.trim()) : [];
            return {subtitle: first?.dataset.subtitle || '', from: first?.dataset.timefrom || '',
                    to: first?.dataset.timeto || '', updated: first?.dataset.updatetime || '', cells};
        }""",
        config.COUNTY,
    )
    temp, obs_time, station, station_id, place = (info["cells"] + [""] * 5)[:5]

    fname = f"{t:%H%M}_縣市溫度極值_{which}_高溫.png"
    shot = shoot(page, os.path.join(common.site_dir(day, SITE_TEMPTOP), fname), t, config.URL_TEMPTOP,
                 f"{info['subtitle']}（{info['from']}～{info['to']}）｜臺北市 {temp}°C ｜ {obs_time} ｜ "
                 f"{station}（{station_id}）｜網頁更新 {info['updated']}")

    api_note = ""
    if which == "今日" and config.CWA_API_KEY:
        try:
            hi = api.taipei_daily_high()
            if hi:
                api_note = f"API核對：{hi['value']}°C {hi['station']}({hi['station_id']}) {hi['time'][11:16]}"
        except Exception as e:  # noqa: BLE001
            api_note = f"API核對失敗：{e}"
    rec = base_row(t, SITE_TEMPTOP, "縣市", config.COUNTY,
                   網頁發佈時間=info["updated"], 網頁有效時間=f"{info['from']}～{info['to']}",
                   數值=temp,
                   補充說明="；".join(x for x in (f"資料日期{day}({which})", f"觀測時間{obs_time}",
                                                f"測站{station}({station_id}) {place}", api_note, *notes) if x),
                   截圖檔名=shot, 狀態="已截圖" if temp else "已截圖-未讀到數值")
    rec["關鍵時段內"] = ""
    return day, [rec]


# ================================================================== 網站二：高溫資訊
def _w29_towns(page: Page) -> tuple[dict[str, str], str]:
    """讀取網頁本身使用的 Warning_63.js，回傳 ({行政區: 燈號}, 原始文字)。"""
    raw = page.evaluate(
        """(code) => fetch('/Data/js/warn/Warning_' + code + '.js?_=' + Date.now())
                       .then(r => r.ok ? r.text() : '')""",
        COUNTY_CODE,
    )
    lights = {}
    for code, items in re.findall(r"'(\d{7})'\s*:\s*\[([^\]]*)\]", raw):
        if code not in TOWN_CODES:
            continue
        levels = [W29_LEVEL[x] for x in re.findall(r"W29(?:-\d)?", items) if x in W29_LEVEL]
        order = ["黃色", "橙色", "紅色"]
        lights[TOWN_CODES[code]] = max(levels, key=order.index) if levels else "無"
    return lights, raw


def _county_light(page: Page) -> tuple[str, str]:
    """回傳 (臺北市燈號, 備註)。高溫資訊過期或未發布時，網頁不會顯示燈號表。"""
    title = page.evaluate(
        """(code) => { const td = document.querySelector('td[data-name="C' + code + '"]');
                       if (!td) return null;
                       const s = td.querySelector('[title]'); return s ? s.getAttribute('title') : ''; }""",
        COUNTY_CODE,
    )
    if title is None:
        return "無", "網頁目前沒有燈號表（未發布或已過有效時間）"
    for c in ("紅色", "橙色", "黃色"):
        if c in title:
            return c, ""
    return "無", ""


def run_w29(page: Page, t, state: dict, force: bool = False) -> list[dict]:
    day = t.strftime("%Y-%m-%d")
    open_page(page, config.URL_W29)
    issue = text_of(page, "#warningTime")
    valid = text_of(page, "#validEnd")
    content = text_of(page, "#WarnContent")
    not_issued = "無發布" in content or "無發佈" in content
    county_light, table_note = _county_light(page)
    if not_issued:
        table_note = "網頁顯示「目前無發布」，發佈時間欄為網頁當下時間"
    towns, raw = _w29_towns(page)
    if not raw:
        raise RuntimeError("讀不到 Warning_63.js")
    towns = {d: towns.get(d, "無") for d in config.DISTRICTS}

    # 無發布時，發佈時間會隨時變動，不能拿來判斷是否更新
    version = short_hash(["無發布" if not_issued else issue, county_light, towns])
    st = state.setdefault("w29", {})
    changed = version != st.get("last_version")
    base = common.baseline_due(state, "w29", t)
    if not_issued:
        issue, valid = "目前無發布", ""
    kw = {"網頁發佈時間": issue, "網頁有效時間": valid}
    lit = [f"{d}{v}" for d, v in towns.items() if v != "無"]
    summary = f"臺北市：{county_light}；行政區：{'、'.join(lit) or '皆無燈號'}"
    if table_note:
        summary += f"（{table_note}）"

    if not (changed or base or force):
        return [base_row(t, SITE_W29, "縣市", config.COUNTY, 燈號_網頁=county_light,
                         狀態="檢查-無變化", 補充說明=f"與上次相同（發佈時間 {issue}）；{summary}", **kw)]

    reason = "手動強制" if force else ("燈號或發佈時間變更" if changed else f"基準截圖{base}時")
    out = common.site_dir(day, SITE_W29)
    stem = f"{t:%H%M}_高溫資訊"

    # 全縣市畫面（含各縣市燈號表）
    shot_all = shoot(page, os.path.join(out, f"{stem}_全縣市.png"), t, config.URL_W29,
                     f"網頁發佈時間：{issue} ｜ 有效時間：{valid} ｜ {summary} ｜ 原因：{reason}")
    # 臺北市畫面（只有臺北市有燈號時才能選取）
    shot_tpe, note = "", "臺北市無燈號，地點切換中不可選取，以全縣市畫面為證"
    if county_light != "無" or lit:
        try:
            note = choose(page, "#CID", config.COUNTY)
            shot_tpe = shoot(page, os.path.join(out, f"{stem}_臺北市.png"), t, config.URL_W29,
                             f"網頁發佈時間：{issue} ｜ 有效時間：{valid} ｜ {summary} ｜ 原因：{reason}")
        except RuntimeError as e:
            note = f"選取臺北市失敗（以全縣市畫面為證）：{e}"
    evidence = save_text(os.path.join(out, f"{stem}_Warning_63.js.txt"), raw)

    rows = [base_row(t, SITE_W29, "縣市", config.COUNTY, 燈號_網頁=county_light,
                     截圖檔名="、".join(x for x in (shot_tpe, shot_all) if x), 狀態="已截圖",
                     補充說明=f"{reason}；{note}；行政區資料檔 {evidence}", **kw)]
    for d in config.DISTRICTS:
        rows.append(base_row(t, SITE_W29, "行政區", f"{config.COUNTY}{d}", 燈號_網頁=towns.get(d, "無"),
                             截圖檔名=shot_tpe or shot_all, 狀態="已記錄",
                             補充說明=f"{reason}；燈號取自 Warning_63.js", **kw))

    try:  # 官方整張圖備份
        img = requests.get(config.URL_W29_IMAGE, timeout=30)
        if img.ok and img.headers.get("content-type", "").startswith("image"):
            p = os.path.join(out, f"{stem}_官方全臺圖.png")
            with open(p, "wb") as f:
                f.write(img.content)
            common.watermark(p, [f"下載時間：{common.stamp(t)}（臺北時間 UTC+8）", f"來源：{config.URL_W29_IMAGE}"])
    except Exception:  # noqa: BLE001
        pass

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
HEALTH_STATUS = {0: "無", 1: "注意", 2: "警戒", 3: "危險", 4: "高風險"}


def _window_entries(entries: list[dict], day: str) -> list[dict]:
    """取當天、時段起點落在 9–14 時的逐三小時資料（通常是 09 時與 12 時兩段）。"""
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


def _page_health(page_json: dict[str, str]) -> tuple[dict, dict, dict]:
    """解析網頁自己載入的兩份 JSON，回傳 (今日燈號 {區: 燈號}, 逐三小時 {區: [...]}, 臺北市子集)。"""
    today, slots, subset = {}, {}, {}
    try:
        wt = json.loads(page_json.get("getWarnTown_HD.json", "{}"))
        for grp in wt.get("getWarnTown", []):
            for x in grp.get("warnDatas", []):
                if x.get("cityName") == config.COUNTY:
                    today[x["townName"]] = HEALTH_STATUS.get(x.get("warnStatus"), str(x.get("warnStatus")))
                    subset.setdefault("getWarnTown", []).append(x)
    except (ValueError, KeyError):
        pass
    try:
        rf = json.loads(page_json.get("getUserRiskForecast_HD.json", "{}"))
        subset["dailyHealthUpdateTime"] = rf.get("dailyHealthUpdateTime")
        for x in rf.get("getUserRiskForecast", []):
            if x.get("city") == config.COUNTY and x.get("healthName", "熱傷害") == "熱傷害":
                slots[x["town"]] = [{"time": (e.get("dt") or "")[:16], "index": e.get("wbgt", ""),
                                     "warning": "" if not e.get("warnStatus")
                                     else HEALTH_STATUS.get(e["warnStatus"], str(e["warnStatus"]))}
                                    for e in x.get("dailyHealth", [])]
                subset.setdefault("getUserRiskForecast", []).append(x)
    except (ValueError, KeyError):
        pass
    return today, slots, subset


def run_health(page: Page, t, state: dict, force: bool = False) -> list[dict]:
    day = t.strftime("%Y-%m-%d")
    st = state.setdefault("health", {})

    # 1) 網頁：開頁時攔下網頁自己載入的燈號資料
    page_json: dict[str, str] = {}

    def keep(resp):
        if "/Lohas/Health/" in resp.url and resp.url.endswith(".json"):
            try:
                page_json[resp.url.rsplit("/", 1)[-1]] = resp.text()
            except Exception:  # noqa: BLE001
                pass

    page.on("response", keep)
    try:
        open_page(page, config.URL_HEALTH)
        note = choose(page, "select", config.COUNTY)
    finally:
        page.remove_listener("response", keep)
    updated = labeled_time(page, "更新時間")
    today, slots, subset = _page_health(page_json)
    if not today and not slots:
        raise RuntimeError("讀不到健康氣象網頁的燈號資料（getWarnTown / getUserRiskForecast）")
    win = {d: _window_entries(slots.get(d, []), day) for d in config.DISTRICTS}

    # 2) API（核對用）
    api_win: dict[str, list[dict]] = {}
    api_err = ""
    if config.CWA_API_KEY:
        try:
            data = api.taipei_heat_injury()
            api_win = {d: _window_entries(data.get(d, []), day) for d in config.DISTRICTS}
        except Exception as e:  # noqa: BLE001
            api_err = f"API核對失敗：{e}"

    version = short_hash([today, win])
    changed = version != st.get("last_version")
    base = common.baseline_due(state, "health", t)
    rank = list(HEALTH_STATUS.values())
    county_today = max((today.get(d, "無") for d in config.DISTRICTS), key=rank.index)
    county_win = api.worst_warning([e for v in win.values() for e in v]) if any(win.values()) else ""
    lit = [f"{d}{today[d]}" for d in config.DISTRICTS if today.get(d, "無") != "無"]
    summary = f"今日燈號：{'、'.join(lit) or '臺北市各區皆無'}"
    kw = {"網頁發佈時間": updated}

    if not (changed or base or force):
        return [base_row(t, SITE_HEALTH, "縣市", config.COUNTY, 燈號_網頁=county_today,
                         狀態="檢查-無變化", 補充說明=f"與上次相同（網頁更新時間 {updated}）；{summary}", **kw)]

    reason = "手動強制" if force else ("燈號資料更新" if changed else f"基準截圖{base}時")
    out = common.site_dir(day, SITE_HEALTH)
    stem = f"{t:%H%M}_熱傷害"
    evidence = save_text(os.path.join(out, f"{stem}_臺北市資料.json"),
                         json.dumps(subset, ensure_ascii=False, indent=1))

    shot = shoot(page, os.path.join(out, f"{stem}_臺北市.png"), t, config.URL_HEALTH,
                 f"網頁更新時間：{updated} ｜ {summary} ｜ 原因：{reason}")
    rows = [base_row(t, SITE_HEALTH, "縣市", config.COUNTY, 燈號_網頁=county_today,
                     燈號_API=county_win and f"9–14時最高：{county_win}",
                     截圖檔名=shot, 狀態="已截圖",
                     補充說明="；".join(x for x in (reason, "縣市燈號取各區最高", note,
                                                  f"資料檔 {evidence}", api_err) if x), **kw)]

    for d in config.DISTRICTS:
        entries = win.get(d, [])
        dshot, dnote, status = "", "", "已記錄"
        if config.DISTRICT_SCREENSHOTS:
            try:
                dnote = click_text(page, d)
                dshot = shoot(page, os.path.join(out, f"{stem}_臺北市{d}.png"), t, config.URL_HEALTH,
                              f"網頁更新時間：{updated} ｜ 臺北市{d} 今日燈號：{today.get(d, '未知')} ｜ "
                              f"9–14時：{_fmt(entries) or '已過時段或無資料'} ｜ 原因：{reason}")
                status = "已截圖"
            except Exception as e:  # noqa: BLE001
                dnote, status = f"行政區截圖失敗：{e}", "已記錄-截圖失敗"
        api_light = api.worst_warning(api_win[d]) if api_win.get(d) else ""
        rows.append(base_row(t, SITE_HEALTH, "行政區", f"{config.COUNTY}{d}", 燈號_網頁=today.get(d, ""),
                             燈號_API=api_light, 數值=_fmt(entries), 截圖檔名=dshot, 狀態=status,
                             補充說明="；".join(x for x in (reason, dnote) if x), **kw))

    st["last_version"] = version
    if base:
        common.mark_baseline(state, "health", t, base)
    return rows


# ================================================================== 探勘模式
def probe(page: Page, t) -> str:
    """把三個網頁的 HTML、截圖、資料連線與「臺北市」候選元素存下來，供調整選取方式。"""
    folder = os.path.join(config.OUT_DIR, "_probe", f"{t:%Y%m%d_%H%M}")
    os.makedirs(folder, exist_ok=True)
    targets = [("temptop", config.URL_TEMPTOP), ("w29", config.URL_W29), ("health", config.URL_HEALTH)]
    extra = os.environ.get("PROBE_URLS", "").split()
    if extra:
        targets = [(f"extra{i}", u) for i, u in enumerate(extra)]
    for key, url in targets:
        responses = []
        bodies = os.path.join(folder, f"{key}_data")
        os.makedirs(bodies, exist_ok=True)

        def on_response(r, acc=responses, bodies=bodies):
            acc.append({"url": r.url, "status": r.status, "type": r.request.resource_type})
            if "/Data/" in r.url and r.request.resource_type in ("xhr", "fetch", "script"):
                try:
                    name = re.sub(r"[^\w.-]", "_", r.url.split("/Data/", 1)[1])[:120]
                    with open(os.path.join(bodies, name + ".txt"), "w", encoding="utf-8") as f:
                        f.write(r.text())
                except Exception:  # noqa: BLE001
                    pass

        page.on("response", on_response)
        info = {"url": url}
        try:
            open_page(page, url)
            for css in os.environ.get("PROBE_CLICKS", "").split():
                try:
                    page.locator(css).first.click(timeout=5_000)
                    settle(page)
                    info.setdefault("clicked", []).append(css)
                except Exception as e:  # noqa: BLE001
                    info.setdefault("click_errors", []).append(f"{css}: {e}")
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
