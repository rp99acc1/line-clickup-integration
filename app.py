# app.py - LINE x ClickUp Integration (LINE SDK v3 - Complete)
from flask import Flask, request, jsonify, render_template_string
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import Configuration, ApiClient, MessagingApi, PushMessageRequest, TextMessage
from linebot.v3.webhooks import MessageEvent, TextMessageContent
import sqlite3
import re
import json
import requests
import os
from datetime import datetime
import threading

LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "3ZVjgFTiTSSfcPrrOWepkSER5JuUeemSH8V2niYLY+jGumWEWX7ftN56ZcxWMmCpQcynRyTvZqiGAlCSLP8sbCLqZbrzIFUTtetDwVdaaarmN+nDnMjU5TOrmFecDRZROIUYPNMhavx0yC5FJGR6xgdB04t89/1O/w1cDnyilFU=")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET", "a22f0cbb61b9659cbecebbd8bedd6431")
CLICKUP_API_TOKEN = os.environ.get("CLICKUP_API_TOKEN", "pk_696759_N61E72DVCLJ2JGT6RY7SX9Q0L85K453R")
CLICKUP_LIST_ID = os.environ.get("CLICKUP_LIST_ID", "14235588")
CLICKUP_DROPDOWN_FIELD_ID = os.environ.get("CLICKUP_DROPDOWN_FIELD_ID", "")

configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)
app = Flask(__name__)
db_lock = threading.Lock()

STATUS_MESSAGES = {
    "OPEN": "📥 งานของคุณเริ่มดำเนินการแล้วค่ะ",
    "โหลดไฟล์แล้ว": "📂 ไฟล์ของคุณโหลดเรียบร้อยแล้วค่ะ",
    "ตรวจเช็คไฟล์": "🔍 กำลังตรวจสอบไฟล์ของคุณค่ะ",
    "กำลังทำไฟล์": "⚙️ กำลังดำเนินการทำไฟล์ให้คุณค่ะ",
    "รอโอนเงิน": "💰 งานของคุณพร้อมชำระเงินแล้วค่ะ",
    "พร้อมพิมพ์": "🖨️ งานของคุณพร้อมพิมพ์แล้วค่ะ",
    "อยู่ในขั้นตอนการผลิต": "🏭 งานของคุณอยู่ในขั้นตอนการผลิตค่ะ",
    "สินค้าพร้อมจัดส่ง": "📦 งานของคุณพร้อมจัดส่งแล้วค่ะ",
    "จัดส่งแล้ว": "✅ งานของคุณจัดส่งเรียบร้อยแล้วค่ะ ขอบคุณที่ใช้บริการนะคะ"
}

