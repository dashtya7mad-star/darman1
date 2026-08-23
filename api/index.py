import os
import json
import logging
import random
import re
import time
from datetime import datetime
from enum import Enum
from http.server import BaseHTTPRequestHandler

import requests
import google.generativeai as genai
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup

# ==================== إعدادات اللوج ====================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== التوكن والمفاتيح ====================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()

GEMINI_KEYS = []
for i in range(1, 11):  # ١٠ كليله
    key = os.environ.get(f"GEMINI_API_KEY_{i}", "").strip()
    if key:
        GEMINI_KEYS.append(key)

if not BOT_TOKEN or not GEMINI_KEYS:
    logger.error("Missing BOT_TOKEN or GEMINI keys!")
    raise ValueError("Missing required environment variables")

logger.info(f"Loaded {len(GEMINI_KEYS)} Gemini keys")

# ==================== نظام Gemini المحسّن ====================
class GeminiKeyManager:
    def __init__(self, keys):
        self.keys = keys
        self.key_index = 0
        self.failed_keys = {}  # کلیل + کاتی شکست
        self.cooldown_seconds = 3600  # 1 ساعة cooldown
    
    def get_next_key(self):
        """گرتنی کلیلی داهاتوو کە کار بکات"""
        now = time.time()
        
        # پاککردنەوەی کلیلە کۆنە شکستخواردووەکان
        expired = [k for k, t in self.failed_keys.items() if now - t > self.cooldown_seconds]
        for k in expired:
            del self.failed_keys[k]
            logger.info(f"Key reactivated after cooldown: {k[:15]}...")
        
        # دۆزینەوەی کلیلی کار
        available = [k for k in self.keys if k not in self.failed_keys]
        
        if not available:
            logger.error("NO AVAILABLE KEYS! All keys failed or on cooldown.")
            return None
        
        key = available[self.key_index % len(available)]
        self.key_index += 1
        return key
    
    def mark_failed(self, key, error_msg=""):
        """نیشانەکردنی کلیلێک وەک شکستخواردوو"""
        self.failed_keys[key] = time.time()
        logger.warning(f"Key marked failed: {key[:15]}... | Error: {error_msg[:50]}")
    
    def get_stats(self):
        """ئاماری کلیلەکان"""
        total = len(self.keys)
        failed = len(self.failed_keys)
        available = total - failed
        return f"کلیل: {available}/{total} کار دەکەن | {failed} لە cooldown"

key_manager = GeminiKeyManager(GEMINI_KEYS)

def configure_gemini_with_key(key):
    """ڕێکخستنی Gemini بە کلیلێکی نوێ"""
    if not key:
        return None
    genai.configure(api_key=key)
    return genai.GenerativeModel("gemini-3.5-flash")

# ==================== دوال آمنة لإنشاء المحتوى ====================
async def safe_gemini_generate(prompt, image_part=None, max_retries_per_key=2):
    """
    فەرمانی پارێزراو بۆ بانگکردنی Gemini
    ئەگەر کلیلێک شکستی هێنا، کلیلێکی دیکە تاقی دەکاتەوە
    """
    last_error = None
    
    for attempt in range(len(GEMINI_KEYS) * max_retries_per_key):
        key = key_manager.get_next_key()
        
        if not key:
            raise Exception("⛔ هیچ کلیلێک بەردەست نییە!\n\n"
                         "هەموو کلیلەکان گەیشتوونەتە سنواری ڕۆژانە.\n"
                         "🕐 تکایە ١ کاتژمێر چاوەڕوانی بکە یان کلیلی نوێ زیاد بکە.")
        
        try:
            model = configure_gemini_with_key(key)
            if not model:
                continue
            
            # بانگکردنی API
            if image_part:
                response = model.generate_content([prompt, image_part])
            else:
                response = model.generate_content(prompt)
            
            text = response.text
            if text and len(text) > 0:
                logger.info(f"Success with key: {key[:15]}... | Stats: {key_manager.get_stats()}")
                return text
            
        except Exception as e:
            error_str = str(e)
            last_error = error_str
            
            # ئەگەر هەڵەی 429 یان quota
            if any(x in error_str for x in ["429", "quota", "exceeded", "limit", "Rate"]):
                key_manager.mark_failed(key, error_str)
                wait_time = min(2 ** (attempt % 5), 30)  # exponential backoff, max 30s
                logger.info(f"429 error, trying next key in {wait_time}s... | Attempt {attempt+1}")
                time.sleep(wait_time)
                continue
            
            # هەڵەی تری API
            elif any(x in error_str for x in ["400", "401", "403", "invalid", "API key"]):
                key_manager.mark_failed(key, error_str)
                logger.error(f"Invalid key or API error: {key[:15]}... | {error_str[:100]}")
                continue
            
            # هەڵەی نenasaf (network, etc)
            else:
                logger.warning(f"Unexpected error: {error_str[:100]}")
                time.sleep(1)
                continue
    
    # هەموو هەوڵەکان شکستیان هێنا
    raise Exception(f"⛔ هەموو کلیلەکان شکستیان هێنا!\n\n"
                   f"هەڵەی کۆتایی: {str(last_error)[:200]}\n\n"
                   f"ئامار: {key_manager.get_stats()}\n\n"
                   f"🕐 تکایە دواتر دووبارە هەوڵبدەرەوە.")

