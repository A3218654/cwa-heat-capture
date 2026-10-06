"""把 CSV 紀錄整理成試算表。

- 每天資料夾：紀錄_YYYY-MM-DD.xlsx，分頁：
  縣市溫度極值／高溫資訊／熱傷害／臺北測站／體感溫度／執行狀況
- Drive 最外層：體感溫度總表.xlsx（每天一個分頁）、縣市溫度極值總表.xlsx、高溫資訊總表.xlsx（每年一個分頁、每天一列）

CSV 仍是程式追加資料用的原始紀錄；試算表每次執行後依 CSV 重新產生。
也可單獨執行補建：REBUILD_DAYS=2026-10-02,2026-10-03 python -m capture.workbook
"""
import csv
import os
import re
import sys

from openpyxl import Workbook, load_workbook
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from . import common, config

HEADER_FILL = PatternFill("solid", fgColor="1F4E79")
HEADER_FONT = Font(bold=True, color="FFFFFF")

# 分頁名稱 → CSV「網站」欄的關鍵字
SHEETS = [
    ("高溫資訊明細", "高溫資訊"),
    ("熱傷害明細", "熱傷害"),
]
DROP_COLS = {"網站"}


def _read_csv(path: str) -> tuple[list[str], list[dict]]:
    if not os.path.exists(path):
        return [], []
    with open(path, encoding="utf-8-sig") as f:
        r = csv.DictReader(f)
        return list(r.fieldnames or []), list(r)


def _write_sheet(ws, headers: list[str], rows: list[list]) -> None:
    ws.append(headers)
    for c in ws[1]:
        c.fill, c.font = HEADER_FILL, HEADER_FONT
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    for row in rows:
        ws.append([_num(v) for v in row])
    ws.freeze_panes = "A2"
    for i, h in enumerate(headers, start=1):
        width = max([len(str(h))] + [len(str(r[i - 1])) for r in rows if i - 1 < len(r)] or [4])
        ws.column_dimensions[get_column_letter(i)].width = min(max(6, width * 1.6 + 2), 60)


def _num(v):
    """數字字串轉成數字，試算表才能排序計算。"""
    if isinstance(v, str):
        try:
            return int(v) if v.strip().lstrip("-").isdigit() else float(v)
        except ValueError:
            return v
    return v


# ------------------------------------------------------------------ 縣市溫度極值（使用者指定格式）
PEACH = PatternFill("solid", fgColor="FCE4D6")
THIN = Side(style="thin", color="D9A08B")
BOX = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)


def _one_line(v: str) -> str:
    """多個測站同為最高溫時，每個一行（時間與測站上下對應）。"""
    return "\n".join(x.strip() for x in str(v).splitlines() if x.strip())


def temptop_record(day: str, rows: list[dict]) -> dict | None:
    """取當天的定案值：優先用隔天截的「昨日／前日」，沒有才用當天最後一次「今日」。"""
    cand = [r for r in rows if "縣市溫度極值" in r.get("網站", "") and r.get("狀態", "").startswith("已截圖")
            and f"資料日期{day}" in r.get("補充說明", "")]
    if not cand:
        return None
    final = [r for r in cand if "(今日)" not in r.get("補充說明", "")]
    r = (final or cand)[-1]
    note = r.get("補充說明", "")
    tm = re.search(r"觀測時間([^；]+)", note)
    st = re.search(r"測站(.+?)\(", note, re.S)
    return {
        "月份": int(day[5:7]), "日期": int(day[8:10]), "最高溫": _num(r.get("數值", "")),
        "時間": _one_line(tm.group(1)) if tm else "",
        "測站": _one_line(st.group(1)) if st else "",
        "截圖": r.get("截圖檔名", ""),
    }


def write_temptop_sheet(ws, records: list[dict]) -> None:
    headers = ["月份", "日期", "最高溫", "時間", "測站"]
    ws.append(headers)
    for rec in records:
        ws.append([rec[h] for h in headers])
    for row in ws.iter_rows(min_row=1, max_row=ws.max_row, max_col=len(headers)):
        lines = max(str(c.value or "").count("\n") + 1 for c in row)
        ws.row_dimensions[row[0].row].height = 20 * lines
        for c in row:
            c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            c.font = Font(size=12)
            c.border = BOX
            if c.row == 1 or c.column == 1:
                c.fill = PEACH
            if c.column == 3 and c.row > 1:
                c.number_format = "0.0"
    for col, w in zip("ABCDE", (10, 10, 12, 14, 18)):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = "A2"


