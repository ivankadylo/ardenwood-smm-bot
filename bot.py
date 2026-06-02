import os, json, base64, re
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

BRAND = "Arden Wood - меблі з масиву дуба, Ostrava CZ, ardenwood.eu"
user_sessions = {}

def get_session(uid):
    if uid not in user_sessions:
        user_sessions[uid] = {"files":[],"description":"","step":"idle"}
    return user_sessions[uid]

async def cmd_start(update, ctx):
    await update.message.reply_text("Привіт! Я SMM-агент Arden Wood.\n\n1. Надішли фото/відео\n2. Напиши опис\n3. Отримай готовий пост\n\n/new - новий пост\n/week - план на тиждень")

async def cmd_new(update, ctx):
    uid = update.effective_user.id
    user_sessions[uid] = {"files":[],"description":"","step":"collecting"}
    await update.message.reply_text("Надсилай фото або відео!")

async def handle_photo(update, ctx):
    uid = update.effective_user.id
    s = get_session(uid)
    photo = update.message.photo[-1]
    f = await ctx.bot.get_file(photo.file_id)
    path = OUTPUT_DIR / f"{uid}_{datetime.now().strftime(chr(37)+chr(72)+chr(37)+chr(77)+chr(37)+chr(83))}.jpg"
    await f.download_to_drive(path)
    s["files"].append({"type":"photo","path":str(path),"file_id":photo.file_id})
    await update.message.reply_text(f"Фото {len(s[chr(102)+chr(105)+chr(108)+chr(101)+chr(115)])} отримано! Напиши що на ньому.")

async def handle_video(update, ctx):
    uid = update.effective_user.id
    s = get_session(uid)
    video = update.message.video
    f = await ctx.bot.get_file(video.file_id)
    path = OUTPUT_DIR / f"{uid}_{datetime.now().strftime(chr(37)+chr(72)+chr(37)+chr(77)+chr(37)+chr(83))}.mp4"
    await f.download_to_drive(path)
    s["files"].append({"type":"video","path":str(path),"duration":video.duration or 0})
    await update.message.reply_text(f"Відео {video.duration}с! Напиши що на ньому.")

async def handle_text(update, ctx):
    uid = update.effective_user.id
    s = get_session(uid)
    text = update.message.text.strip()
    if s["files"]:
        s["description"] = text
        kb = [[InlineKeyboardButton("Генерувати пост", callback_data="gen")],[InlineKeyboardButton("Скасувати", callback_data="cancel")]]
        await update.message.reply_text(f"Готово! {len(s[chr(102)+chr(105)+chr(108)+chr(101)+chr(115)])} фото/відео\nГенерувати?", reply_markup=InlineKeyboardMarkup(kb))
    else:
        await update.message.reply_text("Спочатку надішли фото або відео")

async def button_callback(update, ctx):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    s = get_session(uid)
    if q.data == "gen":
        await q.edit_message_text("Генерую... 20 секунд.")
        await do_generate(q, ctx, uid, s)
    elif q.data == "cancel":
        user_sessions[uid] = {"files":[],"description":"","step":"idle"}
        await q.edit_message_text("Скасовано. /new - новий пост.")
    elif q.data == "new":
        user_sessions[uid] = {"files":[],"description":"","step":"collecting"}
        await q.edit_message_text("Надсилай нові фото!")

async def do_generate(q, ctx, uid, session):
    desc = session.get("description","")
    files = session.get("files",[])
    imgs = []
    for pf in [f for f in files if f["type"]=="photo"][:2]:
        try:
            data = base64.standard_b64encode(open(pf["path"],"rb").read()).decode()
            imgs.append({"type":"image","source":{"type":"base64","media_type":"image/jpeg","data":data}})
        except: pass
    prompt = f"""{BRAND}\nОпис: {desc}\nСтвори JSON без markdown:\n{{"posts":[{{"platform":"instagram","lang":"uk","text":"підпис","hashtags":"хештеги"}},{{"platform":"instagram","lang":"cs","text":"text","hashtags":"tagy"}},{{"platform":"tiktok","lang":"uk","text":"скрипт відео","hashtags":"теги","music":"назва музики"}}]}}"""
    try:
        r = client.messages.create(model="claude-sonnet-4-20250514", max_tokens=2000, messages=[{"role":"user","content":imgs+[{"type":"text","text":prompt}]}])
        raw = r.content[0].text.strip().replace("```json","").replace("```","").strip()
        result = json.loads(raw)
        chat_id = q.message.chat_id
        icons = {"instagram":"📸","tiktok":"🎬"}
        for post in result.get("posts",[]):
            icon = icons.get(post["platform"],"✦")
            music = f"\n🎵 {post[chr(109)+chr(117)+chr(115)+chr(105)+chr(99)]}" if post.get("music") else ""
            msg = f"{icon} *{post[chr(112)+chr(108)+chr(97)+chr(116)+chr(102)+chr(111)+chr(114)+chr(109)].upper()} {post.get(chr(108)+chr(97)+chr(110)+chr(103),chr(32)).upper()}*\n\n{post.get(chr(116)+chr(101)+chr(120)+chr(116),chr(32))}\n\n{post.get(chr(104)+chr(97)+chr(115)+chr(104)+chr(116)+chr(97)+chr(103)+chr(115),chr(32))}{music}"
            await ctx.bot.send_message(chat_id, msg[:4000], parse_mode="Markdown")
        kb = [[InlineKeyboardButton("Новий пост", callback_data="new")]]
        await ctx.bot.send_message(chat_id, "Готово! Копіюй і публікуй.", reply_markup=InlineKeyboardMarkup(kb))
    except Exception as e:
        await q.message.reply_text(f"Помилка: {str(e)[:200]}")
    user_sessions[uid] = {"files":[],"description":"","step":"idle"}

async def cmd_week(update, ctx):
    await update.message.reply_text("Генерую план...")
    prompt = f"{BRAND}\nКонтент-план 7 днів Instagram+TikTok. Щодня 1 пост кожної платформи. Чергуй: стіл-зигзаг, стелаж, двері, кухня, закулісся. Формат: День X - Тема\n📸 Instagram: опис\n🎬 TikTok: сценарій\n⏰ 18:00/19:30"
    try:
        r = client.messages.create(model="claude-sonnet-4-20250514", max_tokens=2000, messages=[{"role":"user","content":prompt}])
        plan = r.content[0].text
        await update.message.reply_text(plan[:4000])
        if len(plan) > 4000: await update.message.reply_text(plan[4000:8000])
    except Exception as e: await update.message.reply_text(f"Помилка: {e}")

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
