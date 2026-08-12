import os
import logging
import requests
from enum import Enum
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
import google.generativeai as genai

# ==================== ١. ڕێکخستن ====================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ✅ لە Environment Variables وەردەگرێت
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

if not BOT_TOKEN or not GEMINI_API_KEY:
    logger.error("❌ BOT_TOKEN یان GEMINI_API_KEY دانەنراوە!")
    exit(1)

# ڕێکخستنی جیمینی
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-1.5-flash")

# ==================== ٢. بەڕێوەبردنی دۆخ ====================
class UserState(Enum):
    IDLE = "idle"
    WAITING_FOR_WEIGHT = "waiting_for_weight"

class Session:
    def __init__(self, medicine_name: str, medicine_info: str):
        self.medicine_name = medicine_name
        self.medicine_info = medicine_info
        self.state = UserState.IDLE

# کاتی ناوەخۆیی
sessions = {}

# ==================== ٣. هەلپەرەکان ====================
def get_main_menu():
    keyboard = [
        [InlineKeyboardButton("🏷️ ناوەکانی تر", callback_data="names")],
        [InlineKeyboardButton("⚖️ پێدان بەپێی کێش", callback_data="dosage")],
        [InlineKeyboardButton("🚫 قەدەغەکراوەکان", callback_data="contraindications")],
        [InlineKeyboardButton("🥗 خۆراکە بەسودەکان", callback_data="beneficial")],
        [InlineKeyboardButton("🧠 سیستەم و مێکانیزم", callback_data="mechanism")],
        [InlineKeyboardButton("📋 زانیاری گشتی", callback_data="general")],
        [InlineKeyboardButton("🔄 دەرمانێکی تر", callback_data="new")],
    ]
    return InlineKeyboardMarkup(keyboard)

# ==================== ٤. فەرمانەکان ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "💊 سڵاو! من بۆتی پزیشکیم.\n\n"
        "دەتوانیت:\n"
        "• ناوی دەرمانەکە بنووسیت\n"
        "• وێنەی دەرمانەکە بنێریت\n\n"
        "من ناسینەوەی دەکەم و زانیاری تەواوت پێدەدەم! 🩺\n\n"
        "بۆ سڕینەوەی زانیاریەکان: /cancel"
    )

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    sessions.pop(chat_id, None)
    await update.message.reply_text(
        "🗑️ زانیارییەکان سڕانەوە. دەرمانێکی نوێ بنووسە یان وێنەی بنێرە."
    )

# ==================== ٥. ناسینەوەی دەرمان ====================
async def identify_medicine_by_text(update: Update, text: str):
    chat_id = update.effective_chat.id
    await update.message.reply_text("⏳ خەریکی ناسینەوەی دەرمانەکەم...")

    try:
        prompt = f'''
تکایە ئەم دەرمانە ناسینەوە بکە: "{text}"
زمان: کوردی سۆرانی

تکایە ئەم زانیاریانە بە کوردی سۆرانی بدە بە شێوەیەکی ڕوون:
١. ناوی گشتی (Generic Name)
٢. ناوی زانستی (Scientific/Chemical Name)
٣. ناوی بازرگانی (Brand Names)
٤. بەکارهێنان و نەخۆشیەکان چاک دەکات
٥. بری پێدانی گشتی و چەن جار لە ڕۆژێک
٦. قەدەغەکراوەکان (دەرمان و خۆراک)
٧. خۆراکە بەسودەکان
٨. ئاگادارییە گرنگەکان
'''
        response = model.generate_content(prompt)
        info = response.text or "⚠️ نەتوانرا زانیاری بدozzer."

        sessions[chat_id] = Session(medicine_name=text, medicine_info=info)
        await update.message.reply_text(
            f"💊 دەرمان ناسێنرا: {text}\n\nهەڵبژاردنێک هەڵبژێرە:",
            reply_markup=get_main_menu(),
        )

    except Exception as e:
        await update.message.reply_text(f"❌ هەڵە لە ناسینەوەی دەرمان: {e}")

