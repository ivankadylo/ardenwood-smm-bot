import os
import io
import base64
import logging
import httpx
from pathlib import Path
from PIL import Image, ImageEnhance, ImageFilter
import rembg
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
sessions = {}

BRAND = "Arden Wood — меблі з масиву дуба, Ostrava CZ. Студійна фотографія для реклами."

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
        "• Приберу фон\n"
        "• Поставлю студійний фон\n"
        "• Додам деталі за твоїми інструкціями\n"
        "• Підготую для реклами\n\n"
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

        # Показуємо вибір фону
        kb = [
            [InlineKeyboardButton(BACKGROUND_LABELS[k], callback_data=f"bg_{k}")]
            for k in BACKGROUNDS
        ]
        kb.append([InlineKeyboardButton("✨ AI сам обере кращий", callback_data="bg_auto")])
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
    if s["bg"]:
        # Є фото і фон — додаткові інструкції для обробки
        s["instructions"] = update.message.text.strip()
        await update.message.reply_text("Застосовую інструкції, зачекай...")
        await process_photo(ctx, uid, update.effective_chat.id)
    else:
        s["instructions"] = update.message.text.strip()
        await update.message.reply_text("Інструкції збережено. Тепер обери фон або надішли нове фото.")


async def btn(update: Update, ctx):
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
        instr = s["instructions"]
        if instr:
            await q.edit_message_text(f"Фон: {BACKGROUND_LABELS.get(bg_key, 'Auto')}\nІнструкції: {instr}\n\nОбробляю...")
        else:
            await q.edit_message_text(f"Фон: {BACKGROUND_LABELS.get(bg_key, 'Auto')}\n\nОбробляю...")
        await process_photo(ctx, uid, chat_id)

    elif q.data == "new":
        sessions[uid] = {"photo_path": None, "instructions": "", "bg": None}
        await q.edit_message_text("Готово! Надсилай нове фото.")


