# ============================================================
#  Nick AI Agent - GitHub Actions Edition (v2 - Structured JSON)
#  ปรับจาก Colab (drive.mount) ให้รันบน GitHub Actions:
#    - ใช้ Google Drive Service Account แทน drive.mount()
#    - อ่าน API Key จาก environment variable แทน getpass()/userdata
#    - เปลี่ยนจาก portfolio.md (ข้อความอิสระ) -> portfolio.json (โครงสร้างชัดเจน)
#      เพื่อให้ Dashboard แสดงผลเป็นตารางได้จริง แทนที่จะเป็นข้อความยาวๆ
#  ไฟล์ทั้งหมด (portfolio.json, mistakes.md, knowledge/) เก็บใน
#  Google Drive โฟลเดอร์ "Nick_AI_Agent_v3" เพื่อให้ข้อมูลอยู่ข้ามรอบการรัน
#
#  ใช้ฟังก์ชันเชื่อม Google Drive ชุดเดียวกับ tee_agent.py (คนละโฟลเดอร์)
# ============================================================

import os
import json
import re
from datetime import datetime, timezone

import google.generativeai as genai

from tee_agent import (
    get_drive_service,
    find_or_create_folder,
    find_file,
    read_file_text,
    write_file_text,
    read_knowledge_base,
)

DRIVE_FOLDER_NAME = "Nick_AI_Agent_v3"

DEFAULT_PORTFOLIO = {
    "cash": 100000.0,
    "positions": [],          # {symbol, qty, entry_price, entry_date, thesis[3], q_condition}
    "watchlist_symbols": [],
    "last_updated": None
}

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
4. ทุกครั้งที่จะเข้าซื้อหุ้น ต้องเขียน Original Thesis 3 ข้อ และตั้ง Q-Condition (เงื่อนไขสั่งขาย) ไว้ล่วงหน้าเป็นข้อความชัดเจน
5. ต้องอ่าน mistakes.md เพื่อเตือนตัวเองก่อนวิเคราะห์เสมอ และถ้าพบว่าตัวเองตัดสินใจพลาด ให้เขียนบทเรียนลงไปเพิ่ม
6. อะไรที่ไม่รู้ หรือข้อมูลไม่พอ ให้ตอบว่า "ไม่รู้" ห้ามเดาเด็ดขาด

ตอบกลับเป็น JSON เท่านั้นตามโครงสร้างนี้ (ไม่ต้องมี ```json fence):
{
  "portfolio_summary_th": "สรุปสถานะพอร์ตปัจจุบัน เป็นภาษาไทย",
  "actions_taken_th": "สรุปการตัดสินใจรอบนี้พร้อมเหตุผลย่อ เป็นภาษาไทย",
  "new_positions": [
    {"symbol": "...", "qty": 0, "entry_price": 0, "thesis": ["เหตุผล1", "เหตุผล2", "เหตุผล3"],
     "q_condition": "เงื่อนไขที่จะขาย เป็นข้อความอธิบาย"}
  ],
  "watchlist_symbols": ["SYMBOL1", "SYMBOL2"],
  "watchlist_th": "เหตุผลของหุ้นที่กำลังเฝ้าดู เป็นภาษาไทย",
  "cash_remaining": 0
}
"""


def run_nick_agent():
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("ไม่พบ GOOGLE_API_KEY ใน environment variables")
    genai.configure(api_key=api_key)

    drive_service = get_drive_service()
    base_folder_id = find_or_create_folder(drive_service, DRIVE_FOLDER_NAME)
    knowledge_folder_id = find_or_create_folder(drive_service, "knowledge", base_folder_id)
    print(f"📁 ใช้งานโฟลเดอร์ Drive: {DRIVE_FOLDER_NAME}")

    portfolio_file_id = find_file(drive_service, "portfolio.json", base_folder_id)
    if portfolio_file_id:
        portfolio = json.loads(read_file_text(drive_service, portfolio_file_id))
    else:
        portfolio = json.loads(json.dumps(DEFAULT_PORTFOLIO))
        write_file_text(drive_service, "portfolio.json", base_folder_id,
                         json.dumps(portfolio, ensure_ascii=False, indent=2), "application/json")

    mistakes_file_id = find_file(drive_service, "mistakes.md", base_folder_id)
    if mistakes_file_id:
        mistakes = read_file_text(drive_service, mistakes_file_id)
    else:
        mistakes = DEFAULT_MISTAKES
        write_file_text(drive_service, "mistakes.md", base_folder_id, mistakes)

    knowledge = read_knowledge_base(drive_service, knowledge_folder_id)

    user_prompt = f"""
    นี่คือข้อมูลอัปเดตล่าสุดของสัปดาห์นี้ จงวิเคราะห์และตอบกลับเป็น JSON ตามโครงสร้างที่กำหนดเท่านั้น:

    [สถานะพอร์ตล่าสุด - JSON]
    {json.dumps(portfolio, ensure_ascii=False, indent=2)}

    [บันทึกความผิดพลาดที่คุณต้องระวัง]
    {mistakes}

    [ข้อมูลดิบ/คลังความรู้ใหม่ในสัปดาห์นี้]
    {knowledge}
    """

    model = genai.GenerativeModel(
        model_name="gemini-flash-latest",
        system_instruction=SYSTEM_INSTRUCTION,
        generation_config={"response_mime_type": "application/json"}
    )

    print("🤖 พี่นิกกำลังอ่านข้อมูลและวิเคราะห์หุ้นสักครู่...")
    response = model.generate_content(user_prompt)

    try:
        result = json.loads(response.text)
    except json.JSONDecodeError:
        result = json.loads(re.sub(r"^```json|```$", "", response.text.strip()))

    now_iso = datetime.now(timezone.utc).isoformat()
    for new_pos in result.get("new_positions", []):
        new_pos["entry_date"] = now_iso
        portfolio["positions"].append(new_pos)
        portfolio["cash"] -= new_pos["qty"] * new_pos["entry_price"]

    portfolio["watchlist_symbols"] = result.get("watchlist_symbols", portfolio.get("watchlist_symbols", []))
    portfolio["cash"] = result.get("cash_remaining", portfolio["cash"])
    portfolio["last_updated"] = now_iso
    portfolio["portfolio_summary_th"] = result.get("portfolio_summary_th", "")
    portfolio["actions_taken_th"] = result.get("actions_taken_th", "")
    portfolio["watchlist_th"] = result.get("watchlist_th", "")

    write_file_text(drive_service, "portfolio.json", base_folder_id,
                     json.dumps(portfolio, ensure_ascii=False, indent=2), "application/json")

    print("\n" + "=" * 60)
    print("     ผลการวิเคราะห์จากพี่นิก")
    print("=" * 60 + "\n")
    print("# รายงานพอร์ตปัจจุบัน\n" + portfolio["portfolio_summary_th"])
    print("\n# การตัดสินใจในรอบนี้\n" + portfolio["actions_taken_th"])
    print("\n# Watchlist\n" + portfolio["watchlist_th"])
    print(f"\n💾 บันทึกสถานะพอร์ต ({len(portfolio['positions'])} ตัวที่ถืออยู่) ลง Google Drive/{DRIVE_FOLDER_NAME}/ เรียบร้อยแล้ว!")


if __name__ == "__main__":
    run_nick_agent()
