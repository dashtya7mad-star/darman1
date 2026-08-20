import os
import logging
import requests
import re
import random
from datetime import datetime
from enum import Enum
from dotenv import load_dotenv
from flask import Flask, request

# بارکردنی گۆڕاوەکان
load_dotenv()

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

# ==================== سیستەمی لۆگ ====================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ==================== تۆکن و کلیلەکان ====================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

# چەند کلیلی جیمینای (١٠ کلیل)
GEMINI_KEYS = []
for i in range(1, 11):
    key = os.environ.get(f"GEMINI_API_KEY_{i}", "")
    if key:
        GEMINI_KEYS.append(key)

if not BOT_TOKEN:
    logger.error("BOT_TOKEN not set!")
    exit(1)

if not GEMINI_KEYS:
    logger.error("No GEMINI_API_KEYs found!")
    exit(1)

# ==================== سیستەمی جیمینای ====================
def get_random_gemini_key():
    return random.choice(GEMINI_KEYS)

current_key = get_random_gemini_key()
genai.configure(api_key=current_key)
model = genai.GenerativeModel("gemini-3.5-flash")

# ==================== سیستەمی زمان ====================
user_langs = {}

class UserState(Enum):
    IDLE = "idle"
    WAITING_FOR_WEIGHT = "waiting_for_weight"
    WAITING_FOR_DRIP_VOLUME = "waiting_for_drip_volume"
    WAITING_FOR_DRIP_TIME = "waiting_for_drip_time"
    WAITING_FOR_DRIP_SET = "waiting_for_drip_set"
    WAITING_FOR_FLUID24_WEIGHT = "waiting_for_fluid24_weight"

class Session:
    def __init__(self, medicine_name="", medicine_info="", lang="ku"):
        self.medicine_name = medicine_name
        self.medicine_info = medicine_info
        self.state = UserState.IDLE
        self.lang = lang
        self.temp_data = {}

sessions = {}

