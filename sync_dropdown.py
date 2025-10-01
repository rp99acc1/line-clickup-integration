import sqlite3
import requests
import os

CLICKUP_API_TOKEN = "pk_696759_N61E72DVCLJ2JGT6RY7SX9QOL8SK453R"
CLICKUP_LIST_ID = "14235588"
CLICKUP_DROPDOWN_FIELD_ID = "b7a38122-a59b-43e8-8260-52a0d1430319"

def clean_name(name):
    import re
    cleaned = re.sub(r'[^\w\sก-๙a-zA-Z0-9]', '', name, flags=re.UNICODE)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned.lower()

# ดึงลูกค้าทั้งหมดจากฐานข้อมูล
conn = sqlite3.connect("customers.db")
c = conn.cursor()
c.execute("SELECT customer_code, display_name FROM customers")
customers = c.fetchall()
conn.close()

print(f"พบลูกค้า {len(customers)} คน")

# ดึง Dropdown ปัจจุบัน
headers = {"Authorization": CLICKUP_API_TOKEN}
response = requests.get(
    f"https://api.clickup.com/api/v2/list/{CLICKUP_LIST_ID}/field",
    headers=headers
)

fields = response.json().get("fields", [])
target_field = None
for field in fields:
    if field["id"] == CLICKUP_DROPDOWN_FIELD_ID:
        target_field = field
        break

if not target_field:
    print("❌ ไม่พบ Dropdown Field")
    exit()

existing_options = target_field.get("type_config", {}).get("options", [])
print(f"ตัวเลือกเดิม: {len(existing_options)} รายการ")

# สร้างตัวเลือกใหม่
new_options = []
for customer in customers:
    code = customer[0]
    name = customer[1]
    clean = clean_name(name)
    option_name = f"{code} - {name} ({clean})"
    
    # เช็คว่ามีแล้วหรือยัง
    if not any(opt.get("name") == option_name for opt in existing_options):
        new_options.append({"name": option_name, "color": None})
        print(f"+ เพิ่ม: {option_name}")

# รวมกับตัวเลือกเดิม
all_options = new_options + existing_options

# อัปเดต
update_response = requests.put(
    f"https://api.clickup.com/api/v2/list/{CLICKUP_LIST_ID}/field/{CLICKUP_DROPDOWN_FIELD_ID}",
    headers=headers,
    json={"type_config": {"options": all_options}}
)

if update_response.status_code == 200:
    print(f"✅ อัปเดตสำเร็จ! เพิ่ม {len(new_options)} รายการ")
else:
    print(f"❌ อัปเดตไม่สำเร็จ: {update_response.text}")