# ==================== نظام اللغة ====================
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

# ==================== النصوص ====================
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
        "limit_reached": "⛔ ئەمڕۆ گەیشتیتە سنواری ٢٠ پرسیار.\n\n🕐 سبەینێ دووبارە هەوڵبدەرەوە!",
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

# ==================== الحد اليومي ====================
DAILY_LIMIT = 200
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

# ==================== مساعدات ====================
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

# ==================== دوال معالجة التحديثات ====================
async def process_update(update_dict):
    """معالجة تحديث Telegram"""
    update = Update.de_json(update_dict, None)
    chat_id = update.effective_chat.id if update.effective_chat else None
    user_id = update.effective_user.id if update.effective_user else None
    message = update.message
    callback_query = update.callback_query
    
    if not chat_id:
        return {"status": "no_chat"}
    
    # تحديد اللغة
    lang = user_langs.get(chat_id, "ku")
    
    # معالجة الأوامر
    if message and message.text:
        text = message.text
        
        if text == "/start":
            await send_message(chat_id, TEXTS["ku"]["start"], get_lang_menu())
            return {"status": "start"}
        
        if text == "/cancel":
            if chat_id in sessions:
                sessions[chat_id].state = UserState.IDLE
                sessions[chat_id].temp_data = {}
            await send_message(chat_id, get_text("cancel", lang))
            return {"status": "cancel"}
        
        # التحقق من الحد اليومي
        if user_id and not check_daily_limit(user_id):
            await send_message(chat_id, get_text("limit_reached", lang))
            return {"status": "limit_reached"}
        
        # معالجة حالات الانتظار
        if chat_id in sessions:
            state = sessions[chat_id].state
            if state == UserState.WAITING_FOR_WEIGHT:
                await handle_weight_input(chat_id, text, lang)
                return {"status": "weight_handled"}
            elif state == UserState.WAITING_FOR_DRIP_VOLUME:
                await handle_drip_volume_input(chat_id, text, lang)
                return {"status": "drip_volume_handled"}
            elif state == UserState.WAITING_FOR_DRIP_TIME:
                await handle_drip_time_input(chat_id, text, lang)
                return {"status": "drip_time_handled"}
            elif state == UserState.WAITING_FOR_FLUID24_WEIGHT:
                await handle_fluid24_weight_input(chat_id, text, lang)
                return {"status": "fluid24_handled"}
        
        # نص عادي - البحث عن دواء
        await identify_medicine_by_text(chat_id, text, lang)
        return {"status": "identify"}
    
    # معالجة الصور
    if message and message.photo:
        if user_id and not check_daily_limit(user_id):
            await send_message(chat_id, get_text("limit_reached", lang))
            return {"status": "limit_reached"}
        await identify_medicine_by_photo(chat_id, message, lang)
        return {"status": "photo"}
    
    # معالجة الأزرار
    if callback_query:
        await process_callback(callback_query, chat_id, lang)
        return {"status": "callback"}
    
    return {"status": "unknown"}