# ==================== دەقەکان ====================
TEXTS = {
    "ku": {
        "start": "💊 سڵاو! من بۆتی پزیشکیم.\n\nزمانەکەت هەڵبژێرە:",
        "welcome": "💊 بەخێربێن! دەتوانیت:\n• ناوی دەرمانەکە بنووسیت\n• وێنەی دەرمانەکە بنێریت\n\nمن ناسینەوەی دەکەم و زانیاری تەواوت پێدەدەم! 🩺\n\nبۆ سڕینەوە: /cancel",
        "cancel": "🗑️ زانیارییەکان سڕانەوە. دەرمانێکی نوێ بنووسە یان وێنەی بنێرە.",
        "identifying": "⏳ خەریکی ناسینەوەی دەرمانەکەم...",
        "photo_scan": "📸 خەریکی شیکاری وێنەکەم...",
        "menu_title": "💊 دەرمان ناسێنرا: {name}\n\nهەڵبژاردنێک هەڵبژێرە:",
        "weight_prompt": "⚖️ تکایە کێشی کەسەکە بە کیلۆگرام بنووسە:\n\nنموونە: 70\n(بۆ هەڵوەشاندنەوە: /cancel)",
        "weight_error": "❌ تکایە کێشی دروست بنووسە (١-٣٠٠ کیلۆ).",
        "new_med": "🔄 دەرمانێکی نوێ بنووسە یان وێنەی بنێرە:",
        "no_session": "❌ تکایە سەرەتا ناوی دەرمانەکە بنووسە یان وێنەی بنێرە.",
        "limit_reached": "⛔ ئەمڕۆ گەیشتیتە سنووری ٢٠ پرسیار.\n\n🕐 سبەینێ دووبارە هەوڵبدەرەوە!",
        "remaining": "📊 ماوەت: {remaining} پرسیار لە ٢٠",
        "buttons": {
            "names": "🏷️ ناوەکانی تر",
            "dosage": "⚖️ پێدان بەپێی کێش",
            "contraindications": "🚫 قەدەغەکراوەکان",
            "iv_fluids": "💉 تێکەڵکردن لەگەل مغەزی",
            "iv_drip": "💧 خێرایی دڕۆپ (IV Drip)",
            "fluid_24h": "🕐 پێداویستی لەش بۆ مغەزی ٢٤ کاتژمێر",
            "mechanism": "🧠 سیستەم و مێکانیزم",
            "new": "🔄 دەرمانێکی تر",
        }
    },
    "ar": {
        "start": "💊 مرحباً! أنا بوت طبي.\n\nاختر لغتك:",
        "welcome": "💊 أهلاً بك! يمكنك:\n• كتابة اسم الدواء\n• إرسال صورة الدواء\n\nسأقوم بالتعرف عليه وإعطائك المعلومات الكاملة! 🩺\n\nللحذف: /cancel",
        "cancel": "🗑️ تم حذف المعلومات. اكتب دواء جديد أو أرسل صورة.",
        "identifying": "⏳ جاري التعرف على الدواء...",
        "photo_scan": "📸 جاري تحليل الصورة...",
        "menu_title": "💊 تم التعرف على: {name}\n\nاختر خياراً:",
        "weight_prompt": "⚖️ أدخل وزن الشخص بالكيلوغرام:\n\nمثال: 70\n(للإلغاء: /cancel)",
        "weight_error": "❌ أدخل وزناً صحيحاً (١-٣٠٠ كغ).",
        "new_med": "🔄 دواء جديد:",
        "no_session": "❌ الرجاء كتابة اسم الدواء أولاً.",
        "limit_reached": "⛔ لقد وصلت إلى الحد الأقصى اليوم (٢٠ سؤال).\n\n🕐 حاول مرة أخرى غداً!",
        "remaining": "📊 المتبقي: {remaining} سؤال من ٢٠",
        "buttons": {
            "names": "🏷️ الأسماء الأخرى",
            "dosage": "⚖️ الجرعة حسب الوزن",
            "contraindications": "🚫 الممنوعات",
            "iv_fluids": "💉 التوافق مع المحاليل الوريدية",
            "iv_drip": "💧 معدل التنقيط (IV Drip)",
            "fluid_24h": "🕐 احتياج ٢٤ ساعة",
            "mechanism": "🧠 الجهاز والآلية",
            "new": "🔄 دواء آخر",
        }
    },
    "en": {
        "start": "💊 Hello! I'm a medical bot.\n\nChoose your language:",
        "welcome": "💊 Welcome! You can:\n• Type medicine name\n• Send medicine photo\n\nI'll identify it and give you complete info! 🩺\n\nTo clear: /cancel",
        "cancel": "🗑️ Cleared. Type a new medicine or send a photo.",
        "identifying": "⏳ Identifying medicine...",
        "photo_scan": "📸 Analyzing photo...",
        "menu_title": "💊 Identified: {name}\n\nChoose an option:",
        "weight_prompt": "⚖️ Enter person's weight in kg:\n\nExample: 70\n(To cancel: /cancel)",
        "weight_error": "❌ Please enter valid weight (1-300 kg).",
        "new_med": "🔄 New medicine:",
        "no_session": "❌ Please type medicine name first.",
        "limit_reached": "⛔ You've reached today's limit (20 questions).\n\n🕐 Try again tomorrow!",
        "remaining": "📊 Remaining: {remaining} questions out of 20",
        "buttons": {
            "names": "🏷️ Other Names",
            "dosage": "⚖️ Dosage by Weight",
            "contraindications": "🚫 Contraindications",
            "iv_fluids": "💉 IV Fluid Compatibility",
            "iv_drip": "💧 IV Drip Rate",
            "fluid_24h": "🕐 24h Fluid Requirement",
            "mechanism": "🧠 System & Mechanism",
            "new": "🔄 Another Medicine",
        }
    }
}

