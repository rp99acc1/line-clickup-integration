import requests
import sqlite3
import re

TOKEN = "pk_696759_N61E72DVCLJ2JGT6RY7SX9Q0L85K453R"
LIST_ID = "14235588"

def clean_name(name):
    cleaned = re.sub(r'[^\w\sก-๙a-zA-Z0-9]', '', name, flags=re.UNICODE)
    return re.sub(r'\s+', ' ', cleaned).strip().lower()

# 1. หา Field ID
print("🔍 กำลังหา Dropdown Field...")
headers = {"Authorization": TOKEN}
resp = requests.get(f"https://api.clickup.com/api/v2/list/{LIST_ID}/field", headers=headers)

dropdown_field = None
for field in resp.json().get("fields", []):
    if field["type"] == "drop_down" and "รหัสลูกค้า" in field["name"]:
        dropdown_field = field
        print(f"✅ พบ Field: {field['name']}")
        print(f"📝 Field ID: {field['id']}")
        break

if not dropdown_field:
    print("❌ ไม่พบ Dropdown Field ชื่อ 'รหัสลูกค้า'")
    print("กรุณาสร้าง Custom Field ใน ClickUp:")
    print("  1. Type: Drop-down")
    print("  2. Name: รหัสลูกค้า")
    exit()

FIELD_ID = dropdown_field["id"]

# 2. ดึงลูกค้าจากฐานข้อมูล
try:
    conn = sqlite3.connect("customers.db")
    c = conn.cursor()
    c.execute("SELECT customer_code, display_name FROM customers")
    customers = c.fetchall()
    conn.close()
    
    if not customers:
        print("ℹ️ ไม่มีลูกค้าในฐานข้อมูล")
        print("ให้ลูกค้าทัก LINE Bot แล้วรันสคริปต์นี้อีกครั้ง")
        exit()
    
    print(f"📊 พบลูกค้า {len(customers)} คน")
except:
    print("ℹ้ ไม่พบไฟล์ customers.db")
    print("รันโค้ดหลักก่อน (python app.py) แล้วให้ลูกค้าทัก BOT")
    exit()

# 3. เพิ่มลูกค้าเข้า Dropdown
existing = dropdown_field.get("type_config", {}).get("options", [])
new_options = []

for code, name in customers:
    option = f"{code} - {name} ({clean_name(name)})"
    if not any(opt.get("name") == option for opt in existing):
        new_options.append({"name": option, "color": None})
        print(f"+ {option}")

if not new_options:
    print("✅ ลูกค้าทั้งหมดอยู่ใน Dropdown แล้ว")
    exit()

all_options = new_options + existing
update_resp = requests.put(
    f"https://api.clickup.com/api/v2/list/{LIST_ID}/field/{FIELD_ID}",
    headers=headers,
    json={"type_config": {"options": all_options}}
)

if update_resp.status_code == 200:
    print(f"✅ เพิ่ม {len(new_options)} รายการสำเร็จ!")
    print(f"\n📝 Field ID: {FIELD_ID}")
    print("🔧 ใส่ Field ID นี้ใน Render Environment Variables:")
    print(f"   CLICKUP_DROPDOWN_FIELD_ID={FIELD_ID}")
else:
    print(f"❌ Error: {update_resp.text}")