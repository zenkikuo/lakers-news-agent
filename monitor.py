import os
import re
import json
import time
import asyncio
import hashlib
import subprocess
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta

import requests
import edge_tts
from PIL import Image, ImageDraw, ImageFont
from google import genai

GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
DISCORD_WEBHOOK = os.environ["DISCORD_WEBHOOK_URL"]
client = genai.Client(api_key=GEMINI_API_KEY)

SEEN_FILE = "seen_news.json"
TAIPEI_TZ = timezone(timedelta(hours=8))

FALLBACK_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash",
]

LAKERS_RSS = "https://news.google.com/rss/search?q=Los+Angeles+Lakers+(NBA+OR+LeBron+OR+Davis+OR+Doncic+OR+trade+OR+injury)&hl=en-US&gl=US&ceid=US:en"

def load_seen() -> list:
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

def save_seen(seen_list: list):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(seen_list[-300:], f, ensure_ascii=False, indent=2)

def fetch_lakers_news(seen_ids: set) -> list:
    candidates = []
    headers = {"User-Agent": "Mozilla/5.0 (compatible; LakersMonitor/1.0)"}
    res = requests.get(LAKERS_RSS, headers=headers, timeout=15)
    res.raise_for_status()

    root = ET.fromstring(res.content)
    for item in root.findall(".//item")[:15]:
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub_date = (item.findtext("pubDate") or "").strip()
        if not title or not link:
            continue

        uid = hashlib.md5(title.lower().encode("utf-8")).hexdigest()[:12]
        if uid in seen_ids:
            continue

        candidates.append({
            "uid": uid,
            "title": title,
            "link": link,
            "pub_date": pub_date
        })
    return candidates[:8]

def summarize_lakers_news(candidates: list) -> dict:
    news_text = "\n".join([f"- {c['title']} ({c['pub_date']})" for c in candidates])

    prompt = f"""
    你是一位熱血專業的 NBA 洛杉磯湖人隊隨隊記者。請將以下最新湖人隊英文新聞翻譯並整理成繁體中文戰報。
    請嚴格輸出合法 JSON 格式（不要包含 markdown 標記或額外文字）：
    {{
      "has_update": true,
      "headline": "18字以內的繁體中文震撼主標題（適合當短影音封面標題）",
      "discord_brief": "條列式繁體中文重點摘要（200字以內）",
      "voice_script": "45秒內可唸完的繁體中文湖人快報口播稿（語氣生動有活力，約110~150字，不可為空字串）"
    }}

    【最新湖人隊新聞】：
    {news_text}
    """

    last_err = None
    for model_name in FALLBACK_MODELS:
        for attempt in range(2):
            try:
                response = client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                )
                clean_text = re.sub(r"^```json\s*|\s*```$", "", response.text.strip(), flags=re.MULTILINE)
                return json.loads(clean_text)
            except Exception as e:
                last_err = e
                print(f"⚠️ {model_name} 暫時失敗 (第 {attempt + 1} 次): {e}", flush=True)
                time.sleep(4)
    raise RuntimeError(f"所有模型皆無法連線: {last_err}")

async def generate_tts(text: str, output_mp3: str):
    clean_speech = re.sub(r"[^\w\u4e00-\u9fff，。！？、；：（）%0-9a-zA-Z\s]", "", text).strip()
    if not clean_speech:
        clean_speech = "洛杉磯湖人隊最新戰報與球員動態更新，請鎖定最新賽況消息。"
    communicate = edge_tts.Communicate(clean_speech, voice="zh-TW-HsiaoChenNeural", rate="+6%")
    await communicate.save(output_mp3)

def create_lakers_video(summary: dict, audio_path: str, output_mp4: str):
    img_path = "lakers_card.png"
    W, H = 1080, 1920
    img = Image.new("RGB", (W, H), color=(35, 18, 66))  # 湖人紫背景
    draw = ImageDraw.Draw(img)

    font_path = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"
    if not os.path.exists(font_path):
        font_path = "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc"

    font_tag = ImageFont.truetype(font_path, 44)
    font_title = ImageFont.truetype(font_path, 68)
    font_body = ImageFont.truetype(font_path, 38)

    now_str = datetime.now(TAIPEI_TZ).strftime("%Y/%m/%d %H:%M 台北時間")

    # 湖人金頂部橫幅
    draw.rectangle([70, 120, 1010, 215], fill=(253, 185, 39))
    draw.text((100, 142), "🏀 LOS ANGELES LAKERS｜紫金即時快報", fill=(35, 18, 66), font=font_tag)
    draw.text((70, 245), now_str, fill=(216, 180, 254), font=font_body)

    headline = summary.get("headline", "湖人隊最新動態快訊")
    y_cursor = 350
    for i in range(0, len(headline), 13):
        draw.text((70, y_cursor), headline[i:i+13], fill=(255, 255, 255), font=font_title)
        y_cursor += 90

    y_cursor += 60
    script = summary.get("voice_script", "")
    for i in range(0, len(script), 22):
        if y_cursor > 1720:
            break
        draw.text((70, y_cursor), script[i:i+22], fill=(243, 232, 255), font=font_body)
        y_cursor += 62

    img.save(img_path)

    cmd = [
        "ffmpeg", "-y", "-loop", "1", "-i", img_path, "-i", audio_path,
        "-c:v", "libx264", "-tune", "stillimage", "-c:a", "aac", "-b:a", "192k",
        "-pix_fmt", "yuv420p", "-shortest", output_mp4
    ]
    subprocess.run(cmd, check=True)

def main():
    print("🏀 開始巡檢洛杉磯湖人隊最新新聞...", flush=True)
    seen_list = load_seen()
    seen_ids = set(seen_list)

    candidates = fetch_lakers_news(seen_ids)
    if not candidates:
        print("☕ 目前無未讀的湖人隊新消息。", flush=True)
        return

    summary = summarize_lakers_news(candidates)

    for c in candidates:
        seen_list.append(c["uid"])
    save_seen(seen_list)

    sources_links = "\n".join([f"• [{c['title']}]({c['link']})" for c in candidates[:4]])
    msg = (
        f"🏀 **【紫金即時快報】{summary['headline']}**\n\n"
        f"{summary['discord_brief']}\n\n"
        f"**🔗 原始新聞來源：**\n{sources_links}"
    )
    requests.post(DISCORD_WEBHOOK, json={"content": msg[:1950]}, timeout=15).raise_for_status()

    try:
        audio_file = "lakers_voice.mp3"
        video_file = "lakers_brief.mp4"
        asyncio.run(generate_tts(summary["voice_script"], audio_file))
        create_lakers_video(summary, audio_file, video_file)
        with open(video_file, "rb") as vf:
            requests.post(
                DISCORD_WEBHOOK,
                files={"file": ("lakers_brief.mp4", vf, "video/mp4")},
                timeout=30
            ).raise_for_status()
        print("✅ 湖人快報與短影音已成功推送至 Discord！", flush=True)
    except Exception as tts_err:
        print(f"⚠️ 語音短影音生成受限（已自動降級為純文字快報送達 Discord）: {tts_err}", flush=True)

if __name__ == "__main__":
    main()
