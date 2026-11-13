import os
import re
import json
import wave
import asyncio
import subprocess
import requests
import xml.etree.ElementTree as ET
from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFont, ImageFilter
import edge_tts
from google import genai

# 1. 初始化環境變數
client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
DISCORD_WEBHOOK = os.environ["DISCORD_WEBHOOK_URL"]
# RSS_URL = "https://sports.yahoo.com/nba/rss/"
RSS_URL = "https://news.google.com/rss/search?q=Los+Angeles+Lakers+when:1d&hl=en-US&gl=US&ceid=US:en"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
}
HISTORY_FILE = "seen_news.json"

def load_seen():
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

def save_seen(seen_list):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(seen_list[-100:], f, ensure_ascii=False, indent=2)

def extract_og_image(article_url: str) -> str:
    """潛入新聞網頁原始碼抓取高清 og:image 照片"""
    try:
        r = requests.get(article_url, headers=HEADERS, timeout=10)
        soup = BeautifulSoup(r.text, "html.parser")
        og_img = soup.find("meta", property="og:image")
        if og_img and og_img.get("content"):
            return og_img["content"]
        twitter_img = soup.find("meta", attrs={"name": "twitter:image"})
        if twitter_img and twitter_img.get("content"):
            return twitter_img["content"]
    except Exception as e:
        print(f"抓取網頁大圖失敗: {e}")
    return None

def fetch_lakers_news():
    res = requests.get(RSS_URL, headers=HEADERS, timeout=15)
    res.raise_for_status()
    root = ET.fromstring(res.content)
    seen_links = load_seen()
    new_articles = []

    items = root.findall("./channel/item")
    print(f"Google News 共抓到 {len(items)} 則最新湖人隊新聞！")

    for item in items:
        title = item.findtext("title") or ""
        link = item.findtext("link") or ""
        desc = item.findtext("description") or ""

        if link and link not in seen_links:
            img_url = extract_og_image(link)
            new_articles.append({"title": title, "link": link, "desc": desc, "img_url": img_url})
            seen_links.append(link)
            # 每次最多抓 3 則就夠做簡報，避免花太多時間抓網頁圖片
            if len(new_articles) >= 3:
                break

    return new_articles, seen_links

def generate_structured_script(articles):
    """請 Gemini 輸出 JSON 格式並安全解析"""
    batch = articles[:3]
    raw_text = "\n\n".join([f"標題: {a['title']}\n摘要: {a['desc']}\n連結: {a['link']}" for a in batch])

    prompt = f"""
    你是一位專業的 NBA 洛杉磯湖人隊隨隊記者。請根據以下新聞回傳一個標準 JSON 物件，不要加任何其他說明文字。
    JSON 必須包含兩個欄位：
    1. "discord_text": 發送到 Discord 的繁體中文快報，每則用「🏀 **中文吸睛標題**」條列重點並附上原文連結（總字數 250 字內）。
    2. "voice_script": 給體育男主播朗讀的 20～25 秒口播稿。必須用自然流暢、充滿朝氣的台灣口語新聞腔調，開頭直接說「各位湖人球迷大家好，帶您掌握最新湖人快報！」。絕對不可包含任何星號、井字號、網址、括號或標題說明，純粹是主播要唸出來的口語文字（約 90～115 個中文字）。

    新聞資料：
    {raw_text}
    """

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
    )
    clean_text = re.sub(r"^```json\s*|\s*```$", "", response.text.strip(), flags=re.MULTILINE)
    data = json.loads(clean_text)
    return data["discord_text"], data["voice_script"]

def format_srt_time(seconds: float) -> str:
    hrs = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds - int(seconds)) * 1000)
    return f"{hrs:02d}:{mins:02d}:{secs:02d},{millis:03d}"

async def create_voiceover_and_srt(text: str, output_mp3: str, output_srt: str):
    """生成台灣男主播語音，並依語音實際秒數自動切分逐句 SRT 字幕"""
    communicate = edge_tts.Communicate(text, "zh-TW-YunJheNeural", rate="+8%", pitch="+2Hz")
    await communicate.save(output_mp3)

    # 用 ffprobe 取得精準音檔秒數
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", output_mp3],
        capture_output=True, text=True, check=True
    )
    total_duration = float(probe.stdout.strip())

    # 依中文標點符號切分為短句，每句最多 16 字避免超出畫面
    raw_sentences = [s.strip() for s in re.split(r"[，。！？；、]+", text) if s.strip()]
    sentences = []
    for s in raw_sentences:
        sentences.extend([s[i:i+16] for i in range(0, len(s), 16)])
