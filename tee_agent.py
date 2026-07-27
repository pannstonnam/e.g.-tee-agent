# ============================================================
#  Tee AI Agent (คุณตี๋) v4 - GitHub Actions Edition
#  ปรับจาก v3 (Colab + drive.mount) ให้รันบน GitHub Actions ได้:
#    - ใช้ Google Drive Service Account แทน drive.mount()
#    - อ่านค่าลับทั้งหมดจาก Environment Variables แทน getpass()/userdata
#    - ส่งแจ้งเตือนเข้า Telegram ท้ายรอบการทำงาน
#  ไฟล์ทั้งหมด (portfolio/trades_log/mistakes/knowledge) ยังอยู่ใน
#  Google Drive โฟลเดอร์ "ตี๋เอเจนต์" เหมือนเดิม เพื่อให้ข้อมูลอยู่ข้ามรอบการรัน
# ============================================================

import os
import io
import json
import re
from datetime import datetime, timezone

import google.generativeai as genai
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload
import requests as http_requests

try:
    import yfinance as yf
except ImportError:
    yf = None

DRIVE_FOLDER_NAME = "ตี๋เอเจนต์"
DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive"]

DEFAULT_PORTFOLIO = {
    "cash": 100000.0,
    "positions": [],          # {symbol, qty, entry_price, entry_date, thesis[3], target_price, cutloss_price, horizon_days}
    "watchlist_symbols": [],  # ["PTT.BK", "AAPL", ...]
    "last_updated": None
}

DEFAULT_MISTAKES = "# บันทึกความผิดพลาด (Mistakes Log)\n- ยังไม่มีบันทึกความผิดพลาด\n"


# ------------------------------------------------------------
# 1. Google Drive helper functions (แทนที่ os.path/open() แบบ Colab)
# ------------------------------------------------------------
def get_drive_service():
    raw = os.environ.get("GDRIVE_SERVICE_ACCOUNT_JSON")
    if not raw:
        raise RuntimeError("ไม่พบ GDRIVE_SERVICE_ACCOUNT_JSON ใน environment variables")
    creds_info = json.loads(raw)
    creds = service_account.Credentials.from_service_account_info(creds_info, scopes=DRIVE_SCOPES)
    return build("drive", "v3", credentials=creds)


def find_or_create_folder(service, name, parent_id=None):
    query = f"name='{name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
    if parent_id:
        query += f" and '{parent_id}' in parents"
    results = service.files().list(q=query, fields="files(id, name)").execute()
    files = results.get("files", [])
    if files:
        return files[0]["id"]
    body = {"name": name, "mimeType": "application/vnd.google-apps.folder"}
    if parent_id:
        body["parents"] = [parent_id]
    folder = service.files().create(body=body, fields="id").execute()
    print(f"🆕 สร้างโฟลเดอร์ '{name}' ใน Google Drive แล้ว")
    return folder["id"]


def find_file(service, name, parent_id):
    query = f"name='{name}' and '{parent_id}' in parents and trashed=false"
    results = service.files().list(q=query, fields="files(id, name)").execute()
    files = results.get("files", [])
    return files[0]["id"] if files else None


def read_file_text(service, file_id):
    request = service.files().get_media(fileId=file_id)
    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return buf.getvalue().decode("utf-8")


def write_file_text(service, name, parent_id, content, mime_type="text/plain"):
    existing_id = find_file(service, name, parent_id)
    media = MediaIoBaseUpload(io.BytesIO(content.encode("utf-8")), mimetype=mime_type)
    if existing_id:
        service.files().update(fileId=existing_id, media_body=media).execute()
    else:
        body = {"name": name, "parents": [parent_id]}
        service.files().create(body=body, media_body=media, fields="id").execute()
        print(f"🆕 สร้างไฟล์ '{name}' ใน Google Drive แล้ว")


def list_knowledge_files(service, folder_id):
    query = (f"'{folder_id}' in parents and trashed=false "
             f"and (name contains '.txt' or name contains '.md')")
    results = service.files().list(q=query, fields="files(id, name)").execute()
    return results.get("files", [])


def read_knowledge_base(service, folder_id):
    files = list_knowledge_files(service, folder_id)
    if not files:
        return "⚠️ ไม่พบไฟล์ในโฟลเดอร์ knowledge (รองรับ .txt และ .md)"
    knowledge_text = ""
    for f in files:
        content = read_file_text(service, f["id"])
        knowledge_text += f"\n\n--- ข้อมูลจากไฟล์: {f['name']} ---\n" + content
    return knowledge_text


