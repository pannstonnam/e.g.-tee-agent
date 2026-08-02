# ============================================================
#  Portfolio Dashboard Builder
#  อ่านพอร์ตของคุณตี๋ (JSON) และ Nick (Markdown) จาก Google Drive
#  (อ่านอย่างเดียว - ไม่เจอปัญหา storage quota เพราะปัญหาเกิดเฉพาะตอน "สร้าง/เขียน")
#  แล้วสร้างหน้าเว็บสรุป (docs/index.html) commit กลับเข้า repo เอง
#  เพื่อให้ GitHub Pages แสดงผลแบบอัปเดตอัตโนมัติทุกวัน ฟรี 100% ตลอดไป
# ============================================================

import os
import json
import html
from datetime import datetime, timezone

from tee_agent import get_drive_service, find_or_create_folder, find_file, read_file_text

TEE_FOLDER = "ตี๋เอเจนต์"
NICK_FOLDER = "Nick_AI_Agent_v3"  # ⚠️ แก้ให้ตรงกับชื่อโฟลเดอร์จริงที่ Nick ใช้อยู่ตอนนี้


def load_tee_portfolio(service):
    try:
        base_id = find_or_create_folder(service, TEE_FOLDER)
        file_id = find_file(service, "portfolio.json", base_id)
        if not file_id:
            return None
        return json.loads(read_file_text(service, file_id))
    except Exception as e:
        print(f"⚠️ อ่านพอร์ตคุณตี๋ไม่สำเร็จ: {e}")
        return None


def load_nick_portfolio(service):
    try:
        base_id = find_or_create_folder(service, NICK_FOLDER)
        file_id = find_file(service, "portfolio.md", base_id)
        if not file_id:
            return None
        return read_file_text(service, file_id)
    except Exception as e:
        print(f"⚠️ อ่านพอร์ต Nick ไม่สำเร็จ: {e}")
        return None


def render_tee_section(portfolio):
    if not portfolio:
        return '<p class="empty">ยังไม่มีข้อมูลพอร์ตของคุณตี๋</p>'
    positions = portfolio.get("positions", [])
    rows = ""
    for p in positions:
        rows += (
            "<tr>"
            f"<td>{html.escape(str(p.get('symbol', '')))}</td>"
            f"<td>{html.escape(str(p.get('qty', '')))}</td>"
            f"<td>${html.escape(str(p.get('entry_price', '')))}</td>"
            f"<td>${html.escape(str(p.get('target_price', '')))}</td>"
            f"<td>${html.escape(str(p.get('cutloss_price', '')))}</td>"
            "</tr>"
        )
    table = (
        "<table><thead><tr><th>Symbol</th><th>Qty</th><th>Entry</th>"
        "<th>Target</th><th>Cut Loss</th></tr></thead><tbody>"
        + (rows if rows else '<tr><td colspan="5">ไม่มีหุ้นที่ถืออยู่</td></tr>')
        + "</tbody></table>"
    )
    cash = portfolio.get("cash", 0)
    updated = portfolio.get("last_updated", "-")
    try:
        cash_fmt = f"${float(cash):,.2f}"
    except (TypeError, ValueError):
        cash_fmt = str(cash)
    return f"""
    <div class="stat-row">
      <div class="stat"><span class="label">เงินสด</span><span class="value">{cash_fmt}</span></div>
      <div class="stat"><span class="label">จำนวนหุ้นที่ถือ</span><span class="value">{len(positions)}</span></div>
    </div>
    {table}
    <p class="updated">อัปเดตล่าสุด: {html.escape(str(updated))}</p>
    """


def render_nick_section(text):
    if not text:
        return '<p class="empty">ยังไม่มีข้อมูลพอร์ตของ Nick</p>'
    return f'<pre class="markdown-block">{html.escape(text)}</pre>'