async def process_photo(ctx, uid, chat_id):
    s = sess(uid)
    photo_path = s["photo_path"]
    instructions = s["instructions"]
    bg_key = s["bg"]

    try:
        await ctx.bot.send_chat_action(chat_id, "upload_photo")

        # 1. Завантажуємо оригінал
        img = Image.open(photo_path).convert("RGBA")

        # 2. Аналізуємо фото через Claude
        with open(photo_path, "rb") as f:
            img_data = base64.standard_b64encode(f.read()).decode()

        analysis_prompt = (
            f"{BRAND}\n"
            f"Користувач надіслав фото меблів з інструкціями: '{instructions or 'немає'}'\n"
            f"Вибраний фон: {BACKGROUND_LABELS.get(bg_key, 'авто')}\n\n"
            "Як дизайнер-фотограф, опиши:\n"
            "1. Що на фото (який меблевий виріб)\n"
            "2. Яскравість/контраст який потрібно застосувати (числа 0.5-2.0)\n"
            "3. Чи потрібна тінь під предметом (так/ні)\n"
            "4. Яке вирівнювання предмету (ліво/центр/право)\n"
            "Відповідай ТІЛЬКИ JSON: {\"item\":\"...\",\"brightness\":1.1,\"contrast\":1.2,\"shadow\":true,\"align\":\"center\"}"
        )

        r = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=300,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": img_data}},
                {"type": "text", "text": analysis_prompt}
            ]}]
        )

        import json
        raw = r.content[0].text.strip()
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        params = json.loads(raw.strip())
        logger.info(f"AI params: {params}")

        # 3. Прибираємо фон (rembg)
        await ctx.bot.send_chat_action(chat_id, "upload_photo")
        with open(photo_path, "rb") as f:
            no_bg_data = rembg.remove(f.read())
        subject = Image.open(io.BytesIO(no_bg_data)).convert("RGBA")

        # 4. Визначаємо колір фону
        if bg_key == "auto":
            # Claude обрав — беремо теплий для дерева
            bg_color = BACKGROUNDS["studio_warm"]
        else:
            bg_color = BACKGROUNDS.get(bg_key, BACKGROUNDS["studio_white"])

        # 5. Створюємо фінальне зображення
        W, H = 1080, 1080  # Instagram square
        canvas = Image.new("RGBA", (W, H), (*bg_color, 255))

        # Масштабуємо предмет — займає 75% висоти
        sw, sh = subject.size
        scale = min((W * 0.75) / sw, (H * 0.75) / sh)
        new_w, new_h = int(sw * scale), int(sh * scale)
        subject = subject.resize((new_w, new_h), Image.LANCZOS)

        # Позиціонуємо
        align = params.get("align", "center")
        if align == "left":
            x = int(W * 0.1)
        elif align == "right":
            x = int(W * 0.9 - new_w)
        else:
            x = (W - new_w) // 2
        y = (H - new_h) // 2 + int(H * 0.03)  # трохи нижче центру

        # 6. Тінь під предметом
        if params.get("shadow", True):
            shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            shadow_ellipse = Image.new("RGBA", (new_w, int(new_h * 0.08)), (0, 0, 0, 0))
            from PIL import ImageDraw
            draw = ImageDraw.Draw(shadow_ellipse)
            draw.ellipse([0, 0, new_w, int(new_h * 0.08)], fill=(0, 0, 0, 60))
            shadow_ellipse = shadow_ellipse.filter(ImageFilter.GaussianBlur(15))
            shadow.paste(shadow_ellipse, (x, y + new_h - int(new_h * 0.04)), shadow_ellipse)
            shadow = shadow.filter(ImageFilter.GaussianBlur(8))
            canvas = Image.alpha_composite(canvas, shadow)

        # 7. Вставляємо предмет
        canvas.paste(subject, (x, y), subject)

        # 8. Корекція яскравості/контрасту
        canvas_rgb = canvas.convert("RGB")
        brightness = params.get("brightness", 1.05)
        contrast = params.get("contrast", 1.1)
        canvas_rgb = ImageEnhance.Brightness(canvas_rgb).enhance(brightness)
        canvas_rgb = ImageEnhance.Contrast(canvas_rgb).enhance(contrast)

        # 9. Зберігаємо і відправляємо
        out_path = TMP / f"{uid}_result.jpg"
        canvas_rgb.save(out_path, "JPEG", quality=95)

        item_name = params.get("item", "меблевий виріб")
        caption = f"✅ Готово! {item_name.capitalize()}, фон: {BACKGROUND_LABELS.get(bg_key, 'авто')}"
        if instructions:
            caption += f"\nІнструкції: {instructions}"

        kb = [
            [
                InlineKeyboardButton("🔄 Інший фон", callback_data="change_bg"),
                InlineKeyboardButton("📝 Нові інструкції", callback_data="new_instructions"),
            ],
            [InlineKeyboardButton("🆕 Нове фото", callback_data="new")]
        ]

        with open(out_path, "rb") as f:
            await ctx.bot.send_photo(chat_id, f, caption=caption, reply_markup=InlineKeyboardMarkup(kb))

    except Exception as e:
        logger.error(f"process_photo: {e}", exc_info=True)
        await ctx.bot.send_message(chat_id, f"Помилка обробки: {str(e)[:300]}\n\nСпробуй /start")
    finally:
        sessions[uid] = {"photo_path": photo_path, "instructions": instructions, "bg": bg_key}


async def btn_change_bg(update: Update, ctx):
    """Повторний вибір фону для того ж фото"""
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    s = sess(uid)
    if not s["photo_path"]:
        await q.edit_message_text("Надішли нове фото!")
        return
    kb = [
        [InlineKeyboardButton(BACKGROUND_LABELS[k], callback_data=f"bg_{k}")]
        for k in BACKGROUNDS
    ]
    kb.append([InlineKeyboardButton("✨ AI сам обере кращий", callback_data="bg_auto")])
    await q.edit_message_text("Обери інший фон:", reply_markup=InlineKeyboardMarkup(kb))


async def btn_handler(update: Update, ctx):
    q = update.callback_query
    if q.data == "change_bg":
        await btn_change_bg(update, ctx)
    elif q.data == "new_instructions":
        await q.answer()
        uid = q.from_user.id
        sess(uid)
        await q.edit_message_caption(
            caption="Напиши нові інструкції для обробки (наприклад: 'додай більше контрасту, посунь вліво')"
        )
    else:
        await btn(update, ctx)


def main():
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN_PHOTO is not set!")
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
