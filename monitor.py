import os
import re
import json
import asyncio
import textwrap
import subprocess
import requests
import xml.etree.ElementTree as ET
from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFont, ImageFilter
import edge_tts
from google import genai
from google.genai import types

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

    for item in root.findall("./channel/item"):
        title = item.findtext("title") or ""
        link = item.findtext("link") or ""
        desc = item.findtext("description") or ""

        keywords = ["lakers", "los angeles", "lebron", "anthony davis", "luka", "redick"]
        text_to_check = (title + " " + desc).lower()

        if any(k in text_to_check for k in keywords) and link and link not in seen_links:
            img_url = extract_og_image(link)
            new_articles.append({"title": title, "link": link, "desc": desc, "img_url": img_url})
            seen_links.append(link)

    return new_articles, seen_links

def generate_structured_script(articles):
    """強制 Gemini 輸出純 JSON，徹底杜絕多餘標題被唸出來"""
    batch = articles[:3]
    raw_text = "\n\n".join([f"標題: {a['title']}\n摘要: {a['desc']}\n連結: {a['link']}" for a in batch])

    prompt = f"""
    你是一位專業的 NBA 洛杉磯湖人隊隨隊記者。請根據以下新聞回傳一個 JSON 物件，包含兩個欄位：
    1. "discord_text": 發送到 Discord 的繁體中文快報，每則用「🏀 **中文吸睛標題**」條列重點並附上原文連結（總字數 250 字內）。
    2. "voice_script": 給體育男主播朗讀的 20～25 秒口播稿。必須用自然流暢、充滿朝氣的台灣口語新聞腔調，開頭直接說「各位湖人球迷大家好，帶您掌握最新湖人快報！」。絕對不可包含任何星號、井字號、網址、括號或標題說明，純粹是主播要唸出來的口語文字（約 90～115 個中文字）。

    新聞資料：
    {raw_text}
    """

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
        config=types.GenerateContentConfig(response_mime_type="application/json"),
    )
    data = json.loads(response.text)
    return data["discord_text"], data["voice_script"]

async def create_voiceover_and_srt(text: str, output_mp3: str, output_srt: str):
    """使用台灣男主播 YunJhe 生成語音，並同步產生逐句字幕檔 (.srt)"""
    communicate = edge_tts.Communicate(text, "zh-TW-YunJheNeural", rate="+8%", pitch="+2Hz")
    submaker = edge_tts.SubMaker()
    with open(output_mp3, "wb") as audio_file:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_file.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                submaker.feed(chunk)

    with open(output_srt, "w", encoding="utf-8") as srt_file:
        srt_file.write(submaker.get_srt())

def build_dynamic_video(img_url: str, srt_path: str, output_mp4: str):
    """合成 1080x1920 動態鏡頭推進 + 電視新聞框 + 逐句字幕的短影音"""
    W, H = 1080, 1920
    bg_path = "base_frame.png"

    # 1. 製作高質感背景底圖
    font_path = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"
    if not os.path.exists(font_path):
        font_path = "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc"
    title_font = ImageFont.truetype(font_path, 56)

    has_photo = False
    if img_url:
        try:
            r = requests.get(img_url, headers=HEADERS, timeout=10)
            with open("raw_photo.jpg", "wb") as f:
                f.write(r.content)
            raw_img = Image.open("raw_photo.jpg").convert("RGB")
            has_photo = True
        except Exception as e:
            print(f"下載新聞大圖失敗: {e}")

    if has_photo:
        # 滿版模糊延伸背景 + 中央高清主圖 (模擬電視新聞直式排版)
        blurred_bg = raw_img.resize((W, H)).filter(ImageFilter.GaussianBlur(radius=35))
        # 壓暗背景突顯前景
        overlay = Image.new("RGB", (W, H), (20, 10, 40))
        canvas = Image.blend(blurred_bg, overlay, alpha=0.45)

        # 等比例縮放高清新聞照片放在畫面正中央
        aspect = raw_img.height / raw_img.width
        new_w = 1020
        new_h = min(int(new_w * aspect), 820)
        fg_img = raw_img.resize((new_w, new_h), Image.Resampling.LANCZOS)
        canvas.paste(fg_img, ((W - new_w) // 2, 340))
    else:
        canvas = Image.new("RGB", (W, H), color=(35, 17, 66))

    draw = ImageDraw.Draw(canvas)
    # 頂部湖人紫金新聞橫幅 (避免使用字型不支援的彩色 Emoji)
    draw.rectangle([0, 90, W, 210], fill=(253, 185, 39))
    draw.text((65, 118), "LAKERS 24H | 洛杉磯湖人即時戰報", fill=(35, 17, 66), font=title_font)
    # 底部字幕深色遮罩條
    draw.rectangle([40, 1260, W - 40, 1620], fill=(15, 8, 30))
    canvas.save(bg_path)

    # 2. 使用 FFmpeg 加上「緩慢鏡頭推進縮放動畫 (zoompan)」+「燒錄動態逐句字幕」
    subtitle_style = (
        "FontName=Noto Sans CJK TC,FontSize=22,PrimaryColour=&H00FFFFFF,"
        "OutlineColour=&H00000000,BorderStyle=1,Outline=2,Alignment=2,MarginV=140"
    )
    vf_filter = (
        f"scale=2160:3840,"
        f"zoompan=z='min(zoom+0.0008,1.15)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={W}x{H}:fps=30,"
        f"subtitles={srt_path}:force_style='{subtitle_style}'"
    )

    cmd = [
        "ffmpeg", "-y",
        "-loop", "1", "-i", bg_path,
        "-i", "voice.mp3",
        "-vf", vf_filter,
        "-c:v", "libx264", "-preset", "fast",
        "-c:a", "aac", "-b:a", "192k",
        "-pix_fmt", "yuv420p",
        "-shortest",
        output_mp4
    ]
    subprocess.run(cmd, check=True)

def send_to_discord(text_report: str, video_path: str):
    payload = {"content": f"🎬 **湖人隊動態影音速報已生成！**\n\n{text_report}"}
    with open(video_path, "rb") as video_file:
        files = {"file": ("lakers_dynamic_news.mp4", video_file, "video/mp4")}
        r = requests.post(DISCORD_WEBHOOK, data={"payload_json": json.dumps(payload)}, files=files, timeout=30)
        r.raise_for_status()
    print("成功發送動態短影音至 Discord！")

if __name__ == "__main__":
    articles, updated_seen = fetch_lakers_news()
    if articles:
        # 優先挑選有抓到高清圖片的新聞當作影片封面
        articles_with_img = [a for a in articles if a.get("img_url")]
        cover_img = articles_with_img[0]["img_url"] if articles_with_img else None

        discord_text, voice_script = generate_structured_script(articles)
        print(f"口播稿確認：{voice_script}")
        asyncio.run(create_voiceover_and_srt(voice_script, "voice.mp3", "subs.srt"))
        build_dynamic_video(cover_img, "subs.srt", "lakers_dynamic_news.mp4")
        send_to_discord(discord_text, "lakers_dynamic_news.mp4")
        save_seen(updated_seen)
    else:
        print("巡檢完成：目前沒有未讀的湖人隊新聞。")