def build_html(tee_portfolio, nick_text):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return f"""<!DOCTYPE html>
<html lang="th">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AI Portfolio Dashboard</title>
<style>
  :root {{
    --bg: #0f1115; --card: #1a1d24; --border: #2a2e37;
    --text: #e8e9ec; --muted: #8b8f9a; --accent: #6ee7b7; --accent2: #93c5fd;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 2rem 1.25rem; background: var(--bg); color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  }}
  .wrap {{ max-width: 1100px; margin: 0 auto; }}
  h1 {{ font-size: 1.5rem; margin-bottom: 0.25rem; }}
  .subtitle {{ color: var(--muted); font-size: 0.9rem; margin-bottom: 2rem; }}
  .columns {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 1.5rem;
    align-items: start;
  }}
  @media (max-width: 720px) {{
    .columns {{ grid-template-columns: 1fr; }}
  }}
  .card {{
    background: var(--card); border: 1px solid var(--border); border-radius: 12px;
    padding: 1.5rem;
  }}
  .card h2 {{ margin-top: 0; font-size: 1.15rem; display: flex; align-items: center; gap: 0.5rem; }}
  .stat-row {{ display: flex; gap: 1.5rem; margin-bottom: 1rem; flex-wrap: wrap; }}
  .stat {{ display: flex; flex-direction: column; }}
  .stat .label {{ font-size: 0.75rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.03em; }}
  .stat .value {{ font-size: 1.4rem; font-weight: 600; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 0.85rem; }}
  th, td {{ text-align: left; padding: 0.5rem 0.5rem; border-bottom: 1px solid var(--border); }}
  th {{ color: var(--muted); font-weight: 500; font-size: 0.74rem; text-transform: uppercase; }}
  .updated {{ color: var(--muted); font-size: 0.78rem; margin-top: 1rem; margin-bottom: 0; }}
  .empty {{ color: var(--muted); font-style: italic; }}
  .markdown-block {{
    white-space: pre-wrap; word-wrap: break-word; font-family: inherit;
    font-size: 0.85rem; line-height: 1.6; color: var(--text); margin: 0;
    max-height: 600px; overflow-y: auto;
  }}
  footer {{ text-align: center; color: var(--muted); font-size: 0.75rem; margin-top: 2rem; }}
</style>
</head>
<body>
  <div class="wrap">
    <h1>📊 AI Portfolio Dashboard</h1>
    <p class="subtitle">สร้างอัตโนมัติทุกวันโดย GitHub Actions — อัปเดตล่าสุด {now}</p>

    <div class="columns">
      <div class="card">
        <h2>📈 Nick <span style="color:var(--accent2); font-size:0.7rem; font-weight:400;">Buy &amp; Hold ระยะยาว</span></h2>
        {render_nick_section(nick_text)}
      </div>

      <div class="card">
        <h2>🤖 คุณตี๋ <span style="color:var(--accent); font-size:0.7rem; font-weight:400;">ระยะสั้น 1-2 เดือน</span></h2>
        {render_tee_section(tee_portfolio)}
      </div>
    </div>

    <footer>Generated by GitHub Actions · ไม่ใช่คำแนะนำการลงทุน ใช้เพื่อการศึกษาเท่านั้น</footer>
  </div>
</body>
</html>"""


def main():
    drive_service = get_drive_service()
    tee_portfolio = load_tee_portfolio(drive_service)
    nick_text = load_nick_portfolio(drive_service)

    html_content = build_html(tee_portfolio, nick_text)

    os.makedirs("docs", exist_ok=True)
    with open("docs/index.html", "w", encoding="utf-8") as f:
        f.write(html_content)
    # บอก GitHub Pages ให้ข้าม Jekyll ไปเลย (ไฟล์เราเป็น static HTML ธรรมดาอยู่แล้ว)
    open("docs/.nojekyll", "w").close()
    print("✅ สร้าง docs/index.html และ docs/.nojekyll เรียบร้อยแล้ว")


if __name__ == "__main__":
    main()