# ------------------------------------------------------------------ 高溫資訊（使用者指定格式）
LIGHTS = ["黃燈", "橙燈", "紅燈", "–"]
LIGHT_FROM = {"黃色": "黃燈", "橙色": "橙燈", "紅色": "紅燈"}
LIGHT_FILL = {"黃燈": "FFFF00", "橙燈": "F4A261", "紅燈": "FF4D4D"}
LIGHT_RANK = {"–": 0, "黃燈": 1, "橙燈": 2, "紅燈": 3}
HEAD_GRAY = PatternFill("solid", fgColor="D9D9D9")
MONTH_BLUE = PatternFill("solid", fgColor="DCE6F1")
GRID = Side(style="thin", color="BFBFBF")
GRID_BOX = Border(left=GRID, right=GRID, top=GRID, bottom=GRID)


def w29_summary(rows: list[dict]) -> tuple[str, dict[str, str]]:
    """當天 9–14 時（關鍵時段內）臺北市與各區出現過的最高燈號。"""
    county, towns = "–", {d: "–" for d in config.DISTRICTS}

    def up(cur, val):
        v = LIGHT_FROM.get(val, "–")
        return v if LIGHT_RANK[v] > LIGHT_RANK[cur] else cur

    for r in rows:
        if "高溫資訊" not in r.get("網站", "") or r.get("關鍵時段內") != "是":
            continue
        if r.get("層級") == "縣市":
            county = up(county, r.get("燈號_網頁", ""))
        elif r.get("層級") == "行政區":
            name = r.get("地區", "").replace(config.COUNTY, "")
            if name in towns:
                towns[name] = up(towns[name], r.get("燈號_網頁", ""))
    return county, towns


def _light_cells(ws, ref: str, options=None, fills=None, white_text=()) -> None:
    """燈號欄：下拉選單＋依內容自動上色（手動修改也會跟著變色）。"""
    options, fills = options or LIGHTS, fills or LIGHT_FILL
    dv = DataValidation(type="list", formula1='"' + ",".join(options) + '"', allow_blank=True)
    ws.add_data_validation(dv)
    dv.add(ref)
    first = ref.split(":")[0]
    for name, color in fills.items():
        ws.conditional_formatting.add(ref, FormulaRule(
            formula=[f'{first}="{name}"'], fill=PatternFill("solid", fgColor=color, bgColor=color),
            font=Font(color="FFFFFF") if name in white_text else None))


def _style_grid(ws, ncols: int, header_fill, first_col_fill=None, widths=None) -> None:
    for row in ws.iter_rows(min_row=1, max_row=ws.max_row, max_col=ncols):
        for c in row:
            c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            c.font = Font(size=12)
            c.border = GRID_BOX
            if c.row == 1:
                c.fill = header_fill
            elif c.column == 1 and first_col_fill:
                c.fill = first_col_fill
    for i in range(1, ncols + 1):
        ws.column_dimensions[get_column_letter(i)].width = (widths or {}).get(i, 11)
    ws.freeze_panes = "C2" if ncols > 5 else "A2"


def record_row(day: str, rec: dict | None, county: str) -> list:
    return [int(day[5:7]), int(day[8:10]), rec["最高溫"] if rec else None, county]


def write_record_sheet(ws, rows: list[list]) -> None:
    """高溫紀錄表：月份／日期／最高溫／燈號（每日檔與總表共用）。"""
    ws.append(["月份", "日期", "最高溫", "燈號"])
    for r in rows:
        ws.append(r)
    _style_grid(ws, 4, PEACH, PEACH, {1: 10, 2: 10, 3: 12, 4: 12})
    last = max(ws.max_row, 2)
    for (c,) in ws.iter_rows(min_row=2, max_row=last, min_col=3, max_col=3):
        c.number_format = "0.0"
    _light_cells(ws, f"D2:D{last}")


