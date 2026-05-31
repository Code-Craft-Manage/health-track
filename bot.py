"""Health-Track — a lightweight weekly body-measurement Telegram bot.

Flow:
  * The authorized user runs /track (or taps the button in the weekly reminder).
  * The bot asks for each measurement one at a time, validating that each is a
    number (a comma decimal like ``82,5`` is accepted and normalised to ``82.5``).
  * It shows a summary and asks for confirmation, then appends a row to a Google
    Sheet.
  * A proactive reminder is sent every Saturday at 14:00 (America/Sao_Paulo).

Only users whose IDs are listed in ``AUTHORIZED_USER_ID`` may use the bot
(one ID, or several comma-separated).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, time
from functools import wraps
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

import config
from sheets import FIELDS, append_measurements

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("health-track")

# python-telegram-bot logs full Telegram API URLs (which embed the bot token)
# via httpx at INFO level. Raise httpx to WARNING so the token never hits logs.
logging.getLogger("httpx").setLevel(logging.WARNING)

TZ = ZoneInfo(config.TIMEZONE)

# In python-telegram-bot, run_daily `days` are 0-6 == Sunday-Saturday.
SATURDAY = 6

# Prompt + display metadata per measurement key (everything except "date").
# key -> (short label, unit, prompt shown to the user)
_MEASUREMENTS: dict[str, tuple[str, str, str]] = {
    "weight": ("Weight", "kg", "Please enter your current *Weight* in kg (e.g. 82.5):"),
    "neck": ("Neck", "cm", "Got it 👍 Now your *Neck* in cm:"),
    "shoulders": ("Shoulders", "cm", "Now your *Shoulders* in cm:"),
    "chest": ("Chest", "cm", "Now your *Chest* in cm:"),
    "biceps_left": ("Biceps Left", "cm", "Now your *Left Biceps* in cm:"),
    "biceps_right": ("Biceps Right", "cm", "Now your *Right Biceps* in cm:"),
    "waist": ("Waist", "cm", "Now your *Waist* in cm:"),
    "abdomen": ("Abdomen", "cm", "Now your *Abdomen* in cm:"),
    "hips": ("Hips", "cm", "Now your *Hips* in cm:"),
    "thigh_left": ("Thigh Left", "cm", "Now your *Left Thigh* in cm:"),
    "thigh_right": ("Thigh Right", "cm", "Now your *Right Thigh* in cm:"),
    "calf_left": ("Calf Left", "cm", "Now your *Left Calf* in cm:"),
    "calf_right": ("Calf Right", "cm", "And finally, your *Right Calf* in cm:"),
}

# Ordered (key, label, unit, prompt), following the sheet's column order. Built
# from sheets.FIELDS so the conversation can never drift from the spreadsheet.
# Raises KeyError here at import time if a field is missing a prompt.
STEPS: list[tuple[str, str, str, str]] = [
    (key, *_MEASUREMENTS[key]) for key, _ in FIELDS if key != "date"
]

# Conversation states: one integer per measurement step (0 .. len-1), then a
# final confirmation state, then the /start greeting's yes/no state.
# Measurement state N is simply index N into STEPS.
CONFIRM = len(STEPS)
ASK_RECORD = len(STEPS) + 1


def _fmt(value: float) -> str:
    """Render a number without a trailing ``.0`` (39.0 -> "39", 82.5 -> "82.5")."""
    return str(int(value)) if float(value).is_integer() else str(value)


def restricted(func):
    """Allow only the configured user; reject everyone else politely."""

    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user = update.effective_user
        if user is None or user.id not in config.AUTHORIZED_USER_IDS:
            logger.warning("Unauthorized access attempt by user_id=%s", getattr(user, "id", None))
            if update.callback_query:
                await update.callback_query.answer("Not authorized.", show_alert=True)
            elif update.message:
                await update.message.reply_text(
                    "Sorry, this is a private bot and you are not authorized to use it."
                )
            return ConversationHandler.END
        return await func(update, context, *args, **kwargs)

    return wrapper


@restricted
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 I track your weekly body measurements and log them to your Google Sheet.\n\n"
        "• /start — say hi and choose to record now\n"
        "• /track — jump straight into a check-in\n"
        "• /cancel — abort the current check-in\n\n"
        "I'll also remind you every Saturday at 14:00."
    )


@restricted
async def greet(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """/start — say hi and ask whether to record measurements right now."""
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Yes, let's go", callback_data="rec_yes"),
                InlineKeyboardButton("Not now", callback_data="rec_no"),
            ]
        ]
    )
    await update.message.reply_text(
        "Hi! 👋 I'm your body-measurement tracker.\n\n"
        "Do you want to record your measurements now?",
        reply_markup=keyboard,
    )
    return ASK_RECORD


@restricted
async def on_record_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle the Yes/Not-now answer to the /start greeting."""
    query = update.callback_query
    await query.answer()
    if query.data == "rec_no":
        await query.edit_message_text(
            "No problem! Send /start or /track whenever you're ready. 💪"
        )
        return ConversationHandler.END
    # rec_yes — begin the measurement flow immediately (any time, not just Saturday)
    context.user_data.clear()
    context.user_data["values"] = {}
    await query.edit_message_text("Great! Let's record your measurements. 💪")
    await context.bot.send_message(
        chat_id=update.effective_chat.id, text=STEPS[0][3], parse_mode="Markdown"
    )
    return 0


