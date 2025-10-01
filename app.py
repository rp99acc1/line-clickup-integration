# app.py - LINE x ClickUp Integration (แก้ไขแล้ว)
from flask import Flask, request, jsonify, render_template_string
from linebot import LineBotApi, WebhookHandler
from linebot.models import TextSendMessage
from linebot.exceptions import LineBotApiError
import sqlite3
import re
import json
import requests
import os
from datetime import datetime
import threading

# ============ CONFIG ============
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "3ZVjgFTiTSSfcPrrOWepkSER5JuUeemSH8V2niYLY+jGumWEWX7ftN56ZcxWMmCpQcynRyTvZqiGAlCSLP8sbCLqZbrzIFUTtetDwVdaaarmN+nDnMjU5TOrmFecDRZROIUYPNMhavx0yC5FJGR6xgdB04t89/1O/w1cDnyilFU=")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET", "a22f0cbb61b9659cbecebbd8bedd6431")
CLICKUP_API_TOKEN = os.environ.get("CLICKUP_API_TOKEN", "pk_696759_N61E72DVCLJ2JGT6RY7SX9QOL8SK453R")

CLICKUP_LIST_ID = os.environ.get("CLICKUP_LIST_ID", "14235588")
CLICKUP_DROPDOWN_FIELD_ID = os.environ.get("CLICKUP_DROPDOWN_FIELD_ID", "b7a38122-a59b-43e8-8260-52a0d1430319")

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)
app = Flask(__name__)

# สร้าง lock สำหรับป้องกันการสร้างรหัสซ้ำ
db_lock = threading.Lock()

# ============ สถานะและข้อความ ============
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