def write_w29_daily_sheet(ws, day: str, rec: dict | None, county: str) -> None:
    write_record_sheet(ws, [record_row(day, rec, county)])


def update_record_master(day: str) -> bool:
    """高溫紀錄表總表：每年一個分頁，每天一列（最高溫取縣市溫度極值定案值，燈號取 9–14 時臺北市最高燈號）。"""
    _, rows = _read_csv(common.csv_path(day))
    rec = temptop_record(day, rows)
    has_w29 = any("高溫資訊" in r.get("網站", "") and r.get("關鍵時段內") == "是" for r in rows)
    if not rec and not has_w29:
        return False
    county, _ = w29_summary(rows)
    path = os.path.join(config.OUT_DIR, config.MASTER_RECORD_XLSX)
    exists = os.path.exists(path)
    wb = load_workbook(path) if exists else Workbook()
    if not exists:
        wb.remove(wb.active)
    year = day[:4]
    data = {}
    if year in wb.sheetnames:
        for vals in wb[year].iter_rows(min_row=2, values_only=True):
            if vals and vals[0] is not None:
                data[(int(vals[0]), int(vals[1]))] = list(vals[:4])
        del wb[year]
    data[(int(day[5:7]), int(day[8:10]))] = record_row(day, rec, county)
    write_record_sheet(wb.create_sheet(year), [data[k] for k in sorted(data)])
    finalize_master(wb, path)
    return True


def w29_district_row(day: str, towns: dict[str, str]) -> list:
    return [int(day[5:7]), int(day[8:10]), *[towns[d] for d in config.DISTRICTS]]


def write_w29_district_sheet(ws, rows: list[list]) -> None:
    """月份／日期／12 區燈號（每日檔與總表共用同一格式）。"""
    headers = ["月份", "日期", *config.DISTRICTS]
    ws.append(headers)
    for r in rows:
        ws.append(r)
    _style_grid(ws, len(headers), HEAD_GRAY, MONTH_BLUE, {1: 8, 2: 8})
    _light_cells(ws, f"C2:{get_column_letter(len(headers))}{max(ws.max_row, 2)}")


def update_w29_master(day: str) -> bool:
    """高溫資訊總表：每年一個分頁，每天一列，12 區各一欄。"""
    _, rows = _read_csv(common.csv_path(day))
    if not any("高溫資訊" in r.get("網站", "") and r.get("關鍵時段內") == "是" for r in rows):
        return False
    _, towns = w29_summary(rows)
    path = os.path.join(config.OUT_DIR, config.MASTER_W29_XLSX)
    exists = os.path.exists(path)
    wb = load_workbook(path) if exists else Workbook()
    if not exists:
        wb.remove(wb.active)
    year = day[:4]
    headers = ["月份", "日期", *config.DISTRICTS]
    data = {}
    if year in wb.sheetnames:
        for vals in wb[year].iter_rows(min_row=2, values_only=True):
            if vals and vals[0] is not None:
                data[(int(vals[0]), int(vals[1]))] = list(vals[:len(headers)])
        del wb[year]
    data[(int(day[5:7]), int(day[8:10]))] = w29_district_row(day, towns)
    write_w29_district_sheet(wb.create_sheet(year), [data[k] for k in sorted(data)])
    finalize_master(wb, path)
    return True


# ------------------------------------------------------------------ 熱傷害（使用者指定格式）
HEAT_LEVELS = ["注意", "警戒", "危險", "高危險", "–"]
HEAT_FILL = {"注意": "FFFF00", "警戒": "ED7D31", "危險": "FF0000", "高危險": "7030A0"}
HEAT_RANK = {"–": 0, "注意": 1, "警戒": 2, "危險": 3, "高危險": 4}
HEAT_ALIAS = {"高風險": "高危險"}


