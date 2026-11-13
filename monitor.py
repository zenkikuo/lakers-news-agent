import os
import json
import requests
import xml.etree.ElementTree as ET
from google import genai

# 1. 初始化 Gemini 與讀取環境變數
client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
DISCORD_WEBHOOK = os.environ["DISCORD_WEBHOOK_URL"]
RSS_URL = "https://www.espn.com/espn/rss/nba/news"  # ESPN NBA 新聞源
HISTORY_FILE = "seen_news.json"

def load_seen():
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

def save_seen(seen_list):
    # 只保留最近 100 筆紀錄避免檔案無限膨脹
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(seen_list[-100:], f, ensure_ascii=False, indent=2)

def fetch_lakers_news():
    res = requests.get(RSS_URL, timeout=15)
    root = ET.fromstring(res.content)
    seen_links = load_seen()
    new_articles = []

    for item in root.findall("./channel/item"):
        title = item.find("title").text
        link = item.find("link").text
        desc = item.find("description").text if item.find("description") is not None else ""

        # 篩選與湖人隊相關且未通知過的新聞
        keywords = ["lakers", "los angeles", "lebron", "anthony davis", "luka", "redick"]
        text_to_check = (title + " " + desc).lower()

        if any(k in text_to_check for k in keywords) and link not in seen_links:
            new_articles.append({"title": title, "link": link, "desc": desc})
            seen_links.append(link)

    return new_articles, seen_links

def summarize_and_notify(articles):
    if not articles:
        print("目前沒有新的湖人隊新聞。")
        return

    # 每次最多處理最新 3 則，避免洗版
    batch = articles[:3]
    raw_text = "\n\n".join([f"標題: {a['title']}\n摘要: {a['desc']}\n連結: {a['link']}" for a in batch])

    prompt = f"""
    你是一位專業的 NBA 洛杉磯湖人隊隨隊記者。請將以下最新英文新聞翻譯並整理成繁體中文快報，準備發送到 Discord 手機通知：
    規則：
    1. 每則新聞用「🏀 **中文吸睛標題**」開頭。
    2. 用 2 句話精準提煉核心重點（例如：傷兵狀態、交易動向、比賽關鍵數據）。
    3. 附上原文連結方便查閱。
    4. 語氣俐落有精神，總字數控制在 350 字以內。

    新聞資料：
    {raw_text}
    """

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
    )

    # 發送到 Discord
    payload = {"content": f"🚨 **湖人隊最新動態快報** 🚨\n\n{response.text}"}
    requests.post(DISCORD_WEBHOOK, json=payload, timeout=10)
    print(f"成功推送 {len(batch)} 則湖人隊新聞到 Discord！")

if __name__ == "__main__":
    articles, updated_seen = fetch_lakers_news()
    if articles:
        summarize_and_notify(articles)
        save_seen(updated_seen)