# ==================== سنووری ڕۆژانە ====================
DAILY_LIMIT = 20
user_daily_counts = {}

def check_daily_limit(user_id: int) -> bool:
    today = datetime.now().strftime("%Y-%m-%d")
    if user_id not in user_daily_counts:
        user_daily_counts[user_id] = {"count": 1, "date": today}
        return True
    user_data = user_daily_counts[user_id]
    if user_data["date"] != today:
        user_daily_counts[user_id] = {"count": 1, "date": today}
        return True
    if user_data["count"] >= DAILY_LIMIT:
        return False
    user_data["count"] += 1
    return True

def get_remaining_questions(user_id: int) -> int:
    today = datetime.now().strftime("%Y-%m-%d")
    if user_id not in user_daily_counts:
        return DAILY_LIMIT
    user_data = user_daily_counts[user_id]
    if user_data["date"] != today:
        return DAILY_LIMIT
    return DAILY_LIMIT - user_data["count"]

# ==================== یارمەتی‌ده‌رەکان ====================
def get_text(key, lang="ku", **kwargs):
    text = TEXTS.get(lang, TEXTS["ku"]).get(key, key)
    if kwargs:
        text = text.format(**kwargs)
    return text

def get_main_menu(lang="ku"):
    b = TEXTS[lang]["buttons"]
    keyboard = [
        [InlineKeyboardButton(b["names"], callback_data="names")],
        [InlineKeyboardButton(b["dosage"], callback_data="dosage")],
        [InlineKeyboardButton(b["contraindications"], callback_data="contraindications")],
        [InlineKeyboardButton(b["iv_fluids"], callback_data="iv_fluids")],
        [InlineKeyboardButton(b["iv_drip"], callback_data="iv_drip")],
        [InlineKeyboardButton(b["fluid_24h"], callback_data="fluid_24h")],
        [InlineKeyboardButton(b["mechanism"], callback_data="mechanism")],
        [InlineKeyboardButton(b["new"], callback_data="new")],
    ]
    return InlineKeyboardMarkup(keyboard)

def get_lang_menu():
    keyboard = [
        [InlineKeyboardButton("🇮🇶 کوردی", callback_data="lang_ku")],
        [InlineKeyboardButton("🇸🇦 العربية", callback_data="lang_ar")],
        [InlineKeyboardButton("🇬🇧 English", callback_data="lang_en")],
    ]
    return InlineKeyboardMarkup(keyboard)

# ==================== Flask App ====================
app = Flask(__name__)

# دروستکردنی Telegram Application
application = Application.builder().token(BOT_TOKEN).build()

# ==================== Handlerەکان ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(TEXTS["ku"]["start"], reply_markup=get_lang_menu())

async def set_language(update: Update, context: ContextTypes.DEFAULT_TYPE, lang: str):
    chat_id = update.effective_chat.id
    user_langs[chat_id] = lang
    if chat_id not in sessions:
        sessions[chat_id] = Session(lang=lang)
    else:
        sessions[chat_id].lang = lang
    await update.callback_query.edit_message_text(get_text("welcome", lang))

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    lang = user_langs.get(chat_id, "ku")
    if chat_id in sessions:
        sessions[chat_id].state = UserState.IDLE
        sessions[chat_id].temp_data = {}
    await update.message.reply_text(get_text("cancel", lang))