def health_summary(rows: list[dict]) -> dict[str, str]:
    """各區當天 09、12 時段（9–14 時）出現過的最高熱傷害燈號。"""
    towns = {d: "–" for d in config.DISTRICTS}
    for r in rows:
        if "熱傷害" not in r.get("網站", "") or r.get("層級") != "行政區":
            continue
        name = r.get("地區", "").replace(config.COUNTY, "")
        if name not in towns:
            continue
        found = re.findall(r"(\d{2}):00 (\S+?)\(", r.get("數值", ""))
        levels = [w for h, w in found if config.WINDOW_START[0] <= int(h) <= config.WINDOW_END[0]]
        if not found and r.get("關鍵時段內") == "是":   # 沒有時段資料時退回當日燈號
            levels = [r.get("燈號_網頁", "")]
        for w in levels:
            w = HEAT_ALIAS.get(w, w)
            if HEAT_RANK.get(w, 0) > HEAT_RANK[towns[name]]:
                towns[name] = w
    return towns


def write_health_district_sheet(ws, rows: list[list]) -> None:
    headers = ["月份", "日期", *config.DISTRICTS]
    ws.append(headers)
    for r in rows:
        ws.append(r)
    _style_grid(ws, len(headers), HEAD_GRAY, MONTH_BLUE, {1: 8, 2: 8})
    _light_cells(ws, f"C2:{get_column_letter(len(headers))}{max(ws.max_row, 2)}",
                 HEAT_LEVELS, HEAT_FILL, white_text=("危險", "高危險"))


def update_health_master(day: str) -> bool:
    _, rows = _read_csv(common.csv_path(day))
    if not any("熱傷害" in r.get("網站", "") and r.get("層級") == "行政區" for r in rows):
        return False
    towns = health_summary(rows)
    path = os.path.join(config.OUT_DIR, config.MASTER_HEALTH_XLSX)
    exists = os.path.exists(path)
    wb = load_workbook(path) if exists else Workbook()
    if not exists:
        wb.remove(wb.active)
    year = day[:4]
    data = {}
    if year in wb.sheetnames:
        for vals in wb[year].iter_rows(min_row=2, values_only=True):
            if vals and vals[0] is not None:
                data[(int(vals[0]), int(vals[1]))] = list(vals[:2 + len(config.DISTRICTS)])
        del wb[year]
    data[(int(day[5:7]), int(day[8:10]))] = w29_district_row(day, towns)
    write_health_district_sheet(wb.create_sheet(year), [data[k] for k in sorted(data)])
    finalize_master(wb, path)
    return True


# ------------------------------------------------------------------ 臺北測站中午溫度（使用者指定格式）
DAY_GRAY = PatternFill("solid", fgColor="EDEDED")


def station_row(day: str, rows: list[dict]) -> list | None:
    temps = {}
    for r in rows:
        if "臺北測站" not in r.get("網站", "") or not r.get("狀態", "").startswith("已截圖"):
            continue
        m = re.search(r"(\d{2}):00", r.get("網頁發佈時間", ""))
        v = re.search(r"-?\d+(\.\d+)?", r.get("數值", ""))
        if m and v:
            temps[int(m.group(1))] = float(v.group(0))
    if not temps:
        return None
    return [f"{int(day[5:7])}月", int(day[8:10]), *[temps.get(h) for h in config.STATION_HOURS]]


def write_station_sheet(ws, roc_year: int, rows: list[list]) -> None:
    ws.append([f"{roc_year}年", "日", *[f"{h}時" for h in config.STATION_HOURS]])
    for r in rows:
        ws.append(r)
    ncols = 2 + len(config.STATION_HOURS)
    for row in ws.iter_rows(min_row=1, max_row=ws.max_row, max_col=ncols):
        for c in row:
            c.alignment = Alignment(horizontal="center", vertical="center")
            c.font = Font(size=12, bold=c.row == 1)
            c.border = GRID_BOX
            if c.row > 1 and c.column == 1:
                c.fill = MONTH_BLUE
            elif c.row > 1 and c.column == 2:
                c.fill = DAY_GRAY
            if c.row > 1 and c.column >= 2:
                c.number_format = "0.0"
    for i in range(1, ncols + 1):
        ws.column_dimensions[get_column_letter(i)].width = 10
    ws.freeze_panes = "A2"


