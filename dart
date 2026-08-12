import 'dart:io';
import 'package:http/http.dart' as http;
import 'package:televerse/televerse.dart';
import 'package:google_generative_ai/google_generative_ai.dart';

// ✅ لە Environment Variables وەردەگرێت (ئەمنترە)
final String botToken = Platform.environment['8981441543:AAFUz_UWADttNZKxlQm46FOToBvLPiXrue8'] ?? '';
final String geminiApiKey = Platform.environment['AQ.Ab8RN6LYECH-Rf7lZ8FRpo2BzX4x6iTwuDpSMPFqS-HBwdWhrA'] ?? '';

enum UserState { idle, waitingForWeight }

class Session {
  String medicineName;
  String medicineInfo;
  UserState state;
  Session({required this.medicineName, required this.medicineInfo, this.state = UserState.idle});
}

final Map<int, Session> sessions = {};
final model = GenerativeModel(model: 'gemini-1.5-flash', apiKey: geminiApiKey);

void main() async {
  if (botToken.isEmpty || geminiApiKey.isEmpty) {
    print('❌ BOT_TOKEN یان GEMINI_API_KEY دانەنراوە!');
    exit(1);
  }

  final bot = Bot(botToken);
  bot.onError((err) => print('⚠️ هەڵە: ${err.error}'));

  // /start
  bot.command('start', (ctx) async {
    await ctx.reply('💊 سڵاو! من بۆتی پزیشکیم.\n\nدەتوانیت:\n• ناوی دەرمانەکە بنووسیت\n• وێنەی دەرمانەکە بنێریت\n\nبۆ سڕینەوە: /cancel');
  });

  // /cancel
  bot.command('cancel', (ctx) async {
    sessions.remove(ctx.chat.id);
    await ctx.reply('🗑️ زانیارییەکان سڕانەوە.');
  });

  // دەق
  bot.on(bot.filters.text, (ctx) async {
    final chatId = ctx.chat.id;
    final text = ctx.message?.text ?? '';
    if (text.startsWith('/')) return;

    if (sessions[chatId]?.state == UserState.waitingForWeight) {
      await handleWeightInput(ctx, text);
      return;
    }
    await identifyMedicineByText(ctx, text);
  });

  // وێنە
  bot.on(bot.filters.photo, (ctx) async {
    await identifyMedicineByPhoto(ctx);
  });

  // کلیکی دوگمە
  bot.on(bot.filters.callbackQuery, (ctx) async {
    final data = ctx.callbackQuery?.data ?? '';
    final chatId = ctx.chat?.id ?? ctx.callbackQuery?.message?.chat?.id;
    if (chatId == null) return;

    await ctx.answerCallbackQuery();
    if (!sessions.containsKey(chatId)) {
      await ctx.reply('❌ سەرەتا ناوی دەرمانەکە بنووسە.');
      return;
    }

    final session = sessions[chatId]!;
    switch (data) {
      case 'names': await getOtherNames(ctx, session.medicineName); break;
      case 'dosage': await askForWeight(ctx); session.state = UserState.waitingForWeight; break;
      case 'contraindications': await getContraindications(ctx, session.medicineName); break;
      case 'beneficial': await getBeneficialFoods(ctx, session.medicineName); break;
      case 'general': await ctx.reply('📋 زانیاری گشتی:\n\n${session.medicineInfo}'); break;
      case 'mechanism': await getMechanismAndSystem(ctx, session.medicineName); break;
      case 'new': sessions.remove(chatId); await ctx.reply('🔄 دەرمانێکی نوێ بنووسە:'); break;
    }
  });

  print('🚀 بۆتەکە لەسەر Northflank دەستی بە کارکرد کرد...');
  await bot.start();
}

// ==================== ناسینەوە ====================