async def identify_medicine_by_text(update: Update, text: str):
    chat_id = update.effective_chat.id
    lang = user_langs.get(chat_id, "ku")
    await update.message.reply_text(get_text("identifying", lang))
    
    try:
        if lang == "ku":
            prompt = f'''تکایە ئەم دەرمانە ناسینەوە بکە: "{text}"\nزمان: کوردی سۆرانی\n\n🟢 ناوی گشتی\n🟢 ناوی زانستی\n🟢 ناوی بازرگانی\n🟢 بەکارهێنان\n🟢 بری پێدان\n🟢 قەدەغەکراوەکان\n🟢 تێکەڵکردن لەگەڵ محاڵیلی وریدی\n🟢 ئاگادارییەکان'''
        elif lang == "ar":
            prompt = f'''يرجى التعرف على هذا الدواء: "{text}"\nاللغة: العربية\n\n🟢 الاسم العام\n🟢 الاسم العلمي\n🟢 الاسم التجاري\n🟢 الاستخدام\n🟢 الجرعة\n🟢 الممنوعات\n🟢 التوافق مع المحاليل الوريدية\n🟢 التحذيرات'''
        else:
            prompt = f'''Please identify this medicine: "{text}"\nLanguage: English\n\n🟢 Generic Name\n🟢 Scientific Name\n🟢 Brand Names\n🟢 Uses\n🟢 Dosage\n🟢 Contraindications\n🟢 IV Fluid Compatibility\n🟢 Important warnings'''
        
        current_key = get_random_gemini_key()
        genai.configure(api_key=current_key)
        response = model.generate_content(prompt)
        info = response.text or "⚠️"
        
        sessions[chat_id] = Session(medicine_name=text, medicine_info=info, lang=lang)
        await update.message.reply_text(get_text("menu_title", lang, name=text), reply_markup=get_main_menu(lang))
    except Exception as e:
        await update.message.reply_text(f"❌ {e}")

async def identify_medicine_by_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    lang = user_langs.get(chat_id, "ku")
    await update.message.reply_text(get_text("photo_scan", lang))
    
    try:
        photo = update.message.photo[-1]
        file = await context.bot.get_file(photo.file_id)
        response = requests.get(file.file_path)
        if response.status_code != 200:
            await update.message.reply_text("❌")
            return
        
        image_data = response.content
        
        if lang == "ku":
            prompt = '''ئەم وێنەیە دەرمانێکە. ناسینەوەی بکە بە کوردی سۆرانی:'''
        elif lang == "ar":
            prompt = '''هذه الصورة لدواء. قم بالتعرف عليه بالعربية:'''
        else:
            prompt = '''This image is a medicine. Identify it in English:'''
        
        image_part = {"mime_type": "image/jpeg", "data": image_data}
        current_key = get_random_gemini_key()
        genai.configure(api_key=current_key)
        gemini_response = model.generate_content([prompt, image_part])
        info = gemini_response.text or "⚠️"
        
        med_name = "نەناسراو"
        for line in info.split("\n")[:10]:
            clean = line.strip().lstrip("🟢١٢٣٤٥٦٧٨٩٠.-*# ")
            if clean and len(clean) > 2:
                med_name = clean
                break
        
        sessions[chat_id] = Session(medicine_name=med_name, medicine_info=info, lang=lang)
        await update.message.reply_text(get_text("menu_title", lang, name=med_name), reply_markup=get_main_menu(lang))
    except Exception as e:
        await update.message.reply_text(f"❌ {e}")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    text = update.message.text
    lang = user_langs.get(chat_id, "ku")
    
    if text.startswith("/"):
        return
    
    # پشکنینی سنووری ڕۆژانە
    if not check_daily_limit(user_id):
        await update.message.reply_text(get_text("limit_reached", lang))
        return
    
    if chat_id in sessions:
        state = sessions[chat_id].state
        if state == UserState.WAITING_FOR_WEIGHT:
            await handle_weight_input(update, text)
            return
        elif state == UserState.WAITING_FOR_DRIP_VOLUME:
            await handle_drip_volume_input(update, text)
            return
        elif state == UserState.WAITING_FOR_DRIP_TIME:
            await handle_drip_time_input(update, text)
            return
        elif state == UserState.WAITING_FOR_FLUID24_WEIGHT:
            await handle_fluid24_weight_input(update, text)
            return
    
    await identify_medicine_by_text(update, text)

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    lang = user_langs.get(chat_id, "ku")
    
    # پشکنینی سنووری ڕۆژانە
    if not check_daily_limit(user_id):
        await update.message.reply_text(get_text("limit_reached", lang))
        return
    
    await identify_medicine_by_photo(update, context)

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = update.effective_chat.id
    data = query.data
    lang = user_langs.get(chat_id, "ku")
    
    if data.startswith("lang_"):
        selected_lang = data.split("_")[1]
        await set_language(update, context, selected_lang)
        return
    
    session = sessions.get(chat_id)
    if not session:
        session = Session(lang=lang)
        sessions[chat_id] = session
    
    if data == "names":
        await get_other_names(query, session, lang)
    elif data == "dosage":
        await ask_for_weight(query, session, lang)
    elif data == "contraindications":
        await get_contraindications(query, session, lang)
    elif data == "iv_fluids":
        await get_iv_fluids(query, session, lang)
    elif data == "iv_drip":
        await ask_drip_volume(query, session, lang)
    elif data == "fluid_24h":
        await ask_fluid24_weight(query, session, lang)
    elif data == "mechanism":
        await get_mechanism_and_system(query, session, lang)
    elif data == "new":
        session.medicine_name = ""
        session.medicine_info = ""
        session.state = UserState.IDLE
        session.temp_data = {}
        await query.edit_message_text(get_text("new_med", lang))
    elif data == "drip_macro":
        await handle_drip_set(query, session, lang, 20)
    elif data == "drip_micro":
        await handle_drip_set(query, session, lang, 60)