async def send_message(chat_id, text, reply_markup=None):
    """إرسال رسالة عبر Telegram API"""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML"
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup.to_json()
    
    requests.post(url, json=payload)

async def edit_message(chat_id, message_id, text, reply_markup=None):
    """تعديل رسالة"""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText"
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML"
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup.to_json()
    
    requests.post(url, json=payload)

async def answer_callback(callback_query_id):
    """الرد على callback"""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/answerCallbackQuery"
    requests.post(url, json={"callback_query_id": callback_query_id})

# ==================== دوال البوت ====================
async def identify_medicine_by_text(chat_id, text, lang):
    await send_message(chat_id, get_text("identifying", lang))
    
    try:
        if lang == "ku":
            prompt = f"""تکایە ئەم دەرمانە ناسینەوە بکە: "{text}"
زمان: کوردی سۆرانی

🟢 ناوی گشتی
🟢 ناوی زانستی
🟢 ناوی بازرگانی
🟢 بەکارهێنان
🟢 بری پێدان
🟢 قەدەغەکراوەکان
🟢 تێکەڵکردن لەگەڵ محاڵیلی وریدی
🟢 ئاگادارییەکان"""
        elif lang == "ar":
            prompt = f"""يرجى التعرف على هذا الدواء: "{text}"
اللغة: العربية

🟢 الاسم العام
🟢 الاسم العلمي
🟢 الاسم التجاري
🟢 الاستخدام
🟢 الجرعة
🟢 الممنوعات
🟢 التوافق مع المحاليل الوريدية
🟢 التحذيرات"""
        else:
            prompt = f"""Please identify this medicine: "{text}"
Language: English

🟢 Generic Name
🟢 Scientific Name
🟢 Brand Names
🟢 Uses
🟢 Dosage
🟢 Contraindications
🟢 IV Fluid Compatibility
🟢 Important warnings"""

        info = await safe_gemini_generate(prompt)
        
        sessions[chat_id] = Session(medicine_name=text, medicine_info=info, lang=lang)
        await send_message(chat_id, get_text("menu_title", lang, name=text), get_main_menu(lang))
    except Exception as e:
        logger.error(f"Error: {e}")
        await send_message(chat_id, f"❌ هەڵە: {str(e)}")

async def identify_medicine_by_photo(chat_id, message, lang):
    await send_message(chat_id, get_text("photo_scan", lang))
    
    try:
        photo = message.photo[-1]
        file_id = photo.file_id
        
        # تحميل الصورة
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/getFile?file_id={file_id}"
        resp = requests.get(url).json()
        file_path = resp["result"]["file_path"]
        
        img_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
        img_data = requests.get(img_url).content
        
        if lang == "ku":
            prompt = "ئەم وێنەیە دەرمانێکە. ناسینەوەی بکە بە کوردی سۆرانی:"
        elif lang == "ar":
            prompt = "هذه الصورة لدواء. قم بالتعرف عليه بالعربية:"
        else:
            prompt = "This image is a medicine. Identify it in English:"
        
        image_part = {"mime_type": "image/jpeg", "data": img_data}
        info = await safe_gemini_generate(prompt, image_part=image_part)
        
        med_name = "نەناسراو"
        for line in info.split("\n")[:10]:
            clean = line.strip().lstrip("🟢١٢٣٤٥٦٧٨٩٠.-*# ")
            if clean and len(clean) > 2:
                med_name = clean
                break
        
        sessions[chat_id] = Session(medicine_name=med_name, medicine_info=info, lang=lang)
        await send_message(chat_id, get_text("menu_title", lang, name=med_name), get_main_menu(lang))
    except Exception as e:
        logger.error(f"Error: {e}")
        await send_message(chat_id, f"❌ هەڵە: {str(e)}")