# ------------------------------------------------------------
# 2. หลักที่ 1 "Accurate Data" — ดึงราคาล่าสุดจริง
# ------------------------------------------------------------
def fetch_latest_prices(symbols):
    prices = {}
    if not symbols:
        return prices
    if yf is None:
        print("⚠️ ไม่พบ yfinance ข้ามการดึงราคาสด")
        return prices
    for sym in symbols:
        try:
            hist = yf.Ticker(sym).history(period="1d")
            if not hist.empty:
                prices[sym] = round(float(hist["Close"].iloc[-1]), 4)
        except Exception as e:
            print(f"⚠️ ดึงราคา {sym} ไม่สำเร็จ: {e}")
    return prices


# ------------------------------------------------------------
# 3. หลักที่ 4 "Feedback Loop ที่แท้จริง" — ตัด Target/Cut Loss ด้วยโค้ด
# ------------------------------------------------------------
def evaluate_open_positions(portfolio, live_prices):
    closed_trades, still_open = [], []
    for pos in portfolio["positions"]:
        price = live_prices.get(pos["symbol"])
        if price is None:
            still_open.append(pos)
            continue
        outcome = None
        if price >= pos["target_price"]:
            outcome = "TARGET_HIT"
        elif price <= pos["cutloss_price"]:
            outcome = "CUTLOSS_HIT"
        if outcome:
            pnl_pct = round((price - pos["entry_price"]) / pos["entry_price"] * 100, 2)
            closed_trades.append({**pos, "exit_price": price,
                                   "exit_date": datetime.now(timezone.utc).isoformat(),
                                   "outcome": outcome, "pnl_pct": pnl_pct})
            portfolio["cash"] += pos["qty"] * price
        else:
            still_open.append(pos)
    portfolio["positions"] = still_open
    return closed_trades


def append_mistakes_notes(mistakes_text, closed_trades):
    losses = [t for t in closed_trades if t["outcome"] == "CUTLOSS_HIT"]
    for t in losses:
        note = ("\n- [" + t["exit_date"][:10] + "] " + t["symbol"] + ": โดน Cut Loss ที่ "
                 + str(t["exit_price"]) + " (เข้า " + str(t["entry_price"]) + ", ขาดทุน "
                 + str(t["pnl_pct"]) + "%) - Thesis เดิม: " + str(t["thesis"]) + "\n")
        mistakes_text += note
    return mistakes_text, len(losses)


def compute_performance_stats(trades_log):
    if not trades_log:
        return "ยังไม่มีประวัติการเทรดที่ปิดจบ"
    wins = [t for t in trades_log if t["outcome"] == "TARGET_HIT"]
    losses = [t for t in trades_log if t["outcome"] == "CUTLOSS_HIT"]
    win_rate = len(wins) / len(trades_log) * 100
    avg_win = sum(t["pnl_pct"] for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t["pnl_pct"] for t in losses) / len(losses) if losses else 0
    return ("เทรดที่ปิดแล้วทั้งหมด: " + str(len(trades_log)) + " ไม้ | Win Rate: "
            + f"{win_rate:.1f}" + "% | กำไรเฉลี่ยไม้ชนะ: " + f"{avg_win:.1f}"
            + "% | ขาดทุนเฉลี่ยไม้แพ้: " + f"{avg_loss:.1f}" + "%")


# ------------------------------------------------------------
# 4. Telegram notification
# ------------------------------------------------------------
def send_to_telegram(text):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("⚠️ ไม่ได้ตั้งค่า TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID ข้ามการแจ้งเตือน")
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        resp = http_requests.post(url, data={"chat_id": chat_id, "text": text[:4096]})
        if resp.status_code == 200:
            print("📨 ส่งแจ้งเตือนเข้า Telegram สำเร็จ")
        else:
            print(f"⚠️ ส่ง Telegram ไม่สำเร็จ: {resp.text}")
    except Exception as e:
        print(f"⚠️ ส่ง Telegram ไม่สำเร็จ: {e}")


