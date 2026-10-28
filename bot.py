import json, base64, os, sqlite3
from datetime import datetime
import requests
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes
from config import BOT_TOKEN, CHAT_ID, MOONSHOT_KEY, MOONSHOT_BASE, MOONSHOT_MODEL
import time

PROMPT = """Extract invoice data from this image and reply with ONLY a JSON object:
{"date":"YYYY-MM-DD or null","invoice_no":"string or null","vendor":"string or null",
"total":number or null,"tax":number or null,"currency":"3-letter code or null",
"category":"one of: meals,transport,office,software,travel,utilities,other",
"confidence":"high|medium|low","notes":"string or empty"}
No markdown, no explanation."""
def extract_with_moonshot(image_bytes: bytes) -> dict:
    b64 = base64.b64encode(image_bytes).decode()
    url = f"{MOONSHOT_BASE}/chat/completions"
    headers = {"Authorization": f"Bearer {MOONSHOT_KEY}"}
    body = {
        "model": MOONSHOT_MODEL,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": PROMPT},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}
            ]
        }]
    }
    for attempt in range(4):          # try up to 4 times
        r = requests.post(url, json=body, headers=headers, timeout=60)
        if r.status_code == 200:
            break
        if r.status_code == 429 and attempt < 3:
            wait = 20 * (attempt + 1)  # 20s, 40s, 60s
            print(f"429 rate-limited, waiting {wait}s…")
            time.sleep(wait)
            continue
        r.raise_for_status()
    else:
        r.raise_for_status()

    text = r.json()["choices"][0]["message"]["content"].strip()
    text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    return json.loads(text)

def init_db():
    con = sqlite3.connect("invoices.db")
    con.execute("""CREATE TABLE IF NOT EXISTS invoices(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        added_at TEXT, date TEXT, invoice_no TEXT, vendor TEXT,
        total REAL, tax REAL, currency TEXT, category TEXT,
        confidence TEXT, source_file TEXT, notes TEXT)""")
    con.commit()
    con.close()

def save_to_db(data: dict, filename: str):
    con = sqlite3.connect("invoices.db")
    con.execute("""INSERT INTO invoices(added_at,date,invoice_no,vendor,total,tax,
        currency,category,confidence,source_file,notes)
        VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (datetime.now().strftime("%Y-%m-%d %H:%M"), data.get("date"),
         data.get("invoice_no"), data.get("vendor"), data.get("total"),
         data.get("tax"), data.get("currency"), data.get("category"),
         data.get("confidence"), filename, data.get("notes")))
    con.commit()
    con.close()

async def handle(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != CHAT_ID:
        return
    msg = update.message
    status = await msg.reply_text("⏳ Processing…")

    if msg.photo:
        file = await ctx.bot.get_file(msg.photo[-1].file_id)
    elif msg.document and msg.document.mime_type.startswith("image/"):
        file = await ctx.bot.get_file(msg.document.file_id)
    else:
        await status.edit_text("⚠️ Send a photo or image file of an invoice.")
        return
    image_bytes = await file.download_as_bytearray()

    try:
        data = extract_with_moonshot(bytes(image_bytes))
    except Exception as e:
        await status.edit_text(f"❌ Extraction failed: {e}\nCheck balance at platform.moonshot.cn → 用量统计")
        return

    os.makedirs("processed", exist_ok=True)
    fname = f"{datetime.now():%Y%m%d_%H%M%S}_{msg.id}.jpg"
    with open(os.path.join("processed", fname), "wb") as f:
        f.write(image_bytes)

    try:
        save_to_db(data, fname)
    except Exception as e:
        await status.edit_text(f"⚠️ Extracted but DB save failed: {e}\n\n{json.dumps(data, indent=1)}")
        return

    await status.edit_text(
        f"✅ Saved!\nVendor: {data.get('vendor')}\nTotal: {data.get('currency')} {data.get('total')}\n"
        f"Date: {data.get('date')} | Category: {data.get('category')} | Confidence: {data.get('confidence')}"
    )

def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.IMAGE, handle))
    print("Bot running… send an invoice photo to Telegram.")
    app.run_polling()

if __name__ == "__main__":
    main()