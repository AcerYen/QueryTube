# QueryTube — Agent Handoff Prompt

> 將本文件整段貼給新的 AI Agent 或新加入的開發者，即可快速接手專案。

---

## 專案目標

**QueryTube** 是一個自動化 YouTube 頻道監控 Agent：

1. 針對 `.env` 中設定的**多個 YouTube 頻道**，在固定時間掃描新影片
2. 取得新影片的**字幕／轉錄文字**
3. 透過 **Google Gemini** 分析內容，產出繁體中文「本期大綱」
4. 以 **Discord Webhook Embed** 推播至**單一 Discord 頻道**（手機 App 可收通知）

### 產品決策

- **推播時間**：每日 **07:30**、**19:30**（`Asia/Taipei`）
- **Discord**：暫只支援單一 `DISCORD_WEBHOOK_URL`
- **部署**：NAS 掛載 Docker Compose（`restart: always`）
- **優先順序**：主要功能先上線，多使用者等功能延後

---

## 目前進度摘要（截至 2026-06-06）

### 已完成 ✅

| 模組 | 檔案 | 狀態 |
|------|------|------|
| 設定載入 | `config/settings.py` | ✅ env、CHECK_TIMES、TZ、RUN_ON_STARTUP |
| 主排程 | `app/main.py` | ✅ 每日 07:30 / 19:30 固定排程 |
| 影片抓取 | `app/fetcher.py` | ✅ YouTube Data API v3 |
| 字幕取得 | `app/fetcher.py` | ✅ transcript API + Whisper fallback |
| AI 摘要 | `app/brain.py` | ✅ Gemini 2.5 Flash，字幕截斷 |
| Discord 推播 | `app/notifier.py` | ✅ Rich Embed，回傳 bool |
| 去重紀錄 | `app/database.py` | ✅ SQLite，推播成功才標記 |
| 容器化 | `Dockerfile`, `docker-compose.yml` | ✅ 含 TZ、volume 持久化 |
| 部署文件 | `README.md` | ✅ NAS Docker 指南 |

### 資料流

```
CHANNEL_IDS (.env)
    ↓
main.py (07:30 / 19:30 Asia/Taipei)
    ↓
fetcher.get_latest_videos()  ← YouTube Data API
    ↓
database.is_video_processed()? → 是 → 跳過
    ↓ 否
fetcher.get_transcript()     ← transcript API 或 Whisper fallback
    ↓
brain.summarize_video()      ← Gemini API
    ↓
notifier.send_discord_notification()  ← Discord Webhook
    ↓ 成功
database.mark_video_processed()
```

### 待驗證 / 後續 ⚠️

1. **NAS 實機部署**：尚未在 NAS 上跑過完整流程
2. **無字幕重試**：下次排程會重試；尚未有「N 次失敗後放棄」邏輯
3. **無 retry**：API 瞬間失敗需等下一個排程時段
4. **Whisper**：NAS 記憶體需 ≥2GB；無 healthcheck
5. **無測試 / CI**
6. **Git**：建議初始化 repository

---

## 目錄結構

```
QueryTube/
├── app/
│   ├── main.py
│   ├── fetcher.py
│   ├── brain.py
│   ├── notifier.py
│   └── database.py
├── config/
│   └── settings.py
├── data/              # SQLite（volume）
├── temp/              # 音訊暫存（volume）
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
├── .env.example
├── README.md
├── AGENT_HANDOFF_PROMPT.md
└── ROADMAP.md
```

---

## 環境變數

| 變數 | 必填 | 預設 | 說明 |
|------|------|------|------|
| `YOUTUBE_API_KEY` | ✅ | — | YouTube Data API v3 |
| `GEMINI_API_KEY` | ✅ | — | Gemini API |
| `DISCORD_WEBHOOK_URL` | ✅ | — | Discord Webhook |
| `CHANNEL_IDS` | ✅ | — | 逗號分隔 Channel ID |
| `CHECK_TIMES` | | `07:30,19:30` | 每日推播時間 |
| `TZ` | | `Asia/Taipei` | 排程時區 |
| `RUN_ON_STARTUP` | | `false` | 啟動時是否立即跑 |
| `WHISPER_API_URL` | | `http://whisper-api:9000/asr` | Whisper 服務 |

---

## NAS Docker 部署

```bash
cp .env.example .env   # 編輯填入金鑰
docker compose up -d --build
docker compose logs -f querytube
```

詳見 [README.md](./README.md)。

---

## 接手開發優先順序

1. **若 MVP 未上線**：協助 NAS 實測、排查 whisper / API 問題
2. **上線後**：見 `ROADMAP.md` Phase 2（retry、Embed 優化、healthcheck）
3. **不要先做**：多使用者 Webhook、Web UI（已明確延後）

---

## 給 Agent 的指令範本

```
你正在開發 QueryTube（d:\Code\QueryTube）。
請先閱讀 AGENT_HANDOFF_PROMPT.md、ROADMAP.md、README.md。

目標：NAS Docker 部署，每日 07:30/19:30 掃描 YouTube 新片，
Gemini 摘要後推播至單一 Discord 頻道。

目前 MVP 核心已實作，優先確保 NAS 穩定運行，再處理 Phase 2。
修改前閱讀相關模組，遵循繁中文案與 logging 風格。
```

---

## 外部服務配額

- **YouTube Data API**：每頻道每輪 ~2 quota units；每日 10,000 上限（兩次排程綽綽有餘）
- **Gemini API**：依字幕長度計費；已截斷至 80,000 字元
- **Discord Webhook**：30 req/min；Embed description 上限 4096（已截斷至 4000）
- **Whisper**：本地容器，長片 transcription 可能需數分鐘
