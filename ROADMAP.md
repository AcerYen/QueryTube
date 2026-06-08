# QueryTube 開發路線圖

> 目標：每日 **07:30**、**19:30** 掃描指定 YouTube 頻道新片 → AI 分析 → 推播至**單一 Discord 頻道**（NAS Docker 部署）

---

## 產品決策（2026-06-06 更新）

| 項目 | 決定 |
|------|------|
| 推播時間 | 每日 07:30、19:30（`Asia/Taipei`） |
| Discord | 單一 Webhook / 單一頻道（暫不支援多使用者） |
| 部署 | NAS 掛載 Docker Compose |
| 優先順序 | **主要功能先上線**，進階功能延後 |

---

## 現況評估

| 面向 | 完成度 | 說明 |
|------|--------|------|
| 核心流程 | 🟢 ~85% | 抓取 → 字幕 → 摘要 → Discord 主路徑已通 |
| 排程 | 🟢 完成 | 固定每日兩次，非 interval 輪詢 |
| 穩定性 | 🟡 ~60% | 關鍵 bug 已修；無 retry、Whisper 無 healthcheck |
| 部署 | 🟡 ~70% | Docker + README；缺 healthcheck / 監控 |
| 測試 | 🔴 0% | 無自動化測試 |

---

## Phase 1 — MVP 上線（進行中 → 即將完成）

**目標**：NAS Docker 7×24 穩定跑，07:30 / 19:30 準時推播。

### 已完成 ✅

- [x] 排程改為 `CHECK_TIMES=07:30,19:30` + `TZ=Asia/Taipei`
- [x] 修正 `fetcher.py` `verbose_id` → `video_id`
- [x] 啟動時驗證必填 env
- [x] Discord 推播成功後才 `mark_video_processed`
- [x] Gemini 輸入字幕截斷（80,000 字元）
- [x] `RUN_ON_STARTUP=false`（NAS 正式環境不啟動即跑）
- [x] Docker 設定 `TZ=Asia/Taipei`
- [x] README（NAS 部署指南）
- [x] `.gitignore`、`__init__.py`

### 上線前待驗證 ⏳

- [ ] 在 NAS 實際部署並跑過一輪完整流程
- [ ] 確認 Discord 手機推播正常
- [ ] 確認 whisper-api 在 NAS 記憶體足夠（≥2GB）
- [ ] 初始化 Git repository

**Phase 1 完成標準**：NAS Docker 連續運行 3 天，新片在下一個排程時段內推播成功；重啟不重複推播。

---

## Phase 2 — 推播品質與穩定（上線後）

**目標**：訊息更好讀、失敗可恢復。仍維持單一 Discord 頻道。

- [ ] Discord Embed 加上 `published_at`、影片長度
- [ ] 同一輪多片推播間隔（避免 rate limit）
- [ ] API retry（YouTube / Gemini / Discord / Whisper）
- [ ] 無字幕影片：N 次失敗後推播「無法分析」簡訊並標記
- [ ] docker-compose healthcheck
- [ ] 連續失敗告警（可另設告警 Webhook）

---

## Phase 3 — 測試與維運

- [ ] Unit tests（database、notifier payload、settings）
- [ ] Mock integration tests
- [ ] GitHub Actions CI
- [ ] 日誌持久化（volume 或 NAS log 目錄）

---

## Phase 4 — 進階功能（暫緩，有需求再做）

| 功能 | 備註 |
|------|------|
| 多使用者 / 多 Webhook 訂閱 | 目前不需要 |
| Web UI 管理頻道 | 改 `.env` 即可 |
| 關鍵字過濾 | 依使用回饋 |
| YouTube PubSubHubbub 即時通知 | 降低延遲，複雜度高 |
| Telegram / Line | 非現階段目標 |

---

## 里程碑

| 里程碑 | 內容 | 狀態 |
|--------|------|------|
| **M1** | NAS Docker 07:30/19:30 自動推播 | 🟡 待 NAS 實測 |
| **M2** | Phase 2 穩定與推播優化 | 未開始 |
| **M3** | 測試 + CI | 未開始 |

---

## 技術債（非阻塞）

1. `fetcher.py` 裸 `except:` 應改為具體例外
2. SQLite 無 connection context manager
3. `requirements.txt` 版本需定期更新（尤其 yt-dlp）
4. Whisper `base` 模型中文準確度有限，可升級 `small`

---

## 相關文件

- [AGENT_HANDOFF_PROMPT.md](./AGENT_HANDOFF_PROMPT.md)
- [README.md](./README.md)
- [.env.example](./.env.example)
