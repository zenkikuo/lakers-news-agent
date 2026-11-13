import os
import json
import asyncio
import textwrap
import subprocess
import requests
import xml.etree.ElementTree as ET
from PIL import Image, ImageDraw, ImageFont
import edge_tts
from google import genai

# 1. 初始化環境變數
client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
DISCORD_WEBHOOK = os.environ["DISCORD_WEBHOOK_URL"]
RSS_URL = "https://sports.yahoo.com/nba/rss/"
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

def fetch_lakers_news():
    res = requests.get(RSS_URL, headers=HEADERS, timeout=15)
    res.raise_for_status()
    root = ET.fromstring(res.content)
    seen_links = load_seen()
    new_articles = []

    # 命名空間 (用來抓 Yahoo RSS 裡的新聞圖片)
    ns = {"media": "http://search.yahoo.com/mrss/"}

    for item in root.findall("./channel/item"):
        title = item.findtext("title") or ""
        link = item.findtext("link") or ""
        desc = item.findtext("description") or ""

        # 嘗試抓取新聞縮圖 URL
        media_el = item.find("media:content", ns)
        img_url = media_el.attrib.get("url") if media_el is not None else None

        keywords = ["lakers", "los angeles", "lebron", "anthony davis", "luka", "redick"]
        text_to_check = (title + " " + desc).lower()

        if any(k in text_to_check for k in keywords) and link and link not in seen_links:
            new_articles.append({"title": title, "link": link, "desc": desc, "img_url": img_url})
            seen_links.append(link)

    return new_articles, seen_links

def generate_scripts(articles):
    batch = articles[:3]
    raw_text = "\n\n".join([f"標題: {a['title']}\n摘要: {a['desc']}\n連結: {a['link']}" for a in batch])

    prompt = f"""
    你是一位專業的 NBA 洛杉磯湖人隊隨隊記者。請根據以下新聞產出兩段內容，並嚴格使用「===SPLIT===」分隔：

    第一部分（發送給 Discord 的文字快報）：
    - 用「🏀 **中文吸睛標題**」條列每則新聞重點與原文連結，總字數 250 字內。

    ===SPLIT===

    第二部分（給 AI 主播朗讀的 20～30 秒口播稿）：
    - 口語化、有朝氣的繁體中文新聞播報稿，開頭說「各位湖人球迷大家好，為您帶來最新湖人快報！」。
    - 不要包含任何網址、Markdown 符號（如 * 或 #）或表情符號，純文字約 90～120 個中文字即可。

    新聞資料：
    {raw_text}
    """

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
    )
    parts = response.text.split("===SPLIT===")
    text_report = parts[0].strip()
    voice_script = parts[1].strip() if len(parts) > 1 else text_report
    return text_report, voice_script

async def create_voiceover(text: str, output_mp3: str):
    # 使用微軟台灣國語女聲 (曉臻)
    communicate = edge_tts.Communicate(text, "zh-TW-HsiaoChenNeural", rate="+5%")
    await communicate.save(output_mp3)

def create_video_card(img_url: str, voice_script: str, output_img: str):
    # 建立 1080x1920 直式短影音畫布 (湖人深紫背景)
    W, H = 1080, 1920
    canvas = Image.new("RGB", (W, H), color=(35, 17, 66))
    draw = ImageDraw.Draw(canvas)

    # 載入中文字型
    font_path = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"
    if not os.path.exists(font_path):
        font_path = "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc"

    title_font = ImageFont.truetype(font_path, 58)
    body_font = ImageFont.truetype(font_path, 42)

    # 1. 頂部湖人金標題橫幅
    draw.rectangle([0, 0, W, 160], fill=(253, 185, 39))
    draw.text((60, 48), "🏀 洛杉磯湖人 24H 速報", fill=(35, 17, 66), font=title_font)

    # 2. 放置新聞圖片 (若無圖片則繪製提示框)
    photo_y = 200
    photo_h = 620
    has_photo = False
    if img_url:
        try:
            r = requests.get(img_url, headers=HEADERS, timeout=10)
            with open("temp_news.jpg", "wb") as f:
                f.write(r.content)
            news_img = Image.open("temp_news.jpg").convert("RGB")
            news_img = news_img.resize((960, photo_h))
            canvas.paste(news_img, (60, photo_y))
            has_photo = True
        except Exception as e:
            print(f"圖片下載略過: {e}")

    if not has_photo:
        draw.rectangle([60, photo_y, 1020, photo_y + photo_h], fill=(55, 30, 95), outline=(253, 185, 39), width=4)
        draw.text((280, photo_y + 280), "LA LAKERS NEWS", fill=(253, 185, 39), font=title_font)

    # 3. 下半部放置大字報口播字幕
    text_y = 880
    wrapped_lines = textwrap.wrap(voice_script, width=20)
    for line in wrapped_lines[:14]:
        draw.text((70, text_y), line, fill=(255, 255, 255), font=body_font)
        text_y += 68

    canvas.save(output_img)

def render_mp4(image_path: str, audio_path: str, output_mp4: str):
    # 使用 FFmpeg 將靜態圖卡與語音合併為標準 MP4 (加入微幅縮放動態效果)
    cmd = [
        "ffmpeg", "-y",
        "-loop", "1", "-i", image_path,
        "-i", audio_path,
        "-c:v", "libx264",
        "-tune", "stillimage",
        "-c:a", "aac", "-b:a", "192k",
        "-pix_fmt", "yuv420p",
        "-shortest",
        output_mp4
    ]
    subprocess.run(cmd, check=True)

def send_to_discord(text_report: str, video_path: str):
    payload = {"content": f"🎬 **湖人隊影音速報已生成！請審核確認：**\n\n{text_report}"}
    with open(video_path, "rb") as video_file:
        files = {"file": ("lakers_briefing.mp4", video_file, "video/mp4")}
        r = requests.post(DISCORD_WEBHOOK, data={"payload_json": json.dumps(payload)}, files=files, timeout=30)
        r.raise_for_status()
    print("成功將文字快報與 MP4 短影片發送至 Discord！")

if __name__ == "__main__":
    articles, updated_seen = fetch_lakers_news()
    if articles:
        text_report, voice_script = generate_scripts(articles)
        print("生成語音中...")
        asyncio.run(create_voiceover(voice_script, "voice.mp3"))
        print("繪製影音圖卡中...")
        create_video_card(articles[0].get("img_url"), voice_script, "card.png")
        print("合成 MP4 短影片中...")
        render_mp4("card.png", "voice.mp3", "lakers_briefing.mp4")
        send_to_discord(text_report, "lakers_briefing.mp4")
        save_seen(updated_seen)
    else:
        print("巡檢完成：目前沒有未讀的湖人隊新聞。")
