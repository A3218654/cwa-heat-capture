"""所有可調整的設定集中在這裡。"""
import os

# ---- 網址 ----
URL_TEMPTOP = "https://www.cwa.gov.tw/V8/C/W/County_TempTop.html"
URL_W29 = "https://www.cwa.gov.tw/V8/C/P/Warning/W29.html"
URL_W29_IMAGE = "https://www.cwa.gov.tw/Data/warning/W29_C.png"
URL_HEALTH = "https://crowa.cwa.gov.tw/HealthWeather/"

# ---- 開放資料 API ----
API_BASE = "https://opendata.cwa.gov.tw/api/v1/rest/datastore"
DS_OBS = "O-A0001-001"        # 全測站逐時觀測（含今日最高溫）
DS_HEALTH = "M-A0085-001"     # 健康氣象熱傷害指數及警示（各鄉鎮逐三小時）
DS_WARNING = "W-C0033-001"    # 各縣市目前警特報
CWA_API_KEY = os.environ.get("CWA_API_KEY", "")

# ---- 地區 ----
COUNTY = "臺北市"
DISTRICTS = [
    "松山區", "信義區", "大安區", "中山區", "中正區", "大同區",
    "萬華區", "文山區", "南港區", "內湖區", "士林區", "北投區",
]

# ---- 關鍵時段（臺北時間）----
WINDOW_START = (9, 0)
WINDOW_END = (14, 0)
# 每天在這些整點之後的第一次執行，不論有無變化都固定截一張當基準
BASELINE_HOURS = [9, 14]

# 是否每個行政區都各截一張圖（False 則只截臺北市整體畫面，行政區只記錄燈號）
DISTRICT_SCREENSHOTS = os.environ.get("DISTRICT_SCREENSHOTS", "true").lower() == "true"

# ---- 輸出 ----
OUT_DIR = os.environ.get("OUT_DIR", "out")
STATE_DIR = os.environ.get("STATE_DIR", "state")
# rclone 遠端名稱（由環境變數 RCLONE_CONFIG_GDRIVE_* 設定，見 README）
RCLONE_REMOTE = os.environ.get("RCLONE_REMOTE", "gdrive:")

# ---- 瀏覽器 ----
VIEWPORT = {"width": 1440, "height": 900}
PAGE_TIMEOUT_MS = 60_000
RETRIES = 2