# ============ DATABASE ============
def get_db_connection():
    conn = sqlite3.connect("customers.db", check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def init_db():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS customers (
            customer_code TEXT PRIMARY KEY,
            line_user_id TEXT UNIQUE,
            display_name TEXT,
            clean_name TEXT,
            phone TEXT,
            created_at TEXT
        )
    """)
    c.execute("CREATE INDEX IF NOT EXISTS idx_clean_name ON customers(clean_name)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_user_id ON customers(line_user_id)")
    conn.commit()
    conn.close()
    print("✅ ฐานข้อมูลพร้อมใช้งาน")

# ============ ฟังก์ชันทำความสะอาดชื่อ ============
def clean_name(name):
    if not name:
        return ""
    cleaned = re.sub(r'[^\w\sก-๙a-zA-Z0-9]', '', name, flags=re.UNICODE)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned.lower()

# ============ จัดการลูกค้า (แก้ไขแล้ว - ป้องกันรหัสซ้ำ) ============
def generate_customer_code():
    """สร้างรหัสลูกค้าแบบไม่ซ้ำ"""
    with db_lock:
        conn = get_db_connection()
        c = conn.cursor()
        
        # ดึงรหัสล่าสุด
        c.execute("SELECT customer_code FROM customers ORDER BY customer_code DESC LIMIT 1")
        last_code = c.fetchone()
        
        if last_code:
            # ดึงเลขจากรหัส เช่น CUS0001 -> 1
            last_num = int(last_code[0].replace("CUS", ""))
            new_num = last_num + 1
        else:
            new_num = 1
        
        customer_code = f"CUS{str(new_num).zfill(4)}"
        conn.close()
        return customer_code

def save_customer(line_user_id, display_name, phone=None):
    """บันทึกลูกค้า - ป้องกันรหัสซ้ำ"""
    with db_lock:
        conn = get_db_connection()
        c = conn.cursor()
        
        # เช็คว่ามีอยู่แล้วหรือไม่
        c.execute("SELECT customer_code, display_name FROM customers WHERE line_user_id=?", (line_user_id,))
        existing = c.fetchone()
        
        if existing:
            # อัปเดตชื่อถ้าเปลี่ยน
            if existing[1] != display_name:
                clean_name_value = clean_name(display_name)
                c.execute("UPDATE customers SET display_name=?, clean_name=? WHERE line_user_id=?",
                         (display_name, clean_name_value, line_user_id))
                conn.commit()
                print(f"🔄 อัปเดตชื่อ {existing[0]}")
            conn.close()
            return existing[0], False  # False = ไม่ใช่ลูกค้าใหม่
        
        # สร้างลูกค้าใหม่
        customer_code = generate_customer_code()
        clean_name_value = clean_name(display_name)
        created_at = datetime.now().isoformat()
        
        try:
            c.execute("""
                INSERT INTO customers (customer_code, line_user_id, display_name, clean_name, phone, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (customer_code, line_user_id, display_name, clean_name_value, phone, created_at))
            
            conn.commit()
            print(f"✅ บันทึกลูกค้าใหม่: {customer_code} - {display_name}")
            conn.close()
            return customer_code, True  # True = ลูกค้าใหม่
        except sqlite3.IntegrityError as e:
            print(f"❌ Error บันทึกลูกค้า: {e}")
            conn.close()
            # ถ้าซ้ำ ลองสร้างรหัสใหม่อีกครั้ง
            return save_customer(line_user_id, display_name, phone)

def get_customer_by_code(customer_code):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM customers WHERE customer_code=?", (customer_code,))
    row = c.fetchone()
    conn.close()
    return row

def search_customer(keyword):
    if not keyword:
        return []
    clean_keyword = clean_name(keyword)
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("""
        SELECT customer_code, display_name, clean_name, line_user_id, created_at
        FROM customers 
        WHERE clean_name LIKE ? OR customer_code LIKE ? OR display_name LIKE ?
        ORDER BY created_at DESC
        LIMIT 50
    """, (f"%{clean_keyword}%", f"%{keyword.upper()}%", f"%{keyword}%"))
    rows = c.fetchall()
    conn.close()
    return rows

# ============ อัปเดต CLICKUP DROPDOWN ============
def update_clickup_dropdown(customer_code, display_name, clean_name_value):
    """อัปเดตรายชื่อลูกค้าใน ClickUp Dropdown"""
    if not CLICKUP_LIST_ID or not CLICKUP_DROPDOWN_FIELD_ID:
        print("ℹ️ ไม่ได้ตั้งค่า ClickUp Dropdown")
        return False
    
    headers = {
        "Authorization": CLICKUP_API_TOKEN,
        "Content-Type": "application/json"
    }
    
    # รูปแบบ: CUS0001 - ชื่อลูกค้า (ชื่อค้นหา)
    option_name = f"{customer_code} - {display_name} ({clean_name_value})"
    
    try:
        # ดึง Custom Fields ปัจจุบัน
        response = requests.get(
            f"https://api.clickup.com/api/v2/list/{CLICKUP_LIST_ID}/field",
            headers=headers,
            timeout=10
        )
        
        if response.status_code != 200:
            print(f"❌ ดึง Fields ไม่ได้: {response.text}")
            return False
        
        fields = response.json().get("fields", [])
        target_field = None
        
        for field in fields:
            if field["id"] == CLICKUP_DROPDOWN_FIELD_ID:
                target_field = field
                break
        
        if not target_field:
            print(f"❌ ไม่พบ Field ID: {CLICKUP_DROPDOWN_FIELD_ID}")
            return False
        
        # เช็คว่ามีตัวเลือกนี้แล้วหรือไม่
        existing_options = target_field.get("type_config", {}).get("options", [])
        option_exists = any(opt.get("name") == option_name for opt in existing_options)
        
        if option_exists:
            print(f"ℹ️ มี '{option_name}' แล้ว")
            return True
        
        # เพิ่มตัวเลือกใหม่ (ใส่ด้านบนสุด)
        new_option = {"name": option_name, "color": None}
        new_options = [new_option] + existing_options
        
        # อัปเดต Dropdown
        update_response = requests.put(
            f"https://api.clickup.com/api/v2/list/{CLICKUP_LIST_ID}/field/{CLICKUP_DROPDOWN_FIELD_ID}",
            headers=headers,
            json={"type_config": {"options": new_options}},
            timeout=10
        )
        
        if update_response.status_code == 200:
            print(f"✅ เพิ่ม '{option_name}' ลงใน ClickUp Dropdown")
            return True
        else:
            print(f"❌ อัปเดตไม่ได้: {update_response.text}")
            return False
            
    except Exception as e:
        print(f"❌ Error อัปเดต Dropdown: {e}")
        return False

# ============ HTML SEARCH PAGE (แก้ไขแล้ว) ============
SEARCH_PAGE = """
<!DOCTYPE html>
<html lang="th">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>🔍 ค้นหาลูกค้า</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Segoe UI', Tahoma, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
        }
        .container {
            max-width: 900px;
            margin: 0 auto;
            background: white;
            border-radius: 20px;
            padding: 40px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
        }
        h1 { color: #667eea; margin-bottom: 10px; font-size: 32px; }
        .subtitle { color: #666; margin-bottom: 30px; }
        input {
            width: 100%;
            padding: 18px 20px;
            border: 2px solid #e0e0e0;
            border-radius: 12px;
            font-size: 17px;
            margin-bottom: 20px;
        }
        input:focus {
            outline: none;
            border-color: #667eea;
            box-shadow: 0 0 0 4px rgba(102, 126, 234, 0.1);
        }
        .customer-card {
            background: linear-gradient(135deg, #f8f9fa 0%, #e9ecef 100%);
            padding: 20px;
            border-radius: 12px;
            margin-bottom: 15px;
            border-left: 5px solid #667eea;
            transition: all 0.3s;
        }
        .customer-card:hover {
            transform: translateX(5px);
            box-shadow: 0 5px 15px rgba(0,0,0,0.1);
        }
        .customer-code {
            font-weight: bold;
            color: #667eea;
            font-size: 20px;
            margin-bottom: 8px;
        }
        .copy-btn {
            background: #667eea;
            color: white;
            border: none;
            padding: 8px 20px;
            border-radius: 8px;
            cursor: pointer;
            float: right;
            transition: all 0.2s;
        }
        .copy-btn:hover { 
            background: #5568d3; 
            transform: scale(1.05);
        }
        .copied { background: #28a745 !important; }
        .no-results {
            text-align: center;
            padding: 40px;
            color: #999;
        }
        .loading {
            text-align: center;
            padding: 20px;
            color: #667eea;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>🔍 ค้นหาลูกค้า</h1>
        <p class="subtitle">พิมพ์ชื่อหรือรหัสลูกค้า</p>
        <input type="text" id="searchInput" placeholder="เช่น: สมชาย, CUS0001" autofocus>
        <div id="results"></div>
    </div>
    <script>
        const searchInput = document.getElementById('searchInput');
        const resultsDiv = document.getElementById('results');
        let debounceTimer;

        searchInput.addEventListener('input', function() {
            clearTimeout(debounceTimer);
            const keyword = this.value.trim();
            
            if (keyword.length < 1) {
                resultsDiv.innerHTML = '';
                return;
            }

            resultsDiv.innerHTML = '<div class="loading">🔄 กำลังค้นหา...</div>';

            debounceTimer = setTimeout(() => {
                fetch('/api/search?q=' + encodeURIComponent(keyword))
                    .then(res => {
                        if (!res.ok) throw new Error('Network error');
                        return res.json();
                    })
                    .then(data => {
                        if (!data.results || data.results.length === 0) {
                            resultsDiv.innerHTML = '<div class="no-results">😔 ไม่พบลูกค้า</div>';
                            return;
                        }
                        let html = '';
                        data.results.forEach(customer => {
                            html += '<div class="customer-card">';
                            html += '<div class="customer-code">' + escapeHtml(customer.code);
                            html += '<button class="copy-btn" onclick="copyCode(\'' + escapeHtml(customer.code) + '\', this)">📋 คัดลอก</button>';
                            html += '</div>';
                            html += '<div>📝 ' + escapeHtml(customer.display_name) + '</div>';
                            html += '<div style="color:#999;font-size:14px;">🔎 ' + escapeHtml(customer.clean_name) + '</div>';
                            html += '</div>';
                        });
                        resultsDiv.innerHTML = html;
                    })
                    .catch(err => {
                        console.error('Error:', err);
                        resultsDiv.innerHTML = '<div class="no-results">❌ เกิดข้อผิดพลาด กรุณาลองใหม่</div>';
                    });
            }, 300);
        });

        function escapeHtml(text) {
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }

        function copyCode(code, btn) {
            navigator.clipboard.writeText(code).then(() => {
                btn.textContent = '✓ คัดลอกแล้ว';
                btn.classList.add('copied');
                setTimeout(() => {
                    btn.textContent = '📋 คัดลอก';
                    btn.classList.remove('copied');
                }, 2000);
            }).catch(() => {
                alert('คัดลอกไม่สำเร็จ');
            });
        }
    </script>
</body>
</html>
"""

# ============ ROUTES ============
@app.route("/")
def index():
    return """
    <html><head><meta charset="UTF-8"><title>LINE x ClickUp</title>
    <style>
        body { font-family: Arial; padding: 40px; background: #f5f5f5; text-align: center; }
        h1 { color: #667eea; font-size: 36px; }
        .status { color: #28a745; font-weight: bold; font-size: 18px; margin: 20px 0; }
        a { 
            display: inline-block; 
            margin: 10px; 
            padding: 15px 30px; 
            background: #667eea; 
            color: white; 
            text-decoration: none; 
            border-radius: 10px; 
            font-size: 16px;
            transition: all 0.3s;
        }
        a:hover { 
            background: #5568d3; 
            transform: translateY(-2px);
            box-shadow: 0 5px 15px rgba(0,0,0,0.2);
        }
    </style>
    </head><body>
    <h1>🚀 LINE x ClickUp Integration</h1>
    <p class="status">✅ Server ทำงานปกติ</p>
    <div>
        <a href="/search">🔍 ค้นหาลูกค้า</a>
        <a href="/customers">📋 รายชื่อลูกค้า</a>
    </div>
    </body></html>
    """

@app.route("/search")
def search_page():
    return render_template_string(SEARCH_PAGE)

@app.route("/api/search")
def search_api():
    try:
        keyword = request.args.get("q", "")
        results = search_customer(keyword)
        return jsonify({
            "results": [
                {
                    "code": row[0], 
                    "display_name": row[1], 
                    "clean_name": row[2], 
                    "user_id": row[3]
                }
                for row in results
            ]
        })
    except Exception as e:
        print(f"❌ Search API Error: {e}")
        return jsonify({"results": []}), 500

@app.route("/customers")
def list_customers():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT customer_code, display_name, clean_name, created_at FROM customers ORDER BY created_at DESC")
    rows = c.fetchall()
    conn.close()
    
    html = """<html><head><meta charset="UTF-8"><title>รายชื่อลูกค้า</title>
    <style>
        body { font-family: Arial; padding: 20px; background: #f5f5f5; }
        h1 { color: #667eea; }
        table { width: 100%; border-collapse: collapse; background: white; margin-top: 20px; }
        th, td { padding: 15px; text-align: left; border-bottom: 1px solid #ddd; }
        th { background: #667eea; color: white; }
        tr:hover { background: #f5f5f5; }
        .code { font-weight: bold; color: #667eea; }
        a { color: #667eea; text-decoration: none; margin-right: 20px; }
        a:hover { text-decoration: underline; }
    </style></head><body>
    <h1>📋 รายชื่อลูกค้าทั้งหมด</h1>
    <p><a href="/">← กลับหน้าแรก</a> <a href="/search">🔍 ค้นหา</a></p>
    <p><strong>จำนวน:</strong> """ + str(len(rows)) + """ คน</p>
    <table>
    <tr><th>รหัสลูกค้า</th><th>ชื่อ</th><th>ชื่อค้นหา</th><th>วันที่สร้าง</th></tr>"""
    
    for row in rows:
        date_str = row[3][:10] if row[3] else "-"
        html += f"<tr><td class='code'>{row[0]}</td><td>{row[1]}</td><td>{row[2]}</td><td>{date_str}</td></tr>"
    
    html += "</table></body></html>"
    return html

# ============ LINE WEBHOOK (แก้ไขแล้ว - ไม่ส่งข้อความหาลูกค้า) ============
@app.route("/line_webhook", methods=["POST"])
def line_webhook():
    body = request.get_data(as_text=True)
    try:
        events = json.loads(body).get("events", [])
        for event in events:
            if event["type"] == "message" and event["message"]["type"] == "text":
                user_id = event["source"]["userId"]
                try:
                    profile = line_bot_api.get_profile(user_id)
                    display_name = profile.display_name
                    clean_name_value = clean_name(display_name)
                    
                    # บันทึกลูกค้า (ไม่ส่งข้อความกลับ)
                    customer_code, is_new = save_customer(user_id, display_name)
                    
                    # อัปเดต ClickUp Dropdown (ถ้าตั้งค่าแล้ว)
                    if CLICKUP_LIST_ID and CLICKUP_DROPDOWN_FIELD_ID:
                        update_clickup_dropdown(customer_code, display_name, clean_name_value)
                    
                    print(f"✅ บันทึก {customer_code} - {display_name} ({'ใหม่' if is_new else 'เดิม'})")
                    
                except LineBotApiError as e:
                    print(f"❌ LINE API Error: {e}")
                except Exception as e:
                    print(f"❌ Error: {e}")
        
        return "OK"
    except Exception as e:
        print(f"❌ LINE Webhook Error: {e}")
        return "Error", 500

# ============ CLICKUP WEBHOOK ============
@app.route("/clickup_webhook", methods=["POST"])
def clickup_webhook():
    try:
        data = request.json
        
        if data.get("event") != "taskStatusUpdated":
            return "OK"
        
        task_id = data.get("task_id")
        
        # หาสถานะใหม่
        new_status = None
        for item in data.get("history_items", []):
            if item.get("field") == "status":
                new_status = item.get("after", {}).get("status")
                break
        
        if not new_status:
            print("⚠️ ไม่พบการเปลี่ยนสถานะ")
            return "OK"
        
        # ดึงข้อมูล Task
        headers = {"Authorization": CLICKUP_API_TOKEN}
        task_response = requests.get(
            f"https://api.clickup.com/api/v2/task/{task_id}",
            headers=headers,
            timeout=10
        )
        
        if task_response.status_code != 200:
            print(f"❌ ไม่สามารถดึงข้อมูล Task: {task_response.status_code}")
            return "OK"
        
        task_data = task_response.json()
        task_name = task_data.get("name", "")
        
        # หารหัสลูกค้าจาก Custom Field
        customer_code = None
        for field in task_data.get("custom_fields", []):
            field_name = field.get("name", "")
            if field_name in ["รหัสลูกค้า", "CUSTOMER_CODE", "Customer Code"]:
                value = field.get("value")
                
                if isinstance(value, dict):
                    customer_code = value.get("name", "").split(" - ")[0].strip()
                elif isinstance(value, str):
                    customer_code = value.split(" - ")[0].strip()
                
                break
        
        if not customer_code:
            print(f"⚠️ Task {task_id} ไม่มีรหัสลูกค้า")
            return "OK"
        
        # ดึงข้อมูลลูกค้า
        customer = get_customer_by_code(customer_code)
        
        if not customer:
            print(f"❌ ไม่พบลูกค้ารหัส: {customer_code}")
            return "OK"
        
        line_user_id = customer[1]
        customer_name = customer[2]
        
        # ดึงข้อความตามสถานะ
        status_text = STATUS_MESSAGES.get(new_status, f"งานของคุณอัปเดตสถานะแล้วค่ะ")
        
        # สร้างข้อความ
        message = f"คุณ {customer_name} {status_text}"
        
        # ส่งข้อความ LINE
        try:
            line_bot_api.push_message(
                line_user_id, 
                TextSendMessage(text=message)
            )
            print(f"✅ ส่งข้อความถึง {customer_code} ({new_status})")
        except LineBotApiError as e:
            print(f"❌ ส่งข้อความไม่สำเร็จ: {e}")
        except Exception as e:
            print(f"❌ Error: {e}")
        
        return "OK"
        
    except Exception as e:
        print(f"❌ ClickUp Webhook Error: {e}")
        return "Error", 500

# ============ HEALTH CHECK ============
@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "timestamp": datetime.now().isoformat()
    })
if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 5000))
    print("\n" + "="*60)
    print("🚀 LINE x ClickUp Integration Server")
    print("="*60)
    print(f"✅ Server starting on port {port}")
    print(f"📍 Local: http://localhost:{port}")
    print(f"🔍 Search: http://localhost:{port}/search")
    print(f"📋 Customers: http://localhost:{port}/customers")
    print("="*60 + "\n")
    
    app.run(host="0.0.0.0", port=port, debug=False)