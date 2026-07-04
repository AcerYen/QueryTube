# QueryTube

自動監控 YouTube 頻道新片，以 Gemini 產出繁中摘要，推播至 Telegram。

> **快速啟動**：首次部署請先看 [QUICKSTART.md](./QUICKSTART.md)（含 Docker 一鍵部署、本地開發、功能驗收清單）。

**排程**：每日 **20:00**（`Asia/Taipei`）掃描並推播新片。

支援**多用戶獨立訂閱**：每位 Telegram 用戶可透過 Bot 指令管理自己的頻道清單；管理員帳號會同步收到各用戶的操作與推播動態，並可使用專用指令查詢系統狀態。

---

## NAS Docker 部署（推薦）

完整遷移步驟（含 Synology / QNAP、本機停服、資料複製）請見 [QUICKSTART.md — NAS 遷移指南](./QUICKSTART.md#nas-遷移指南windows-本機--synology--qnap)。

### 1. 準備設定

```bash
cp .env.example .env
```

編輯 `.env`，填入：

| 變數 | 說明 |
|------|------|
| `YOUTUBE_API_KEY` | [YouTube Data API v3](https://console.cloud.google.com/) 金鑰 |
| `GEMINI_API_KEY` | [Google AI Studio](https://aistudio.google.com/) 金鑰 |
| `TELEGRAM_BOT_TOKEN` | 透過 [@BotFather](https://t.me/BotFather) 建立的 Bot Token |
| `TELEGRAM_ADMIN_ID` | 管理員 Telegram User ID（可透過 [@userinfobot](https://t.me/userinfobot) 查詢） |
| `CHANNEL_IDS` | 逗號分隔的 Channel ID（啟動時加入管理員訂閱清單，可選） |
| `GROQ_API_KEY` | [Groq Console](https://console.groq.com/keys) 金鑰（無字幕備援轉錄，建議填） |
| `LINE_CHANNEL_SECRET` | （可選）LINE Messaging API Channel secret |
| `LINE_CHANNEL_ACCESS_TOKEN` | （可選）LINE Channel access token；與 secret 皆填才啟用 LINE 被動摘要 |

### 2. 啟動

在 Synology / QNAP 等 NAS 上，將專案資料夾掛載後執行：

```bash
docker compose up -d --build
```

### 3. 確認運作

```bash
docker compose logs -f querytube
```

正常啟動後會看到類似：

```
Scheduled daily job at 20:00 (Asia/Taipei)
Telegram bot started for channel management.
QueryTube Agent started. Monitoring N channel(s), push at 20:00 (Asia/Taipei).
Application started
```

在 Telegram 對 Bot 傳送 `/start` 即可開始使用。

### 4. LINE 被動摘要（可選）

在 LINE **群組或私訊**貼上 YouTube 連結即可自動產出摘要，**無需 @bot**，也不提供訂閱功能（訂閱仍只用 Telegram）。

回覆策略（省免費額度）：先以 **Reply** 回「正在整理…」（不計入月額度），完成後以 **Push** 送摘要（計入額度；群組依人數計算則數）。

#### LINE Developers 設定

1. 於 [LINE Developers](https://developers.line.biz/) 建立 Messaging API channel
2. 關閉 Greeting messages / Auto-reply messages（避免與 webhook 搶回覆）
3. 開啟 **Allow bot to join group chats**
4. 將 Channel secret、Channel access token 填入 `.env` 的 `LINE_CHANNEL_SECRET`、`LINE_CHANNEL_ACCESS_TOKEN`
5. Webhook URL 設為 `https://<你的網域>/line/webhook` 並 Verify

#### Cloudflare Tunnel（建議：NAS 不開公網 port）

NAS 若有私人資料，請用 Tunnel **只**暴露 QueryTube webhook，不要把整台 NAS 對外：

1. 安裝並登入 [cloudflared](https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/)
2. 建立 tunnel，ingress 指到本機 `http://127.0.0.1:8080`（compose 已將容器 `8080` 綁在 host localhost）
3. 公開路徑對應 `/line/webhook`（或整個 host 轉發即可，bot 只處理該 path）
4. 將 HTTPS 網域填入 LINE Webhook URL

未設定 LINE 憑證時，程式行為與先前相同，Telegram 不受影響。

### 5. 持久化

以下目錄已掛載 volume，重啟不會遺失已處理紀錄：

- `./data` — SQLite 資料庫
- `./temp` — 音訊暫存（Groq 轉錄備援用）

### 資源需求

- `querytube`：輕量（約 128 MB RAM），無需額外語音服務容器

---

## Telegram Bot 指令

| 指令 | 說明 |
|------|------|
| `/start` | 註冊並顯示歡迎訊息 |
| `/add <ID或網址>` | 加入要監控的 YouTube 頻道（僅影響你的清單） |
| `/remove <ID或網址>` | 從你的清單移除頻道 |
| `/list` | 查看你訂閱的頻道 |
| `/help` | 說明 |

**管理員專用**（`TELEGRAM_ADMIN_ID`）：

| 指令 | 說明 |
|------|------|
| `/status` | 查看系統執行狀態（背景任務、用戶/頻道統計） |
| `/users` | 列出所有註冊用戶 |
| `/user <Telegram ID>` | 查詢指定用戶的訂閱詳情 |

管理員會同步收到各用戶的操作與推播動態（訊息標題為「用戶動態」）。

---

## 環境變數

| 變數 | 預設 | 說明 |
|------|------|------|
| `CHECK_TIMES` | `20:00` | 每日推播時間 |
| `TZ` | `Asia/Taipei` | 排程時區 |
| `RUN_ON_STARTUP` | `false` | 容器啟動時是否立即跑一次 |
| `YOUTUBE_API_DELAY` | `0.5` | YouTube API 呼叫間隔（秒） |
| `GEMINI_API_DELAY` | `6.5` | Gemini 摘要間隔（秒） |
| `TRANSCRIPT_DELAY` | `2.0` | 字幕擷取間隔（秒） |
| `CHANNEL_PROCESS_DELAY` | `3.0` | 頻道間處理間隔（秒） |
| `VIDEO_PROCESS_DELAY` | `2.0` | 影片間處理間隔（秒） |
| `GROQ_API_KEY` | — | 無字幕備援轉錄（建議填） |
| `GROQ_WHISPER_MODEL` | `whisper-large-v3-turbo` | Groq Whisper 模型 |
| `LINE_CHANNEL_SECRET` | — | （可選）LINE Channel secret |
| `LINE_CHANNEL_ACCESS_TOKEN` | — | （可選）LINE Channel access token |
| `LINE_WEBHOOK_PORT` | `8080` | LINE webhook 監聽 port |

---

## 本地開發

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# 編輯 .env，填入 GROQ_API_KEY（無字幕備援用）

set PYTHONPATH=d:\Code\QueryTube
set RUN_ON_STARTUP=true
python app/main.py
```

---

## 常見問題

**Q: 啟動後出現 `409 Conflict`？**  
本機 `python app/main.py` 與 NAS Docker 容器不可同時運行。停止本機程序後重啟 NAS 容器。

**Q: Docker log 沒有 `Application started`？**  
確認 `querytube` 容器 log 無 Telegram 或環境變數錯誤。

**Q: 新片沒推播？**  
確認 `.env` 必填變數、已對 Bot 傳送 `/start` 並用 `/add` 加入頻道，且該片尚未在 `data/querytube.db` 標記為已處理。

**Q: 無字幕的片？**  
會跳過並在下次排程（預設 20:00）重試；若持續失敗，確認 `.env` 已設定 `GROQ_API_KEY` 且 Groq 帳戶額度正常。

**Q: 想立刻測試？**  
暫時設 `RUN_ON_STARTUP=true` 重啟容器，測完改回 `false`。

**Q: 連續多片推播會被限流嗎？**  
系統內建 Telegram 頻率限制保護（全域與單一聊天室間隔），並在收到 429 時自動等待重試。YouTube 與 Gemini 亦有呼叫間隔與配額錯誤重試，詳見 [QUICKSTART.md](./QUICKSTART.md#api-用量與頻率控制)。

**Q: LINE 免費訊息額度？**  
台灣輕用量方案每月約 200 則 Push（Reply 不計費）。群組 Push 依**群組成員數**計則。額度用完後摘要 Push 會失敗。詳見 [LINE Biz 訊息費用](https://tw.linebiz.com/faq/oa-price/message-price-list/)。

**Q: 沒填 LINE 變數會怎樣？**  
LINE bot 不啟動，Telegram 訂閱／推播／貼連結摘要行為不變。