async def identify_medicine_by_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await update.message.reply_text("📸 خەریکی شیکاری وێنەکەم...")

    try:
        photo = update.message.photo[-1]  # گەورەترین وێنە
        file = await context.bot.get_file(photo.file_id)
        file_url = file.file_path

        # داگرتنی وێنە
        response = requests.get(file_url)
        if response.status_code != 200:
            await update.message.reply_text("❌ نەتوانرا وێنە دابگیرێت.")
            return

        image_data = response.content

        prompt = '''
ئەم وێنەیە دەرمانێکە. تکایە ناسینەوەی بکە و زانیاری تەواو لەسەر بنووسە بە کوردی سۆرانی.

تکایە ئەم زانیاریانە بدە:
١. ناوی گشتی (Generic Name)
٢. ناوی زانستی (Scientific/Chemical Name)
٣. ناوی بازرگانی (Brand Names)
٤. بەکارهێنان و نەخۆشیەکان چاک دەکات
٥. بری پێدانی گشتی و چەن جار لە ڕۆژێک
٦. قەدەغەکراوەکان (دەرمان و خۆراک)
٧. خۆراکە بەسودەکان
٨. ئاگادارییە گرنگەکان
'''

        image_part = {"mime_type": "image/jpeg", "data": image_data}
        gemini_response = model.generate_content([prompt, image_part])
        info = gemini_response.text or "⚠️ نەتوانرا زانیاری بدozzer."

        # هەوڵدان بۆ دۆزینەوەی ناوی دەرمان
        med_name = "نەناسراو"
        for line in info.split("\n")[:10]:
            clean = line.strip().lstrip("١٢٣٤٥٦٧٨٩٠.-*# ")
            if clean and len(clean) > 2:
                med_name = clean
                break

        sessions[chat_id] = Session(medicine_name=med_name, medicine_info=info)
        await update.message.reply_text(
            f"💊 دەرمان ناسێنرا: {med_name}\n\nهەڵبژاردنێک هەڵبژێرە:",
            reply_markup=get_main_menu(),
        )

    except Exception as e:
        await update.message.reply_text(f"❌ هەڵە لە شیکاری وێنە: {e}")

# ==================== ٦. هەڵبژاردنەکان ====================
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = update.message.text

    if text.startswith("/"):
        return

    # ئایا چاوەڕوانی کێشە؟
    if chat_id in sessions and sessions[chat_id].state == UserState.WAITING_FOR_WEIGHT:
        await handle_weight_input(update, text)
        return

    # دەرمانێکی نوێ
    await identify_medicine_by_text(update, text)

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await identify_medicine_by_photo(update, context)

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = update.effective_chat.id
    data = query.data

    if chat_id not in sessions:
        await query.edit_message_text(
            "❌ تکایە سەرەتا ناوی دەرمانەکە بنووسە یان وێنەی بنێرە."
        )
        return

    session = sessions[chat_id]

    if data == "names":
        await get_other_names(query, session.medicine_name)
    elif data == "dosage":
        await ask_for_weight(query)
        session.state = UserState.WAITING_FOR_WEIGHT
    elif data == "contraindications":
        await get_contraindications(query, session.medicine_name)
    elif data == "beneficial":
        await get_beneficial_foods(query, session.medicine_name)
    elif data == "general":
        await query.edit_message_text(
            f"📋 زانیاری گشتی:\n\n{session.medicine_info}",
            reply_markup=get_main_menu(),
        )
    elif data == "mechanism":
        await get_mechanism_and_system(query, session.medicine_name)
    elif data == "new":
        sessions.pop(chat_id, None)
        await query.edit_message_text("🔄 دەرمانێکی نوێ بنووسە یان وێنەی بنێرە:")