async def process_callback(callback_query, chat_id, lang):
    await answer_callback(callback_query.id)
    data = callback_query.data
    
    if data.startswith("lang_"):
        selected_lang = data.split("_")[1]
        user_langs[chat_id] = selected_lang
        if chat_id not in sessions:
            sessions[chat_id] = Session(lang=selected_lang)
        else:
            sessions[chat_id].lang = selected_lang
        await edit_message(chat_id, callback_query.message.message_id, get_text("welcome", selected_lang))
        return
    
    session = sessions.get(chat_id)
    if not session:
        session = Session(lang=lang)
        sessions[chat_id] = session
    
    if data == "names":
        await get_other_names(chat_id, session, lang)
    elif data == "dosage":
        await ask_for_weight(chat_id, session, lang)
    elif data == "contraindications":
        await get_contraindications(chat_id, session, lang)
    elif data == "iv_fluids":
        await get_iv_fluids(chat_id, session, lang)
    elif data == "iv_drip":
        await ask_drip_volume(chat_id, session, lang)
    elif data == "fluid_24h":
        await ask_fluid24_weight(chat_id, session, lang)
    elif data == "mechanism":
        await get_mechanism_and_system(chat_id, session, lang)
    elif data == "new":
        session.medicine_name = ""
        session.medicine_info = ""
        session.state = UserState.IDLE
        session.temp_data = {}
        await edit_message(chat_id, callback_query.message.message_id, get_text("new_med", lang))
    elif data == "drip_macro":
        await handle_drip_set(chat_id, session, lang, 20)
    elif data == "drip_micro":
        await handle_drip_set(chat_id, session, lang, 60)

# ==================== دوال إضافية ====================
async def get_other_names(chat_id, session, lang):
    try:
        if lang == "ku":
            prompt = f'تکایە تەنها ناوی زانستی و ناوی بازرگانی دەرمانی "{session.medicine_name}" بە کوردی سۆرانی بنووسە.'
        elif lang == "ar":
            prompt = f'اكتب فقط الاسم العلمي والتجاري للدواء "{session.medicine_name}" بالعربية.'
        else:
            prompt = f'Write only scientific and brand names of "{session.medicine_name}" in English.'

        response_text = await safe_gemini_generate(prompt)
        await send_message(chat_id, response_text, get_main_menu(lang))
    except Exception as e:
        await send_message(chat_id, f"❌ هەڵە: {str(e)}", get_main_menu(lang))

async def ask_for_weight(chat_id, session, lang):
    await send_message(chat_id, get_text("weight_prompt", lang))
    session.state = UserState.WAITING_FOR_WEIGHT

async def handle_weight_input(chat_id, text, lang):
    session = sessions.get(chat_id)
    if not session:
        return
    
    clean_text = re.sub(r"[^\d.]", "", text)
    try:
        weight = float(clean_text)
    except ValueError:
        weight = None
    
    if weight is None or weight <= 0 or weight > 300:
        await send_message(chat_id, get_text("weight_error", lang))
        return
    
    session.state = UserState.IDLE
    
    try:
        if lang == "ku":
            prompt = f"""بۆ دەرمانی "{session.medicine_name}"، کەسێک کێشی {weight} کیلۆگرامە.
بری پێدانی دەرمانەکە ئەژمار بکە بە کوردی سۆرانی:"""
        elif lang == "ar":
            prompt = f"""لدواء "{session.medicine_name}"، شخص وزنه {weight} كغ.
احسب الجرعة بالعربية:"""
        else:
            prompt = f"""For "{session.medicine_name}", person weighs {weight}kg.
Calculate dosage in English:"""

        response_text = await safe_gemini_generate(prompt)
        await send_message(chat_id, response_text, get_main_menu(lang))
    except Exception as e:
        await send_message(chat_id, f"❌ هەڵە: {str(e)}", get_main_menu(lang))

async def get_contraindications(chat_id, session, lang):
    try:
        if lang == "ku":
            prompt = f'قەدەغەکراوەکانی دەرمانی "{session.medicine_name}" بە کوردی سۆرانی:'
        elif lang == "ar":
            prompt = f'ممنوعات دواء "{session.medicine_name}" بالعربية:'
        else:
            prompt = f'Contraindications of "{session.medicine_name}" in English:'

        response_text = await safe_gemini_generate(prompt)
        await send_message(chat_id, response_text, get_main_menu(lang))
    except Exception as e:
        await send_message(chat_id, f"❌ هەڵە: {str(e)}", get_main_menu(lang))

