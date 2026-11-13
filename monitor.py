import os
import re
import json
import asyncio
import subprocess
import requests
import xml.etree.ElementTree as ET
from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFont, ImageFilter
import edge_tts
from google import genai

# 1. 初始化環境變數與設定
client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
DISCORD_WEBHOOK = os.environ["DISCORD_WEBHOOK_URL"]

# 使用 Google News 洛杉磯湖人專屬即時新聞 RSS（100% 鎖定湖人隊，不會被其他球隊洗版）
RSS_URL = "https://news.google.com/rss/search?q=Los+Angeles+Lakers&hl=en-US&gl=US&ceid=US:en"
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
    """追蹤轉址並潛入新聞網頁原始碼抓取高清 og:image 照片"""
    try:
        r = requests.get(article_url, headers=HEADERS, timeout=10, allow_redirects=True)
        soup = BeautifulSoup(r.text, "html.parser")
        og_img = soup.find("meta", property="og:image")
        if og_img and og_img.get("content") and og_img["content"].startswith("http"):
            return og_img["content"]
        twitter_img = soup.find("meta", attrs={"name": "twitter:image"})
        if twitter_img and twitter_img.get("content") and twitter_img["content"].startswith("http"):
            return twitter_img["content"]
    except Exception as e:
        print(f"抓取網頁大圖略過: {e}", flush=True)
    return None

def fetch_lakers_news():
    res = requests.get(RSS_URL, headers=HEADERS, timeout=15)
    res.raise_for_status()
    root = ET.fromstring(res.content)
    seen_links = load_seen()
    new_articles = []

    items = root.findall("./channel/item")
    print(f"Google News 共抓到 {len(items)} 則湖人隊新聞！", flush=True)

    for item in items:
        title = item.findtext("title") or ""
        link = item.findtext("link") or ""
        desc = item.findtext("description") or ""

        if link and link not in seen_links:
            img_url = extract_og_image(link)
            new_articles.append({"title": title, "link": link, "desc": desc, "img_url": img_url})
            seen_links.append(link)
            if len(new_articles) >= 3:
                break

    return new_articles, seen_links

def generate_structured_script(articles):
    """請 Gemini 輸出 JSON 格式並安全解析，杜絕多餘標題被唸出來"""
    raw_text = "\n\n".join([f"標題: {a['title']}\n連結: {a['link']}" for a in articles])

    prompt = f"""
    你是一位專業的 NBA 洛杉磯湖人隊隨隊記者。請根據以下新聞標題回傳一個標準 JSON 物件，不要加任何其他說明文字。
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

    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", output_mp3],
        capture_output=True, text=True, check=True
    )
    total_duration = float(probe.stdout.strip())

    raw_sentences = [s.strip() for s in re.split(r"[，。！？；、]+", text) if s.strip()]
    sentences = []
    for s in raw_sentences:
        sentences.extend([s[i:i+16] for i in range(0, len(s), 16)])

    total_chars = sum(len(s) for s in sentences) or 1
    current_time = 0.0
    srt_lines = []

    for idx, s in enumerate(sentences, start=1):
        seg_duration = (len(s) / total_chars) * total_duration
        start_str = format_srt_time(current_time)
        end_str = format_srt_time(min(current_time + seg_duration, total_duration))
        srt_lines.append(f"{idx}\n{start_str} --> {end_str}\n{s}\n")
        current_time += seg_duration

    with open(output_srt, "w", encoding="utf-8") as f:
        f.write("\n".join(srt_lines))

def build_dynamic_video(img_url: str, srt_path: str, output_mp4: str):
    """合成 1080x1920 動態鏡頭推進 + 電視新聞框 + 逐句字幕的短影音"""
    W, H = 1080, 1920
    bg_path = "base_frame.png"

    font_path = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"
    if not os.path.exists(font_path):
        font_path = "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc"
    title_font = ImageFont.truetype(font_path, 52)
    sub_banner_font = ImageFont.truetype(font_path, 42)

    has_photo = False
    if img_url:
        try:
            r = requests.get(img_url, headers=HEADERS, timeout=10)
            with open("raw_photo.jpg", "wb") as f:
                f.write(r.content)
            raw_img = Image.open("raw_photo.jpg").convert("RGB")
            if raw_img.width >= 300:
                has_photo = True
        except Exception as e:
            print(f"下載新聞大圖失敗: {e}", flush=True)

    if has_photo:
        blurred_bg = raw_img.resize((W, H)).filter(ImageFilter.GaussianBlur(radius=35))
        overlay = Image.new("RGB", (W, H), (20, 10, 40))
        canvas = Image.blend(blurred_bg, overlay, alpha=0.45)

        aspect = raw_img.height / raw_img.width
        new_w = 1020
        new_h = min(int(new_w * aspect), 820)
        fg_img = raw_img.resize((new_w, new_h), Image.Resampling.LANCZOS)
        canvas.paste(fg_img, ((W - new_w) // 2, 340))
    else:
        canvas = Image.new("RGB", (W, H), color=(35, 17, 66))
        draw_fallback = ImageDraw.Draw(canvas)
        draw_fallback.rectangle([60, 380, W - 60, 1050], fill=(55, 30, 95), outline=(253, 185, 39), width=5)
        draw_fallback.text((220, 680), "LOS ANGELES LAKERS", fill=(253, 185, 39), font=title_font)

    draw = ImageDraw.Draw(canvas)
    draw.rectangle([0, 90, W, 210], fill=(253, 185, 39))
    draw.text((65, 120), "LAKERS 24H | 洛杉磯湖人即時戰報", fill=(35, 17, 66), font=title_font)
    draw.rectangle([40, 1320, W - 40, 1580], fill=(15, 8, 30))
    draw.text((70, 1265), "LIVE BRIEFING", fill=(253, 185, 39), font=sub_banner_font)
    canvas.save(bg_path)

    subtitle_style = (
        "FontName=Noto Sans CJK TC,FontSize=20,PrimaryColour=&H00FFFFFF,"
        "OutlineColour=&H00000000,BorderStyle=1,Outline=2,Alignment=2,MarginV=160"
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
    print("準備發送文字快報至 Discord...", flush=True)
    r1 = requests.post(
        DISCORD_WEBHOOK,
        json={"content": f"🎬 **湖人隊動態影音速報已生成！**\n\n{text_report}"},
        timeout=(5, 15)
    )
    r1.raise_for_status()

    print("準備上傳 MP4 短影音至 Discord...", flush=True)
    with open(video_path, "rb") as video_file:
        r2 = requests.post(
            DISCORD_WEBHOOK,
            files={"file": ("lakers_dynamic_news.mp4", video_file, "video/mp4")},
            timeout=(5, 20)
        )
        r2.raise_for_status()
    print("成功發送動態短影音至 Discord！", flush=True)

if __name__ == "__main__":
    articles, updated_seen = fetch_lakers_news()
    if articles:
        articles_with_img = [a for a in articles if a.get("img_url")]
        cover_img = articles_with_img[0]["img_url"] if articles_with_img else None

        discord_text, voice_script = generate_structured_script(articles)
        print(f"口播稿確認：{voice_script}", flush=True)
        asyncio.run(create_voiceover_and_srt(voice_script, "voice.mp3", "subs.srt"))
        build_dynamic_video(cover_img, "subs.srt", "lakers_dynamic_news.mp4")
        send_to_discord(discord_text, "lakers_dynamic_news.mp4")
        save_seen(updated_seen)
    else:
        print("巡檢完成：目前沒有未讀的湖人隊新聞。", flush=True)