# ==================== ٧. هەڵبژاردنی تایبەت ====================
async def get_other_names(query, medicine_name):
    await query.edit_message_text("⏳ خەریکی گەڕان بۆ ناوەکانی تر...")

    try:
        prompt = f'''
تکایە تەنها ناوی زانستی و ناوی بازرگانی دەرمانی "{medicineName}" بە کوردی سۆرانی بنووسە.
بە شێوەی لیستێک پیشان بدە.
'''
        response = model.generate_content(prompt)
        await query.edit_message_text(
            response.text or "⚠️ زانیاری بەردەست نییە.",
            reply_markup=get_main_menu(),
        )
    except Exception as e:
        await query.edit_message_text(f"❌ هەڵە: {e}", reply_markup=get_main_menu())

async def ask_for_weight(query):
    await query.edit_message_text(
        "⚖️ تکایە کێشی کەسەکە بە کیلۆگرام بنووسە:\n\n"
        "نموونە: 70\n"
        "یان: 12.5\n\n"
        "(تەنها ژمارە بنووسە، بۆ هەڵوەشاندنەوە: /cancel)",
    )

async def handle_weight_input(update: Update, text: str):
    chat_id = update.effective_chat.id

    # ژمارە لە دەقەکە جیا بکەرەوە
    import re
    clean_text = re.sub(r"[^\d.]", "", text)
    try:
        weight = float(clean_text)
    except ValueError:
        weight = None

    if weight is None or weight <= 0 or weight > 300:
        await update.message.reply_text(
            "❌ تکایە کێکێکی دروست بنووسە (١-٣٠٠ کیلۆ).\n"
            "نموونە: 70\n\n"
            "بۆ هەڵوەشاندنەوە: /cancel"
        )
        return

    session = sessions[chat_id]
    session.state = UserState.IDLE

    await update.message.reply_text(
        f"⏳ خەریکی ئەژماری بری پێدانم بۆ کێشی {weight} کیلۆگرام..."
    )

    try:
        prompt = f'''
بۆ دەرمانی "{session.medicine_name}"، کەسێک کێشی {weight} کیلۆگرامە.
تکایە بری پێدانی دەرمانەکە بۆ ئەم کێشە ئەژمار بکە بە کوردی سۆرانی.

تکایە ڕوون بکەوە:
- چەند میلیگرام/گرام لە هەر دانەیەک
- چەن جار لە ڕۆژێک
- کاتی نێوان هەر دوو دەرمان (چەند کاتژمێر)
- ئایا پێش خواردنە یان دوای خواردنە
- ئایا لە کاتی نوستنەوەدا دەبێت یان نا
- ئاگادارییە تایبەتەکان بۆ ئەم تەمەنە/کێشە
'''
        response = model.generate_content(prompt)
        await update.message.reply_text(
            response.text or "⚠️ نەتوانرا ئەژمار بکرێت.",
            reply_markup=get_main_menu(),
        )
    except Exception as e:
        await update.message.reply_text(f"❌ هەڵە لە ئەژمار: {e}")

async def get_contraindications(query, medicine_name):
    await query.edit_message_text("⏳ خەریکی گەڕان بۆ قەدەغەکراوەکان...")

    try:
        prompt = f'''
تکایە تەنها قەدەغەکراوەکانی دەرمانی "{medicineName}" بە کوردی سۆرانی بنووسە:

١. 🚫 ئەو دەرمانانەی نابێت لەگەڵی بەکاربهێنرێت
٢. 🍽️ ئەو خۆراکانەی نابێت لە کاتی بەکارهێنانی ئەم دەرمانە بخوات
٣. ⚠️ ئەو نەخۆشیانەی نابێت بەکاری بهێنێت
٤. 👶 ئایا بۆ منداڵ یان دووگیان قەدەغەیە
'''
        response = model.generate_content(prompt)
        await query.edit_message_text(
            response.text or "⚠️ زانیاری بەردەست نییە.",
            reply_markup=get_main_menu(),
        )
    except Exception as e:
        await query.edit_message_text(f"❌ هەڵە: {e}", reply_markup=get_main_menu())