# ==================== Handlerەکانی تر ====================
async def get_other_names(query, session, lang):
    await query.edit_message_text("⏳ ...")
    try:
        if lang == "ku":
            prompt = f'تکایە تەنها ناوی زانستی و ناوی بازرگانی دەرمانی "{session.medicine_name}" بە کوردی سۆرانی بنووسە.'
        elif lang == "ar":
            prompt = f'اكتب فقط الاسم العلمي والتجاري للدواء "{session.medicine_name}" بالعربية.'
        else:
            prompt = f'Write only scientific and brand names of "{session.medicine_name}" in English.'
        
        current_key = get_random_gemini_key()
        genai.configure(api_key=current_key)
        response = model.generate_content(prompt)
        await query.edit_message_text(response.text or "⚠️", reply_markup=get_main_menu(lang))
    except Exception as e:
        await query.edit_message_text(f"❌ {e}", reply_markup=get_main_menu(lang))

async def ask_for_weight(query, session, lang):
    await query.edit_message_text(get_text("weight_prompt", lang))
    session.state = UserState.WAITING_FOR_WEIGHT

async def handle_weight_input(update: Update, text: str):
    chat_id = update.effective_chat.id
    session = sessions.get(chat_id)
    if not session:
        return
    lang = session.lang
    
    clean_text = re.sub(r"[^\d.]", "", text)
    try:
        weight = float(clean_text)
    except ValueError:
        weight = None
    
    if weight is None or weight <= 0 or weight > 300:
        await update.message.reply_text(get_text("weight_error", lang))
        return
    
    session.state = UserState.IDLE
    await update.message.reply_text("⏳ ...")
    
    try:
        if lang == "ku":
            prompt = f'''بۆ دەرمانی "{session.medicine_name}"، کەسێک کێشی {weight} کیلۆگرامە.\nبری پێدانی دەرمانەکە ئەژمار بکە بە کوردی سۆرانی:'''
        elif lang == "ar":
            prompt = f'''لدواء "{session.medicine_name}"، شخص وزنه {weight} كغ.\nاحسب الجرعة بالعربية:'''
        else:
            prompt = f'''For "{session.medicine_name}", person weighs {weight}kg.\nCalculate dosage in English:'''
        
        current_key = get_random_gemini_key()
        genai.configure(api_key=current_key)
        response = model.generate_content(prompt)
        await update.message.reply_text(response.text or "⚠️", reply_markup=get_main_menu(lang))
    except Exception as e:
        await update.message.reply_text(f"❌ {e}")

