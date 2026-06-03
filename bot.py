import os
import json
import base64
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
        "SMM-агент Arden Wood\n\nНадiшли фото з пiдписом - отримаєш пост для Instagram та TikTok.\n\n/new - новий пост\n/week - план на 7 днiв"
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
            f"Фото {len(s['files'])} збережено.\nНапиши опис - i я одразу згенерую пост."
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
        await update.message.reply_text("Надiшли спочатку фото!\nМожеш надiслати фото одразу з пiдписом.")


async def generate(ctx, uid, chat_id):
    s = sess(uid)
    desc = s.get("desc", "")
    files = s.get("files", [])
    imgs = []
    for path in files[:2]:
        try:
            data = base64.standard_b64encode(open(path, "rb").read()).decode()
            imgs.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}})
        except Exception:
            pass
    prompt = (
        f"{BRAND}\nОпис: {desc}\n\n"
        "Створи пости. Вiдповiдай ТIЛЬКИ JSON без markdown:\n"
        '{"posts":['
        '{"platform":"instagram","lang":"uk","text":"пiдпис 3-5 речень","hashtags":"15 хештегiв"},'
        '{"platform":"instagram","lang":"cs","text":"2-3 vety","hashtags":"10 tagu"},'
        '{"platform":"instagram","lang":"en","text":"2-3 sentences","hashtags":"10 tags"},'
        '{"platform":"tiktok","lang":"uk","text":"Скрипт 20-30с покроково","hashtags":"8 хештегiв","music":"назва музики"}'
        ']}'
    )
    try:
        content = imgs + [{"type": "text", "text": prompt}]
        r = client.messages.create(model="claude-sonnet-4-20250514", max_tokens=2000, messages=[{"role": "user", "content": content}])
        raw = r.content[0].text.strip().replace("\u0060\u0060\u0060json", "").replace("\u0060\u0060\u0060", "").strip()
        result = json.loads(raw)
        for post in result.get("posts", []):
            icon = "\ud83d\udcf8" if post["platform"] == "instagram" else "\ud83c\udfa6"
            lang = post.get("lang", "").upper()
            music = f"\n\n\ud83c\udfb5 Музика: {post['music']}" if post.get("music") else ""
            msg = f"{icon} *{post['platform'].upper()} {lang}*\n\n{post.get('text', '')}\n\n{post.get('hashtags', '')}{music}"
            await ctx.bot.send_message(chat_id, msg[:4096], parse_mode="Markdown")
        kb = [[InlineKeyboardButton("Новий пост", callback_data="new")]]
        await ctx.bot.send_message(chat_id, "Готово! Копiюй i публiкуй.", reply_markup=InlineKeyboardMarkup(kb))
    except Exception as e:
        await ctx.bot.send_message(chat_id, f"Помилка: {str(e)[:300]}\n\nСпробуй /new")
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
    prompt = f"{BRAND}\nКонтент-план 7 днiв Instagram+TikTok. Теми: стiл-зигзаг, стелаж, дверi, кухня, закулiсся, освiтнiй, промо.\nДень X - Тема\n\ud83d\udcf8 Instagram: iдея\n\ud83c\udfa6 TikTok: сценарiй\n\u23f0 18:00/19:30"
    try:
        r = client.messages.create(model="claude-sonnet-4-20250514", max_tokens=2000, messages=[{"role": "user", "content": prompt}])
        plan = r.content[0].text
        for i in range(0, min(len(plan), 8000), 4000):
            await update.message.reply_text(plan[i:i + 4000])
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
