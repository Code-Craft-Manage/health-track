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
import os
from datetime import datetime, time
from functools import wraps
from pathlib import Path
from zoneinfo import ZoneInfo

from telegram import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Update,
)
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
import prefs
from sheets import FIELDS, append_measurements, last_values

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

# --- Self-heal / liveness ----------------------------------------------------
# Two layers, because the bot can fail two different ways (seen 2026-06-28, when
# the container stayed "Up" but stopped polling Telegram and `restart:
# unless-stopped` never fired because the process never exited):
#   1. The event loop writes HEARTBEAT_FILE every HEARTBEAT_INTERVAL seconds.
#      healthcheck.py fails the container probe if it goes stale, so a stalled
#      loop or hung process gets restarted by the autoheal sidecar.
#   2. A watchdog job exits the process if the Telegram poller has died while the
#      loop (and thus the heartbeat) is still alive — the case a heartbeat alone
#      can't see. `restart: unless-stopped` then brings the bot back.
HEARTBEAT_FILE = Path(__file__).resolve().parent / "data" / "heartbeat"
HEARTBEAT_INTERVAL = 30  # seconds
WATCHDOG_INTERVAL = 60  # seconds

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


def _short_date(date: str) -> str:
    """Trim a ``DD/MM/YYYY`` date to ``DD/MM``; leave anything else untouched."""
    parts = date.split("/")
    return "/".join(parts[:2]) if len(parts) == 3 else date


def _trend(key: str, value: float, context: ContextTypes.DEFAULT_TYPE) -> str:
    """A directional arrow comparing ``value`` to the last logged value.

    Neutral by design — body measurements aren't "good" up or down — so we only
    show direction: ⬆️ higher, ⬇️ lower. Returns ``""`` when the value is
    unchanged, when we have no prior value, or when it can't be parsed.
    """
    last = context.user_data.get("last", {}).get(key)
    if not last:
        return ""
    try:
        previous = float(last[0])
    except (TypeError, ValueError):
        return ""
    if value > previous:
        return " ⬆️"
    if value < previous:
        return " ⬇️"
    return ""


async def _load_last_values(user_id: int) -> dict:
    """Best-effort: the user's last logged value per field, ``{}`` on any failure.

    Read once per check-in (not per field) and never block entry — mirrors how
    guide images are sent best-effort.
    """
    tab = config.tab_for_user(user_id)
    if tab is None:
        return {}
    try:
        return await asyncio.to_thread(last_values, tab)
    except Exception:  # noqa: BLE001
        logger.exception("Could not load last values for user_id=%s", user_id)
        return {}


def _prompt_for(key: str, context: ContextTypes.DEFAULT_TYPE) -> str:
    """The measurement prompt, with a ``last: …`` reminder when we have history."""
    prompt = _MEASUREMENTS[key][2]
    last = context.user_data.get("last", {}).get(key)
    if not last:
        return prompt
    value, date = last
    unit = _MEASUREMENTS[key][1]
    suffix = f"last: {value} {unit}"
    if date:
        suffix += f" ({_short_date(date)})"
    return f"{prompt}\n_{suffix}_"


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
        "• /guides — view all the measurement reference images\n"
        "• /cancel — abort the current entry\n\n"
        "Tip: toggle the how-to images on/off from the menu "
        "(🖼️ Guide images).\n"
        "I'll also remind you every Saturday at 14:00."
    )


# --- Menu -----------------------------------------------------------------

def _menu_keyboard(show_guides: bool) -> InlineKeyboardMarkup:
    toggle = "🖼️ Guide images: ON" if show_guides else "🖼️ Guide images: OFF"
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("⚖️ Weight only", callback_data="menu_weight")],
            [InlineKeyboardButton("🧍 Measurements", callback_data="menu_measurements")],
            [InlineKeyboardButton("🧩 Pick measurements…", callback_data="menu_pick")],
            [InlineKeyboardButton("📏 Height only", callback_data="menu_height")],
            [InlineKeyboardButton(toggle, callback_data="menu_toggle_guides")],
            [InlineKeyboardButton("❌ Cancel", callback_data="menu_cancel")],
        ]
    )


