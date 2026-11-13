# 🏀 Lakers 24H News Monitor (`lakers-news-agent`)

24 小時全自動洛杉磯湖人隊（Los Angeles Lakers）新聞快報與短影音生成機器人。每小時自動追蹤 NBA 湖人隊最新賽況、傷兵名單與交易動態，透過 Gemini AI 編譯為繁體中文隨隊戰報，自動合成紫金配色直式語音短影音推送至 Discord 頻道，並具備 **AI 異常自動修復（Auto-Heal）** 能力。

---

## ✨ 核心特色

* **24 小時自動追蹤與去重**：每小時透過 RSS 聚合最新湖人隊英文動態，並以 `seen_news.json` 記錄已推播新聞雜湊值，避免重複通知。
* **AI 隨隊記者編譯與語音合成**：
  * 呼叫 Gemini 模型將英文報導濃縮為繁體中文重點摘要與 45 秒生動口播稿。
  * 結合 `edge-tts`（台灣中文語音）、`Pillow` 與 `FFmpeg` 自動產出 1080×1920 直式紫金戰報短影音並推送至 Discord。
* **高可用容錯設計**：
  * **多模型備援輪詢**：支援 `gemini-2.5-flash` -> `gemini-2.5-flash-lite` -> `gemini-2.0-flash` 自動切換，防止尖峰時段 API `503` 滿載中斷。
  * **TTS 優雅降級（Fallback）**：優先推送文字快報與原始新聞連結，若雲端 TTS 語音服務暫時受限則自動略過影音生成，確保訊息必達。
* **全自動自我修復（Auto-Heal）**：執行失敗時自動啟動 `debug_agent.py` 分析錯誤日誌、修補 `monitor.py` 並推送回 `main` 分支重新驗證，同時發送修復報告至 Discord。

---

## 📂 專案結構

    lakers-news-agent/
    ├── .github/workflows/
    │   └── watch_lakers.yml     # GitHub Actions 每小時排程與 Auto-Heal 流程定義
    ├── monitor.py               # 湖人新聞抓取、AI 摘要、短影音合成與 Discord 推播
    ├── debug_agent.py           # 異常自動診斷、程式碼修補與多模型容錯代理
    ├── seen_news.json           # 已推播新聞紀錄
    └── README.md                # 專案說明文件

---

## ⚙️ 環境與 Secrets 設定

1. **Repository Secrets**（`Settings` -> `Secrets and variables` -> `Actions`）：
   * `GEMINI_API_KEY`：Google AI Studio API 金鑰。
   * `DISCORD_WEBHOOK_URL`：Discord 湖人新聞頻道的專屬 Webhook 網址。
2. **Workflow 權限**（`Settings` -> `Actions` -> `General`）：
   * 勾選 **Read and write permissions**（允許更新 `seen_news.json` 與自動推送修復程式碼）。

---

## 🚀 執行方式

* **自動排程**：每小時整點（`0 * * * *` UTC）自動執行。
* **手動即時測試**：前往 **Actions** -> **Lakers 24H News Monitor** -> 點擊 **Run workflow** 即可手動觸發。
