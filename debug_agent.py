import os
import json
import requests
from openai import OpenAI

# 使用 GitHub Models 免費推論端點 + 內建 GITHUB_TOKEN (0 元免儲值呼叫 GPT-4o)
client = OpenAI(
    base_url="https://models.inference.ai.azure.com",
    api_key=os.environ["GITHUB_TOKEN"],
)
DISCORD_WEBHOOK = os.environ["DISCORD_WEBHOOK_URL"]

def run_auto_debug():
    if not os.path.exists("error.log"):
        print("找不到 error.log，略過自動除錯。", flush=True)
        return

    with open("error.log", "r", encoding="utf-8", errors="ignore") as f:
        error_trace = f.read()[-4000:]

    with open("monitor.py", "r", encoding="utf-8") as f:
        source_code = f.read()

    prompt = f"""
    你是一位資深的 Python 自動化與 CI/CD 除錯工程師。我們的雲端排程程式 `monitor.py` 在執行時發生錯誤中斷。
    請分析以下錯誤日誌與原始碼，回傳一個標準 JSON 物件，包含兩個欄位：
    1. "diagnosis": 用繁體中文簡潔說明「錯誤原因」與「修復方式」（150 字以內，準備發送到 Discord 通知開發者）。
    2. "fixed_code": 修復完成後、可直接覆蓋存檔的「完整 `monitor.py` 程式碼」（務必保留所有原有功能與正確縮排，不可省略任何段落）。

    【錯誤日誌 Traceback】：
    {error_trace}

    【目前的 monitor.py 原始碼】：
    {source_code}
    """

    print("GitHub Models (GPT-4o) Debug Agent 正在交叉分析錯誤並生成修復程式碼...", flush=True)
    response = client.chat.completions.create(
        model="gpt-4o",
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": "你是一位精準的 Python 除錯專家，只輸出合法的 JSON 格式。"},
            {"role": "user", "content": prompt}
        ],
        temperature=0.1
    )

    result = json.loads(response.choices[0].message.content)
    diagnosis = result["diagnosis"]
    fixed_code = result["fixed_code"]

    # 寫入修復後的程式碼與診斷摘要（供後續 GitHub Action 開立 PR）
    with open("monitor.py", "w", encoding="utf-8") as f:
        f.write(fixed_code)

    with open("pr_body.md", "w", encoding="utf-8") as f:
        f.write(
            f"### 🤖 GitHub Models (GPT-4o) 自動診斷報告\n\n"
            f"{diagnosis}\n\n---\n"
            f"*請檢視 Files changed 確認無誤後，點擊下方 **Merge pull request** 即可套用修復。*"
        )

    # 發送緊急通知到 Discord
    repo_url = f"https://github.com/{os.environ.get('GITHUB_REPOSITORY')}/pulls"
    discord_msg = (
        f"🛠️ **自動除錯警報！監控腳本執行失敗**\n\n"
        f"**🔍 GPT-4o (GitHub Models) 交叉診斷：**\n{diagnosis}\n\n"
        f"👉 **請前往 GitHub 審核並合併修復 PR：**\n{repo_url}"
    )
    requests.post(DISCORD_WEBHOOK, json={"content": discord_msg}, timeout=10)
    print("已完成程式碼修復並發送 Discord 審核通知！", flush=True)

if __name__ == "__main__":
    run_auto_debug()