@restricted
async def show_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry point (/start, /track, "Hi", reminder button): show the log menu."""
    context.user_data.clear()
    text = "Hi! 👋 What would you like to log?"
    show_guides = prefs.get_show_guides(update.effective_user.id)
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            text, reply_markup=_menu_keyboard(show_guides)
        )
    else:
        await update.message.reply_text(text, reply_markup=_menu_keyboard(show_guides))
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
    if choice == "menu_toggle_guides":
        uid = update.effective_user.id
        new_val = not prefs.get_show_guides(uid)
        prefs.set_show_guides(uid, new_val)
        await query.edit_message_reply_markup(reply_markup=_menu_keyboard(new_val))
        return MENU
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

# How-to-measure cards: one image per technique (left/right share a card).
GUIDES_DIR = Path(__file__).resolve().parent / "measurement-guides" / "cards"


def _guide_key(field: str) -> str:
    """Map a measurement field to its guide card (e.g. biceps_left -> biceps)."""
    for suffix in ("_left", "_right"):
        if field.endswith(suffix):
            return field[: -len(suffix)]
    return field


async def _send_guide(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    user_id: int,
    field: str,
    shown: set[str],
) -> None:
    """Send the how-to card for `field`, once per check-in. Best effort: a missing
    or failed image must never block the user from entering a measurement."""
    if not prefs.get_show_guides(user_id):  # user turned guide images off
        return
    key = _guide_key(field)
    if key in shown:
        return
    path = GUIDES_DIR / f"{key}.png"
    if not path.exists():
        return
    try:
        with path.open("rb") as photo:
            await context.bot.send_photo(chat_id=chat_id, photo=photo)
        shown.add(key)
    except Exception:  # noqa: BLE001
        logger.exception("Could not send guide image for %s", key)


# Order for "view all guides" (10 = Telegram's media-group max → one album).
GUIDE_CARDS: list[tuple[str, str]] = [
    ("neck", "Neck"), ("shoulders", "Shoulders"), ("chest", "Chest"),
    ("biceps", "Biceps"), ("waist", "Waist"), ("abdomen", "Abdomen"),
    ("hips", "Hips"), ("thigh", "Thigh"), ("calf", "Calf"), ("weight", "Weight"),
]


async def _send_all_guides(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> bool:
    """Send every guide card as a single album. Returns False if none are found."""
    media = [
        InputMediaPhoto(media=(GUIDES_DIR / f"{key}.png").read_bytes(), caption=label)
        for key, label in GUIDE_CARDS
        if (GUIDES_DIR / f"{key}.png").exists()
    ]
    if not media:
        return False
    try:
        await context.bot.send_media_group(chat_id=chat_id, media=media)
        return True
    except Exception:  # noqa: BLE001
        logger.exception("Could not send the guide album")
        return False


@restricted
async def guides_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/guides — show all measurement reference cards, anytime."""
    if not await _send_all_guides(context, update.effective_chat.id):
        await update.message.reply_text(
            "Sorry, the guide images aren't available right now."
        )