@restricted
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry point for /track and for the reminder's inline button."""
    context.user_data.clear()
    context.user_data["values"] = {}

    first_prompt = STEPS[0][3]
    if update.callback_query:
        # Triggered by tapping the reminder button.
        await update.callback_query.answer()
        await update.callback_query.edit_message_text("Let's do your weekly check-in! 💪")
        await context.bot.send_message(
            chat_id=update.effective_chat.id, text=first_prompt, parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(
            "Good day! Time for your weekly check-in.\n\n" + first_prompt,
            parse_mode="Markdown",
        )
    return 0


@restricted
async def handle_measurement(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Validate and store one measurement, then prompt for the next (or summarise)."""
    values: dict = context.user_data.setdefault("values", {})
    step = len(values)  # the index of the field we're expecting now
    if step >= len(STEPS):
        return CONFIRM

    key, label, _unit, _prompt = STEPS[step]
    raw = (update.message.text or "").strip().replace(",", ".")
    try:
        value = float(raw)
    except ValueError:
        await update.message.reply_text(
            f"Hmm, that doesn't look like a number. Please enter your *{label}* "
            f"as a number (e.g. 82.5):",
            parse_mode="Markdown",
        )
        return step
    if value <= 0:
        await update.message.reply_text(
            f"Please enter a positive number for your *{label}* (e.g. 82.5):",
            parse_mode="Markdown",
        )
        return step

    values[key] = round(value, 2)
    next_step = len(values)
    if next_step < len(STEPS):
        await update.message.reply_text(STEPS[next_step][3], parse_mode="Markdown")
        return next_step

    return await _show_summary(update, context)


async def _show_summary(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    values: dict = context.user_data["values"]
    date_str = datetime.now(TZ).strftime("%d/%m/%Y")
    context.user_data["date"] = date_str

    lines = [f"📋 *Weekly check-in — {date_str}*", ""]
    for key, label, unit, _prompt in STEPS:
        lines.append(f"• {label}: {_fmt(values[key])} {unit}")
    lines.append("")
    lines.append("Save this entry?")

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Confirm", callback_data="confirm"),
                InlineKeyboardButton("❌ Cancel", callback_data="cancel"),
            ]
        ]
    )
    await update.message.reply_text(
        "\n".join(lines), reply_markup=keyboard, parse_mode="Markdown"
    )
    return CONFIRM


@restricted
async def on_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "cancel":
        context.user_data.clear()
        await query.edit_message_text("Check-in cancelled. Nothing was saved.")
        return ConversationHandler.END

    data = {"date": context.user_data["date"], **context.user_data["values"]}
    try:
        # gspread is synchronous; run it off the event loop.
        await asyncio.to_thread(append_measurements, data)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to append row to Google Sheets")
        await query.edit_message_text(
            f"⚠️ Could not save to Google Sheets: {exc}\n\nPlease try again later."
        )
        context.user_data.clear()
        return ConversationHandler.END

    context.user_data.clear()
    await query.edit_message_text("Data successfully logged! Keep up the great work! 💪")
    return ConversationHandler.END


@restricted
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text("Check-in cancelled.")
    return ConversationHandler.END


async def weekly_reminder(context: ContextTypes.DEFAULT_TYPE) -> None:
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("📋 Start check-in", callback_data="start_track")]]
    )
    for uid in config.AUTHORIZED_USER_IDS:
        await context.bot.send_message(
            chat_id=uid,
            text="Good afternoon! 💪 Time for your weekly body-measurement check-in.",
            reply_markup=keyboard,
        )


def main() -> None:
    application = ApplicationBuilder().token(config.TELEGRAM_BOT_TOKEN).build()

    conversation = ConversationHandler(
        entry_points=[
            CommandHandler("start", greet),
            CommandHandler("track", start),
            CallbackQueryHandler(start, pattern="^start_track$"),
        ],
        states={
            ASK_RECORD: [CallbackQueryHandler(on_record_choice, pattern="^rec_(yes|no)$")],
            # Every measurement step shares one handler; it knows which field to
            # expect from how many values have been collected so far.
            **{i: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_measurement)]
               for i in range(len(STEPS))},
            CONFIRM: [CallbackQueryHandler(on_confirm, pattern="^(confirm|cancel)$")],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    application.add_handler(conversation)
    application.add_handler(CommandHandler("help", help_command))

    # Proactive weekly reminder: Saturday at 14:00 local time.
    application.job_queue.run_daily(
        weekly_reminder,
        time=time(hour=14, minute=0, tzinfo=TZ),
        days=(SATURDAY,),
        name="weekly_reminder",
    )

    logger.info("Health-Track bot starting (timezone=%s)…", config.TIMEZONE)
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