async def get_beneficial_foods(query, medicine_name):
    await query.edit_message_text("⏳ خەریکی گەڕان بۆ خۆراکە بەسودەکان...")

    try:
        prompt = f'''
تکایە تەنها خۆراکە بەسودەکان بۆ دەرمانی "{medicineName}" بە کوردی سۆرانی بنووسە:

١. 🥗 ئەو خۆراکانەی وا دەکات دەرمانەکە باشتر کار بکات
٢. 💊 ئەو ڤیتامین و معدنە کانەی پێویستە لەگەڵی بخوات
٣. 💧 ئایا ئاوی زۆر پێویستە
٤. 🍎 سەردەم و خۆراکی تایبەت
'''
        response = model.generate_content(prompt)
        await query.edit_message_text(
            response.text or "⚠️ زانیاری بەردەست نییە.",
            reply_markup=get_main_menu(),
        )
    except Exception as e:
        await query.edit_message_text(f"❌ هەڵە: {e}", reply_markup=get_main_menu())

async def get_mechanism_and_system(query, medicine_name):
    await query.edit_message_text("🧠 خەریکی شیکاری سیستەمی لەش و مێکانیزمی کارکردن...")

    try:
        prompt = f'''
تکایە زانیاری تەواو لەسەر دەرمانی "{medicineName}" بە کوردی سۆرانی بدە لەسەر:

🧠 **١. سیستەمی لەش (Body System):**
ئەم دەرمانە کام سیستەمی لەش کاری لەسەر دەکات؟ لەم لیستەی خوارەوە هەڵبژێرە:
- N/s = Nervous System (سیستەمی دەماری / مێشک و دەمار)
- C/v = Cardiovascular (سیستەمی دڵ و خوێنبەرەکان)
- G/I = Gastrointestinal (سیستەمی هەرسکردن)
- R/s = Respiratory System (سیستەمی هەناسەدان)
- R/L = Renal/Liver (گورچیلە و جەردە)
- E/n = Endocrine (سیستەمی هۆرمۆنی)
- M/s = Musculoskeletal (سیستەمی ئێسقان و ماسولکە)
- I/m = Immune System (سیستەمی بەرگری)
- D/m = Dermatological (پێست)
- G/U = Genitourinary (سیستەمی زاوزێ و میز)
- O/b = Obstetric (دووگیانی و زایمان)

⚗️ **٢. چینایەتی دەرمانی (Pharmacological Class):**
ئەم دەرمانە لە کام چینایەتی دەرمانیدایە؟ (وەک NSAID, Antibiotic, Beta-blocker, ACE inhibitor...)

🔬 **٣. مێکانیزمی کارکردن (Mechanism of Action):**
لە ئاستی مۆلیکولیدا چۆن کار دەکات؟ (وەک inhibition, receptor agonist/antagonist, enzyme blocker...)

🎯 **٤. ئامانجی کارکردن:**
چی ڕێگری لێ دەکات یان چی چاک دەکات لە ئاستی ژینگەی ناوخۆییدا؟

تکایە بە شێوەی ڕوون و تێگەیشتوو بنووسە.
'''
        response = model.generate_content(prompt)
        await query.edit_message_text(
            response.text or "⚠️ نەتوانرا زانیاری بدozzer.",
            reply_markup=get_main_menu(),
        )
    except Exception as e:
        await query.edit_message_text(f"❌ هەڵە لە گەڕان: {e}", reply_markup=get_main_menu())

# ==================== ٨. سەرەکی ====================
def main():
    application = Application.builder().token(BOT_TOKEN).build()

    # فەرمانەکان
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("cancel", cancel))

    # پەیامەکان
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    # کلیکی دوگمە
    application.add_handler(CallbackQueryHandler(button_callback))

    print("🚀 بۆتەکە بە سەرکەوتوویی دەستی بە کارکرد کرد...")
    application.run_polling()

if __name__ == "__main__":
    main()
