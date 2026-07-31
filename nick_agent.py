# ============================================================
#  Nick AI Agent - GitHub Actions Edition
#  ปรับจาก Colab (drive.mount) ให้รันบน GitHub Actions:
#    - ใช้ Google Drive Service Account แทน drive.mount()
#    - อ่าน API Key จาก environment variable แทน getpass()/userdata
#    - ส่งสรุปผลการรันเข้า Telegram ทุกครั้งที่รันเสร็จ
#  ไฟล์ทั้งหมด (portfolio.md, mistakes.md, knowledge/) เก็บใน
#  Google Drive โฟลเดอร์ "Nick_AI_Agent" เพื่อให้ข้อมูลอยู่ข้ามรอบการรัน
#
#  ใช้ฟังก์ชันเชื่อม Google Drive ชุดเดียวกับ tee_agent.py (คนละโฟลเดอร์)
# ============================================================

import os
import time
import requests
from google import genai
from google.genai import types, errors

from tee_agent import (
    get_drive_service,
    find_or_create_folder,
    find_file,
    read_file_text,
    write_file_text,
    read_knowledge_base,
)

DRIVE_FOLDER_NAME = "Nick_AI_Agent_v3"
TELEGRAM_MAX_LEN = 4096  # Telegram จำกัดความยาวข้อความต่อ 1 ครั้งไว้ที่ 4096 ตัวอักษร
MAX_RETRIES = 5  # จำนวนครั้งที่ลองใหม่เมื่อโมเดลตอบ 503/overload ชั่วคราว
INITIAL_BACKOFF_SECONDS = 20  # หน่วงเวลาก่อนลองใหม่ครั้งแรก แล้วเพิ่มเป็น 2 เท่าทุกครั้ง

DEFAULT_PORTFOLIO = """# พอร์ตโฟลิโอเริ่มต้น
- เงินสด: 100%
- หุ้นที่ถือ: ไม่มี
- มูลค่ารวม: $100,000
"""

DEFAULT_MISTAKES = """# บันทึกความผิดพลาด
- ยังไม่มีบันทึกความผิดพลาด
"""

SYSTEM_INSTRUCTION = """
คุณคือ "Nick" เอเจนต์ AI ผู้จัดการกองทุนอัจฉริยะที่จำลองปรัชญาการลงทุนมาจาก Nick Sleep
หน้าที่ของคุณคือบริหารพอร์ตโฟลิโอจำลอง โดยตัดสินใจลงทุนจาก "คลังความรู้" ที่ผู้ใช้ป้อนให้เท่านั้น
คุณมีเหตุผลเชิงตรรกะระดับสูง มีความอดทน และไม่หวั่นไหวต่อตลาดระยะสั้น

กฎเหล็ก:
1. เน้นการลงทุนระยะยาวแบบ "Buy and Hold" ถือหุ้น 3-10 ตัว ห้ามถือเงินสด 0% และถือเงินสดได้สูงสุด 40%
2. "Doing nothing most of the time" อยู่เฉยๆ คือสิ่งที่ดีที่สุด จะแอ็กชันก็ต่อเมื่อมีความมั่นใจสูงสุดจากข้อมูลใหม่
3. ห้ามแอบดูพอร์ตจริงของผู้สร้างเด็ดขาด (Portfolio Blindness)
4. ทุกครั้งที่จะเข้าซื้อหุ้น ต้องเขียน Original Thesis 3 ข้อ และตั้ง Q-Condition (เงื่อนไขสั่งขาย) ไว้ล่วงหน้า
5. ต้องอ่านไฟล์ mistakes.md เพื่อเตือนตัวเองก่อนวิเคราะห์เสมอ และถ้าพบว่าตัวเองตัดสินใจพลาด ให้เขียนบทเรียนลงไปเพิ่ม
6. อะไรที่ไม่รู้ หรือข้อมูลไม่พอ ให้ตอบว่า "ไม่รู้" ห้ามเดาเด็ดขาด

รูปแบบการสรุปรายงาน (Output Format):
จงสรุปผลลัพธ์ออกมาในรูปแบบความเรียงและตาราง Markdown โดยต้องมีหัวข้อเหล่านี้:
- # รายงานพอร์ตปัจจุบัน (Current Portfolio Status)
- # การตัดสินใจในรอบนี้ (Action Taken) พร้อมเหตุผลย่อ
- # Thesis & Q-Condition ของหุ้นแต่ละตัว
- # Watchlist ที่กำลังเฝ้าดู
"""


