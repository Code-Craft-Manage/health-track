"""Health-Track — a lightweight body-measurement Telegram bot.

Flow:
  * The user types /start (or just "Hi"), or taps the weekly reminder button.
  * The bot shows a menu: log Height only, Weight only, Measurements (all tape
    measurements, i.e. everything except weight and height), or Pick a custom
    subset.
  * It asks for each chosen measurement one at a time, validating numbers (a
    comma decimal like ``82,5`` is accepted and normalised to ``82.5``).
  * It shows a summary, asks for confirmation, then appends a dated row to the
    user's own tab in the Google Sheet (columns not logged are left blank).
  * A proactive reminder is sent every Saturday at 14:00 (America/Sao_Paulo).

Only users whose IDs are listed in ``AUTHORIZED_USER_ID`` may use the bot
(one ID, or several comma-separated). Each user logs to their own tab
(see ``config.USER_TABS``).
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

# Per-measurement metadata: key -> (short label, unit, prompt shown to the user).
_MEASUREMENTS: dict[str, tuple[str, str, str]] = {
    "weight": ("Weight", "kg", "Please enter your *Weight* in kg (e.g. 82.5):"),
    "height": ("Height", "cm", "Please enter your *Height* in cm (e.g. 175):"),
    "neck": ("Neck", "cm", "Please enter your *Neck* in cm:"),
    "shoulders": ("Shoulders", "cm", "Please enter your *Shoulders* in cm:"),
    "chest": ("Chest", "cm", "Please enter your *Chest* in cm:"),
    "biceps_left": ("Biceps Left", "cm", "Please enter your *Left Biceps* in cm:"),
    "biceps_right": ("Biceps Right", "cm", "Please enter your *Right Biceps* in cm:"),
    "waist": ("Waist", "cm", "Please enter your *Waist* in cm:"),
    "abdomen": ("Abdomen", "cm", "Please enter your *Abdomen* in cm:"),
    "hips": ("Hips", "cm", "Please enter your *Hips* in cm:"),
    "thigh_left": ("Thigh Left", "cm", "Please enter your *Left Thigh* in cm:"),
    "thigh_right": ("Thigh Right", "cm", "Please enter your *Right Thigh* in cm:"),
    "calf_left": ("Calf Left", "cm", "Please enter your *Left Calf* in cm:"),
    "calf_right": ("Calf Right", "cm", "Please enter your *Right Calf* in cm:"),
}

# Canonical measurement order (everything except the date), from the sheet.
# Raises KeyError here at import time if a field is missing its metadata.
ALL_KEYS: list[str] = [key for key, _ in FIELDS if key != "date"]
_ = [_MEASUREMENTS[k] for k in ALL_KEYS]  # fail fast if a prompt is missing

# Tape measurements: everything except weight (logged often, on its own) and
# height (logged rarely, on its own).
TAPE_KEYS: list[str] = [k for k in ALL_KEYS if k not in ("weight", "height")]

# Conversation states.
MENU, SELECT, COLLECTING, CONFIRM = range(4)


def _fmt(value: float) -> str:
    """Render a number without a trailing ``.0`` (39.0 -> "39", 82.5 -> "82.5")."""
    return str(int(value)) if float(value).is_integer() else str(value)


def restricted(func):
    """Allow only the configured users; reject everyone else politely."""

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
        "👋 I track your body measurements and log them to your Google Sheet.\n\n"
        "• /start (or just say “Hi”) — open the menu to log height, weight, "
        "the tape measurements, or pick a custom set\n"
        "• /cancel — abort the current entry\n\n"
        "I'll also remind you every Saturday at 14:00."
    )


# --- Menu -----------------------------------------------------------------

def _menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📏 Height only", callback_data="menu_height")],
            [InlineKeyboardButton("⚖️ Weight only", callback_data="menu_weight")],
            [InlineKeyboardButton("🧍 Measurements", callback_data="menu_measurements")],
            [InlineKeyboardButton("🧩 Pick measurements…", callback_data="menu_pick")],
            [InlineKeyboardButton("❌ Cancel", callback_data="menu_cancel")],
        ]
    )


@restricted
async def show_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry point (/start, /track, "Hi", reminder button): show the log menu."""
    context.user_data.clear()
    text = "Hi! 👋 What would you like to log?"
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text, reply_markup=_menu_keyboard())
    else:
        await update.message.reply_text(text, reply_markup=_menu_keyboard())
    return MENU


@restricted
async def on_menu_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    choice = query.data

    if choice == "menu_cancel":
        await query.edit_message_text("No problem! Send /start or “Hi” whenever you're ready. 💪")
        return ConversationHandler.END
    if choice == "menu_height":
        context.user_data["pending"] = ["height"]
        return await _begin_collecting(update, context)
    if choice == "menu_weight":
        context.user_data["pending"] = ["weight"]
        return await _begin_collecting(update, context)
    if choice == "menu_measurements":
        context.user_data["pending"] = list(TAPE_KEYS)
        return await _begin_collecting(update, context)
    if choice == "menu_pick":
        context.user_data["selected"] = set()
        await query.edit_message_text(
            "Tap the measurements you want to log, then press *Done*:",
            reply_markup=_select_keyboard(set()),
            parse_mode="Markdown",
        )
        return SELECT
    return MENU


# --- Pick (multi-select) --------------------------------------------------