async def get_contraindications(query, session, lang):
    await query.edit_message_text("⏳ ...")
    try:
        if lang == "ku":
            prompt = f'''قەدەغەکراوەکانی دەرمانی "{session.medicine_name}" بە کوردی سۆرانی:'''
        elif lang == "ar":
            prompt = f'''ممنوعات دواء "{session.medicine_name}" بالعربية:'''
        else:
            prompt = f'''Contraindications of "{session.medicine_name}" in English:'''
        
        current_key = get_random_gemini_key()
        genai.configure(api_key=current_key)
        response = model.generate_content(prompt)
        await query.edit_message_text(response.text or "⚠️", reply_markup=get_main_menu(lang))
    except Exception as e:
        await query.edit_message_text(f"❌ {e}", reply_markup=get_main_menu(lang))

async def get_iv_fluids(query, session, lang):
    await query.edit_message_text("💉 ...")
    try:
        if lang == "ku":
            prompt = f'''تێکەڵکردنی دەرمانی "{session.medicine_name}" لەگەڵ مغەزی بە کوردی سۆرانی:'''
        elif lang == "ar":
            prompt = f'''هل يمكن خلط دواء "{session.medicine_name}" مع المحاليل الوريدية؟ بالعربية:'''
        else:
            prompt = f'''Can "{session.medicine_name}" be mixed with IV fluids? In English:'''
        
        current_key = get_random_gemini_key()
        genai.configure(api_key=current_key)
        response = model.generate_content(prompt)
        await query.edit_message_text(response.text or "⚠️", reply_markup=get_main_menu(lang))
    except Exception as e:
        await query.edit_message_text(f"❌ {e}", reply_markup=get_main_menu(lang))

async def get_mechanism_and_system(query, session, lang):
    await query.edit_message_text("🧠 ...")
    try:
        if lang == "ku":
            prompt = f'''سیستەمی لەش و مێکانیزمی دەرمانی "{session.medicine_name}" بە کوردی سۆرانی:'''
        elif lang == "ar":
            prompt = f'''جهاز الجسم وآلية دواء "{session.medicine_name}" بالعربية:'''
        else:
            prompt = f'''Body system and mechanism of "{session.medicine_name}" in English:'''
        
        current_key = get_random_gemini_key()
        genai.configure(api_key=current_key)
        response = model.generate_content(prompt)
        await query.edit_message_text(response.text or "⚠️", reply_markup=get_main_menu(lang))
    except Exception as e:
        await query.edit_message_text(f"❌ {e}", reply_markup=get_main_menu(lang))

async def ask_drip_volume(query, session, lang):
    session.state = UserState.WAITING_FOR_DRIP_VOLUME
    session.temp_data = {}
    if lang == "ku":
        text = "💧 ڕێکخستنی خێرایی دڕۆپ\n\nتکایە جۆری مغەزی و بری بە میلی لیتر بنووسە:\n\n🟢 نموونە: N/S 500"
    elif lang == "ar":
        text = "💧 حساب معدل التنقيط\n\nاكتب نوع المحلول والكمية بالملليتر:\n\n🟢 مثال: N/S 500"
    else:
        text = "💧 IV Drip Rate Calculator\n\nEnter fluid type and volume in mL:\n\n🟢 Example: N/S 500"
    await query.edit_message_text(text)

