import os
import re
import json
import requests
from google import genai

DISCORD_WEBHOOK = os.environ["DISCORD_WEBHOOK_URL"]
GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

def ask_debug_llm(prompt: str) -> tuple[str, dict]:
    """第一順位嘗試 GitHub Models (GPT-4o)，若端點未開通或無回應則自動無縫備援至 Gemini 2.5 Pro"""
    url = "https://models.github.ai/inference/chat/completions"
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "openai/gpt-4o",
        "messages": [
            {"role": "system", "content": "你是一位精準的 Python 除錯專家，只輸出合法的 JSON 格式。"},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.1
    }

    try:
        res = requests.post(url, headers=headers, json=payload, timeout=30)
        print(f"GitHub Models HTTP 狀態碼: {res.status_code}", flush=True)
        if res.status_code == 200 and res.text.strip():
            data = res.json()
            raw_content = data["choices"][0]["message"]["content"]
            clean_text = re.sub(r"^```json\s*|\s*```$", "", raw_content.strip(), flags=re.MULTILINE)
            return "GitHub Models (GPT-4o)", json.loads(clean_text)
        else:
            print(f"GitHub Models 未回傳有效 JSON（回應內容：{res.text[:200]}），自動切換至備援模型...", flush=True)
    except Exception as e:
        print(f"GitHub Models 連線異常 ({e})，自動切換至備援模型...", flush=True)

    # 第二順位備援：呼叫推理能力最強的 Gemini 2.5 Pro 進行深度除錯
    print("啟動備援引擎 Gemini 2.5 Pro 進行深度診斷...", flush=True)
    client = genai.Client(api_key=GEMINI_API_KEY)
    response = client.models.generate_content(
        model="gemini-2.5-pro",
        contents=prompt,
    )
    clean_text = re.sub(r"^```json\s*|\s*```$", "", response.text.strip(), flags=re.MULTILINE)
    return "Gemini 2.5 Pro (備援除錯引擎)", json.loads(clean_text)

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
    請分析以下錯誤日誌與原始碼，回傳一個標準 JSON 物件（不要加任何其他說明文字），包含兩個欄位：
    1. "diagnosis": 用繁體中文簡潔說明「錯誤原因」與「修復方式」（150 字以內，準備發送到 Discord 通知開發者）。
    2. "fixed_code": 修復完成後、可直接覆蓋存檔的「完整 `monitor.py` 程式碼」（務必保留所有原有功能與正確縮排，不可省略任何段落）。

    【錯誤日誌 Traceback】：
    {error_trace}

    【目前的 monitor.py 原始碼】：
    {source_code}
    """

    engine_name, result = ask_debug_llm(prompt)
    diagnosis = result["diagnosis"]
    fixed_code = result["fixed_code"]

    # 寫入修復後的程式碼與診斷摘要（供後續 GitHub Action 開立 PR）
    with open("monitor.py", "w", encoding="utf-8") as f:
        f.write(fixed_code)

    with open("pr_body.md", "w", encoding="utf-8") as f:
        f.write(
            f"### 🤖 {engine_name} 自動診斷報告\n\n"
            f"{diagnosis}\n\n---\n"
            f"*請檢視 Files changed 確認無誤後，點擊下方 **Merge pull request** 即可套用修復。*"
        )

    # 發送緊急通知到 Discord
    repo_url = f"https://github.com/{os.environ.get('GITHUB_REPOSITORY')}/pulls"
    discord_msg = (
        f"🛠️ **自動除錯警報！監控腳本執行失敗**\n\n"
        f"**🔍 {engine_name} 診斷結果：**\n{diagnosis}\n\n"
        f"👉 **請前往 GitHub 審核並合併修復 PR：**\n{repo_url}"
    )
    requests.post(DISCORD_WEBHOOK, json={"content": discord_msg}, timeout=10)
    print(f"已透過 {engine_name} 完成程式碼修復並發送 Discord 審核通知！", flush=True)

if __name__ == "__main__":
    run_auto_debug()
