import os
import io
import base64
import logging
import json
import asyncio
from pathlib import Path
from PIL import Image, ImageEnhance, ImageFilter, ImageDraw
import anthropic
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN_PHOTO", "")
ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY", "")
TMP = Path("/tmp/photo_agent")
TMP.mkdir(exist_ok=True)

client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

BACKGROUNDS = {
    "studio_white": (245, 245, 240),
    "studio_warm": (255, 248, 235),
    "studio_dark": (30, 30, 35),
    "studio_beige": (235, 220, 200),
    "studio_gray": (180, 180, 185),
}

BACKGROUND_LABELS = {
    "studio_white": "⬜ Білий студійний",
    "studio_warm": "🟡 Теплий кремовий",
    "studio_dark": "⬛ Темний преміум",
    "studio_beige": "🟤 Бежевий натуральний",
    "studio_gray": "🔘 Сірий мінімалізм",
}

sessions = {}


def sess(uid):
    if uid not in sessions:
        sessions[uid] = {"photo_path": None, "instructions": "", "bg": None}
    return sessions[uid]


async def cmd_start(update: Update, ctx):
    uid = update.effective_user.id
    sessions[uid] = {"photo_path": None, "instructions": "", "bg": None}
    await update.message.reply_text(
        "📸 *Фото-агент Arden Wood*\n\n"
        "Надішли фото меблів — я:\n"
        "• Поставлю студійний фон\n"
        "• Покращу яскравість і контраст\n"
        "• Підготую для реклами 1080×1080\n\n"
        "Просто надішли фото!",
        parse_mode="Markdown"
    )


async def handle_photo(update: Update, ctx):
    uid = update.effective_user.id
    s = sess(uid)
    try:
        photo = update.message.photo[-1]
        f = await ctx.bot.get_file(photo.file_id)
        path = TMP / f"{uid}_original.jpg"
        await f.download_to_drive(path)
        s["photo_path"] = str(path)
        s["instructions"] = (update.message.caption or "").strip()

        kb = [
            [InlineKeyboardButton(BACKGROUND_LABELS[k], callback_data=f"bg_{k}")]
            for k in BACKGROUNDS
        ]
        await update.message.reply_text(
            "✅ Фото отримано! Обери фон:",
            reply_markup=InlineKeyboardMarkup(kb)
        )
    except Exception as e:
        logger.error(f"handle_photo: {e}", exc_info=True)
        await update.message.reply_text(f"Помилка: {str(e)[:200]}")


async def handle_text(update: Update, ctx):
    uid = update.effective_user.id
    s = sess(uid)
    if not s["photo_path"]:
        await update.message.reply_text("Спочатку надішли фото!")
        return
    s["instructions"] = update.message.text.strip()
    await update.message.reply_text("Інструкції збережено. Тепер обери фон.")


async def btn_handler(update: Update, ctx):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    s = sess(uid)
    chat_id = q.message.chat_id

    if q.data.startswith("bg_"):
        bg_key = q.data[3:]
        s["bg"] = bg_key
        if not s["photo_path"]:
            await q.edit_message_text("Спочатку надішли фото!")
            return
        await q.edit_message_text(f"Фон: {BACKGROUND_LABELS.get(bg_key, bg_key)}\nОбробляю... ⏳")
        # Запускаємо в окремій задачі — не блокує бота
        asyncio.create_task(process_photo(ctx, uid, chat_id, bg_key, s["instructions"], s["photo_path"]))

    elif q.data == "new":
        sessions[uid] = {"photo_path": None, "instructions": "", "bg": None}
        await q.edit_message_text("Готово! Надсилай нове фото.")

    elif q.data == "change_bg":
        kb = [
            [InlineKeyboardButton(BACKGROUND_LABELS[k], callback_data=f"bg_{k}")]
            for k in BACKGROUNDS
        ]
        await q.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(kb))