async def handle_drip_volume_input(update: Update, text: str):
    chat_id = update.effective_chat.id
    session = sessions.get(chat_id)
    if not session:
        return
    lang = session.lang
    
    parts = text.split()
    if len(parts) < 2:
        await update.message.reply_text("❌ تکایە بەم فۆرمەتە بنووسە: N/S 500")
        return
    
    fluid = parts[0].upper()
    try:
        volume = float(parts[1].replace("ml", "").replace("mL", "").replace("ML", ""))
    except ValueError:
        await update.message.reply_text("❌ تکایە ژمارەی دروست بنووسە.")
        return
    
    session.temp_data["drip_fluid"] = fluid
    session.temp_data["drip_volume"] = volume
    session.state = UserState.WAITING_FOR_DRIP_TIME
    
    if lang == "ku":
        msg = f"🟢 جۆر: {fluid}\n🟢 بری: {volume} mL\n\nچەند سەعات دەبێت تێپەڕێت؟"
    elif lang == "ar":
        msg = f"🟢 النوع: {fluid}\n🟢 الكمية: {volume} mL\n\nكم ساعة يجب أن تمر؟"
    else:
        msg = f"🟢 Type: {fluid}\n🟢 Volume: {volume} mL\n\nHow many hours should it run?"
    
    await update.message.reply_text(msg)

async def handle_drip_time_input(update: Update, text: str):
    chat_id = update.effective_chat.id
    session = sessions.get(chat_id)
    if not session:
        return
    lang = session.lang
    
    try:
        time_hrs = float(text.replace("h", "").replace("s", "").replace("س", "").strip())
    except ValueError:
        await update.message.reply_text("❌ تکایە ژمارەی دروست بنووسە.")
        return
    
    session.temp_data["drip_time"] = time_hrs
    session.state = UserState.WAITING_FOR_DRIP_SET
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Macro drip (20 gtt/mL)", callback_data="drip_macro")],
        [InlineKeyboardButton("Micro drip (60 gtt/mL)", callback_data="drip_micro")],
    ])
    
    if lang == "ku":
        msg = f"🟢 کات: {time_hrs} سەعات\n\nجۆری IV Set هەڵبژێرە:"
    elif lang == "ar":
        msg = f"🟢 الوقت: {time_hrs} ساعة\n\nاختر نوع IV Set:"
    else:
        msg = f"🟢 Time: {time_hrs} hours\n\nSelect IV Set type:"
    
    await update.message.reply_text(msg, reply_markup=keyboard)

async def handle_drip_set(query, session, lang, drop_factor):
    volume = session.temp_data.get("drip_volume", 0)
    time_hrs = session.temp_data.get("drip_time", 0)
    fluid = session.temp_data.get("drip_fluid", "")
    time_min = time_hrs * 60
    
    if time_min <= 0:
        await query.edit_message_text("❌ کات نادروستە.")
        return
    
    gtt_per_min = (volume * drop_factor) / time_min
    ml_per_hour = volume / time_hrs
    
    session.state = UserState.IDLE
    session.temp_data = {}
    
    if lang == "ku":
        result = f"""💉 ڕێکخستنی دڕۆپ:\n\n🟢 جۆری موغەزی: {fluid}\n🟢 بری: {volume} mL\n🟢 کات: {time_hrs} سەعات\n🟢 IV Set: {'Macro' if drop_factor == 20 else 'Micro'}\n\n✅ خێرایی: {gtt_per_min:.1f} دڕۆپ/خولەک\n✅ خێرایی: {ml_per_hour:.1f} mL/سەعات"""
    elif lang == "ar":
        result = f"""💧 معدل التنقيط:\n\n🟢 نوع المحلول: {fluid}\n🟢 الكمية: {volume} mL\n🟢 الوقت: {time_hrs} ساعة\n🟢 IV Set: {'Macro' if drop_factor == 20 else 'Micro'}\n\n✅ المعدل: {gtt_per_min:.1f} نقطة/دقيقة"""
    else:
        result = f"""💧 IV Drip Rate:\n\n🟢 Fluid: {fluid}\n🟢 Volume: {volume} mL\n🟢 Time: {time_hrs} hrs\n🟢 IV Set: {'Macro' if drop_factor == 20 else 'Micro'}\n\n✅ Rate: {gtt_per_min:.1f} drops/min"""
    
    await query.edit_message_text(result, reply_markup=get_main_menu(lang))

