# ============================================================
#  Nick AI Agent - GitHub Actions Edition
#  ปรับจาก Colab (drive.mount) ให้รันบน GitHub Actions:
#    - ใช้ Google Drive Service Account แทน drive.mount()
#    - อ่าน API Key จาก environment variable แทน getpass()/userdata
#  ไฟล์ทั้งหมด (portfolio.md, mistakes.md, knowledge/) เก็บใน
#  Google Drive โฟลเดอร์ "Nick_AI_Agent" เพื่อให้ข้อมูลอยู่ข้ามรอบการรัน
#
#  ใช้ฟังก์ชันเชื่อม Google Drive ชุดเดียวกับ tee_agent.py (คนละโฟลเดอร์)
# ============================================================

import os
import google.generativeai as genai

from tee_agent import (
    get_drive_service,
    find_or_create_folder,
    find_file,
    read_file_text,
    write_file_text,
    read_knowledge_base,
)

DRIVE_FOLDER_NAME = "Nick_AI_Agent"

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


def run_nick_agent():
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("ไม่พบ GOOGLE_API_KEY ใน environment variables")
    genai.configure(api_key=api_key)

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

    model = genai.GenerativeModel(
        model_name="gemini-flash-latest",
        system_instruction=SYSTEM_INSTRUCTION
    )

    print("🤖 พี่นิกกำลังอ่านข้อมูลและวิเคราะห์หุ้นสักครู่...")
    response = model.generate_content(user_prompt)

    print("\n" + "=" * 60)
    print("     ผลการวิเคราะห์จากพี่นิก")
    print("=" * 60 + "\n")
    print(response.text)

    write_file_text(drive_service, "portfolio.md", base_folder_id, response.text)
    print(f"\n💾 บันทึกสถานะพอร์ตลง Google Drive/{DRIVE_FOLDER_NAME}/ เรียบร้อยแล้ว!")


if __name__ == "__main__":
    run_nick_agent()
