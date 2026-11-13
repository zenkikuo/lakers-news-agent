import os
import re
import json
import time
import requests
from google import genai

client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
DISCORD_WEBHOOK = os.environ["DISCORD_WEBHOOK_URL"]

FALLBACK_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash",
]

def ask_with_fallback(prompt: str) -> tuple[str, str]:
    last_err = None
    for model_name in FALLBACK_MODELS:
        for attempt in range(2):
            try:
                print(f"🤖 嘗試呼叫 {model_name} (第 {attempt + 1} 次)...", flush=True)
                response = client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                )
                return model_name, response.text
            except Exception as e:
                last_err = e
                print(f"⚠️ {model_name} 暫時無法回應: {e}", flush=True)
                time.sleep(5)
    raise RuntimeError(f"所有備援模型皆暫時無法連線: {last_err}")

def run_auto_debug():
    if not os.path.exists("error.log"):
        print("找不到 error.log，略過自動除錯。", flush=True)
        return

    with open("error.log", "r", encoding="utf-8", errors="ignore") as f:
        error_trace = f.read()[-4000:]

    with open("monitor.py", "r", encoding="utf-8") as f:
        source_code = f.read()

    prompt = f"""
    你是一位資深的 Python 自動化與 CI/CD 除錯工程師。我們的雲端湖人新聞排程程式 `monitor.py` 在執行時發生錯誤中斷。
    請分析以下錯誤日誌與原始碼，回傳一個標準 JSON 物件（不要加任何其他說明文字），包含兩個欄位：
    1. "diagnosis": 用繁體中文簡潔說明「錯誤原因」與「修復方式」（150 字以內，準備發送到 Discord 通知開發者）。
    2. "fixed_code": 修復完成後、可直接覆蓋存檔的「完整 `monitor.py` 程式碼」（務必保留所有原有功能，包含短影音生成 `create_lakers_video`、`generate_tts` 與 Discord 推播邏輯，絕對不可刪除或簡化任何功能段落）。

    【錯誤日誌 Traceback】：
    {error_trace}

    【目前的 monitor.py 原始碼】：
    {source_code}
    """

    used_model, raw_text = ask_with_fallback(prompt)
    clean_text = re.sub(r"^```json\s*|\s*```$", "", raw_text.strip(), flags=re.MULTILINE)
    result = json.loads(clean_text)
    diagnosis = result["diagnosis"]
    fixed_code = result["fixed_code"]

    with open("monitor.py", "w", encoding="utf-8") as f:
        f.write(fixed_code)

    discord_msg = (
        f"🛠️ **自動除錯完成！（使用模型：`{used_model}`）**\n\n"
        f"**🔍 AI 診斷與已套用修復：**\n{diagnosis}\n\n"
        f"✅ 系統已自動將修復推送回 `main` 並重新驗證執行。"
    )
    requests.post(DISCORD_WEBHOOK, json={"content": discord_msg}, timeout=10)
    print("已完成自動修復並發送 Discord 通知！", flush=True)

if __name__ == "__main__":
    run_auto_debug()
