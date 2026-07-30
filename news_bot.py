# ============================================================
#  Stock News Bot - GitHub Actions Edition
#  ดึงข่าวหุ้นแบบครอบคลุม (ไม่ผูกกับ Agent ตัวใดตัวหนึ่ง) จาก Investing.com
#  ผ่าน Gemini Google Search grounding แล้วส่งเป็น "บัตรข่าวสรุป" เข้า Telegram
#  พร้อมไฟล์รายงานฉบับเต็มแนบไปด้วย และสำรองไว้ใน Google Drive
# ============================================================

import os
from datetime import datetime, timezone

from google import genai
from google.genai import types
import requests as http_requests

from tee_agent import get_drive_service, find_or_create_folder, write_file_text, DRIVE_FOLDER_NAME


# ------------------------------------------------------------
# Prompt: v3 (general-purpose, sector-organized, no-guessing)
# + Quick Digest Card ต่อท้ายด้วย delimiter ให้ดึงออกมาทำการ์ดสั้นได้
# ------------------------------------------------------------
NEWS_PROMPT_TEMPLATE = """
Search Investing.com under News > Stock Markets (https://www.investing.com/news/stock-market-news)
for ALL substantive stock-specific news dated yesterday (relative to {today}). Treat Investing.com
as the PRIMARY source. Use Reuters, CNBC, Yahoo Finance, Zacks, TheStreet, and StocksToTrade only
as SECONDARY sources to fill in a number Investing.com doesn't provide — never to replace what
Investing.com itself reports.

This digest is general-purpose. It is not written for any single AI, agent, or trading strategy —
write it so any AI system could read this file with zero other context and have everything it
needs to run its own independent analysis.

SEARCH BUDGET — Be economical with search calls. Do not run a separate search for every single
candidate stock. Start with 1-2 broad searches against Investing.com's Stock Markets news page to
identify the day's biggest stories, then run only as many additional targeted searches as needed
to fill in missing numbers (aim for roughly 5-8 total searches for this whole task, not one per
stock). Prioritize breadth of real information over exhaustiveness.

Requirements:

0. QUICK DIGEST CARD — Before anything else, output a short section wrapped EXACTLY like this
   (the marker lines must appear exactly as shown, each on its own line, nothing else on those
   lines):

===QUICK_DIGEST_START===
📰 Stock News Digest — [actual calendar date of "yesterday"]

🔹 [TICKER] $[price] ([+/-X%]): [one punchy sentence, under 140 characters, capturing the single
   most important fact from that stock's news]
   (repeat one line per stock covered below, same order as the full report)

📊 Market: Dow [+/-X%], S&P 500 [+/-X%], Nasdaq [+/-X%] — [one-sentence dominant macro theme]
===QUICK_DIGEST_END===

1. COVERAGE — Cover the 10-12 most significant companies with real, substantive news from that
   day's Investing.com Stock Markets coverage — prioritize the biggest/most market-moving stories
   rather than trying to be exhaustive. Report however many genuine stories you found within your
   search budget; never pad the list with filler just to hit a number, and never fabricate a story
   to fill a gap.

2. ORGANIZATION — Group companies under sector headings: Technology, Financials, Healthcare,
   Energy, Consumer, Industrials, Communications, Other. (Skip any heading with zero stories.)

3. For each stock, use this exact structure:

## [Number]. [Company Name] ([Ticker])
News summary: [Detailed summary in full sentences, including specific numbers — EPS, revenue,
% price change, price targets, deal size, etc. Write it so someone who never read the original
article still understands exactly what happened and why it matters.]
Key data points: [Compact machine-readable line, e.g. "EPS: $X actual vs $Y est. | Revenue: $X
actual vs $Y est. | Price target: $X (from $Y, Firm Name) | Price change: +/-X%" — include only
fields that actually apply; omit fields with no reported data rather than guessing.]
Date received: [the actual calendar date of "yesterday"]
Current price: [$XXX.XX (+/- change, % change)]
Potential impact: [Describe what could follow FROM THIS SPECIFIC REPORTED NEWS ONLY — an
already-scheduled event mentioned in the article (earnings date, FDA decision, shareholder vote,
covenant threshold), or a consequence a named analyst/company explicitly stated. Never invent a
price target or directional prediction the sources didn't state. If none was reported, write
exactly: "No forward-looking impact was reported in the sourced coverage."]

4. After all sectors, add a "Market Context" section covering the Dow, S&P 500, and Nasdaq with
   actual point/percentage moves, plus the dominant macro theme(s) of the day — sourced the same
   way, no invented numbers.

5. List every source used at the end, one line per source, naming the specific article referenced.

Absolute rule — no guessing, ever:
- Every number, date, and claim must trace back to something actually reported in a real source
  found during this search. This file may be consumed by other AI systems as ground truth, so an
  unverified guess here becomes someone else's false fact downstream.
- If a number can't be confirmed, write "not confirmed" instead of estimating it.
- "Potential impact" must never contain a speculative price call unless a named analyst or the
  company itself stated that exact figure in a cited source.
- If yesterday was a weekend, holiday, or non-trading day, do not invent results. Clearly label
  the file as a "weekend/holiday update," use the most recent real trading day's confirmed data,
  and list confirmed upcoming events instead of fabricated outcomes.

Write the entire response in English, formatted in Markdown.
"""