Future<void> identifyMedicineByText(Context ctx, String name) async {
  await ctx.reply('⏳ خەریکی ناسینەوەی دەرمانەکەم...');
  try {
    final prompt = '''
تکایە ئەم دەرمانە ناسینەوە بکە: "$name"
زمان: کوردی سۆرانی

1. ناوی گشتی
2. ناوی زانستی
3. ناوی بازرگانی
4. بەکارهێنان
5. بری پێدانی گشتی
6. قەدەغەکراوەکان
7. خۆراکە بەسودەکان
8. ئاگادارییەکان
''';
    final response = await model.generateContent([Content.text(prompt)]);
    final info = response.text ?? '⚠️ نەتوانرا زانیاری بدozzer.';
    sessions[ctx.chat.id] = Session(medicineName: name, medicineInfo: info);
    await showMainMenu(ctx, name);
  } catch (e) {
    await ctx.reply('❌ هەڵە: $e');
  }
}

Future<void> identifyMedicineByPhoto(Context ctx) async {
  await ctx.reply('📸 خەریکی شیکاری وێنەکەم...');
  try {
    final photos = ctx.message?.photo;
    if (photos == null || photos.isEmpty) return;

    final photo = photos.last;
    final file = await ctx.api.getFile(photo.fileId);
    final fileUrl = 'https://api.telegram.org/file/bot$botToken/${file.filePath}';
    final response = await http.get(Uri.parse(fileUrl));
    if (response.statusCode != 200) {
      await ctx.reply('❌ نەتوانرا وێنە دابگیرێت.');
      return;
    }

    final prompt = TextPart('''
ئەم وێنەیە دەرمانێکە. ناسینەوەی بکە بە کوردی سۆرانی:
1. ناوی گشتی
2. ناوی زانستی
3. ناوی بازرگانی
4. بەکارهێنان
5. بری پێدانی گشتی
6. قەدەغەکراوەکان
7. خۆراکە بەسودەکان
8. ئاگادارییەکان
''');
    final content = Content.multi([prompt, InlineDataPart('image/jpeg', response.bodyBytes)]);
    final geminiResponse = await model.generateContent([content]);
    final info = geminiResponse.text ?? '⚠️ نەتوانرا زانیاری بدozzer.';

    String medName = 'نەناسراو';
    for (final line in info.split('\n').take(10)) {
      final clean = line.replaceAll(RegExp(r'[*#\-\d.]'), '').trim();
      if (clean.isNotEmpty && clean.length > 2) { medName = clean; break; }
    }

    sessions[ctx.chat.id] = Session(medicineName: medName, medicineInfo: info);
    await showMainMenu(ctx, medName);
  } catch (e) {
    await ctx.reply('❌ هەڵە لە شیکاری وێنە: $e');
  }
}

// ==================== مێنیو ====================

Future<void> showMainMenu(Context ctx, String medicineName) async {
  final keyboard = InlineKeyboard()
    .text('🏷️ ناوەکانی تر', 'names').row()
    .text('⚖️ پێدان بەپێی کێش', 'dosage').row()
    .text('🚫 قەدەغەکراوەکان', 'contraindications').row()
    .text('🥗 خۆراکە بەسودەکان', 'beneficial').row()
    .text('🧠 سیستەم و مێکانیزم', 'mechanism').row()
    .text('📋 زانیاری گشتی', 'general').row()
    .text('🔄 دەرمانێکی تر', 'new');

  await ctx.reply('💊 دەرمان ناسێنرا: $medicineName\n\nهەڵبژاردنێک هەڵبژێرە:', replyMarkup: keyboard);
}

// ==================== هەڵبژاردنەکان ====================

Future<void> getOtherNames(Context ctx, String medicineName) async {
  await ctx.reply('⏳ خەریکی گەڕان...');
  final response = await model.generateContent([Content.text('تکایە تەنها ناوی زانستی و بازرگانی دەرمانی "$medicineName" بە کوردی سۆرانی بنووسە.')]);
  await ctx.reply(response.text ?? '⚠️ زانیاری بەردەست نییە.');
}

