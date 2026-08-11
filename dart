import 'dart:io';
import 'package:http/http.dart' as http;
import 'package:televerse/televerse.dart';
import 'package:google_generative_ai/google_generative_ai.dart';

// ==================== ١. توکنەکان ====================
const String botToken = 'لێرە_توکنی_بۆت_دانە';
const String geminiApiKey = 'لێرە_ئەیپای_جیمینی_دانە';

// ==================== ٢. بەڕێوەبردنی دۆخ ====================
enum UserState { idle, waitingForWeight }

class Session {
  String medicineName;
  String medicineInfo;
  UserState state;

  Session({
    required this.medicineName,
    required this.medicineInfo,
    this.state = UserState.idle,
  });
}

final Map<int, Session> sessions = {};

// ==================== ٣. ڕێکخستنی جیمینی ====================
final model = GenerativeModel(
  model: 'gemini-1.5-flash',
  apiKey: geminiApiKey,
);

// ==================== ٤. سەرەکی ====================
void main() async {
  final bot = Bot(botToken);

  bot.onError((err) {
    print('⚠️ هەڵە: ${err.error}');
  });

  // ✅ /start
  bot.command('start', (ctx) async {
    await ctx.reply(
      '💊 سڵاو! من بۆتی پزیشکیم.\n\n'
      'دەتوانیت:\n'
      '• ناوی دەرمانەکە بنووسیت\n'
      '• وێنەی دەرمانەکە بنێریت\n\n'
      'من ناسینەوەی دەکەم و زانیاری تەواوت پێدەدەم! 🩺\n\n'
      'بۆ سڕینەوەی زانیاریەکان: /cancel',
    );
  });

  // ✅ /cancel
  bot.command('cancel', (ctx) async {
    final chatId = ctx.chat.id;
    sessions.remove(chatId);
    await ctx.reply('🗑️ زانیارییەکان سڕانەوە. دەرمانێکی نوێ بنووسە یان وێنەی بنێرە.');
  });

  // ✅ ورگرتنی پەیامی دەق
  bot.on(bot.filters.text, (ctx) async {
    final chatId = ctx.chat.id;
    final text = ctx.message?.text ?? '';

    if (text.startsWith('/')) return;

    if (sessions.containsKey(chatId) && sessions[chatId]!.state == UserState.waitingForWeight) {
      await handleWeightInput(ctx, text);
      return;
    }

    await identifyMedicineByText(ctx, text);
  });

  // ✅ ورگرتنی وێنە
  bot.on(bot.filters.photo, (ctx) async {
    await identifyMedicineByPhoto(ctx);
  });

  // ✅ ورگرتنی کلیکی دوگمە
  bot.on(bot.filters.callbackQuery, (ctx) async {
    final data = ctx.callbackQuery?.data ?? '';
    final chatId = ctx.chat?.id ?? ctx.callbackQuery?.message?.chat?.id;

    if (chatId == null) return;

    await ctx.answerCallbackQuery();

    if (!sessions.containsKey(chatId)) {
      await ctx.reply('❌ تکایە سەرەتا ناوی دەرمانەکە بنووسە یان وێنەی بنێرە.');
      return;
    }

    final session = sessions[chatId]!;

    switch (data) {
      case 'names':
        await getOtherNames(ctx, session.medicineName);
        break;
      case 'dosage':
        await askForWeight(ctx);
        session.state = UserState.waitingForWeight;
        break;
      case 'contraindications':
        await getContraindications(ctx, session.medicineName);
        break;
      case 'beneficial':
        await getBeneficialFoods(ctx, session.medicineName);
        break;
      case 'general':
        await ctx.reply('📋 زانیاری گشتی:\n\n${session.medicineInfo}');
        break;
      // 🆕 سیستەمی لەش و مێکانیزم
      case 'mechanism':
        await getMechanismAndSystem(ctx, session.medicineName);
        break;
      case 'new':
        sessions.remove(chatId);
        await ctx.reply('🔄 دەرمانێکی نوێ بنووسە یان وێنەی بنێرە:');
        break;
    }
  });

  print('🚀 بۆتەکە لەسەر دارت دەستی بە کارکرد کرد...');
  await bot.start();
}

// ==================== ٥. ناسینەوەی دەرمان ====================