def update_station_master(day: str) -> bool:
    """歷年中午溫度_臺北測站：每年（民國）一個分頁，每天一列。"""
    _, rows = _read_csv(common.csv_path(day))
    row = station_row(day, rows)
    if not row:
        return False
    path = os.path.join(config.OUT_DIR, config.MASTER_STATION_XLSX)
    exists = os.path.exists(path)
    wb = load_workbook(path) if exists else Workbook()
    if not exists:
        wb.remove(wb.active)
    roc = int(day[:4]) - 1911
    title = f"{roc}年"
    data = {}
    if title in wb.sheetnames:
        for vals in wb[title].iter_rows(min_row=2, values_only=True):
            if vals and vals[0]:
                data[(int(str(vals[0]).rstrip("月")), int(vals[1]))] = list(vals[:2 + len(config.STATION_HOURS)])
        del wb[title]
    data[(int(day[5:7]), int(day[8:10]))] = row
    write_station_sheet(wb.create_sheet(title), roc, [data[k] for k in sorted(data)])
    finalize_master(wb, path)
    return True


# ------------------------------------------------------------------ 總表的「說明」分頁
CWA = "交通部中央氣象署"
COMMON_ROWS = [
    ("資料提供機關", CWA),
    ("時間", "表中所有時間皆為臺北時間（UTC+8）"),
    ("截圖佐證", "每次擷取的網頁截圖存放在 Google Drive 的「年-月／年-月-日」每日資料夾，截圖上方印有截圖時間與來源網址"),
    ("每日明細", "每日資料夾中的「紀錄_日期.xlsx」記錄當天每一次檢查的時間、內容與截圖檔名"),
    ("自動化程式", "https://github.com/A3218654/cwa-heat-capture"),
]
README = {
    config.MASTER_TEMPTOP_XLSX: {
        "title": "縣市溫度極值總表",
        "rows": [
            ("記錄內容", "臺北市每日最高溫、出現時間與測站（多個測站同為最高溫時全部列出，上下對應）"),
            ("資料來源網頁", "縣市溫度極值（選「高溫」）"),
            ("網址", config.URL_TEMPTOP),
            ("擷取時間", "每天 23:55 擷取「今日」；隔天 00:30 擷取「昨日」作為定案值"),
            ("取值規則", "優先採用隔天擷取的「昨日」定案值；若無，採用當晚「今日」最後一次擷取值"),
            ("核對資料", "氣象資料開放平臺 O-A0001-001 氣象觀測站-全測站逐時氣象資料（取臺北市各站當日最高溫核對）"),
            ("核對資料網址", "https://opendata.cwa.gov.tw/dataset/observation/O-A0001-001"),
            ("截圖資料夾", "每日資料夾／1_縣市溫度極值"),
        ],
    },
    config.MASTER_RECORD_XLSX: {
        "title": "高溫紀錄表總表",
        "rows": [
            ("記錄內容", "每日臺北市最高溫與當天臺北市高溫燈號"),
            ("最高溫來源", "縣市溫度極值（選「高溫」），採隔天擷取的「昨日」定案值；詳見「縣市溫度極值總表」"),
            ("最高溫網址", config.URL_TEMPTOP),
            ("燈號來源", "高溫資訊（地點切換：臺北市），取 9:00–14:00 關鍵時段內臺北市出現過的最高燈號；「–」表示無燈號"),
            ("燈號網址", config.URL_W29),
            ("燈號定義", "黃燈：氣溫達 36°C 以上；橙燈：達 36°C 以上且持續 3 天以上，或達 38°C 以上；紅燈：達 38°C 以上且持續 3 天以上"),
            ("產品說明文件", "https://www.cwa.gov.tw/V8/assets/pdf/HeatInformation_ProductDescription.pdf"),
            ("更新時間", "燈號於當天 9–14 時檢查後更新；最高溫於當晚 23:55 先填入，隔天 00:30 以定案值覆蓋"),
            ("截圖資料夾", "每日資料夾／1_縣市溫度極值、2_高溫資訊"),
        ],
    },
    config.MASTER_W29_XLSX: {
        "title": "高溫資訊總表",
        "rows": [
            ("記錄內容", "臺北市 12 行政區每日 9:00–14:00 關鍵時段內出現過的最高高溫燈號"),
            ("資料來源網頁", "高溫資訊（地點切換：臺北市）"),
            ("網址", config.URL_W29),
            ("行政區燈號資料", "網頁使用的資料檔（W29-1／2／3＝黃／橙／紅燈）"),
            ("行政區燈號資料網址", "https://www.cwa.gov.tw/Data/js/warn/Warning_63.js"),
            ("燈號定義", "黃燈：氣溫達 36°C 以上；橙燈：達 36°C 以上且持續 3 天以上，或達 38°C 以上；紅燈：達 38°C 以上且持續 3 天以上"),
            ("官方發布時間", "每日 17:30 發布隔日高溫資訊，並於 7:30、11:30、14:30 定時更新；遇突發顯著高溫依整點觀測即時更新"),
            ("產品說明文件", "https://www.cwa.gov.tw/V8/assets/pdf/HeatInformation_ProductDescription.pdf"),
            ("擷取時間", "7:00–14:45 每 15 分鐘檢查；燈號或發布時間有變更即截圖，另於 9、12、14 時固定截圖"),
            ("取值規則", "只計 9:00–14:00 內的檢查紀錄（17:30 發布的是隔日燈號，不計入當天）；「–」表示無燈號"),
            ("截圖資料夾", "每日資料夾／2_高溫資訊（含 Warning_63.js 原始資料檔）"),
        ],
    },
    config.MASTER_HEALTH_XLSX: {
        "title": "熱傷害總表",
        "rows": [
            ("記錄內容", "臺北市 12 行政區每日 09 時、12 時兩個時段（涵蓋 9:00–14:00）中最高的熱傷害預警燈號"),
            ("資料來源網頁", "健康氣象－熱傷害（選擇縣市：臺北市，逐區點選）"),
            ("網址", config.URL_HEALTH),
            ("網頁燈號資料", "網頁本身載入的資料檔"),
            ("網頁燈號資料網址", "https://crowa.cwa.gov.tw/api/v2.0/static/Lohas/Health/getWarnTown_HD.json\n"
                                 "https://crowa.cwa.gov.tw/api/v2.0/static/Lohas/Health/getUserRiskForecast_HD.json"),
            ("燈號說明", "依 WBGT（綜合溫度熱指數）分為 注意／警戒／危險／高危險（網頁標示為高風險）四級；逐三小時預報"),
            ("核對資料", "氣象資料開放平臺 M-A0085-001 健康氣象熱傷害指數及警示（全台各鄉鎮五日逐三小時預報）"),
            ("核對資料網址", "https://opendata.cwa.gov.tw/"),
            ("擷取時間", "7:00–14:45 每 15 分鐘檢查；資料更新即截圖（臺北市＋12 區各一張），另於 9、12、14 時固定截圖"),
            ("取值規則", "取 09、12 時段中較高者；15 時以後時段不計；「–」表示無燈號"),
            ("截圖資料夾", "每日資料夾／3_熱傷害（含臺北市資料.json）"),
        ],
    },
    config.MASTER_STATION_XLSX: {
        "title": "歷年中午溫度_臺北測站",
        "rows": [
            ("記錄內容", "臺北氣象站（站號 466920）每日 11:00、12:00、13:00 整點氣溫"),
            ("資料來源網頁", "測站觀測資料－臺北（過去 24 小時，每 10 分鐘一筆）"),
            ("網址", config.URL_STATION),
            ("擷取時間", "每個整點後第一次檢查（約 11:15、12:15、13:15）擷取並將該整點列標黃截圖；當晚 23:55 補抓漏掉的整點"),
            ("取值規則", "取表格中該整點（hh:00）那一列的溫度；分頁名稱為民國年"),
            ("截圖資料夾", "每日資料夾／4_臺北測站逐時"),
        ],
    },
    config.MASTER_TOWN_XLSX: {
        "title": "體感溫度總表",
        "rows": [
            ("記錄內容", "臺北市 12 行政區每日 07–18 時逐時體感溫度與溫度（每天一個分頁）"),
            ("資料來源網頁", "鄉鎮預報－「過去 24 小時」表格"),
            ("網址", config.URL_TOWN.format(tid="6300100") + "\n（TID 6300100 松山區 … 6301200 北投區，共 12 區）"),
            ("數值資料", "網頁使用的資料檔（12 區逐時溫度與體感溫度）"),
            ("數值資料網址", "https://www.cwa.gov.tw" + config.URL_TOWN_24HR_DATA),
            ("擷取時間", "每天 19:00 後擷取一次（含 07–18 時），當晚 23:55 補抓"),
            ("取值規則", "空白表示氣象署該小時無資料"),
            ("截圖資料夾", "每日資料夾／5_鄉鎮體感溫度（含 ChartData_GT24hr_T_63.js 原始資料檔）"),
        ],
    },
}