async def get_iv_fluids(chat_id, session, lang):
    try:
        if lang == "ku":
            prompt = f'تێکەڵکردنی دەرمانی "{session.medicine_name}" لەگەڵ مغەزی بە کوردی سۆرانی:'
        elif lang == "ar":
            prompt = f'هل يمكن خلط دواء "{session.medicine_name}" مع المحاليل الوريدية؟ بالعربية:'
        else:
            prompt = f'Can "{session.medicine_name}" be mixed with IV fluids? In English:'

        response_text = await safe_gemini_generate(prompt)
        await send_message(chat_id, response_text, get_main_menu(lang))
    except Exception as e:
        await send_message(chat_id, f"❌ هەڵە: {str(e)}", get_main_menu(lang))

async def get_mechanism_and_system(chat_id, session, lang):
    try:
        if lang == "ku":
            prompt = f'سیستەمی لەش و مێکانیزمی دەرمانی "{session.medicine_name}" بە کوردی سۆرانی:'
        elif lang == "ar":
            prompt = f'جهاز الجسم وآلية دواء "{session.medicine_name}" بالعربية:'
        else:
            prompt = f'Body system and mechanism of "{session.medicine_name}" in English:'

        response_text = await safe_gemini_generate(prompt)
        await send_message(chat_id, response_text, get_main_menu(lang))
    except Exception as e:
        await send_message(chat_id, f"❌ هەڵە: {str(e)}", get_main_menu(lang))

async def ask_drip_volume(chat_id, session, lang):
    session.state = UserState.WAITING_FOR_DRIP_VOLUME
    session.temp_data = {}
    if lang == "ku":
        text = "💧 ڕێکخستنی خێرایی دڕۆپ\n\nتکایە جۆری مغەزی و بری بە میلی لیتر بنووسە:\n\n🟢 نموونە: N/S 500"
    elif lang == "ar":
        text = "💧 حساب معدل التنقيط\n\nاكتب نوع المحلول والكمية بالملليتر:\n\n🟢 مثال: N/S 500"
    else:
        text = "💧 IV Drip Rate Calculator\n\nEnter fluid type and volume in mL:\n\n🟢 Example: N/S 500"
    await send_message(chat_id, text)

async def handle_drip_volume_input(chat_id, text, lang):
    session = sessions.get(chat_id)
    if not session:
        return
    
    parts = text.split()
    if len(parts) < 2:
        await send_message(chat_id, "❌ تکایە بەم فۆرمەتە بنووسە: N/S 500")
        return
    
    fluid = parts[0].upper()
    try:
        volume = float(parts[1].replace("ml", "").replace("mL", "").replace("ML", ""))
    except ValueError:
        await send_message(chat_id, "❌ تکایە ژمارەی دروست بنووسە.")
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
    
    await send_message(chat_id, msg)

async def handle_drip_time_input(chat_id, text, lang):
    session = sessions.get(chat_id)
    if not session:
        return
    
    try:
        time_hrs = float(text.replace("h", "").replace("s", "").replace("س", "").strip())
    except ValueError:
        await send_message(chat_id, "❌ تکایە ژمارەی دروست بنووسە.")
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
    
    await send_message(chat_id, msg, keyboard)