def get_db_connection():
    conn = sqlite3.connect("customers.db", timeout=30, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS customers (customer_code TEXT PRIMARY KEY, line_user_id TEXT UNIQUE NOT NULL, display_name TEXT, clean_name TEXT, phone TEXT, created_at TEXT)""")
    c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_user_id ON customers(line_user_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_clean_name ON customers(clean_name)")
    conn.commit()
    conn.close()
    print("✅ Database initialized")

def clean_name(name):
    if not name:
        return ""
    cleaned = re.sub(r'[^\w\sก-๙a-zA-Z0-9]', '', name, flags=re.UNICODE)
    return re.sub(r'\s+', ' ', cleaned).strip().lower()

def generate_customer_code():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT customer_code FROM customers ORDER BY CAST(SUBSTR(customer_code, 4) AS INTEGER) DESC LIMIT 1")
    last = c.fetchone()
    new_num = int(last['customer_code'].replace("CUS", "")) + 1 if last else 1
    conn.close()
    return f"CUS{str(new_num).zfill(4)}"

def save_customer(line_user_id, display_name, phone=None):
    with db_lock:
        conn = get_db_connection()
        c = conn.cursor()
        try:
            c.execute("SELECT customer_code FROM customers WHERE line_user_id=?", (line_user_id,))
            existing = c.fetchone()
            if existing:
                conn.close()
                print(f"ℹ️ ลูกค้าเดิม: {existing['customer_code']}")
                return existing['customer_code'], False
            customer_code = generate_customer_code()
            clean_name_value = clean_name(display_name)
            created_at = datetime.now().isoformat()
            c.execute("INSERT INTO customers (customer_code, line_user_id, display_name, clean_name, phone, created_at) VALUES (?, ?, ?, ?, ?, ?)", (customer_code, line_user_id, display_name, clean_name_value, phone, created_at))
            conn.commit()
            print(f"✅ ลูกค้าใหม่: {customer_code} - {display_name}")
            conn.close()
            return customer_code, True
        except sqlite3.IntegrityError:
            conn.close()
            return save_customer(line_user_id, display_name, phone)

def get_customer_by_code(customer_code):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM customers WHERE customer_code=?", (customer_code,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None

def search_customer(keyword):
    if not keyword:
        return []
    clean_keyword = clean_name(keyword)
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT customer_code, display_name, clean_name, line_user_id, created_at FROM customers WHERE clean_name LIKE ? OR customer_code LIKE ? OR display_name LIKE ? ORDER BY created_at DESC LIMIT 50", (f"%{clean_keyword}%", f"%{keyword.upper()}%", f"%{keyword}%"))
    rows = c.fetchall()
    conn.close()
    return [dict(row) for row in rows]

def update_clickup_dropdown_async(customer_code, display_name, clean_name_value):
    def do_update():
        if not CLICKUP_DROPDOWN_FIELD_ID:
            return
        headers = {"Authorization": CLICKUP_API_TOKEN, "Content-Type": "application/json"}
        option_name = f"{customer_code} - {display_name} ({clean_name_value})"
        try:
            resp = requests.get(f"https://api.clickup.com/api/v2/list/{CLICKUP_LIST_ID}/field", headers=headers, timeout=10)
            if resp.status_code != 200:
                return
            fields = resp.json().get("fields", [])
            target = None
            for f in fields:
                if f["id"] == CLICKUP_DROPDOWN_FIELD_ID and f["type"] == "drop_down":
                    target = f
                    break
            if not target:
                return
            existing = target.get("type_config", {}).get("options", [])
            if any(opt.get("name") == option_name for opt in existing):
                return
            new_opts = [{"name": option_name, "color": None}] + existing
            requests.put(f"https://api.clickup.com/api/v2/list/{CLICKUP_LIST_ID}/field/{CLICKUP_DROPDOWN_FIELD_ID}", headers=headers, json={"type_config": {"options": new_opts}}, timeout=10)
            print(f"✅ เพิ่ม '{option_name}' เข้า dropdown")
        except:
            pass
    threading.Thread(target=do_update, daemon=True).start()

SEARCH_PAGE = """<!DOCTYPE html><html lang="th"><head><meta charset="UTF-8"><title>ค้นหาลูกค้า</title><style>*{margin:0;padding:0;box-sizing:border-box}body{font-family:Arial;background:linear-gradient(135deg,#667eea 0%,#764ba2 100%);min-height:100vh;padding:20px}.container{max-width:900px;margin:0 auto;background:white;border-radius:20px;padding:40px;box-shadow:0 20px 60px rgba(0,0,0,0.3)}h1{color:#667eea;margin-bottom:30px}input{width:100%;padding:15px;border:2px solid #e0e0e0;border-radius:10px;font-size:16px;margin-bottom:20px}input:focus{outline:none;border-color:#667eea}.customer-card{background:#f8f9fa;padding:20px;border-radius:10px;margin-bottom:15px;border-left:5px solid #667eea}.customer-code{font-weight:bold;color:#667eea;font-size:20px;margin-bottom:8px}.copy-btn{background:#667eea;color:white;border:none;padding:8px 20px;border-radius:8px;cursor:pointer;float:right}.copy-btn:hover{background:#5568d3}.copied{background:#28a745!important}</style></head><body><div class="container"><h1>ค้นหาลูกค้า</h1><input type="text" id="searchInput" placeholder="พิมพ์ชื่อหรือรหัส..." autofocus><div id="results"></div></div><script>const searchInput=document.getElementById('searchInput');const resultsDiv=document.getElementById('results');let debounceTimer;searchInput.addEventListener('input',function(){clearTimeout(debounceTimer);const keyword=this.value.trim();if(keyword.length<1){resultsDiv.innerHTML='';return}resultsDiv.innerHTML='<p style="text-align:center;color:#667eea;">กำลังค้นหา...</p>';debounceTimer=setTimeout(()=>{fetch('/api/search?q='+encodeURIComponent(keyword)).then(res=>res.json()).then(data=>{if(!data.results||data.results.length===0){resultsDiv.innerHTML='<p style="text-align:center;color:#999;">ไม่พบข้อมูล</p>';return}let html='';data.results.forEach(customer=>{html+='<div class="customer-card">';html+='<div class="customer-code">'+customer.customer_code;html+='<button class="copy-btn" onclick="copyCode(\''+customer.customer_code+'\',this)">คัดลอก</button>';html+='</div>';html+='<div>'+customer.display_name+'</div>';html+='<div style="color:#999;font-size:14px;">'+customer.clean_name+'</div>';html+='</div>'});resultsDiv.innerHTML=html}).catch(()=>{resultsDiv.innerHTML='<p style="text-align:center;color:#999;">เกิดข้อผิดพลาด</p>'})},300)});function copyCode(code,btn){navigator.clipboard.writeText(code).then(()=>{btn.textContent='คัดลอกแล้ว';btn.classList.add('copied');setTimeout(()=>{btn.textContent='คัดลอก';btn.classList.remove('copied')},2000)})}</script></body></html>"""

@app.route("/")
def index():
    return """<html><head><meta charset="UTF-8"><title>LINE x ClickUp</title><style>body{font-family:Arial;padding:40px;background:#f5f5f5;text-align:center}h1{color:#667eea}a{display:inline-block;margin:10px;padding:15px 30px;background:#667eea;color:white;text-decoration:none;border-radius:10px}a:hover{background:#5568d3}</style></head><body><h1>LINE x ClickUp Integration</h1><p style="color:#28a745;font-weight:bold;">Server ทำงานปกติ</p><a href="/search">ค้นหาลูกค้า</a><a href="/customers">รายชื่อลูกค้า</a></body></html>"""

@app.route("/search")
def search_page():
    return render_template_string(SEARCH_PAGE)

@app.route("/api/search")
def search_api():
    try:
        keyword = request.args.get("q", "")
        results = search_customer(keyword)
        return jsonify({"results": results})
    except:
        return jsonify({"results": []}), 500

@app.route("/customers")
def list_customers():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT customer_code, display_name, clean_name, created_at FROM customers ORDER BY created_at DESC")
    rows = c.fetchall()
    conn.close()
    html = """<html><head><meta charset="UTF-8"><title>รายชื่อลูกค้า</title><style>body{font-family:Arial;padding:20px;background:#f5f5f5}h1{color:#667eea}table{width:100%;border-collapse:collapse;background:white;margin-top:20px}th,td{padding:15px;text-align:left;border-bottom:1px solid #ddd}th{background:#667eea;color:white}tr:hover{background:#f5f5f5}.code{font-weight:bold;color:#667eea}a{color:#667eea;text-decoration:none;margin-right:20px}</style></head><body><h1>รายชื่อลูกค้าทั้งหมด</h1><p><a href="/">กลับหน้าแรก</a> <a href="/search">ค้นหา</a></p><p><strong>จำนวน:</strong> """ + str(len(rows)) + """ คน</p><table><tr><th>รหัสลูกค้า</th><th>ชื่อ</th><th>ชื่อค้นหา</th><th>วันที่สร้าง</th></tr>"""
    for row in rows:
        date_str = row['created_at'][:10] if row['created_at'] else "-"
        html += f"<tr><td class='code'>{row['customer_code']}</td><td>{row['display_name']}</td><td>{row['clean_name']}</td><td>{date_str}</td></tr>"
    html += "</table></body></html>"
    return html

@app.route("/line_webhook", methods=["POST"])
def line_webhook():
    signature = request.headers.get('X-Line-Signature', '')
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        return "Invalid signature", 400
    except Exception as e:
        print(f"❌ Error: {e}")
    return "OK"

@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    user_id = event.source.user_id
    try:
        with ApiClient(configuration) as api_client:
            api = MessagingApi(api_client)
            profile = api.get_profile(user_id)
            display_name = profile.display_name
        clean_name_value = clean_name(display_name)
        customer_code, is_new = save_customer(user_id, display_name)
        if is_new and CLICKUP_DROPDOWN_FIELD_ID:
            update_clickup_dropdown_async(customer_code, display_name, clean_name_value)
        print(f"{'✅ ใหม่' if is_new else 'ℹ️ เดิม'}: {customer_code} - {display_name}")
    except Exception as e:
        print(f"❌ Error: {e}")

@app.route("/clickup_webhook", methods=["POST"])
def clickup_webhook():
    try:
        data = request.json
        if data.get("event") != "taskStatusUpdated":
            return "OK"
        task_id = data.get("task_id")
        new_status = None
        for item in data.get("history_items", []):
            if item.get("field") == "status":
                new_status = item.get("after", {}).get("status")
                break
        if not new_status:
            return "OK"
        headers = {"Authorization": CLICKUP_API_TOKEN}
        task_resp = requests.get(f"https://api.clickup.com/api/v2/task/{task_id}", headers=headers, timeout=10)
        if task_resp.status_code != 200:
            return "OK"
        task_data = task_resp.json()
        customer_code = None
        for field in task_data.get("custom_fields", []):
            if field.get("name") in ["รหัสลูกค้า", "CUSTOMER_CODE", "Customer Code"]:
                value = field.get("value")
                if isinstance(value, dict):
                    customer_code = value.get("name", "").split(" - ")[0].strip()
                elif isinstance(value, str):
                    customer_code = value.split(" - ")[0].strip()
                break
        if not customer_code:
            return "OK"
        customer = get_customer_by_code(customer_code)
        if not customer:
            print(f"❌ ไม่พบ: {customer_code}")
            return "OK"
        status_text = STATUS_MESSAGES.get(new_status, "งานของคุณอัปเดตสถานะแล้วค่ะ")
        message = f"คุณ {customer['display_name']} {status_text}"
        try:
            with ApiClient(configuration) as api_client:
                api = MessagingApi(api_client)
                api.push_message(PushMessageRequest(to=customer['line_user_id'], messages=[TextMessage(text=message)]))
            print(f"✅ ส่งข้อความถึง {customer_code}")
        except Exception as e:
            print(f"❌ Error: {e}")
        return "OK"
    except Exception as e:
        print(f"❌ Error: {e}")
        return "Error", 500

@app.route("/health")
def health():
    return jsonify({"status": "ok", "time": datetime.now().isoformat()})

if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 5000))
    print("="*60)
    print("LINE x ClickUp Integration Server")
    print("="*60)
    print(f"Port: {port}")
    print("="*60)
    app.run(host="0.0.0.0", port=port, debug=False)