def send_telegram_message(text: str) -> None:
    """ส่งข้อความสรุปผลการรันเข้า Telegram ผ่าน Bot API

    ต้องตั้งค่า environment variables (แนะนำให้เก็บเป็น GitHub Secrets):
      - TELEGRAM_BOT_TOKEN : token ของบอทที่ได้จาก BotFather
      - TELEGRAM_CHAT_ID   : chat id ปลายทาง (ห้อง/กลุ่ม/ผู้ใช้ที่จะรับข้อความ)
    """
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not bot_token or not chat_id:
        print("⚠️ ไม่พบ TELEGRAM_BOT_TOKEN หรือ TELEGRAM_CHAT_ID จึงข้ามการส่ง Telegram")
        return

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

    # Telegram จำกัดความยาวข้อความไว้ที่ 4096 ตัวอักษร ถ้ายาวเกินให้แบ่งส่งเป็นหลายข้อความ
    chunks = [
        text[i:i + TELEGRAM_MAX_LEN] for i in range(0, len(text), TELEGRAM_MAX_LEN)
    ] or [text]

    for idx, chunk in enumerate(chunks, start=1):
        payload = {
            "chat_id": chat_id,
            "text": chunk,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }
        try:
            resp = requests.post(url, json=payload, timeout=30)
            resp.raise_for_status()
            print(f"📨 ส่งข้อความ Telegram ส่วนที่ {idx}/{len(chunks)} สำเร็จ")
        except requests.exceptions.RequestException as e:
            # เผื่อกรณี Markdown ที่พี่นิกสร้างมี syntax ที่ Telegram parse ไม่ผ่าน
            # ให้ลองส่งใหม่แบบ plain text แทน จะได้ไม่พลาดการแจ้งเตือน
            print(f"⚠️ ส่งแบบ Markdown ไม่สำเร็จ ({e}) กำลังลองส่งแบบข้อความธรรมดา...")
            try:
                payload.pop("parse_mode", None)
                resp = requests.post(url, json=payload, timeout=30)
                resp.raise_for_status()
                print(f"📨 ส่งข้อความ Telegram ส่วนที่ {idx}/{len(chunks)} สำเร็จ (plain text)")
            except requests.exceptions.RequestException as e2:
                print(f"❌ ส่งข้อความ Telegram ไม่สำเร็จ: {e2}")


def generate_with_retry(client: "genai.Client", user_prompt: str):
    """เรียก Gemini API พร้อม retry แบบ exponential backoff
    เผื่อกรณีโมเดลตอบ 503 UNAVAILABLE เพราะมีคนใช้งานเยอะชั่วคราว
    (SDK มี retry ในตัวอยู่แล้ว แต่ถ้าโดนหนักจริงๆ อาจไม่พอ จึงเผื่อรอบเพิ่มตรงนี้)
    """
    delay = INITIAL_BACKOFF_SECONDS
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return client.models.generate_content(
                model="gemini-flash-latest",
                contents=user_prompt,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_INSTRUCTION
                ),
            )
        except errors.ServerError as e:
            last_error = e
            print(f"⚠️ โมเดลตอบ ServerError (ครั้งที่ {attempt}/{MAX_RETRIES}): {e}")
            if attempt < MAX_RETRIES:
                print(f"   รอ {delay} วินาทีก่อนลองใหม่...")
                time.sleep(delay)
                delay *= 2
    raise last_error


def run_nick_agent():
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("ไม่พบ GOOGLE_API_KEY ใน environment variables")
    client = genai.Client(api_key=api_key)

    drive_service = get_drive_service()
    base_folder_id = find_or_create_folder(drive_service, DRIVE_FOLDER_NAME)
    knowledge_folder_id = find_or_create_folder(drive_service, "knowledge", base_folder_id)
    print(f"📁 ใช้งานโฟลเดอร์ Drive: {DRIVE_FOLDER_NAME}")

    portfolio_file_id = find_file(drive_service, "portfolio.md", base_folder_id)
    if portfolio_file_id:
        portfolio = read_file_text(drive_service, portfolio_file_id)
    else:
        portfolio = DEFAULT_PORTFOLIO
        write_file_text(drive_service, "portfolio.md", base_folder_id, portfolio)

    mistakes_file_id = find_file(drive_service, "mistakes.md", base_folder_id)
    if mistakes_file_id:
        mistakes = read_file_text(drive_service, mistakes_file_id)
    else:
        mistakes = DEFAULT_MISTAKES
        write_file_text(drive_service, "mistakes.md", base_folder_id, mistakes)

    knowledge = read_knowledge_base(drive_service, knowledge_folder_id)

    user_prompt = f"""
    นี่คือข้อมูลอัปเดตล่าสุดของสัปดาห์นี้ จงวิเคราะห์และตอบกลับตามกฎของคุณ:

    [สถานะพอร์ตล่าสุดของคุณ]
    {portfolio}

    [บันทึกความผิดพลาดที่คุณต้องระวัง]
    {mistakes}

    [ข้อมูลดิบ/คลังความรู้ใหม่ในสัปดาห์นี้]
    {knowledge}
    """

    print("🤖 พี่นิกกำลังอ่านข้อมูลและวิเคราะห์หุ้นสักครู่...")
    response = generate_with_retry(client, user_prompt)

    print("\n" + "=" * 60)
    print("     ผลการวิเคราะห์จากพี่นิก")
    print("=" * 60 + "\n")
    print(response.text)

    write_file_text(drive_service, "portfolio.md", base_folder_id, response.text)
    print(f"\n💾 บันทึกสถานะพอร์ตลง Google Drive/{DRIVE_FOLDER_NAME}/ เรียบร้อยแล้ว!")

    # ส่งสรุปผลการรันรอบนี้เข้า Telegram
    telegram_summary = f"🤖 รายงานพี่นิก ({DRIVE_FOLDER_NAME})\n\n{response.text}"
    send_telegram_message(telegram_summary)


if __name__ == "__main__":
    try:
        run_nick_agent()
    except Exception as e:
        failure_message = f"❌ Nick Agent รันไม่สำเร็จวันนี้: {e}"
        print(failure_message)
        send_telegram_message(failure_message)
        raise
