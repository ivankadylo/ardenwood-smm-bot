import os, json, base64
from pathlib import Path
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters
import anthropic

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY", "")
OUTPUT_DIR = Path("./posts")
OUTPUT_DIR.mkdir(exist_ok=True)
client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
BRAND = "Arden Wood - меблi з масиву дуба, Ostrava CZ, ardenwood.eu. Стиль: премiум, натуральний, авторський."
sessions = {}

def sess(uid):
    if uid not in sessions:
        sessions[uid] = {"files": [], "desc": ""}
    return sessions[uid]

async def cmd_start(update, ctx):
    uid = update.effective_user.id
    sessions[uid] = {"files": [], "desc": ""}
    await update.message.reply_text(
        "SMM-агент Arden Wood\n\n"
        "Як користуватись:\n"
        "- Надiшли фото з пiдписом одним повiдомленням\n"
        "- Або спочатку фото, потiм текст\n\n"
        "/new - скинути i почати заново\n"
        "/week - план на тиждень"
    )

async def cmd_new(update, ctx):
    uid = update.effective_user.id
    sessions[uid] = {"files": [], "desc": ""}
    await update.message.reply_text("Готово! Надсилай фото.")

async def handle_photo(update, ctx):
    uid = update.effective_user.id
    s = sess(uid)
    photo = update.message.photo[-1]
    f = await ctx.bot.get_file(photo.file_id)
    path = OUTPUT_DIR / f"{uid}_{len(s['files'])}.jpg"
    await f.download_to_drive(path)
    s["files"].append(str(path))
    caption = (update.message.caption or "").strip()
    if caption:
        s["desc"] = caption
        await update.message.reply_text("Отримав! Генерую пост...")
        await generate(ctx, uid, update.effective_chat.id)
    else:
        await update.message.reply_text(
            f"Фото {len(s['files'])} збережено.\n"
            "Напиши опис - i я одразу згенерую пост."
        )

async def handle_text(update, ctx):
    uid = update.effective_user.id
    s = sess(uid)
    text = update.message.text.strip()
    if s["files"]:
        s["desc"] = text
        await update.message.reply_text("Генерую пост...")
        await generate(ctx, uid, update.effective_chat.id)
    else:
        await update.message.reply_text(
            "Спочатку надiшли фото!\n"
            "Можеш надiслати фото одразу з пiдписом."
        )

async def generate(ctx, uid, chat_id):
    s = sess(uid)
    desc = s.get("desc", "")
    files = s.get("files", [])
    imgs = []
    for path in files[:2]:
        try:
            data = base64.standard_b64encode(open(path, "rb").read()).decode()
            imgs.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}})
        except:
            pass
    prompt = (
        f"{BRAND}\nОпис: {desc}\n\n"
        "Створи пости. Вiдповiдай ТIЛЬКИ JSON без markdown:\n"
        '{"posts":['
        '{"platform":"instagram","lang":"uk","text":"пiдпис 3-5 речень емоцiйно","hashtags":"15 хештегiв"},'
        '{"platform":"instagram","lang":"cs","text":"2-3 vety o produktu","hashtags":"10 ceskich tagu"},'
        '{"platform":"instagram","lang":"en","text":"2-3 sentences about the piece","hashtags":"10 english tags"},'
        '{"platform":"tiktok","lang":"uk","text":"Скрипт 20-30с:\n[0-3с] що показати\n[3-15с] що сказати\n[15-25с] деталi\n[25-30с] заклик писати в DM","hashtags":"8 тiкток хештегiв","music":"назва пiснi для фону"}'
        "]}"
    )
    try:
        content = imgs + [{"type": "text", "text": prompt}]
        r = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2000,
            messages=[{"role": "user", "content": content}]
        )
        raw = r.content[0].text.strip().replace("```json","").replace("```","").strip()
        result = json.loads(raw)
        for post in result.get("posts", []):
            icon = "📸" if post["platform"] == "instagram" else "🎬"
            lang = post.get("lang","").upper()
            music = f"\n\n🎵 {post['music']}" if post.get("music") else ""
            msg = f"{icon} *{post['platform'].upper()} {lang}*\n\n{post.get('text','')}\n\n{post.get('hashtags','')}{music}"
            await ctx.bot.send_message(chat_id, msg[:4096], parse_mode="Markdown")
        kb = [[InlineKeyboardButton("Новий пост", callback_data="new")]]
        await ctx.bot.send_message(chat_id, "Готово! Копiюй i постав.", reply_markup=InlineKeyboardMarkup(kb))
    except Exception as e:
        await ctx.bot.send_message(chat_id, f"Помилка: {str(e)[:300]}\n/new - спробуй знову")
    sessions[uid] = {"files": [], "desc": ""}

async def btn(update, ctx):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    if q.data == "new":
        sessions[uid] = {"files": [], "desc": ""}
        await q.edit_message_text("Надсилай нове фото!")

async def cmd_week(update, ctx):
    await update.message.reply_text("Генерую план на 7 днiв...")
    p = f"{BRAND}\nПлан 7 днiв Instagram+TikTok щодня. Теми: стiл-зигзаг, стелаж, дверi, кухня, закулiсся, освiтнiй, промо.\nДень X (пн/вт...) - Тема\n📸 Instagram: iдея\n🎬 TikTok: сценарiй\n⏰ 18:00/19:30"
    try:
        r = client.messages.create(model="claude-sonnet-4-20250514", max_tokens=2000, messages=[{"role":"user","content":p}])
        plan = r.content[0].text
        for i in range(0, min(len(plan), 8000), 4000):
            await update.message.reply_text(plan[i:i+4000])
    except Exception as e:
        await update.message.reply_text(f"Помилка: {e}")

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("new", cmd_new))
    app.add_handler(CommandHandler("week", cmd_week))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(CallbackQueryHandler(btn))
    print("Bot running!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