async def _begin_collecting(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Acknowledge the selection and prompt for the first chosen measurement."""
    context.user_data["values"] = {}
    shown: set[str] = context.user_data.setdefault("guides_shown", set())
    shown.clear()
    pending: list[str] = context.user_data["pending"]
    context.user_data["last"] = await _load_last_values(update.effective_user.id)
    labels = ", ".join(_MEASUREMENTS[k][0] for k in pending)
    first_prompt = _prompt_for(pending[0], context)
    chat_id = update.effective_chat.id
    if update.callback_query:
        await update.callback_query.edit_message_text(f"Let's log: {labels} 💪")
    await _send_guide(context, chat_id, update.effective_user.id, pending[0], shown)
    await context.bot.send_message(
        chat_id=chat_id, text=first_prompt, parse_mode="Markdown"
    )
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
        shown: set[str] = context.user_data.setdefault("guides_shown", set())
        await _send_guide(
            context, update.effective_chat.id, update.effective_user.id, pending[idx], shown
        )
        await update.message.reply_text(_prompt_for(pending[idx], context), parse_mode="Markdown")
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
        lines.append(f"• {label}: {_fmt(values[key])} {unit}{_trend(key, values[key], context)}")
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


async def _heartbeat(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Record that the event loop is alive by rewriting the heartbeat file.

    Runs on the asyncio loop, so if the loop stalls or the process hangs the file
    stops updating and the container healthcheck (healthcheck.py) goes red.
    """
    try:
        HEARTBEAT_FILE.parent.mkdir(parents=True, exist_ok=True)
        # Write-then-rename so healthcheck.py, which reads this file concurrently,
        # never sees a half-written (and thus "corrupt") heartbeat. replace() is
        # atomic within the same directory — mirrors prefs.py's atomic write.
        tmp = HEARTBEAT_FILE.with_suffix(".tmp")
        tmp.write_text(str(datetime.now(TZ).timestamp()), encoding="utf-8")
        tmp.replace(HEARTBEAT_FILE)
    except OSError:
        logger.exception("Could not write heartbeat file")


async def _poller_watchdog(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Exit if Telegram polling has stopped while the loop keeps running.

    The heartbeat only catches a stalled loop; a poller that dies on its own (as
    on 2026-06-28) leaves the loop — and the heartbeat — alive, so we detect that
    here and hard-exit. `os._exit` is deliberate: a graceful stop could block on
    the already-broken poller, and `restart: unless-stopped` restarts us anyway.
    """
    updater = context.application.updater
    if updater is not None and not updater.running:
        logger.error("Telegram poller is no longer running — exiting for a restart.")
        os._exit(1)


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


# Commands surfaced in Telegram's blue "Menu" button next to the message box.
# This is a curated list, not every command: /track is intentionally omitted
# (it's a hidden alias of /start), and each entry must have a handler below.
BOT_COMMANDS: list[BotCommand] = [
    BotCommand("start", "Open the menu and log a check-in"),
    BotCommand("guides", "View the measurement guide images"),
    BotCommand("help", "How this bot works"),
    BotCommand("cancel", "Cancel the current entry"),
]


async def _post_init(application) -> None:
    """Register the command menu so Telegram shows the ☰ Menu button.

    Best-effort: a transient Telegram error here must not stop the bot from
    starting, so we log and carry on (the menu just won't refresh this run).
    """
    try:
        await application.bot.set_my_commands(BOT_COMMANDS)
    except Exception:  # noqa: BLE001
        logger.exception("Could not register the bot command menu")


def main() -> None:
    application = (
        ApplicationBuilder()
        .token(config.TELEGRAM_BOT_TOKEN)
        .post_init(_post_init)
        .build()
    )

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
            MENU: [CallbackQueryHandler(on_menu_choice, pattern="^menu_(height|weight|measurements|pick|toggle_guides|cancel)$")],
            SELECT: [CallbackQueryHandler(on_select, pattern="^(pick:[a-z_]+|pick_done|pick_cancel)$")],
            COLLECTING: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_measurement)],
            CONFIRM: [CallbackQueryHandler(on_confirm, pattern="^(confirm|cancel)$")],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    application.add_handler(conversation)
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("guides", guides_command))

    # Proactive weekly reminder: Saturday at 14:00 local time.
    application.job_queue.run_daily(
        weekly_reminder,
        time=time(hour=14, minute=0, tzinfo=TZ),
        days=(SATURDAY,),
        name="weekly_reminder",
    )

    # Self-heal: keep the heartbeat fresh, and bail out if polling silently dies.
    application.job_queue.run_repeating(
        _heartbeat, interval=HEARTBEAT_INTERVAL, first=0, name="heartbeat"
    )
    application.job_queue.run_repeating(
        _poller_watchdog, interval=WATCHDOG_INTERVAL, first=WATCHDOG_INTERVAL, name="poller_watchdog"
    )

    logger.info("Health-Track bot starting (timezone=%s)…", config.TIMEZONE)
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
