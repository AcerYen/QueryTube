# QueryTube 快速啟動指南

本文件協助你在 **30 分鐘內** 啟動 QueryTube 全部功能：YouTube 監控、AI 摘要、Telegram 推播，以及無字幕影片的 Groq 轉錄備援。

---

## 系統組成

| 元件 | 用途 | 啟動方式 |
|------|------|----------|
| **QueryTube Agent** | 排程掃描頻道、產生摘要、推播 | `docker compose up` 或 `python app/main.py` |
| **Groq Whisper** | 無官方字幕時，語音轉文字 | Groq 雲端 API（無需額外容器） |
| **Telegram Bot** | 訂閱管理、即時推播 | 內建於 Agent，無需另起 |
| **Gemini** | 產出繁中摘要 | Google AI Studio API |

```
YouTube API ──► QueryTube Agent ──► Gemini ──► Telegram 推播
                     │
                     └── 無字幕 ──► Groq Whisper ──► 字幕 ──► 摘要
```

---

## 啟動前準備

### 1. 取得 API 金鑰

| 項目 | 取得方式 |
|------|----------|
| **YouTube Data API v3** | [Google Cloud Console](https://console.cloud.google.com/) → 建立專案 → 啟用 YouTube Data API v3 → 建立 API 金鑰 |
| **Gemini API**（正式環境） | [Google AI Studio](https://aistudio.google.com/) → 建立 API Key |
| **Telegram Bot Token** | Telegram 搜尋 [@BotFather](https://t.me/BotFather) → `/newbot` 建立 Bot |
| **Telegram User ID** | 搜尋 [@userinfobot](https://t.me/userinfobot) 或 [@getidsbot](https://t.me/getidsbot) 查詢自己的數字 ID |
| **Groq API**（無字幕備援） | [console.groq.com/keys](https://console.groq.com/keys) → 建立 API Key |

### 2. 環境需求

- **Docker 部署**：Docker + Docker Compose（NAS / Linux / Windows Docker Desktop）
- **本地開發**：Python 3.11+、ffmpeg（yt-dlp 下載音訊用）
- **Groq API Key**：[console.groq.com](https://console.groq.com/keys)（無字幕備援用，建議設定）

---

## 方式一：Docker 一鍵部署（推薦）

適用於 Synology / QNAP NAS 或任何有 Docker 的機器，**一次啟動全部服務**。

### 步驟 1：複製並編輯設定

```bash
cd QueryTube
cp .env.example .env
```

編輯 `.env`，至少填入以下必填項：

```env
YOUTUBE_API_KEY=你的_YouTube_金鑰
GEMINI_API_KEY=你的_Gemini_金鑰
TELEGRAM_BOT_TOKEN=你的_Bot_Token
TELEGRAM_ADMIN_ID=你的_Telegram_數字ID

# 啟動時自動加入管理員的訂閱清單（可選，逗號分隔）
CHANNEL_IDS=UCxxxxxxxx,UCyyyyyyyy

# 無字幕備援（建議填）
GROQ_API_KEY=你的_Groq_金鑰
GROQ_WHISPER_MODEL=whisper-large-v3-turbo
```

### 步驟 2：建置並啟動

```bash
docker compose up -d --build
```

這會啟動 `querytube` 容器（主程式 + Telegram Bot）。語音轉錄透過 Groq 雲端 API，無需額外容器。

### 步驟 3：確認運作

```bash
docker compose logs -f querytube
```

看到以下訊息代表成功：

```
Scheduled daily job at 20:00 (Asia/Taipei)
Telegram bot started for channel management.
QueryTube Agent started. Monitoring N channel(s), push at 20:00 (Asia/Taipei).
Application started
```

```bash
docker compose ps
```

`querytube` 容器應為 `running` 狀態。

### 步驟 4：Telegram 首次使用

1. 在 Telegram 搜尋你的 Bot 名稱
2. 傳送 `/start` — 若 `.env` 有設定 `CHANNEL_IDS`，管理員會自動加入預設頻道並收到最新片 catch-up 推播
3. 傳送 `/add UCxxxxxxxx` 或頻道網址，加入要監控的頻道
4. 傳送 `/list` 確認清單

---

## 方式二：本機正式試運行（Windows）

在上線 NAS 前，以**與正式環境相同的設定**在本機驗證。

### 步驟 1：Python 環境（首次）

```powershell
cd d:\Code\QueryTube
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 步驟 2：設定 `.env`（正式參數）

```powershell
copy .env.example .env
```

編輯 `.env`，填入 API 金鑰，並確認以下**正式上線參數**：

```env
YOUTUBE_API_KEY=你的_YouTube_金鑰
GEMINI_API_KEY=你的_Gemini_金鑰
TELEGRAM_BOT_TOKEN=你的_Bot_Token
TELEGRAM_ADMIN_ID=你的_Telegram_數字ID
CHANNEL_IDS=UCxxxxxxxx,UCyyyyyyyy

# 無字幕備援
GROQ_API_KEY=你的_Groq_金鑰

# 正式排程（與 NAS 相同）
CHECK_TIMES=20:00
TZ=Asia/Taipei
RUN_ON_STARTUP=false
```

### 步驟 3：確認無重複程序

Telegram Bot **同一時間只能有一個實例**在 polling。啟動前先清掉舊程序：

```powershell
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -like '*app/main.py*' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```

### 步驟 4：啟動 QueryTube

```powershell
cd d:\Code\QueryTube
.\.venv\Scripts\Activate.ps1
$env:PYTHONPATH = "d:\Code\QueryTube"
python app/main.py
```

看到以下訊息代表成功：

```
Scheduled daily job at 20:00 (Asia/Taipei)
Telegram bot started for channel management.
QueryTube Agent started. Monitoring N channel(s), push at 20:00 (Asia/Taipei).
Application started
```

程序會持續在前景執行，等到 `CHECK_TIMES` 設定的時間自動掃描推播。按 `Ctrl+C` 停止。

### 步驟 5：Telegram 驗證

1. 對 Bot 傳送 `/start`
2. 傳送 `/list` 確認頻道清單
3. 若要手動觸發推播，傳送 `/push`（不需改 `RUN_ON_STARTUP`）

### 僅限除錯：啟動時立刻掃描

正式試運行**不建議**開啟。僅在需要驗證排程邏輯時，暫時設：

```env
RUN_ON_STARTUP=true
```

測完務必改回 `false` 並重啟。

---

## 功能驗收清單

依序確認以下項目，代表**全部功能**已就緒：

- [ ] **設定檢查**：啟動 log 無 `Missing required environment variables` 錯誤
- [ ] **排程**：log 顯示 `Scheduled daily job at ...`
- [ ] **Telegram Bot**：對 Bot 傳 `/start` 有回覆
- [ ] **頻道管理**：`/add`、`/list`、`/remove` 正常
- [ ] **Catch-up 推播**：新增頻道或首次 `/start` 後，收到最新影片摘要
- [ ] **排程推播**：等到 `CHECK_TIMES` 設定的時間，或設 `RUN_ON_STARTUP=true` 立即觸發
- [ ] **Groq 備援**（可選）：找一支無官方字幕的影片測試，log 應出現 Groq 轉錄流程

---

## 環境變數速查

| 變數 | 預設 | 說明 |
|------|------|------|
| `YOUTUBE_API_KEY` | — | **必填** YouTube Data API 金鑰 |
| `GEMINI_API_KEY` | — | **必填** Gemini API 金鑰 |
| `GEMINI_MODEL` | `gemini-2.5-flash` | 摘要模型 |
| `TELEGRAM_BOT_TOKEN` | — | **必填** Bot Token |
| `TELEGRAM_ADMIN_ID` | — | **必填** 管理員 User ID（同步接收用戶動態、可使用管理指令） |
| `GROQ_API_KEY` | — | 無字幕備援轉錄（建議填） |
| `GROQ_WHISPER_MODEL` | `whisper-large-v3-turbo` | Groq Whisper 模型 |
| `CHANNEL_IDS` | 空 | 啟動時種入管理員訂閱清單 |
| `CHECK_TIMES` | `20:00` | 每日掃描時間（24h，逗號分隔） |
| `TZ` | `Asia/Taipei` | 排程時區 |
| `RUN_ON_STARTUP` | `false` | `true` = 啟動時立即掃描一次 |

---

## Telegram 指令

| 指令 | 說明 |
|------|------|
| `/start` | 註冊帳號；管理員首次使用會自動加入 `CHANNEL_IDS` 頻道 |
| `/add <ID或網址>` | 加入監控頻道（僅影響你的清單），並推播該頻道最新片 |
| `/remove <ID或網址>` | 從你的清單移除 |
| `/list` | 查看訂閱清單 |
| `/help` | 指令說明 |

**管理員專用**：

| 指令 | 說明 |
|------|------|
| `/status` | 系統執行狀態 |
| `/users` | 所有用戶清單 |
| `/user <Telegram ID>` | 指定用戶詳情 |

管理員（`TELEGRAM_ADMIN_ID`）會同步收到各用戶的操作與推播動態。

---

## API 用量與頻率控制

系統已內建呼叫間隔，避免觸發免費額度限制。預設值可在 `.env` 調整：

| 變數 | 預設 | 說明 |
|------|------|------|
| `YOUTUBE_API_DELAY` | `0.5` | 每次 YouTube Data API 呼叫間隔（秒） |
| `GEMINI_API_DELAY` | `6.5` | 每次 Gemini 摘要間隔（秒，對應約 10 RPM） |
| `TRANSCRIPT_DELAY` | `2.0` | 字幕擷取 / yt-dlp 下載間隔（秒） |
| `CHANNEL_PROCESS_DELAY` | `3.0` | 處理下一個頻道前的等待（秒） |
| `VIDEO_PROCESS_DELAY` | `2.0` | 處理同一頻道下一支影片前的等待（秒） |

### 各服務免費額度估算

| 服務 | 免費額度（約） | 本系統用量（2 頻道、每日 2 次掃描） |
|------|----------------|-------------------------------------|
| YouTube Data API | 10,000 units/日 | 約 12 units/日（每頻道每次掃描 3 units） |
| Gemini 2.5 Flash | 10 RPM、250 RPD | 每日最多 4 次摘要（每頻道每次最多 1 支新片） |
| Telegram Bot | 30 msg/s 全域、1 msg/s 每聊天室 | 已內建限流與 429 重試 |
| Groq Whisper | 免費額度有限，按音訊長度計費 | 僅無字幕時觸發 |

**注意：**
- `/push`、`/add` 會立即觸發 catch-up，若與排程掃描重疊，後者會自動延後（避免同時大量呼叫 API）。
- 訂閱頻道過多時，建議拉長 `GEMINI_API_DELAY`（例如 `10`）或減少 `CHECK_TIMES` 頻率。
- Gemini 免費層配額可能隨 Google 政策調整，請以 [AI Studio 儀表板](https://aistudio.google.com/) 為準。

---

## 常見問題

**Q：想立刻測試排程掃描，不想等到 20:00？**  
對 Bot 傳 `/push` 可手動推播最新影片。若需驗證排程程式本身，可暫時設 `RUN_ON_STARTUP=true`，測完改回 `false`。

**Q：啟動後出現 `409 Conflict` / `only one bot instance is running`？**  
代表有多個 `python app/main.py` 同時在跑。執行「步驟 4」清掉舊程序後，只啟動一個實例即可。

**Q：新片沒推播？**  
1. 確認已對 Bot 傳 `/start` 且 `/list` 有該頻道  
2. 該片可能已在 `data/querytube.db` 標記為已處理（刪除 DB 可重跑，但會重推所有新片）  
3. 確認 log 無 API 金鑰或 Telegram 錯誤

**Q：無字幕的影片？**  
系統會先嘗試 YouTube 官方字幕，失敗則透過 yt-dlp 下載音訊 → Groq Whisper 轉錄。確認 `.env` 已設定 `GROQ_API_KEY`。

**Q：Shorts 會推播嗎？**  
不會。系統自動跳過 YouTube Shorts（≤60 秒或含 `#shorts` 標記）。

**Q：如何停止服務？**

```powershell
# 本機試運行：在執行 python app/main.py 的終端機按 Ctrl+C

# 或強制停止所有 QueryTube 程序
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -like '*app/main.py*' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }

docker compose down          # Docker 部署
```

**Q：資料會保留嗎？**  
`./data`（SQLite）與 `./temp`（音訊暫存）已掛載 volume，容器重啟不會遺失已處理紀錄。

---

## 建議部署策略

| 場景 | GROQ_API_KEY | CHECK_TIMES | RUN_ON_STARTUP |
|------|--------------|-------------|----------------|
| NAS 正式環境 | 必填（建議） | `20:00` | `false` |
| 本機正式試運行 | 同上 | `20:00` | `false` |
| 除錯排程邏輯（暫時） | 同上 | 同上 | `true` |

---

## NAS 遷移指南（Windows 本機 → Synology / QNAP）

從本機試運行轉到 NAS 正式環境，依序完成以下步驟。

### 遷移前：停止本機 Agent

Telegram Bot **同一時間只能有一個實例** polling。上 NAS 前務必停止本機程序：

```powershell
# Windows 本機
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -like '*app/main.py*' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }

docker compose down   # 若本機也有跑 querytube 容器
```

### 步驟 1：準備 NAS 資料夾

在 NAS 建立專案目錄，例如：

| NAS 品牌 | 建議路徑 |
|----------|----------|
| Synology | `/volume1/docker/QueryTube` |
| QNAP | `/share/Container/QueryTube` |

需複製的檔案與目錄（**僅首次遷移**）：

```
QueryTube/
├── .env                 ← 從本機複製（含 API 金鑰）
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
├── app/
├── config/
├── data/
│   └── querytube.db     ← 首次遷移時複製；之後更新程式碼請勿覆蓋
└── temp/                ← 空目錄即可
```

> **注意**：之後用 `deploy-to-synology.ps1` 或手動解壓更新時，不要覆蓋 NAS 上既有的 `data/querytube.db` 與 `.env`。

**不需複製**：`.venv/`、`__pycache__/`、本機 `terminals/` 等。

### 步驟 2：調整 `.env`（NAS 正式參數）

```env
# Groq 語音轉錄（無字幕備援）
GROQ_API_KEY=你的_Groq_金鑰

# 排程：建議每日一次，省 Gemini 配額
CHECK_TIMES=20:00

TZ=Asia/Taipei
RUN_ON_STARTUP=false
```

其餘 API 金鑰、Bot Token、`CHANNEL_IDS` 直接沿用本機 `.env` 即可。

### 步驟 3：NAS 上建置並啟動

**Synology（Container Manager）**

1. 控制台 → 終端機 → 啟用 SSH
2. SSH 登入後：

```bash
cd /volume1/docker/QueryTube
sudo docker compose up -d --build
```

或在 **Container Manager → 專案 → 新增** → 選擇 `docker-compose.yml` → 建置。

**QNAP（Container Station）**

```bash
cd /share/Container/QueryTube
docker compose up -d --build
```

### 步驟 4：驗證

```bash
docker compose ps
docker compose logs -f querytube
```

| 檢查項 | 預期結果 |
|--------|----------|
| `docker compose ps` | `querytube_agent` 為 `running` |
| querytube log | 出現 `Application started` |
| Telegram | 對 Bot 傳 `/list` 有回覆 |

### 步驟 5：日常維護指令

```bash
# 查看 log
docker compose logs -f querytube

# 修改 .env 後重建（設定才會生效）
docker compose up -d --build --force-recreate querytube

# 停止
docker compose down

# 更新程式碼後（不會覆蓋 data/ 與 .env）
git pull   # 或透過 deploy-to-synology.ps1 上傳
docker compose up -d --build
```

**從 Windows 一鍵更新至 Synology**（會自動保留 NAS 上的用戶訂閱與 `.env`）：

```powershell
.\scripts\deploy-to-synology.ps1 -NasHost 192.168.x.x -NasUser admin
```

部署套件**不再包含** `data/querytube.db` 與 `.env`，避免本機資料覆蓋 NAS 正式環境。

### NAS 資源建議

| 元件 | RAM | 說明 |
|------|-----|------|
| `querytube` | ~128 MB | 常駐輕量，語音轉錄走 Groq 雲端 |
| **NAS 總可用** | ≥ 1 GB | 建議保留給 DSM / QTS 系統 |

---

更多細節請參考 [README.md](./README.md)。
