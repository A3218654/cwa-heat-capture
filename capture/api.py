"""氣象署開放資料 API：用來核對截圖上的數值與燈號。"""
import re
import time

import requests

from . import config

DT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}")


def _get(dataset: str, **params) -> dict:
    if not config.CWA_API_KEY:
        raise RuntimeError("未設定 CWA_API_KEY")
    params = {"Authorization": config.CWA_API_KEY, "format": "JSON", **params}
    last = None
    for attempt in range(3):
        try:
            r = requests.get(f"{config.API_BASE}/{dataset}", params=params, timeout=60)
            r.raise_for_status()
            data = r.json()
            if str(data.get("success")).lower() != "true":
                raise RuntimeError(f"API 回傳失敗：{data}")
            return data
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"{dataset} 取得失敗：{last}")


# ------------------------------------------------------------------ 觀測：臺北市今日最高溫
def taipei_daily_high() -> dict | None:
    """回傳臺北市所有測站中今日最高溫 {value, station, station_id, town, time}。"""
    data = _get(config.DS_OBS)
    best = None
    for st in data.get("records", {}).get("Station", []):
        geo = st.get("GeoInfo", {})
        if geo.get("CountyName") != config.COUNTY:
            continue
        info = (st.get("WeatherElement", {}).get("DailyExtreme", {})
                  .get("DailyHigh", {}).get("TemperatureInfo", {}))
        try:
            v = float(info.get("AirTemperature"))
        except (TypeError, ValueError):
            continue
        if v <= -90:  # -99 表示無資料
            continue
        if best is None or v > best["value"]:
            best = {
                "value": v,
                "station": st.get("StationName"),
                "station_id": st.get("StationId"),
                "town": geo.get("TownName"),
                "time": info.get("Occurred_at", {}).get("DateTime", ""),
            }
    return best


# ------------------------------------------------------------------ 熱傷害：各行政區逐三小時
def _walk_locations(node, county=None, out=None):
    """不依賴固定的 JSON 層級，遞迴找出所有帶 TownName 與時間序列的節點。"""
    if out is None:
        out = {}
    if isinstance(node, dict):
        county = node.get("CountyName", county)
        town = node.get("TownName") or node.get("LocationName")
        times = node.get("Time")
        if town and isinstance(times, list) and county == config.COUNTY:
            out[town] = [_parse_time_entry(t) for t in times]
        for v in node.values():
            _walk_locations(v, county, out)
    elif isinstance(node, list):
        for v in node:
            _walk_locations(v, county, out)
    return out


def _parse_time_entry(t: dict) -> dict:
    when = ""
    for k in ("DataTime", "StartTime", "IssueTime", "dataTime", "startTime"):
        if isinstance(t.get(k), str) and DT_RE.match(t[k]):
            when = t[k]
            break
    if not when:
        for v in t.values():
            if isinstance(v, str) and DT_RE.match(v):
                when = v
                break
    el = t.get("WeatherElements") or t.get("ElementValue") or t
    if isinstance(el, list):
        el = el[0] if el else {}
    return {
        "time": when.replace("T", " ")[:16],
        "index": el.get("HeatInjuryIndex", ""),
        "warning": (el.get("HeatInjuryWarning") or "").strip(),
    }


def taipei_heat_injury() -> dict[str, list[dict]]:
    """回傳 {行政區: [{time, index, warning}, ...]}，只含臺北市。"""
    try:
        data = _get(config.DS_HEALTH, CountyName=config.COUNTY)
        res = _walk_locations(data.get("records", {}))
        if res:
            return res
    except RuntimeError:
        pass
    data = _get(config.DS_HEALTH)  # 參數過濾無效時抓全部再篩
    return _walk_locations(data.get("records", {}))


WARN_RANK = {"": 0, "無": 0, "注意": 1, "警戒": 2, "危險": 3, "高危險": 4, "高風險": 4}


def worst_warning(entries: list[dict]) -> str:
    w = ""
    for e in entries:
        if WARN_RANK.get(e["warning"], 0) > WARN_RANK.get(w, 0):
            w = e["warning"]
    return w or "無"


# ------------------------------------------------------------------ 警特報：縣市目前的警特報
def taipei_current_hazards() -> str:
    data = _get(config.DS_WARNING, locationName=config.COUNTY)
    names = []
    for loc in data.get("records", {}).get("location", []):
        for h in loc.get("hazardConditions", {}).get("hazards", []) or []:
            info = h.get("info", {})
            names.append(f"{info.get('phenomena', '')}{info.get('significance', '')}")
    return "、".join(n for n in names if n) or "無"