# ------------------------------------------------------------
# 5. System Prompt สำหรับ "คุณตี๋"
# ------------------------------------------------------------
SYSTEM_INSTRUCTION = """
คุณคือ "คุณตี๋" AI Agent ผู้จัดการพอร์ตเทรดเดอร์สายระยะสั้น (กรอบเข้า-ออก 1–2 เดือน)
มีความอดทนต่อความผันผวนสูง วิเคราะห์อย่างมีเหตุผล ไม่หวั่นไหวต่อ Noise รายวัน

หลักการ 4 ข้อของ Agent ที่ดีที่ต้องยึดถือเสมอ:
1. ข้อมูลต้องแม่นยำ (Accurate Data) — ใช้เฉพาะราคาล่าสุดที่ระบบดึงมาให้จริงเท่านั้น ห้ามเดาราคา
2. ทำงานต่อเนื่องได้ (Reliability) — ทุกการตัดสินใจต้องบันทึกเป็นโครงสร้างที่ระบบใช้ต่อได้
3. เป้าหมายชัดเจน (Explicit Goal) — ทุกไม้ต้องมี Target Price และ Cut Loss เป็นตัวเลข ไม่ใช่ความรู้สึก
4. Feedback Loop ที่แท้จริง — ใช้สถิติ Win Rate และบทเรียนที่ระบบสรุปมาให้ ปรับการตัดสินใจจริง

วิธีคิดแบบ Scientific Method (ทีละตัวแปร):
- ตั้งสมมติฐาน (Hypothesis) ก่อนเปิดไม้ใหม่เสมอ โดยอ้างอิง Win Rate และบทเรียนที่ผ่านมา
- ปรับกลยุทธ์ทีละตัวแปร (เช่น ปรับแค่ Cut Loss หรือแค่ Target ไม่ปรับพร้อมกันหลายอย่าง)
- เทียบผลลัพธ์กับ Baseline (พอร์ตปัจจุบัน) ก่อนเปลี่ยนแนวทาง

กฎเหล็ก:
1. ถือหุ้น 3–10 ตัว ห้ามถือเงินสด 0% และถือเงินสดได้สูงสุดไม่เกิน 40%
2. "Doing nothing most of the time" — Action ต่อเมื่อมั่นใจสูงสุดจากข้อมูล/Setup ใหม่เท่านั้น
3. ห้ามแอบดูพอร์ตจริงของผู้สร้างเด็ดขาด (Portfolio Blindness)
4. ทุกไม้ใหม่ต้องมี Original Thesis 3 ข้อ และ Target/Cut Loss เป็นตัวเลขล่วงหน้า
5. อ่านสถิติ Win Rate และ mistakes.md เตือนตัวเองก่อนวิเคราะห์เสมอ
6. อะไรที่ไม่รู้หรือข้อมูลไม่พอ ให้ตอบว่า "ไม่รู้" ห้ามเดาเด็ดขาด
7. ระบบตัด Cut Loss/Target ให้อัตโนมัติเมื่อราคาแตะจุดที่กำหนด หน้าที่คุณคือตั้งจุดให้แม่นยำล่วงหน้า

ตอบกลับเป็น JSON เท่านั้นตามโครงสร้างนี้ (ไม่ต้องมี ```json fence):
{
  "portfolio_summary_th": "สรุปสถานะพอร์ตปัจจุบัน เป็นภาษาไทย",
  "actions_taken_th": "สรุปการตัดสินใจรอบนี้พร้อมเหตุผลย่อ เป็นภาษาไทย",
  "new_positions": [
    {"symbol": "...", "qty": 0, "entry_price": 0, "thesis": ["เหตุผล1", "เหตุผล2", "เหตุผล3"],
     "target_price": 0, "cutloss_price": 0, "horizon_days": 30}
  ],
  "watchlist_symbols": ["SYMBOL1", "SYMBOL2"],
  "watchlist_th": "เหตุผลของหุ้นที่กำลังเฝ้าดู เป็นภาษาไทย",
  "cash_remaining": 0
}
"""