Future<void> askForWeight(Context ctx) async {
  await ctx.reply('⚖️ تکایە کێشی کەسەکە بە کیلۆگرام بنووسە:\n\nنموونە: 70\n(بۆ هەڵوەشاندنەوە: /cancel)');
}

Future<void> handleWeightInput(Context ctx, String text) async {
  final chatId = ctx.chat.id;
  final cleanText = text.replaceAll(RegExp(r'[^0-9.]'), '');
  final weight = double.tryParse(cleanText);

  if (weight == null || weight <= 0 || weight > 300) {
    await ctx.reply('❌ کێکێکی دروست بنووسە (1-300 کیلۆ).');
    return;
  }

  final session = sessions[chatId]!;
  session.state = UserState.idle;

  await ctx.reply('⏳ خەریکی ئەژمارم بۆ کێشی $weight کیلۆگرام...');
  final prompt = '''
بۆ دەرمانی "${session.medicineName}"، کەسێک کێشی $weight کیلۆگرامە.
بری پێدانی دەرمانەکە ئەژمار بکە بە کوردی سۆرانی:
- چەند میلیگرام/گرام
- چەن جار لە ڕۆژێک
- کاتی نێوان دەرمان
- پێش/دوای خواردن
- ئاگادارییەکان
''';
  final response = await model.generateContent([Content.text(prompt)]);
  await ctx.reply(response.text ?? '⚠️ نەتوانرا ئەژمار بکرێت.');
  await showMainMenu(ctx, session.medicineName);
}

Future<void> getContraindications(Context ctx, String medicineName) async {
  await ctx.reply('⏳ خەریکی گەڕان...');
  final prompt = '''
تکایە تەنها قەدەغەکراوەکانی دەرمانی "$medicineName" بە کوردی سۆرانی بنووسە:
1. 🚫 دەرمانە قەدەغەکراوەکان
2. 🍽️ خۆراکە قەدەغەکراوەکان
3. ⚠️ نەخۆشیە قەدەغەکراوەکان
4. 👶 منداڵ/دووگیان
''';
  final response = await model.generateContent([Content.text(prompt)]);
  await ctx.reply(response.text ?? '⚠️ زانیاری بەردەست نییە.');
}

Future<void> getBeneficialFoods(Context ctx, String medicineName) async {
  await ctx.reply('⏳ خەریکی گەڕان...');
  final prompt = '''
تکایە تەنها خۆراکە بەسودەکان بۆ دەرمانی "$medicineName" بە کوردی سۆرانی بنووسە:
1. 🥗 خۆراکە بەسودەکان
2. 💊 ڤیتامین و معدنە کان
3. 💧 ئاوی زۆر پێویستە؟
''';
  final response = await model.generateContent([Content.text(prompt)]);
  await ctx.reply(response.text ?? '⚠️ زانیاری بەردەست نییە.');
}

Future<void> getMechanismAndSystem(Context ctx, String medicineName) async {
  await ctx.reply('🧠 خەریکی شیکاری سیستەمی لەش...');
  final prompt = '''
تکایە زانیاری لەسەر دەرمانی "$medicineName" بە کوردی سۆرانی بدە:

🧠 **١. سیستەمی لەش:**
- N/s = Nervous System (دەماری)
- C/v = Cardiovascular (دڵ و خوێن)
- G/I = Gastrointestinal (هەرسکردن)
- R/s = Respiratory (هەناسەدان)
- R/L = Renal/Liver (گورچیلە و جەردە)
- E/n = Endocrine (هۆرمۆن)
- M/s = Musculoskeletal (ئێسقان و ماسولکە)
- I/m = Immune (بەرگری)

⚗️ **٢. چینایەتی دەرمانی**
🔬 **٣. مێکانیزمی کارکردن**
🎯 **٤. ئامانجی کارکردن**
''';
  final response = await model.generateContent([Content.text(prompt)]);
  await ctx.reply(response.text ?? '⚠️ نەتوانرا زانیاری بدozzer.');
}