async def ask_fluid24_weight(query, session, lang):
    session.state = UserState.WAITING_FOR_FLUID24_WEIGHT
    if lang == "ku":
        text = "🕐 پێداویستی لەش بۆ موغەزی لە ٢٤ کاتژمێر\n\nکێشی نەخۆشکەکە بە کیلۆگرام بنووسە:\n\nنموونە: 70"
    elif lang == "ar":
        text = "🕐 احتياج السوائل لـ ٢٤ ساعة\n\nأدخل وزن المريض بالكيلوغرام:\n\nمثال: 70"
    else:
        text = "🕐 24-Hour Fluid Requirement\n\nEnter patient weight in kg:\n\nExample: 70"
    await query.edit_message_text(text)

async def handle_fluid24_weight_input(update: Update, text: str):
    chat_id = update.effective_chat.id
    session = sessions.get(chat_id)
    if not session:
        return
    lang = session.lang
    
    try:
        weight = float(text.strip())
    except ValueError:
        await update.message.reply_text("❌ تکایە کێشی دروست بنووسە.")
        return
    
    if weight <= 0 or weight > 300:
        await update.message.reply_text("❌ کێشی دروست بنووسە (١-٣٠٠ کیلۆ)")
        return
    
    if weight <= 10:
        daily = weight * 100
        formula = f"{weight} × 100 = {daily}"
    elif weight <= 20:
        daily = 1000 + (weight - 10) * 50
        formula = f"1000 + ({weight} - 10) × 50 = {daily}"
    else:
        daily = 1500 + (weight - 20) * 20
        formula = f"1500 + ({weight} - 20) × 20 = {daily}"
    
    hourly = daily / 24
    half_hourly = hourly / 2
    
    session.state = UserState.IDLE
    
    if lang == "ku":
        result = f"""💧 پێداویستی لەش بو موغەزی لە  ٢٤ کاتژمێر:\n\n🟢 کێش: {weight} کیلۆگرام\n🟢 بری ٢٤ کاتژمێر: {daily:.0f} mL\n🟢 بری هەر کاتژمێر: {hourly:.1f} mL/hr\n🟢 بری هەر نیو کاتژمێر: {half_hourly:.1f} mL/30min\n\n📋 فۆرمولەی Holliday-Segar:\n{formula}"""
    elif lang == "ar":
        result = f"""💧 احتياج السوائل لـ ٢٤ ساعة:\n\n🟢 الوزن: {weight} كغ\n🟢 الكمية اليومية: {daily:.0f} mL\n🟢 في الساعة: {hourly:.1f} mL/hr\n\n📋 معادلة Holliday-Segar:\n{formula}"""
    else:
        result = f"""💧 24-Hour Fluid Requirement:\n\n🟢 Weight: {weight} kg\n🟢 Daily volume: {daily:.0f} mL\n🟢 Hourly rate: {hourly:.1f} mL/hr\n\n📋 Holliday-Segar Formula:\n{formula}"""
    
    await update.message.reply_text(result, reply_markup=get_main_menu(lang))

# ==================== دانانی Handlerەکان ====================
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("cancel", cancel))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
application.add_handler(CallbackQueryHandler(button_callback))

# ==================== Webhook Routes ====================
@app.route(f'/{BOT_TOKEN}', methods=['POST'])
def webhook():
    update = Update.de_json(request.get_json(force=True), application.bot)
    application.process_update(update)
    return 'OK'

@app.route('/')
def home():
    return "🚀 Medical Bot is running!"

# ==================== ڕێکخستنی Webhook ====================
def set_webhook():
    webhook_url = f"https://Dashtya7mad.pythonanywhere.com/{BOT_TOKEN}"
    application.bot.set_webhook(url=webhook_url)
    logger.info(f"Webhook set to: {webhook_url}")

if __name__ == "__main__":
    set_webhook()
    app.run()
