# 臺北市高溫紀錄自動截圖

每天自動到中央氣象署三個網頁截圖、記錄燈號，存到 Google Drive。程式跑在 GitHub 的雲端機器上，**你的電腦不用開機**。

| 網站 | 記錄內容 | 何時截圖 |
|---|---|---|
| 1. 縣市溫度極值 | 臺北市當日最高溫、觀測時間、測站 | 每天 23:55（今日）與隔天 00:30（昨日定案版） |
| 2. 高溫資訊 | 臺北市＋12 行政區的高溫燈號 | 07:40、08:45、**09:00–14:45 每 15 分鐘檢查**、17:45；發佈時間有變才截圖，09 與 14 時固定各截一次 |
| 3. 健康氣象（熱傷害） | 臺北市＋12 行政區 09–14 時的熱傷害燈號 | 同上；燈號來自氣象署開放資料 M-A0085-001 |

---

## 一、Google Drive 會長這樣

```
高溫紀錄/                      ← 你指定的資料夾
├─ 2026-10/
│   └─ 2026-10-02/
│       ├─ 每日紀錄_2026-10-02.csv     ← 每次檢查都會記一筆（用 Excel 開）
│       ├─ 1_縣市溫度極值/2355_縣市溫度極值_今日_高溫.png
│       ├─ 2_高溫資訊/1130_高溫資訊_臺北市.png
│       ├─ 2_高溫資訊/1130_高溫資訊_臺北市信義區.png …
│       └─ 3_熱傷害/0900_熱傷害_臺北市.png …
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

## 二、部署步驟（只需做一次，約 30 分鐘）

### 步驟 1：建立 GitHub repo

1. 到 <https://github.com> 註冊或登入。
2. 右上角「＋」→ **New repository**。
   - 名稱：例如 `cwa-heat-capture`
   - 選 **Public**（公開 repo 的執行時間免費無上限；程式碼裡沒有任何密碼，截圖都存在你的 Drive）
3. 建好後點 **uploading an existing file**，把這個資料夾裡的**所有檔案和資料夾**拖進去，按 **Commit changes**。
   - 確認 `.github/workflows/capture.yml` 有上傳成功（`.github` 是隱藏資料夾，Mac 請按 `Cmd+Shift+.` 顯示）。

### 步驟 2：準備 Google Drive 資料夾

1. 在 Google Drive 建一個資料夾，例如「高溫紀錄」。
2. 打開它，網址會像 `https://drive.google.com/drive/folders/1AbCdEfGh...`
3. `folders/` 後面那串就是**資料夾 ID**，先複製起來。

### 步驟 3：取得 Google Drive 授權（rclone）

程式用 rclone 上傳檔案，需要你本人授權一次。

1. 到 <https://rclone.org/downloads/> 下載 Windows（或 Mac）版，解壓縮。
2. 在解壓縮的資料夾裡開「命令提示字元」（Windows：在資料夾網址列輸入 `cmd` 按 Enter）。
3. 輸入：
   ```
   rclone authorize "drive"
   ```
4. 瀏覽器會打開 Google 登入頁，用**要存檔的那個 Google 帳號**登入並按「允許」。
5. 回到命令視窗，會看到一段 `{"access_token":"...","refresh_token":"...",...}`，把**整段大括號（含大括號）**複製起來。

> 這段 token 等同 Drive 的鑰匙，只貼到下一步的 GitHub Secrets，不要貼到其他地方。

### 步驟 4：在 GitHub 設定三個 Secrets

repo 頁面 → **Settings** → 左側 **Secrets and variables** → **Actions** → **New repository secret**，新增三個：

| Name | Value |
|---|---|
| `CWA_API_KEY` | 你的氣象署授權碼（`CWA-` 開頭） |
| `GDRIVE_TOKEN` | 步驟 3 複製的整段 `{…}` |
| `GDRIVE_FOLDER_ID` | 步驟 2 的資料夾 ID |

### 步驟 5：第一次試跑（探勘模式）

氣象署網頁沒有固定的元素代號，程式用多種方式嘗試點選「臺北市」。第一次請先跑探勘模式，確認網頁實際長相：

1. repo 頁面 → **Actions** 分頁（若提示要啟用就按啟用）。
2. 左側點 **氣象截圖** → 右邊 **Run workflow** → mode 選 `probe` → **Run workflow**。
3. 約 3 分鐘後完成，點進該次執行，最下方 **Artifacts** 可下載 `output-…` 壓縮檔；Drive 裡也會出現 `_probe` 資料夾。
4. **把裡面的 `w29.json`、`health.json`、`temptop.json` 傳給 Claude**，用來確認或調整點選方式。

### 步驟 6：正式試跑

1. 同樣 **Run workflow**，mode 選 `heat`，勾選 **force** → 執行。
2. 到 Drive 確認截圖與 CSV 都有出現，並打開截圖確認是臺北市的畫面。
3. 再跑一次 `temptop-today` 測試溫度極值。

之後就會依排程自動執行，不需再操作。

### 步驟 7：開啟失敗通知

GitHub 預設會在排程失敗時寄信給你。到 GitHub 右上角頭像 → **Settings** → **Notifications** → **Actions**，確認有勾選 **Email**，並建議選 **Only notify for failed workflows**。

---

## 三、日常維護

- **要暫停**：Actions → 氣象截圖 → 右上角「⋯」→ **Disable workflow**。冬天沒有高溫燈號時可以暫停。
- **只想記行政區燈號、不想每區都截圖**：Settings → Secrets and variables → Actions → **Variables** 分頁，新增 `DISTRICT_SCREENSHOTS` = `false`。
- **手動補截**：Run workflow，mode 選 `heat` 並勾 force。
- **排程停用**：公開 repo 若 60 天沒有異動，GitHub 會停用排程；`keepalive.yml` 每月自動寫入兩次來避免這件事。

## 四、已知限制

- **GitHub 排程會延遲**：尖峰時可能晚幾分鐘到十幾分鐘，少數情況會略過一次。每 15 分鐘檢查的設計就是為了容許這種延遲；CSV 的「記錄時間」是實際執行時間。
- **網站改版**：若某天點不到「臺北市」，CSV 會記「失敗」並寄信通知你，當天請手動補截，並把錯誤訊息交給 Claude 修正。
- **熱傷害燈號是逐三小時預報**：09–14 時對應 09:00 與 12:00 兩個時段，CSV 會列出兩段的燈號與指數。
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