# ------------------------------------------------------------
# 6. ฟังก์ชันหลักสำหรับคุณตี๋
# ------------------------------------------------------------
def run_tee_agent():
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("ไม่พบ GOOGLE_API_KEY ใน environment variables")
    genai.configure(api_key=api_key)

    # --- เชื่อมต่อ Google Drive และเตรียมโฟลเดอร์/ไฟล์ ---
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

    trades_file_id = find_file(drive_service, "trades_log.json", base_folder_id)
    if trades_file_id:
        trades_log = json.loads(read_file_text(drive_service, trades_file_id))
    else:
        trades_log = []
        write_file_text(drive_service, "trades_log.json", base_folder_id,
                         json.dumps(trades_log, ensure_ascii=False, indent=2), "application/json")

    mistakes_file_id = find_file(drive_service, "mistakes.md", base_folder_id)
    if mistakes_file_id:
        mistakes = read_file_text(drive_service, mistakes_file_id)
    else:
        mistakes = DEFAULT_MISTAKES
        write_file_text(drive_service, "mistakes.md", base_folder_id, mistakes)

    # Step 1: ดึงราคาจริง (หลักที่ 1 Accurate Data)
    held_symbols = [p["symbol"] for p in portfolio["positions"]]
    all_symbols = list(set(held_symbols + portfolio.get("watchlist_symbols", [])))
    live_prices = fetch_latest_prices(all_symbols)

    # Step 2: ตัด Target/Cut Loss อัตโนมัติก่อนให้ AI คิด (หลักที่ 4 Feedback Loop)
    closed_trades = evaluate_open_positions(portfolio, live_prices)
    if closed_trades:
        trades_log.extend(closed_trades)
        write_file_text(drive_service, "trades_log.json", base_folder_id,
                         json.dumps(trades_log, ensure_ascii=False, indent=2), "application/json")
        mistakes, n_losses = append_mistakes_notes(mistakes, closed_trades)
        if n_losses:
            write_file_text(drive_service, "mistakes.md", base_folder_id, mistakes)
            print(f"📝 บันทึกบทเรียนจากไม้ที่ขาดทุน {n_losses} รายการ")
    perf_stats = compute_performance_stats(trades_log)

    # Step 3: อ่านคลังความรู้ (จาก Google Drive)
    knowledge = read_knowledge_base(drive_service, knowledge_folder_id)

    user_prompt = f"""
    วิเคราะห์ข้อมูลนี้และตอบกลับเป็น JSON ตามโครงสร้างที่กำหนดเท่านั้น:

    [สถานะพอร์ตล่าสุด - JSON]
    {json.dumps(portfolio, ensure_ascii=False, indent=2)}

    [ราคาล่าสุดที่ดึงมาจริงวันนี้]
    {json.dumps(live_prices, ensure_ascii=False, indent=2)}

    [ไม้ที่เพิ่งปิดจบรอบนี้ - ระบบตัดให้อัตโนมัติแล้ว]
    {json.dumps(closed_trades, ensure_ascii=False, indent=2) if closed_trades else "ไม่มี"}

    [สถิติผลงานสะสม]
    {perf_stats}

    [บันทึกความผิดพลาดที่ต้องระวัง]
    {mistakes}

    [ข้อมูลดิบ/คลังความรู้ใหม่สัปดาห์นี้]
    {knowledge}
    """

    model = genai.GenerativeModel(
        model_name="gemini-flash-latest",
        system_instruction=SYSTEM_INSTRUCTION,
        generation_config={"response_mime_type": "application/json"}
    )

    print("🤖 คุณตี๋กำลังอ่านข้อมูล ตรวจ Q-Condition และวิเคราะห์พอร์ต...")
    response = model.generate_content(user_prompt)

    try:
        result = json.loads(response.text)
    except json.JSONDecodeError:
        result = json.loads(re.sub(r"^```json|```$", "", response.text.strip()))

    # Step 4: อัปเดตพอร์ตจากผลลัพธ์ AI แล้วบันทึกกลับ Google Drive
    now_iso = datetime.now(timezone.utc).isoformat()
    for new_pos in result.get("new_positions", []):
        new_pos["entry_date"] = now_iso
        portfolio["positions"].append(new_pos)
        portfolio["cash"] -= new_pos["qty"] * new_pos["entry_price"]

    portfolio["watchlist_symbols"] = result.get("watchlist_symbols", portfolio.get("watchlist_symbols", []))
    portfolio["cash"] = result.get("cash_remaining", portfolio["cash"])
    portfolio["last_updated"] = now_iso

    write_file_text(drive_service, "portfolio.json", base_folder_id,
                     json.dumps(portfolio, ensure_ascii=False, indent=2), "application/json")

    # Step 5: รายงานที่อ่านง่ายสำหรับมนุษย์ + ส่งเข้า Telegram
    summary_th = result.get("portfolio_summary_th", "")
    actions_th = result.get("actions_taken_th", "")
    watchlist_th = result.get("watchlist_th", "")

    print("\n" + "=" * 60)
    print("     ผลการวิเคราะห์จากคุณตี๋")
    print("=" * 60 + "\n")
    print("# รายงานพอร์ตปัจจุบัน\n" + summary_th)
    print("\n# การตัดสินใจในรอบนี้\n" + actions_th)
    print("\n# Watchlist\n" + watchlist_th)
    print(f"\n📊 {perf_stats}")
    print(f"\n💾 บันทึกสถานะพอร์ต ({len(portfolio['positions'])} ไม้ที่ถืออยู่) ลง Google Drive เรียบร้อยแล้ว!")

    telegram_message = (
        "🤖 รายงานคุณตี๋\n\n"
        f"📌 พอร์ต:\n{summary_th}\n\n"
        f"⚡ การตัดสินใจ:\n{actions_th}\n\n"
        f"👀 Watchlist:\n{watchlist_th}\n\n"
        f"📊 {perf_stats}"
    )
    send_to_telegram(telegram_message)


if __name__ == "__main__":
    run_tee_agent()
