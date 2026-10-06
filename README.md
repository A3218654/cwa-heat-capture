# 臺北市高溫紀錄自動截圖

每天自動到中央氣象署三個網頁截圖、記錄燈號，存到 Google Drive。程式跑在 GitHub 的雲端機器上，**你的電腦不用開機**。

| 網站 | 記錄內容 | 何時截圖 |
|---|---|---|
| 1. 縣市溫度極值 | 臺北市當日最高溫、觀測時間、測站 | 每天 23:55（今日）與隔天 00:30（昨日定案版） |
| 2. 高溫資訊 | 臺北市＋12 行政區的高溫燈號 | 07:40、08:45、**09:00–14:45 每 15 分鐘檢查**、17:45；燈號或發佈時間有變才截圖，09、12、14 時固定各截一次 |
| 4. 臺北測站（466920） | 11:00、12:00、13:00 整點溫度、天氣、相對溼度，該列標黃截圖 | 每個整點後第一次執行；23:55 補抓漏掉的 |
| 5. 鄉鎮預報「過去24小時」 | 12 區 07–18 時逐時體感溫度與溫度，每區一張截圖＋彙整表 | 每天 19 時後一次；23:55 補抓 |
| 3. 健康氣象（熱傷害） | 臺北市＋12 行政區的今日燈號，以及 09、12 時段的燈號與 WBGT | 同上；每個行政區各截一張 |

**燈號從哪裡讀**
- 高溫資訊：臺北市燈號讀網頁燈號表；各行政區燈號讀網頁本身使用的資料檔 `Warning_63.js`（W29-1/2/3 = 黃/橙/紅），每次截圖都會另存這個檔案當佐證。臺北市有燈號時會多截一張「臺北市」畫面；沒有燈號時網頁不允許選取臺北市，以「全縣市」畫面為證。
- 熱傷害：讀網頁本身載入的燈號資料（另存為「臺北市資料.json」），再用氣象署開放資料 M-A0085-001 核對（需設定 `CWA_API_KEY`）。
- 熱傷害網頁只保留「還沒過去」的時段，09、12 時段的燈號必須在當天時段內抓到，所以關鍵時段內每 15 分鐘檢查一次。

---

## 一、Google Drive 會長這樣

```
高溫紀錄/                      ← 你指定的資料夾
├─ 體感溫度總表.xlsx             ← 每天一個分頁，最新的在最前面（每份總表第一頁都是「說明」：資料來源與連結）
├─ 縣市溫度極值總表.xlsx         ← 每天一列：月份／日期／最高溫／時間／測站
├─ 高溫紀錄表總表.xlsx           ← 每天一列：月份／日期／最高溫／臺北市燈號
├─ 高溫資訊總表.xlsx             ← 每天一列：12 區當天 9–14 時最高燈號（下拉選單、自動上色）
├─ 熱傷害總表.xlsx               ← 每天一列：12 區當天 09、12 時段最高熱傷害燈號
├─ 歷年中午溫度_臺北測站.xlsx    ← 每天一列：臺北測站 11、12、13 時溫度（每個民國年一個分頁）
├─ 2026-10/
│   └─ 2026-10-02/
│       ├─ 紀錄_2026-10-02.xlsx        ← 當天試算表：縣市溫度極值／高溫紀錄表／北市12行政區_高溫資訊／北市12行政區_熱傷害／歷年中午溫度_臺北測站／體感溫度／執行狀況／高溫資訊明細／熱傷害明細 分頁
│       ├─ 每日紀錄_2026-10-02.csv     ← 每次檢查都會記一筆（用 Excel 開）
│       ├─ 1_縣市溫度極值/2355_縣市溫度極值_今日_高溫.png
│       ├─ 2_高溫資訊/1130_高溫資訊_全縣市.png（臺北市有燈號時另有 _臺北市.png）
│       ├─ 2_高溫資訊/1130_高溫資訊_Warning_63.js.txt（行政區燈號原始資料）
│       ├─ 4_臺北測站逐時/1115_臺北測站_11時.png …
│       ├─ 5_鄉鎮體感溫度/1915_體感溫度_臺北市松山區.png …
│       ├─ 體感溫度_2026-10-02.csv（12 區 × 07–18 時彙整表）
│       └─ 3_熱傷害/0900_熱傷害_臺北市信義區.png …（含臺北市資料.json）
└─ _state/state.json            ← 程式用來記住上次發佈時間，請勿刪除
```