async def handle_drip_set(chat_id, session, lang, drop_factor):
    volume = session.temp_data.get("drip_volume", 0)
    time_hrs = session.temp_data.get("drip_time", 0)
    fluid = session.temp_data.get("drip_fluid", "")
    time_min = time_hrs * 60
    
    if time_min <= 0:
        await send_message(chat_id, "❌ کات نادروستە.")
        return
    
    gtt_per_min = (volume * drop_factor) / time_min
    ml_per_hour = volume / time_hrs
    
    session.state = UserState.IDLE
    session.temp_data = {}
    
    if lang == "ku":
        result = f"""💉 ڕێکخستنی دڕۆپ:

🟢 جۆری موغەزی: {fluid}
🟢 بری: {volume} mL
🟢 کات: {time_hrs} سەعات
🟢 IV Set: {'Macro' if drop_factor == 20 else 'Micro'}

✅ خێرایی: {gtt_per_min:.1f} دڕۆپ/خولەک
✅ خێرایی: {ml_per_hour:.1f} mL/سەعات"""
    elif lang == "ar":
        result = f"""💧 معدل التنقيط:

🟢 نوع المحلول: {fluid}
🟢 الكمية: {volume} mL
🟢 الوقت: {time_hrs} ساعة
🟢 IV Set: {'Macro' if drop_factor == 20 else 'Micro'}

✅ المعدل: {gtt_per_min:.1f} نقطة/دقيقة"""
    else:
        result = f"""💧 IV Drip Rate:

🟢 Fluid: {fluid}
🟢 Volume: {volume} mL
🟢 Time: {time_hrs} hrs
🟢 IV Set: {'Macro' if drop_factor == 20 else 'Micro'}

✅ Rate: {gtt_per_min:.1f} drops/min"""
    
    await send_message(chat_id, result, get_main_menu(lang))

async def ask_fluid24_weight(chat_id, session, lang):
    session.state = UserState.WAITING_FOR_FLUID24_WEIGHT
    if lang == "ku":
        text = "🕐 پێداویستی لەش بۆ موغەزی لە ٢٤ کاتژمێر\n\nکێشی نەخۆشکەکە بە کیلۆگرام بنووسە:\n\nنموونە: 70"
    elif lang == "ar":
        text = "🕐 احتياج السوائل لـ ٢٤ ساعة\n\nأدخل وزن المريض بالكيلوغرام:\n\nمثال: 70"
    else:
        text = "🕐 24-Hour Fluid Requirement\n\nEnter patient weight in kg:\n\nExample: 70"
    await send_message(chat_id, text)

async def handle_fluid24_weight_input(chat_id, text, lang):
    session = sessions.get(chat_id)
    if not session:
        return
    
    try:
        weight = float(text.strip())
    except ValueError:
        await send_message(chat_id, "❌ تکایە کێشى دروست بنووسە.")
        return
    
    if weight <= 0 or weight > 300:
        await send_message(chat_id, "❌ کێشى دروست بنووسە (١-٣٠٠ کیلۆ)")
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
        result = f"""💧 پێداویستی لەش بو موغەزی لە  ٢٤ کاتژمێر:

🟢 کێش: {weight} کیلۆگرام
🟢 بری ٢٤ کاتژمێر: {daily:.0f} mL
🟢 بری هەر کاتژمێر: {hourly:.1f} mL/hr
🟢 بری هەر نیو کاتژمێر: {half_hourly:.1f} mL/30min

📋 فۆرمولەی Holliday-Segar:
{formula}"""
    elif lang == "ar":
        result = f"""💧 احتياج السوائل لـ ٢٤ ساعة:

🟢 الوزن: {weight} كغ
🟢 الكمية اليومية: {daily:.0f} mL
🟢 في الساعة: {hourly:.1f} mL/hr

📋 معادلة Holliday-Segar:
{formula}"""
    else:
        result = f"""💧 24-Hour Fluid Requirement:

🟢 Weight: {weight} kg
🟢 Daily volume: {daily:.0f} mL
🟢 Hourly rate: {hourly:.1f} mL/hr

📋 Holliday-Segar Formula:
{formula}"""
    
    await send_message(chat_id, result, get_main_menu(lang))

# ==================== معالج HTTP ====================
class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers.get('Content-Length', 0))
        post_data = self.rfile.read(content_length)
        
        try:
            update_dict = json.loads(post_data.decode('utf-8'))
            import asyncio
            result = asyncio.run(process_update(update_dict))
            
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True, **result}).encode())
        except Exception as e:
            logger.error(f"Webhook error: {e}")
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True}).encode())
    
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b"Medical Bot is running! \xf0\x9f\x8f\xa5")