# ------------------------------------------------------------
# Telegram helpers
# ------------------------------------------------------------
def send_telegram_message(text):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("⚠️ ไม่ได้ตั้งค่า TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID ข้ามการส่งข้อความ")
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    # Telegram จำกัด 4096 ตัวอักษรต่อข้อความ ตัดเป็นชิ้นถ้ายาวเกิน
    chunks = [text[i:i + 4000] for i in range(0, len(text), 4000)] or [text]
    for chunk in chunks:
        try:
            resp = http_requests.post(url, data={"chat_id": chat_id, "text": chunk})
            if resp.status_code == 200:
                print("📨 ส่งการ์ดสรุปข่าวเข้า Telegram สำเร็จ")
            else:
                print(f"⚠️ ส่งข้อความ Telegram ไม่สำเร็จ: {resp.text}")
        except Exception as e:
            print(f"⚠️ ส่งข้อความ Telegram ไม่สำเร็จ: {e}")


def send_telegram_document(file_bytes, filename, caption=""):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("⚠️ ไม่ได้ตั้งค่า TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID ข้ามการส่งไฟล์")
        return
    url = f"https://api.telegram.org/bot{token}/sendDocument"
    files = {"document": (filename, file_bytes)}
    data = {"chat_id": chat_id, "caption": caption[:1024]}
    try:
        resp = http_requests.post(url, data=data, files=files)
        if resp.status_code == 200:
            print("📨 ส่งไฟล์รายงานฉบับเต็มเข้า Telegram สำเร็จ")
        else:
            print(f"⚠️ ส่งไฟล์ Telegram ไม่สำเร็จ: {resp.text}")
    except Exception as e:
        print(f"⚠️ ส่งไฟล์ Telegram ไม่สำเร็จ: {e}")


def extract_quick_digest(full_text):
    start_marker = "===QUICK_DIGEST_START==="
    end_marker = "===QUICK_DIGEST_END==="
    if start_marker in full_text and end_marker in full_text:
        start = full_text.index(start_marker) + len(start_marker)
        end = full_text.index(end_marker)
        return full_text[start:end].strip()
    return None


# ------------------------------------------------------------
# Main
# ------------------------------------------------------------
def run_news_bot():
    # ใช้ API Key แยกต่างหาก (เช่นของ Nick) เพื่อไม่ให้แย่งโควต้ากับ tee_agent.py
    # ถ้าไม่ได้ตั้ง NEWS_BOT_API_KEY ไว้ จะ fallback ไปใช้ GOOGLE_API_KEY ตัวเดิมแทน
    api_key = os.environ.get("NEWS_BOT_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("ไม่พบ NEWS_BOT_API_KEY หรือ GOOGLE_API_KEY ใน environment variables")

    client = genai.Client(api_key=api_key)
    today_str = datetime.now(timezone.utc).strftime("%B %d, %Y")
    prompt = NEWS_PROMPT_TEMPLATE.format(today=today_str)

    print("🔎 กำลังค้นข่าวหุ้นทั้งหมดของเมื่อวานผ่าน Google Search grounding...")
    response = client.models.generate_content(
        model="gemini-flash-latest",
        contents=prompt,
        config=types.GenerateContentConfig(
            tools=[types.Tool(google_search=types.GoogleSearch())]
        )
    )
    full_report = response.text
    if not full_report:
        raise RuntimeError("Gemini ไม่ได้ตอบข้อความกลับมา")

    quick_digest = extract_quick_digest(full_report)
    if not quick_digest:
        print("⚠️ ไม่พบ Quick Digest marker ในคำตอบ จะส่งข้อความสรุปสั้นแทน")
        quick_digest = "📰 สรุปข่าวหุ้นวันนี้พร้อมแล้ว — ดูรายละเอียดทั้งหมดในไฟล์แนบด้านล่าง"

    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    filename = f"news_{date_str}.md"

    # 1) ส่งการ์ดสรุปสั้นเป็นข้อความ Telegram (อ่านได้ทันทีในแชท)
    send_telegram_message(quick_digest)

    # 2) ส่งรายงานฉบับเต็มเป็นไฟล์แนบ
    send_telegram_document(full_report.encode("utf-8"), filename, caption="📄 รายงานข่าวหุ้นฉบับเต็มวันนี้")

    # 3) สำรองไว้ใน Google Drive knowledge/ เผื่อ AI ตัวอื่นหรือ คุณตี๋ ต้องใช้ต่อ
    try:
        drive_service = get_drive_service()
        base_folder_id = find_or_create_folder(drive_service, DRIVE_FOLDER_NAME)
        knowledge_folder_id = find_or_create_folder(drive_service, "knowledge", base_folder_id)
        write_file_text(drive_service, filename, knowledge_folder_id, full_report, "text/markdown")
        print(f"💾 บันทึก {filename} ลง Google Drive/{DRIVE_FOLDER_NAME}/knowledge/ แล้ว")
    except Exception as e:
        print(f"⚠️ บันทึกลง Drive ไม่สำเร็จ (ไม่กระทบการส่ง Telegram ที่ทำไปแล้ว): {e}")


if __name__ == "__main__":
    run_news_bot()