def _write_readme(ws, info: dict) -> None:
    ws.append([info["title"] + "－資料來源說明"])
    ws.merge_cells("A1:B1")
    ws["A1"].font = Font(size=16, bold=True, color="1F4E79")
    ws.append([])
    ws.append(["項目", "內容"])
    for c in ws[3]:
        c.fill, c.font = HEADER_FILL, HEADER_FONT
    for k, v in info["rows"] + COMMON_ROWS:
        ws.append([k, v])
        cell = ws.cell(row=ws.max_row, column=2)
        if v.startswith("http"):
            cell.hyperlink = v.split("\n")[0]
            cell.font = Font(color="0563C1", underline="single")
    for row in ws.iter_rows(min_row=3, max_row=ws.max_row, max_col=2):
        for c in row:
            c.alignment = Alignment(vertical="center", wrap_text=True)
            c.border = GRID_BOX
        if row[0].row > 3:
            row[0].font = Font(bold=True)
        lines = str(row[1].value or "").count("\n") + 1 + len(str(row[1].value or "")) // 60
        ws.row_dimensions[row[0].row].height = 20 * lines
    ws.column_dimensions["A"].width = 20
    ws.column_dimensions["B"].width = 100
    ws.page_setup.orientation = "landscape"   # 列印時橫向、寬度縮成一頁
    ws.page_setup.fitToWidth, ws.page_setup.fitToHeight = 1, 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True