def _select_keyboard(selected: set[str]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for key in ALL_KEYS:
        mark = "✅" if key in selected else "▫️"
        row.append(
            InlineKeyboardButton(f"{mark} {_MEASUREMENTS[key][0]}", callback_data=f"pick:{key}")
        )
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append(
        [
            InlineKeyboardButton("✅ Done", callback_data="pick_done"),
            InlineKeyboardButton("❌ Cancel", callback_data="pick_cancel"),
        ]
    )
    return InlineKeyboardMarkup(rows)


@restricted
async def on_select(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    data = query.data
    selected: set = context.user_data.setdefault("selected", set())

    if data == "pick_cancel":
        await query.answer()
        await query.edit_message_text("Cancelled. Send /start or “Hi” anytime. 💪")
        return ConversationHandler.END

    if data == "pick_done":
        if not selected:
            await query.answer("Pick at least one measurement first.", show_alert=True)
            return SELECT
        await query.answer()
        context.user_data["pending"] = [k for k in ALL_KEYS if k in selected]
        return await _begin_collecting(update, context)

    # Toggle a single measurement, then refresh the keyboard in place.
    key = data.split(":", 1)[1]
    selected.discard(key) if key in selected else selected.add(key)
    await query.answer()
    await query.edit_message_reply_markup(reply_markup=_select_keyboard(selected))
    return SELECT


# --- Collecting -----------------------------------------------------------

async def _begin_collecting(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Acknowledge the selection and prompt for the first chosen measurement."""
    context.user_data["values"] = {}
    pending: list[str] = context.user_data["pending"]
    labels = ", ".join(_MEASUREMENTS[k][0] for k in pending)
    first_prompt = _MEASUREMENTS[pending[0]][2]
    if update.callback_query:
        await update.callback_query.edit_message_text(f"Let's log: {labels} 💪")
        await context.bot.send_message(
            chat_id=update.effective_chat.id, text=first_prompt, parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(first_prompt, parse_mode="Markdown")
    return COLLECTING


@restricted
async def handle_measurement(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Validate and store one measurement, then prompt the next (or summarise)."""
    pending: list[str] = context.user_data.get("pending", [])
    values: dict = context.user_data.setdefault("values", {})
    idx = len(values)  # index into pending of the field we're expecting now
    if idx >= len(pending):
        return await _show_summary(update, context)

    key = pending[idx]
    label = _MEASUREMENTS[key][0]
    raw = (update.message.text or "").strip().replace(",", ".")
    try:
        value = float(raw)
    except ValueError:
        await update.message.reply_text(
            f"Hmm, that doesn't look like a number. Please enter your *{label}* "
            f"as a number (e.g. 82.5):",
            parse_mode="Markdown",
        )
        return COLLECTING
    if value <= 0:
        await update.message.reply_text(
            f"Please enter a positive number for your *{label}* (e.g. 82.5):",
            parse_mode="Markdown",
        )
        return COLLECTING

    values[key] = round(value, 2)
    idx = len(values)
    if idx < len(pending):
        await update.message.reply_text(_MEASUREMENTS[pending[idx]][2], parse_mode="Markdown")
        return COLLECTING

    return await _show_summary(update, context)


async def _show_summary(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    values: dict = context.user_data["values"]
    pending: list[str] = context.user_data["pending"]
    date_str = datetime.now(TZ).strftime("%d/%m/%Y")
    context.user_data["date"] = date_str

    lines = [f"📋 *Check-in — {date_str}*", ""]
    for key in pending:
        label, unit, _prompt = _MEASUREMENTS[key]
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

    # Route to this user's own tab in the spreadsheet.
    tab = config.tab_for_user(update.effective_user.id)
    if tab is None:
        logger.warning("No sheet tab configured for user_id=%s", update.effective_user.id)
        await query.edit_message_text(
            "⚠️ I don't have a sheet tab set up for your account, so I can't save "
            "this. Please ask the admin to add you."
        )
        context.user_data.clear()
        return ConversationHandler.END

    # Only the collected fields are present; sheets fills the rest with blanks.
    data = {"date": context.user_data["date"], **context.user_data["values"]}
    try:
        # gspread is synchronous; run it off the event loop.
        await asyncio.to_thread(append_measurements, data, tab)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to append row to Google Sheets")
        await query.edit_message_text(
            f"⚠️ Could not save to Google Sheets: {exc}\n\nPlease try again later."
        )
        context.user_data.clear()
        return ConversationHandler.END

    context.user_data.clear()
    await query.edit_message_text(
        f"Data successfully logged to your “{tab}” tab! Keep up the great work! 💪"
    )
    return ConversationHandler.END


@restricted
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text("Cancelled.")
    return ConversationHandler.END


async def weekly_reminder(context: ContextTypes.DEFAULT_TYPE) -> None:
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("📋 Log measurements", callback_data="start_track")]]
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
            CommandHandler("start", show_menu),
            CommandHandler("track", show_menu),
            MessageHandler(
                filters.Regex(r"(?i)^\s*(hi|hello|hey|oi|ol[aá]|menu)\s*$") & ~filters.COMMAND,
                show_menu,
            ),
            CallbackQueryHandler(show_menu, pattern="^start_track$"),
        ],
        states={
            MENU: [CallbackQueryHandler(on_menu_choice, pattern="^menu_(height|weight|measurements|pick|cancel)$")],
            SELECT: [CallbackQueryHandler(on_select, pattern="^(pick:[a-z_]+|pick_done|pick_cancel)$")],
            COLLECTING: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_measurement)],
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
