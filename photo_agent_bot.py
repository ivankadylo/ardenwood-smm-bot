import os
import io
import base64
import logging
import json
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
    "auto": "✨ AI обере кращий",
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
            "Фото отримано! Обери фон:",
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
        await process_photo(ctx, uid, chat_id)

    elif q.data == "new":
        sessions[uid] = {"photo_path": None, "instructions": "", "bg": None}
        await q.edit_message_text("Готово! Надсилай нове фото.")

    elif q.data == "change_bg":
        kb = [
            [InlineKeyboardButton(BACKGROUND_LABELS[k], callback_data=f"bg_{k}")]
            for k in BACKGROUNDS
        ]
        await q.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(kb))


async def process_photo(ctx, uid, chat_id):
    s = sess(uid)
    photo_path = s["photo_path"]
    instructions = s["instructions"]
    bg_key = s.get("bg", "studio_warm")

    try:
        await ctx.bot.send_chat_action(chat_id, "upload_photo")

        # Завантажуємо оригінал
        img = Image.open(photo_path).convert("RGB")
        W_orig, H_orig = img.size

        # Кодуємо для Claude
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        img_data = base64.standard_b64encode(buf.getvalue()).decode()

        # Запитуємо Claude про параметри обробки
        prompt = (
            "Ти дизайнер-фотограф для меблевого бренду Arden Wood.\n"
            f"Інструкції від користувача: '{instructions or 'немає'}'\n"
            f"Вибраний фон: {BACKGROUND_LABELS.get(bg_key, bg_key)}\n\n"
            "Проаналізуй фото і дай параметри обробки.\n"
            "Відповідай ТІЛЬКИ JSON без markdown:\n"
            '{"item":"назва предмету","brightness":1.05,"contrast":1.15,"shadow":true,"crop_ratio":0.8}'
            "\nbrightness: 0.8-1.3, contrast: 0.9-1.4, shadow: true/false, crop_ratio: 0.6-0.95 (частина висоти яку займає предмет)"
        )

        r = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=200,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": img_data}},
                {"type": "text", "text": prompt}
            ]}]
        )

        raw = r.content[0].text.strip()
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        params = json.loads(raw.strip())
        logger.info(f"AI params: {params}")

        # Визначаємо колір фону
        if bg_key == "auto":
            bg_color = BACKGROUNDS["studio_warm"]
            bg_key_display = "studio_warm"
        else:
            bg_color = BACKGROUNDS.get(bg_key, BACKGROUNDS["studio_warm"])
            bg_key_display = bg_key

        # Створюємо 1080x1080 полотно
        W, H = 1080, 1080
        canvas = Image.new("RGB", (W, H), bg_color)

        # Масштабуємо оригінальне фото — предмет займає ~70% висоти
        crop_ratio = params.get("crop_ratio", 0.8)
        target_h = int(H * 0.70)
        target_w = int(W * 0.80)
        scale = min(target_w / W_orig, target_h / H_orig)
        new_w = int(W_orig * scale)
        new_h = int(H_orig * scale)
        img_resized = img.resize((new_w, new_h), Image.LANCZOS)

        # Центруємо трохи нижче центру
        x = (W - new_w) // 2
        y = (H - new_h) // 2 + 20

        # Тінь
        if params.get("shadow", True):
            shadow_layer = Image.new("RGB", (W, H), bg_color)
            shadow_draw = ImageDraw.Draw(shadow_layer)
            shadow_w = int(new_w * 0.85)
            shadow_h = int(new_h * 0.06)
            shadow_x = x + (new_w - shadow_w) // 2
            shadow_y = y + new_h - shadow_h // 2
            shadow_draw.ellipse(
                [shadow_x, shadow_y, shadow_x + shadow_w, shadow_y + shadow_h],
                fill=tuple(max(0, c - 30) for c in bg_color)
            )
            shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(20))
            canvas = Image.blend(canvas, shadow_layer, 0.5)

        # Вставляємо фото
        canvas.paste(img_resized, (x, y))

        # Корекція
        brightness = params.get("brightness", 1.05)
        contrast = params.get("contrast", 1.15)
        canvas = ImageEnhance.Brightness(canvas).enhance(brightness)
        canvas = ImageEnhance.Contrast(canvas).enhance(contrast)

        # Зберігаємо
        out_path = TMP / f"{uid}_result.jpg"
        canvas.save(out_path, "JPEG", quality=95)

        item_name = params.get("item", "меблевий виріб")
        caption = f"✅ {item_name.capitalize()}\nФон: {BACKGROUND_LABELS.get(bg_key_display, '')}"
        if instructions:
            caption += f"\n💬 {instructions}"

        kb = [
            [
                InlineKeyboardButton("🔄 Інший фон", callback_data="change_bg"),
                InlineKeyboardButton("🆕 Нове фото", callback_data="new"),
            ]
        ]

        with open(out_path, "rb") as f:
            await ctx.bot.send_photo(
                chat_id, f,
                caption=caption,
                reply_markup=InlineKeyboardMarkup(kb)
            )

    except Exception as e:
        logger.error(f"process_photo: {e}", exc_info=True)
        await ctx.bot.send_message(chat_id, f"Помилка: {str(e)[:300]}\n\nСпробуй /start")
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
