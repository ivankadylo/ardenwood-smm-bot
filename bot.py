import os, json, base64
from pathlib import Path
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
import anthropic

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY", "")
OUTPUT_DIR = Path("./generated_posts")
OUTPUT_DIR.mkdir(exist_ok=True)
client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
BRAND = "Arden Wood - меблi з масиву дуба, Ostrava CZ, ardenwood.eu. Стиль: премiум, натуральний."
user_sessions = {}

def get_session(uid):
    if uid not in user_sessions:
        user_sessions[uid] = {"files": [], "description": "", "step": "collecting"}
    return user_sessions[uid]

async def cmd_start(update, ctx):
    uid = update.effective_user.id
    user_sessions[uid] = {"files": [], "description": "", "step": "collecting"}
    await update.message.reply_text("Привiт! SMM-агент Arden Wood.\n\nНадiшли фото + напиши опис.\nЗгенерую пост Instagram (UA/CS/EN) та TikTok.\n\n/new - новий пост\n/week - план 7 днiв")

async def cmd_new(update, ctx):
    uid = update.effective_user.id
    user_sessions[uid] = {"files": [], "description": "", "step": "collecting"}
    await update.message.reply_text("Надсилай фото або вiдео!")

async def handle_photo(update, ctx):
    uid = update.effective_user.id
    s = get_session(uid)
    photo = update.message.photo[-1]
    f = await ctx.bot.get_file(photo.file_id)
    fname = OUTPUT_DIR / f"{uid}_{len(s['files'])}.jpg"
    await f.download_to_drive(fname)
    s["files"].append({"type": "photo", "path": str(fname)})
    caption = update.message.caption or ""
    if caption:
        s["description"] = caption
        await update.message.reply_text("Отримав! Генерую пост...")
        await do_generate(ctx, uid, s, update.effective_chat.id)
    else:
        await update.message.reply_text(f"Фото {len(s['files'])} отримано!\nНапиши опис - i я одразу згенерую пост.")

async def handle_video(update, ctx):
    uid = update.effective_user.id
    s = get_session(uid)
    video = update.message.video
    f = await ctx.bot.get_file(video.file_id)
    fname = OUTPUT_DIR / f"{uid}_{len(s['files'])}.mp4"
    await f.download_to_drive(fname)
    s["files"].append({"type": "video", "path": str(fname), "duration": video.duration or 0})
    caption = update.message.caption or ""
    if caption:
        s["description"] = caption
        await update.message.reply_text("Вiдео отримано! Генерую пост...")
        await do_generate(ctx, uid, s, update.effective_chat.id)
    else:
        await update.message.reply_text("Вiдео отримано! Напиши опис.")
async def handle_text(update, ctx):
    uid = update.effective_user.id
    s = get_session(uid)
    text = update.message.text.strip()
    if s["files"]:
        s["description"] = text
        kb = [[InlineKeyboardButton("Генерувати пост", callback_data="gen")],[InlineKeyboardButton("Додати фото", callback_data="more")]]
        await update.message.reply_text(f"Є {len(s['files'])} фото. Опис: {text[:60]}\nГенерувати?", reply_markup=InlineKeyboardMarkup(kb))
    else:
        await update.message.reply_text("Надiшли спочатку фото або вiдео!\nМожеш надiслати фото одразу з пiдписом.")

async def button_callback(update, ctx):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    s = get_session(uid)
    if q.data == "gen":
        await q.edit_message_text("Генерую... 20 секунд.")
        await do_generate(ctx, uid, s, q.message.chat_id)
    elif q.data == "more":
        await q.edit_message_text("Надсилай ще фото!")
    elif q.data == "new":
        user_sessions[uid] = {"files": [], "description": "", "step": "collecting"}
        await q.edit_message_text("Надсилай новi фото!")

async def do_generate(ctx, uid, session, chat_id):
    desc = session.get("description", "")
    files = session.get("files", [])
    imgs = []
    for pf in [f for f in files if f["type"] == "photo"][:2]:
        try:
            data = base64.standard_b64encode(open(pf["path"], "rb").read()).decode()
            imgs.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}})
        except:
            pass
    has_video = any(f["type"] == "video" for f in files)
    video_note = " + є вiдео" if has_video else ""
    prompt = (
        f"{BRAND}\nОпис: {desc}{video_note}\n\n"
        "Створи пости. ТIЛЬКИ JSON без markdown:\n"
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
        raw = r.content[0].text.strip().replace("```json", "").replace("```", "").strip()
        result = json.loads(raw)
        icons = {"instagram": "📸", "tiktok": "🎬"}
        for post in result.get("posts", []):
            icon = icons.get(post["platform"], "✦")
            lang = post.get("lang", "").upper()
            music = f"\n\n🎵 Музика: {post['music']}" if post.get("music") else ""
            msg = f"{icon} *{post['platform'].upper()} {lang}*\n\n{post.get('text','')}\n\n{post.get('hashtags','')}{music}"
            await ctx.bot.send_message(chat_id, msg[:4096], parse_mode="Markdown")
        kb = [[InlineKeyboardButton("Новий пост", callback_data="new")]]
        await ctx.bot.send_message(chat_id, "Готово! Копiюй i публiкуй.", reply_markup=InlineKeyboardMarkup(kb))
    except Exception as e:
        await ctx.bot.send_message(chat_id, f"Помилка: {str(e)[:300]}\n\nСпробуй /new")
    user_sessions[uid] = {"files": [], "description": "", "step": "collecting"}

async def cmd_week(update, ctx):
    await update.message.reply_text("Генерую план...")
    prompt = f"{BRAND}\nКонтент-план 7 днiв Instagram+TikTok. Щодня 1+1. Теми: стiл-зигзаг, стелаж, дверi, кухня, закулiсся, освiтнiй, промо.\nФормат:\nДень X (пн/вт...) - Тема\n📸 Instagram: що знiмати\n🎬 TikTok: сценарiй\n⏰ 18:00/19:30"
    try:
        r = client.messages.create(model="claude-sonnet-4-20250514", max_tokens=2000, messages=[{"role": "user", "content": prompt}])
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
    app.add_handler(MessageHandler(filters.VIDEO, handle_video))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(CallbackQueryHandler(button_callback))
    print("Arden Wood Bot running!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