每張截圖最上方都有一條時間橫幅：截圖時間（臺北時間）、來源網址、網頁上的發佈時間與燈號。

**CSV 欄位說明**

| 欄位 | 意思 |
|---|---|
| 記錄時間 | 程式實際執行的臺北時間 |
| 關鍵時段內 | 是否落在 09:00–14:00 |
| 網頁發佈時間／有效時間 | 從網頁上讀到的官方時間 |
| 燈號_網頁 | 從網頁讀到的燈號（高溫資訊） |
| 燈號_API | 從開放資料讀到的燈號（熱傷害、縣市警特報） |
| 數值 | 溫度，或熱傷害各時段「燈號(指數)」 |
| 狀態 | 已截圖／檢查-無變化／失敗 等 |
| 補充說明 | 截圖原因、點選方式、API 核對結果 |

「檢查-無變化」那幾列就是證明：那個時間點有檢查，只是官方沒有更新。

---

## 二、部署步驟

程式已放在 <https://github.com/A3218654/cwa-heat-capture>，三個網站也已在 GitHub 雲端機器上實測通過。剩下只需要設定存檔位置與金鑰：

### 步驟 1：準備 Google Drive 資料夾

1. 在 Google Drive 建一個資料夾，例如「高溫紀錄」。
2. 打開它，網址會像 `https://drive.google.com/drive/folders/1AbCdEfGh...`
3. `folders/` 後面那串就是**資料夾 ID**，先複製起來。

### 步驟 2：取得 Google Drive 授權（rclone）

1. 到 <https://rclone.org/downloads/> 下載 Windows（或 Mac）版，解壓縮。
2. 在解壓縮的資料夾裡開「命令提示字元」（Windows：在資料夾網址列輸入 `cmd` 按 Enter）。
3. 輸入：
   ```
   rclone authorize "drive"
   ```
4. 瀏覽器會打開 Google 登入頁，用**要存檔的那個 Google 帳號**登入並按「允許」。
5. 回到命令視窗，會看到一段 `{"access_token":"...","refresh_token":"...",...}`，把**整段大括號（含大括號）**複製起來。

> 這段 token 等同 Drive 的鑰匙，只貼到下一步的 GitHub Secrets，不要貼到其他地方。

### 步驟 3：在 GitHub 設定三個 Secrets

repo 頁面 → **Settings** → 左側 **Secrets and variables** → **Actions** → **New repository secret**，新增三個：

| Name | Value |
|---|---|
| `GDRIVE_TOKEN` | 步驟 2 複製的整段 `{…}` |
| `GDRIVE_FOLDER_ID` | 步驟 1 的資料夾 ID |
| `CWA_API_KEY` | 氣象署授權碼（`CWA-` 開頭，用來核對數值，可選但建議） |

在設定好前兩個之前，排程執行會回報「尚未設定 Google Drive」的失敗通知，這是刻意的，避免你以為有存檔。

### 步驟 4：試跑一次

1. repo 頁面 → **Actions** → 左側 **氣象截圖** → **Run workflow**，mode 選 `heat`，勾選 **force** → 執行。
2. 約 3 分鐘後到 Drive 確認截圖與 CSV 都有出現。

### 步驟 5：開啟失敗通知

GitHub 右上角頭像 → **Settings** → **Notifications** → **Actions**，勾選 **Email**，並建議選 **Only notify for failed workflows**。

### 步驟 6：設定準時觸發（cron-job.org）