def do_image_processing(photo_path, bg_key, params):
    """Важка CPU робота — виконується в executor"""
    img = Image.open(photo_path).convert("RGB")
    W_orig, H_orig = img.size

    bg_color = BACKGROUNDS.get(bg_key, BACKGROUNDS["studio_warm"])

    W, H = 1080, 1080
    canvas = Image.new("RGB", (W, H), bg_color)

    target_h = int(H * 0.72)
    target_w = int(W * 0.82)
    scale = min(target_w / W_orig, target_h / H_orig)
    new_w = int(W_orig * scale)
    new_h = int(H_orig * scale)
    img_resized = img.resize((new_w, new_h), Image.LANCZOS)

    x = (W - new_w) // 2
    y = (H - new_h) // 2 + 15

    if params.get("shadow", True):
        shadow_layer = Image.new("RGB", (W, H), bg_color)
        shadow_draw = ImageDraw.Draw(shadow_layer)
        sw = int(new_w * 0.85)
        sh = int(new_h * 0.05)
        sx = x + (new_w - sw) // 2
        sy = y + new_h - sh // 2
        shadow_draw.ellipse([sx, sy, sx + sw, sy + sh],
                            fill=tuple(max(0, c - 25) for c in bg_color))
        shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(18))
        canvas = Image.blend(canvas, shadow_layer, 0.45)

    canvas.paste(img_resized, (x, y))

    brightness = params.get("brightness", 1.05)
    contrast = params.get("contrast", 1.15)
    canvas = ImageEnhance.Brightness(canvas).enhance(brightness)
    canvas = ImageEnhance.Contrast(canvas).enhance(contrast)

    buf = io.BytesIO()
    canvas.save(buf, format="JPEG", quality=95)
    buf.seek(0)
    return buf


async def process_photo(ctx, uid, chat_id, bg_key, instructions, photo_path):
    try:
        await ctx.bot.send_chat_action(chat_id, "upload_photo")

        # Кодуємо фото для Claude (в executor щоб не блокувати)
        def read_image():
            img = Image.open(photo_path).convert("RGB")
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=80)
            return base64.standard_b64encode(buf.getvalue()).decode()

        loop = asyncio.get_event_loop()
        img_data = await loop.run_in_executor(None, read_image)

        # Запит до Claude
        prompt = (
            "Ти дизайнер-фотограф Arden Wood (меблі з дуба).\n"
            f"Інструкції: '{instructions or 'немає'}'\n"
            f"Фон: {BACKGROUND_LABELS.get(bg_key, bg_key)}\n\n"
            "Відповідай ТІЛЬКИ JSON (без markdown):\n"
            '{"item":"назва предмету","brightness":1.05,"contrast":1.15,"shadow":true}'
        )

        r = await loop.run_in_executor(None, lambda: client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=150,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": img_data}},
                {"type": "text", "text": prompt}
            ]}]
        ))

        raw = r.content[0].text.strip()
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        params = json.loads(raw.strip())
        logger.info(f"uid={uid} params={params}")

        # Обробка зображення в executor
        result_buf = await loop.run_in_executor(None, do_image_processing, photo_path, bg_key, params)

        item_name = params.get("item", "меблевий виріб")
        caption = f"✅ {item_name.capitalize()}\nФон: {BACKGROUND_LABELS.get(bg_key, '')}"
        if instructions:
            caption += f"\n💬 {instructions}"

        kb = [[
            InlineKeyboardButton("🔄 Інший фон", callback_data="change_bg"),
            InlineKeyboardButton("🆕 Нове фото", callback_data="new"),
        ]]

        await ctx.bot.send_photo(
            chat_id, result_buf,
            caption=caption,
            reply_markup=InlineKeyboardMarkup(kb)
        )

    except json.JSONDecodeError as e:
        logger.error(f"JSON error: {e}, raw={raw[:100]}")
        # Використовуємо дефолтні параметри
        params = {"brightness": 1.05, "contrast": 1.15, "shadow": True, "item": "меблевий виріб"}
        loop = asyncio.get_event_loop()
        result_buf = await loop.run_in_executor(None, do_image_processing, photo_path, bg_key, params)
        kb = [[InlineKeyboardButton("🆕 Нове фото", callback_data="new")]]
        await ctx.bot.send_photo(chat_id, result_buf, caption="✅ Готово!", reply_markup=InlineKeyboardMarkup(kb))

    except Exception as e:
        logger.error(f"process_photo uid={uid}: {e}", exc_info=True)
        await ctx.bot.send_message(chat_id, f"❌ Помилка: {str(e)[:200]}\n\nСпробуй /start")

    finally:
        sessions[uid] = {"photo_path": photo_path, "instructions": instructions, "bg": bg_key}


def main():
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN_PHOTO is not set!")
    if not ANTHROPIC_KEY:
        raise ValueError("ANTHROPIC_API_KEY is not set!")
    logger.info("Photo Agent Bot starting...")
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(CallbackQueryHandler(btn_handler))
    logger.info("Photo Agent Bot running!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