Future<void> identifyMedicineByText(Context ctx, String name) async {
  await ctx.reply('⏳ خەریکی ناسینەوەی دەرمانەکەم...');

  try {
    final prompt = '''
تکایە ئەم دەرمانە ناسینەوە بکە: "$name"
زمان: کوردی سۆرانی

تکایە ئەم زانیاریانە بە کوردی سۆرانی بدە بە شێوەیەکی ڕوون:
1. ناوی گشتی (Generic Name)
2. ناوی زانستی (Scientific/Chemical Name)
3. ناوی بازرگانی (Brand Names)
4. بەکارهێنان و نەخۆشیەکان چاک دەکات
5. بری پێدانی گشتی و چەن جار لە ڕۆژێک
6. قەدەغەکراوەکان (دەرمان و خۆراک)
7. خۆراکە بەسودەکان
8. ئاگادارییە گرنگەکان
''';

    final response = await model.generateContent([Content.text(prompt)]);
    final info = response.text ?? '⚠️ نەتوانرا زانیاری بدozzer.';

    final chatId = ctx.chat.id;
    sessions[chatId] = Session(
      medicineName: name,
      medicineInfo: info,
    );

    await showMainMenu(ctx, name);

  } catch (e) {
    await ctx.reply('❌ هەڵە لە ناسینەوەی دەرمان: $e');
  }
}

Future<void> identifyMedicineByPhoto(Context ctx) async {
  await ctx.reply('📸 خەریکی شیکاری وێنەکەم...');

  try {
    final photos = ctx.message?.photo;
    if (photos == null || photos.isEmpty) {
      await ctx.reply('❌ وێنە نەدۆزرایەوە.');
      return;
    }

    final photo = photos.last;
    final file = await ctx.api.getFile(photo.fileId);
    final fileUrl = 'https://api.telegram.org/file/bot$botToken/${file.filePath}';

    final response = await http.get(Uri.parse(fileUrl));
    if (response.statusCode != 200) {
      await ctx.reply('❌ نەتوانرا وێنە دابگیرێت.');
      return;
    }

    final bytes = response.bodyBytes;

    final prompt = TextPart('''
ئەم وێنەیە دەرمانێکە. تکایە ناسینەوەی بکە و زانیاری تەواو لەسەر بنووسە بە کوردی سۆرانی.

تکایە ئەم زانیاریانە بدە:
1. ناوی گشتی (Generic Name)
2. ناوی زانستی (Scientific/Chemical Name)
3. ناوی بازرگانی (Brand Names)
4. بەکارهێنان و نەخۆشیەکان چاک دەکات
5. بری پێدانی گشتی و چەن جار لە ڕۆژێک
6. قەدەغەکراوەکان (دەرمان و خۆراک)
7. خۆراکە بەسودەکان
8. ئاگادارییە گرنگەکان
''');

    final content = Content.multi([
      prompt,
      InlineDataPart('image/jpeg', bytes),
    ]);

    final geminiResponse = await model.generateContent([content]);
    final info = geminiResponse.text ?? '⚠️ نەتوانرا زانیاری بدozzer.';

    String medName = 'نەناسراو';
    final lines = info.split('\n');
    for (final line in lines.take(10)) {
      final clean = line.replaceAll(RegExp(r'[*#\-\d.]'), '').trim();
      if (clean.isNotEmpty && clean.length > 2) {
        medName = clean;
        break;
      }
    }

    final chatId = ctx.chat.id;
    sessions[chatId] = Session(
      medicineName: medName,
      medicineInfo: info,
    );

    await showMainMenu(ctx, medName);

  } catch (e) {
    await ctx.reply('❌ هەڵە لە شیکاری وێنە: $e');
  }
}

// ==================== ٦. مێنیو و هەڵبژاردنەکان ====================

Future<void> showMainMenu(Context ctx, String medicineName) async {
  final keyboard = InlineKeyboard()
    .text('🏷️ ناوەکانی تر', 'names')
    .row()
    .text('⚖️ پێدان بەپێی کێش', 'dosage')
    .row()
    .text('🚫 قەدەغەکراوەکان', 'contraindications')
    .row()
    .text('🥗 خۆراکە بەسودەکان', 'beneficial')
    .row()
    // 🆕 دوگمەی نوێ
    .text('🧠 سیستەم و مێکانیزم', 'mechanism')
    .row()
    .text('📋 زانیاری گشتی', 'general')
    .row()
    .text('🔄 دەرمانێکی تر', 'new');

  await ctx.reply(
    '💊 دەرمان ناسێنرا: $medicineName\n\nهەڵبژاردنێک هەڵبژێرە:',
    replyMarkup: keyboard,
  );
}

Future<void> getOtherNames(Context ctx, String medicineName) async {
  await ctx.reply('⏳ خەریکی گەڕان بۆ ناوەکانی تر...');

  try {
    final prompt = '''
تکایە تەنها ناوی زانستی و ناوی بازرگانی دەرمانی "$medicineName" بە کوردی سۆرانی بنووسە.
بە شێوەی لیستێک پیشان بدە.
''';
    final response = await model.generateContent([Content.text(prompt)]);
    await ctx.reply(response.text ?? '⚠️ زانیاری بەردەست نییە.');
  } catch (e) {
    await ctx.reply('❌ هەڵە: $e');
  }
}