GitHub 內建排程常延遲數小時或直接跳過，因此改由免費的 cron-job.org 準時呼叫 GitHub 執行；GitHub 內建排程保留當備援。

**6-1 建立 GitHub 權杖**
1. 打開 <https://github.com/settings/personal-access-tokens/new>
2. Token name：`cron-job`；Expiration：選最長（到期前要回來更新）
3. Repository access：**Only select repositories** → 選 `cwa-heat-capture`
4. Permissions → Repository permissions → **Actions** 改成 **Read and write**
5. 按 **Generate token**，複製 `github_pat_` 開頭的權杖（只會顯示一次）

**6-2 在 cron-job.org 建立三個工作**

註冊並登入 <https://console.cron-job.org>，按 **CREATE CRONJOB**。三個工作的共同設定：

- URL：`https://api.github.com/repos/A3218654/cwa-heat-capture/actions/workflows/capture.yml/dispatches`
- ADVANCED 分頁：
  - Request method：**POST**
  - Headers（三個）：
    - `Authorization` = `Bearer 你的github_pat_權杖`
    - `Accept` = `application/vnd.github+json`
    - `Content-Type` = `application/json`
  - Time zone：**Asia/Taipei**
- 成功時 GitHub 回應 **204**，是正常的

| 工作名稱 | 執行時間（Custom） | Request body |
|---|---|---|
| 高溫與熱傷害 | 分鐘 0,15,30,45；小時 7–14、17、19 | `{"ref":"main","inputs":{"mode":"heat"}}` |
| 今日最高溫 | 每天 23:55 | `{"ref":"main","inputs":{"mode":"temptop-today"}}` |
| 昨日最高溫 | 每天 00:30 | `{"ref":"main","inputs":{"mode":"temptop-yesterday"}}` |

## 三、日常維護

- **要暫停**：Actions → 氣象截圖 → 右上角「⋯」→ **Disable workflow**。冬天沒有高溫燈號時可以暫停。
- **熱傷害只想記行政區燈號、不想每區都截圖**：Settings → Secrets and variables → Actions → **Variables** 分頁，新增 `DISTRICT_SCREENSHOTS` = `false`。
- **測試用分支**：Run workflow 時勾選 debug，本次輸出會推到 `probe-results` 分支，不會存到 Drive。
- **手動補截**：Run workflow，mode 選 `heat` 並勾 force。
- **排程停用**：公開 repo 若 60 天沒有異動，GitHub 會停用排程；`keepalive.yml` 每月自動寫入兩次來避免這件事。

## 四、已知限制

- **GitHub 內建排程不準時**：實測曾延遲 3–5 小時或整段跳過，所以主要靠 cron-job.org 觸發（步驟 6）。CSV 的「記錄時間」永遠是實際執行時間。
- **GitHub 權杖會到期**：到期前 GitHub 會寄信提醒，屆時重做步驟 6-1，並把 cron-job.org 三個工作的 Authorization 換成新權杖。
- **網站改版**：若某天點不到「臺北市」，CSV 會記「失敗」並寄信通知你，當天請手動補截，並把錯誤訊息交給 Claude 修正。
- **熱傷害燈號是逐三小時資料**：09–14 時對應 09:00 與 12:00 兩個時段，CSV 的「數值」欄會列出兩段的燈號與 WBGT。
- **縣市溫度極值與 API 核對值可能不同**：API 核對值取臺北市所有測站中的最高值，網頁可能只列特定測站，兩者僅供互相參照。

## 五、檔案說明

```
capture/config.py   可調整的設定（網址、行政區、時段）
capture/sites.py    三個網站的截圖與燈號判讀
capture/api.py      氣象署開放資料（核對用）
capture/common.py   時間、浮水印、CSV、上傳 Drive
capture/main.py     進入點
.github/workflows/capture.yml    排程（時間為 UTC，臺北 = UTC+8）
.github/workflows/keepalive.yml  避免排程被停用
```