def finalize_master(wb, path: str) -> None:
    """資料分頁新到舊排序，最前面放「說明」分頁，開啟時顯示最新的資料分頁。"""
    if "說明" in wb.sheetnames:
        del wb["說明"]
    wb._sheets.sort(key=lambda s: s.title, reverse=True)
    info = README.get(os.path.basename(path))
    if info:
        _write_readme(wb.create_sheet("說明", 0), info)
    wb.active = 1 if info and len(wb.sheetnames) > 1 else 0
    for ws in wb.worksheets:
        ws.sheet_view.tabSelected = ws is wb.active
    wb.save(path)


def _town_rows(day: str) -> tuple[list[str], list[list]]:
    fields, rows = _read_csv(os.path.join(common.day_dir(day), f"體感溫度_{day}.csv"))
    return fields, [[r.get(f, "") for f in fields] for r in rows]


def build_daily(day: str) -> str | None:
    fields, rows = _read_csv(common.csv_path(day))
    t_fields, t_rows = _town_rows(day)
    if not rows and not t_rows:
        return None
    wb = Workbook()
    wb.remove(wb.active)
    rec = temptop_record(day, rows)
    write_temptop_sheet(wb.create_sheet("縣市溫度極值"), [rec] if rec else [])
    county, towns = w29_summary(rows)
    write_w29_daily_sheet(wb.create_sheet("高溫紀錄表"), day, rec, county)
    write_w29_district_sheet(wb.create_sheet("北市12行政區_高溫資訊"), [w29_district_row(day, towns)])
    write_health_district_sheet(wb.create_sheet("北市12行政區_熱傷害"), [w29_district_row(day, health_summary(rows))])
    srow = station_row(day, rows)
    write_station_sheet(wb.create_sheet("歷年中午溫度_臺北測站"), int(day[:4]) - 1911, [srow] if srow else [])
    cols = [f for f in fields if f not in DROP_COLS]
    for title, key in SHEETS:
        part = [r for r in rows if key in r.get("網站", "") and r.get("狀態") not in ("失敗",)]
        _write_sheet(wb.create_sheet(title), cols, [[r.get(c, "") for c in cols] for r in part])
    ws = wb.create_sheet("體感溫度")
    if t_rows:
        _write_sheet(ws, t_fields, t_rows)
    else:
        ws.append(["今天的體感溫度尚未記錄（每天 19 時後產生）"])
    bad = [r for r in rows if r.get("狀態") in ("失敗", "缺漏") or "截圖失敗" in r.get("狀態", "")]
    _write_sheet(wb.create_sheet("執行狀況"), fields, [[r.get(c, "") for c in fields] for r in bad])
    for name in ("高溫資訊明細", "熱傷害明細"):  # 明細放最後
        wb.move_sheet(name, offset=len(wb.sheetnames) - 1 - wb.sheetnames.index(name))
    path = os.path.join(common.day_dir(day), f"紀錄_{day}.xlsx")
    wb.save(path)
    return path