Future<void> askForWeight(Context ctx) async {
  await ctx.reply(
    '⚖️ تکایە کێشی کەسەکە بە کیلۆگرام بنووسە:\n\n'
    'نموونە: 70\n'
    'یان: 12.5\n\n'
    '(تەنها ژمارە بنووسە، بۆ هەڵوەشاندنەوە: /cancel)',
  );
}

Future<void> handleWeightInput(Context ctx, String text) async {
  final chatId = ctx.chat.id;

  final cleanText = text.replaceAll(RegExp(r'[^0-9.]'), '');
  final weight = double.tryParse(cleanText);

  if (weight == null || weight <= 0 || weight > 300) {
    await ctx.reply(
      '❌ تکایە کێکێکی دروست بنووسە (1-300 کیلۆ).\n'
      'نموونە: 70\n\n'
      'بۆ هەڵوەشاندنەوە: /cancel',
    );
    return;
  }

  final session = sessions[chatId]!;
  session.state = UserState.idle;

  await ctx.reply('⏳ خەریکی ئەژماری بری پێدانم بۆ کێشی $weight کیلۆگرام...');

  try {
    final prompt = '''
بۆ دەرمانی "${session.medicineName}"، کەسێک کێشی $weight کیلۆگرامە.
تکایە بری پێدانی دەرمانەکە بۆ ئەم کێشە ئەژمار بکە بە کوردی سۆرانی.

تکایە ڕوون بکەوە:
- چەند میلیگرام/گرام لە هەر دانەیەک
- چەن جار لە ڕۆژێک
- کاتی نێوان هەر دوو دەرمان (چەند کاتژمێر)
- ئایا پێش خواردنە یان دوای خواردنە
- ئایا لە کاتی نوستنەوەدا دەبێت یان نا
- ئاگادارییە تایبەتەکان بۆ ئەم تەمەنە/کێشە
''';

    final response = await model.generateContent([Content.text(prompt)]);
    await ctx.reply(response.text ?? '⚠️ نەتوانرا ئەژمار بکرێت.');

    await showMainMenu(ctx, session.medicineName);

  } catch (e) {
    await ctx.reply('❌ هەڵە لە ئەژمار: $e');
  }
}

Future<void> getContraindications(Context ctx, String medicineName) async {
  await ctx.reply('⏳ خەریکی گەڕان بۆ قەدەغەکراوەکان...');

  try {
    final prompt = '''
تکایە تەنها قەدەغەکراوەکانی دەرمانی "$medicineName" بە کوردی سۆرانی بنووسە:

1. 🚫 ئەو دەرمانانەی نابێت لەگەڵی بەکاربهێنرێت
2. 🍽️ ئەو خۆراکانەی نابێت لە کاتی بەکارهێنانی ئەم دەرمانە بخوات
3. ⚠️ ئەو نەخۆشیانەی نابێت بەکاری بهێنێت
4. 👶 ئایا بۆ منداڵ یان دووگیان قەدەغەیە
''';
    final response = await model.generateContent([Content.text(prompt)]);
    await ctx.reply(response.text ?? '⚠️ زانیاری بەردەست نییە.');
  } catch (e) {
    await ctx.reply('❌ هەڵە: $e');
  }
}

Future<void> getBeneficialFoods(Context ctx, String medicineName) async {
  await ctx.reply('⏳ خەریکی گەڕان بۆ خۆراکە بەسودەکان...');

  try {
    final prompt = '''
تکایە تەنها خۆراکە بەسودەکان بۆ دەرمانی "$medicineName" بە کوردی سۆرانی بنووسە:

1. 🥗 ئەو خۆراکانەی وا دەکات دەرمانەکە باشتر کار بکات
2. 💊 ئەو ڤیتامین و معدنە کانەی پێویستە لەگەڵی بخوات
3. 💧 ئایا ئاوی زۆر پێویستە
4. 🍎 سەردەم و خۆراکی تایبەت
''';
    final response = await model.generateContent([Content.text(prompt)]);
    await ctx.reply(response.text ?? '⚠️ زانیاری بەردەست نییە.');
  } catch (e) {
    await ctx.reply('❌ هەڵە: $e');
  }
}

// 🆕 ==================== ٧. سیستەمی لەش و مێکانیزم ====================

Future<void> getMechanismAndSystem(Context ctx, String medicineName) async {
  await ctx.reply('🧠 خەریکی شیکاری سیستەمی لەش و مێکانیزمی کارکردن...');

  try {
    final prompt = '''
تکایە زانیاری تەواو لەسەر دەرمانی "$medicineName" بە کوردی سۆرانی بدە لەسەر:

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
''';

    final response = await model.generateContent([Content.text(prompt)]);
    await ctx.reply(response.text ?? '⚠️ نەتوانرا زانیاری بدozzer.');

  } catch (e) {
    await ctx.reply('❌ هەڵە لە گەڕان: $e');
  }
}