def update_master(day: str) -> bool:
    t_fields, t_rows = _town_rows(day)
    if not t_rows:
        return False
    path = os.path.join(config.OUT_DIR, config.MASTER_TOWN_XLSX)
    if os.path.exists(path):
        wb = load_workbook(path)
    else:
        wb = Workbook()
        wb.remove(wb.active)
    if day in wb.sheetnames:
        del wb[day]
    _write_sheet(wb.create_sheet(day), t_fields, t_rows)
    finalize_master(wb, path)  # 最新的一天放最前面
    return True


def update_temptop_master(day: str) -> bool:
    """縣市溫度極值總表：每年一個分頁，每天一列，依日期排序；同一天重抓會覆蓋。"""
    fields, rows = _read_csv(common.csv_path(day))
    rec = temptop_record(day, rows)
    if not rec:
        return False
    path = os.path.join(config.OUT_DIR, config.MASTER_TEMPTOP_XLSX)
    wb = load_workbook(path) if os.path.exists(path) else Workbook()
    if not os.path.exists(path):
        wb.remove(wb.active)
    year = day[:4]
    existing = {}
    if year in wb.sheetnames:
        ws = wb[year]
        hdr = [c.value for c in ws[1]]
        for vals in ws.iter_rows(min_row=2, values_only=True):
            if vals and vals[0] is not None:
                r = dict(zip(hdr, vals))
                existing[(int(r["月份"]), int(r["日期"]))] = r
        del wb[year]
    existing[(rec["月份"], rec["日期"])] = rec
    ws = wb.create_sheet(year)
    write_temptop_sheet(ws, [existing[k] for k in sorted(existing)])
    finalize_master(wb, path)
    return True


def refresh(days: list[str]) -> list[str]:
    done = []
    for day in sorted(set(days)):
        try:
            if build_daily(day):
                done.append(f"紀錄_{day}.xlsx")
            if update_master(day):
                done.append(f"{config.MASTER_TOWN_XLSX}［{day}］")
            if update_record_master(day):
                done.append(f"{config.MASTER_RECORD_XLSX}［{day}］")
            if update_w29_master(day):
                done.append(f"{config.MASTER_W29_XLSX}［{day}］")
            if update_health_master(day):
                done.append(f"{config.MASTER_HEALTH_XLSX}［{day}］")
            if update_station_master(day):
                done.append(f"{config.MASTER_STATION_XLSX}［{day}］")
            if update_temptop_master(day):
                done.append(f"{config.MASTER_TEMPTOP_XLSX}［{day}］")
        except Exception as e:  # noqa: BLE001  試算表失敗不影響截圖與 CSV
            print(f"[試算表] {day} 產生失敗：{e}")
    print("[試算表] 已更新：", "、".join(done) or "無")
    return done


def main() -> int:
    days = [d.strip() for d in os.environ.get("REBUILD_DAYS", "").split(",") if d.strip()]
    if not days:
        print("請設定 REBUILD_DAYS")
        return 1
    common.pull_existing(days)
    refresh(days)
    return 0 if common.push_all() else 1


if __name__ == "__main__":
    sys.exit(main())
